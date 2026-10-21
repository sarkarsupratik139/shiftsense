"""Optional email notifications (secondary channel; in-app messaging is always the primary one).

Email is sent only when ALL of these hold:
  * the server is configured: SHIFTSENSE_EMAIL=smtp, SMTP_HOST and SHIFTSENSE_EMAIL_FROM (SMTP_PORT, SMTP_USER and
    SMTP_PASSWORD as the relay needs; STARTTLS unless SMTP_TLS=off). Credentials come from the server environment
    only and are never stored, logged or shown;
  * the recipient's account prefers "In-app and email" and has a VERIFIED address (confirmed with a code);
  * the workspace is live (a demo scenario never sends real email).

Statuses say only what is known:
  not_configured  email is not set up on this server: nothing was attempted
  not_sent        not attempted for a stated reason (no verified address, in-app preference, demo scenario)
  queued          claimed for sending (a crash after this point leaves it visible, not silently lost)
  sent            the SMTP relay accepted the message. This is NOT delivery: SMTP gives no delivery evidence
  failed          the relay refused it or could not be reached (reason recorded, no credentials)
"delivered" is never set: that would need a provider delivery callback, which this PoC does not have.

Each email has an idempotency key (e.g. reminder id + recipient). Repeating an operation never sends a second
copy of an email that was queued or sent; a failed one may be retried up to MAX_ATTEMPTS times.
"""
from __future__ import annotations

import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from . import db
from .store import _con, db_path

MAX_ATTEMPTS = 3
DONE = ("queued", "sent")


def _db(path: Path | None = None):
    con = _con(path)
    db.setup_once("mailer", con, _setup)
    return con


def _setup(con):
    con.execute("""CREATE TABLE IF NOT EXISTS emails (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, idem_key TEXT UNIQUE, kind TEXT, ref TEXT,
        username TEXT, recipient TEXT, subject TEXT, status TEXT, detail TEXT, attempts INTEGER,
        created TEXT, updated TEXT, sim_time TEXT)""")


def mask(addr: str) -> str:
    if not addr or "@" not in addr:
        return "—"
    name, dom = addr.split("@", 1)
    return f"{name[:1]}•••@{dom}"


# ------------------------------------------------------------------ transport
@dataclass
class Result:
    status: str
    detail: str = ""


class SmtpTransport:
    name = "smtp"

    def __init__(self, host: str, port: int, user: str, password: str, sender: str, tls: bool, timeout: float = 15):
        self.host, self.port, self._user, self._pw, self.sender, self.tls, self.timeout = \
            host, port, user, password, sender, tls, timeout

    def send(self, to: str, subject: str, body: str) -> Result:
        from email.utils import make_msgid
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg["Message-ID"] = mid = make_msgid(domain=self.sender.split("@")[-1] or None)
        msg.set_content(body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as s:
                if self.tls:
                    s.starttls(context=ssl.create_default_context())
                if self._user:
                    s.login(self._user, self._pw)
                refused = s.send_message(msg)
            if refused:
                return Result("failed", "Relay refused the recipient.")
            return Result("sent", f"Accepted by the mail relay (delivery not confirmed). Message-ID {mid} "
                                  "(use it to trace the message in the relay's logs).")
        except smtplib.SMTPAuthenticationError:
            return Result("failed", "Mail relay rejected the server's credentials.")
        except smtplib.SMTPRecipientsRefused:
            return Result("failed", "Relay refused the recipient.")
        except (smtplib.SMTPException, OSError) as e:
            return Result("failed", f"Mail relay error: {type(e).__name__}")


def transport():
    """The configured transport, or None when email is not configured on this server."""
    if os.environ.get("SHIFTSENSE_EMAIL", "off").lower() != "smtp":
        return None
    host, sender = os.environ.get("SMTP_HOST", ""), os.environ.get("SHIFTSENSE_EMAIL_FROM", "")
    if not (host and sender):
        return None
    try:
        port = int(os.environ.get("SMTP_PORT", "587"))
    except ValueError:
        return None
    return SmtpTransport(host, port, os.environ.get("SMTP_USER", ""), os.environ.get("SMTP_PASSWORD", ""), sender,
                         os.environ.get("SMTP_TLS", "on").lower() != "off")


def configured() -> bool:
    return transport() is not None


def status_text() -> str:
    return ("Email: configured (optional extra; in-app is always used)" if configured()
            else "Email: not configured on this server · in-app messaging only")


# ------------------------------------------------------------------ sending
def _row(con, key):
    r = con.execute("SELECT id, status, detail, attempts FROM emails WHERE idem_key = ?", (key,)).fetchone()
    return dict(zip(["id", "status", "detail", "attempts"], r)) if r else None


def send(run_id: str, key: str, *, kind: str, ref: str, account: dict | None, subject: str, body: str,
         sim_time: str = "", live: bool = True, require_verified: bool = True, address: str | None = None,
         via=None) -> dict:
    """Send (at most once per key) and record the attempt. Never raises for delivery problems."""
    acct = account or {}
    to = address if address is not None else (acct.get("email") or "")
    reason = None
    if not live:
        reason = ("not_sent", "Demo scenario: no real email is sent.")
    elif address is None and acct.get("contact_pref") != "email":
        reason = ("not_sent", "Recipient prefers in-app only.")
    elif not to:
        reason = ("not_sent", "No email address on the account.")
    elif require_verified and not acct.get("email_verified"):
        reason = ("not_sent", "Email address not verified yet.")
    tr = via if via is not None else transport()
    if reason is None and tr is None:
        reason = ("not_configured", "Email is not configured on this server.")
    now = datetime.now().isoformat(timespec="seconds")
    with _db() as con:
        prev = _row(con, key)
        if prev and (prev["status"] in DONE or prev["attempts"] >= MAX_ATTEMPTS):
            return {**prev, "duplicate": True}
        if reason:
            status, detail = reason
            if prev:
                con.execute("UPDATE emails SET status=?, detail=?, updated=? WHERE id=?", (status, detail, now, prev["id"]))
                return {"id": prev["id"], "status": status, "detail": detail, "attempts": prev["attempts"],
                        "duplicate": False}
            cur = con.execute("INSERT INTO emails (run_id, idem_key, kind, ref, username, recipient, subject, status, "
                              "detail, attempts, created, updated, sim_time) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (run_id, key, kind, ref, acct.get("username", ""), mask(to), subject, status, detail, 0,
                               now, now, sim_time))
            return {"id": int(cur.lastrowid), "status": status, "detail": detail, "attempts": 0, "duplicate": False}
        # claim the send first so a parallel retry cannot send a second copy
        if prev:
            cur = con.execute("UPDATE emails SET status='queued', attempts=attempts+1, updated=? WHERE id=? AND "
                              "status NOT IN ('queued','sent')", (now, prev["id"]))
            if cur.rowcount != 1:
                return {**_row(con, key), "duplicate": True}
            eid, attempts = prev["id"], prev["attempts"] + 1
        else:
            try:
                cur = con.execute("INSERT INTO emails (run_id, idem_key, kind, ref, username, recipient, subject, "
                                  "status, detail, attempts, created, updated, sim_time) VALUES "
                                  "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                  (run_id, key, kind, ref, acct.get("username", ""), mask(to), subject, "queued", "",
                                   1, now, now, sim_time))
            except db.IntegrityError:
                return {**_row(con, key), "duplicate": True}
            eid, attempts = int(cur.lastrowid), 1
    res = tr.send(to, subject, body)
    with _db() as con:
        con.execute("UPDATE emails SET status=?, detail=?, updated=? WHERE id=?",
                    (res.status, res.detail, datetime.now().isoformat(timespec="seconds"), eid))
    return {"id": eid, "status": res.status, "detail": res.detail, "attempts": attempts, "duplicate": False}


_COLS = ["id", "run_id", "kind", "ref", "username", "recipient", "subject", "status", "detail", "attempts", "created",
         "updated", "sim_time"]


def log(run_id: str, *, kind: str | None = None, ref: str | None = None) -> list[dict]:
    q, args = f"SELECT {', '.join(_COLS)} FROM emails WHERE run_id=?", [run_id]
    if kind:
        q, args = q + " AND kind=?", args + [kind]
    if ref is not None:
        q, args = q + " AND ref=?", args + [str(ref)]
    with _db() as con:
        rows = con.execute(q + " ORDER BY id DESC", args).fetchall()
    return [dict(zip(_COLS, r)) for r in rows]


LABEL = {"not_configured": "Email not configured", "not_sent": "Email not sent", "queued": "Email queued",
         "sent": "Email accepted by relay", "failed": "Email failed"}
