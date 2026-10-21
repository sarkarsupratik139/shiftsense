"""Attendance reminders (in-app) and check-in.

A team lead sends a reminder for an agent's shift. It is delivered to the agent's ShiftSense inbox (an in-app
notification); email is an optional extra handled by `mailer`. The agent signs in and presses "Check in", which
the server validates (own shift, inside the window, not already recorded). Only a successful check-in records
attendance; a reminder never does.

States are kept apart and never inferred from one another:
  created     the reminder row exists
  in inbox    an in-app notification was written for an agent who has an active account
              (no account -> "no account": nobody can read it)
  opened      the agent opened the notification (recorded on the notification itself)
  checked in  the agent completed a valid check-in

SMS was retired. Older rows with channel "sms" are kept as history with their original (simulated) status; the
SMS link tokens in their bodies are masked and no longer accepted anywhere.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import db
from .store import _con, db_path

MAX_ATTEMPTS = 3
OPENS_BEFORE = 1          # intervals (30 min) before shift start that check-in opens
CLOSES_AFTER = 2          # intervals (1 h) after shift start that check-in closes


def _db(path: Path | None = None):
    con = _con(path)
    db.setup_once("attendance", con, _setup)
    return con


def _setup(con):
    con.execute("""CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, agent_id TEXT, day INTEGER, shift TEXT, recipient TEXT,
        channel TEXT, body TEXT, status TEXT, provider TEXT, provider_id TEXT, error TEXT, attempt INTEGER,
        created TEXT, sim_time TEXT, sent_by TEXT, token_hash TEXT UNIQUE, opens_t INTEGER, expires_t INTEGER,
        used_sim TEXT, used_via TEXT)""")
    if "late_min" not in con.columns("reminders"):
        con.execute("ALTER TABLE reminders ADD COLUMN late_min INTEGER")
    _mask_stored_tokens(con)


def _mask_stored_tokens(con):
    """Older versions kept the full SMS link token in simulated message bodies. Mask them in place (delivery log
    only, not the audit chain)."""
    rows = con.execute("SELECT id, body FROM reminders WHERE body LIKE '%checkin=%'").fetchall()
    for rid, body in rows:
        masked = re.sub(r"(checkin=)([A-Za-z0-9_-]{4})[A-Za-z0-9_-]+", r"\1\2…", body)
        if masked != body:
            con.execute("UPDATE reminders SET body = ? WHERE id = ?", (masked, rid))


# ------------------------------------------------------------------ reminders
def create(run_id: str, *, agent_id: str, day: int, shift: str, start_t: int, account: dict | None, sim_time: str,
           sent_by: str, resend: bool = False) -> dict:
    """Create one in-app reminder. Refuses duplicates (unless resend) and caps attempts. The caller writes the
    inbox notification and then records it with `set_inbox`."""
    with _db() as con:
        prior = con.execute("SELECT COUNT(*), SUM(CASE WHEN used_via IS NOT NULL THEN 1 ELSE 0 END) FROM reminders WHERE run_id=? AND agent_id=? "
                            "AND day=?", (run_id, agent_id, day)).fetchone()
    n_prior, used = prior[0] or 0, prior[1] or 0
    if used:
        raise ValueError("This agent has already checked in for that shift.")
    if n_prior and not resend:
        raise ValueError("A reminder was already sent for this shift. Use 'Send again' if it is really needed.")
    if n_prior >= MAX_ATTEMPTS:
        raise ValueError(f"Reminder limit reached ({MAX_ATTEMPTS} per shift).")
    opens, expires = start_t - OPENS_BEFORE, start_t + CLOSES_AFTER
    who = (account or {}).get("username", "")
    status, err = ("created", "") if who else (
        "no_account", "No active ShiftSense account for this agent: nobody can read an in-app reminder.")
    body = f"Reminder for your {shift} shift. Open ShiftSense and check in when you arrive."
    with _db() as con:
        cur = con.execute(
            "INSERT INTO reminders (run_id, agent_id, day, shift, recipient, channel, body, status, provider, "
            "provider_id, error, attempt, created, sim_time, sent_by, token_hash, opens_t, expires_t) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, agent_id, day, shift, who, "in_app", body, status, "in-app", "", err, n_prior + 1,
             datetime.now().isoformat(timespec="seconds"), sim_time, sent_by, None, opens, expires))
        rid = int(cur.lastrowid)
    return {"id": rid, "status": status, "error": err, "channel": "in_app", "recipient": who,
            "attempt": n_prior + 1, "opens_t": opens, "expires_t": expires}


def set_inbox(reminder_id: int, notice_id: int | None):
    """Record that the in-app notification exists (delivered to the inbox) and which notice it is."""
    if not notice_id:
        return
    with _db() as con:
        con.execute("UPDATE reminders SET status='in_inbox', provider_id=? WHERE id=? AND status='created'",
                    (f"notice #{notice_id}", reminder_id))


def _claim(con, where: str, args: tuple, now_t: int, via: str, sim_time: str, interval_min: int = 30) -> tuple[str, int]:
    row = con.execute(f"SELECT id, agent_id, day, opens_t, expires_t, used_sim FROM reminders WHERE {where}",
                      args).fetchone()
    if not row:
        raise ValueError("This check-in is not for you or no longer exists.")
    rid, agent_id, day, opens, expires, used = row
    if used:
        raise ValueError(f"Already checked in ({used}).")
    if now_t < opens:
        raise ValueError("Check-in is not open yet: it opens 30 minutes before the shift starts.")
    if now_t > expires:
        raise ValueError("Check-in for this shift has closed (1 hour after the start). Ask your team lead to "
                         "confirm your attendance.")
    late = max(0, now_t - (expires - CLOSES_AFTER)) * interval_min       # minutes after the shift start
    cur = con.execute("UPDATE reminders SET used_sim=?, used_via=?, late_min=? WHERE id=? AND used_sim IS NULL",
                      (sim_time, via, late, rid))
    if cur.rowcount != 1:                               # lost a race with a parallel submission
        raise ValueError("Already checked in.")
    con.execute("UPDATE reminders SET used_sim=? WHERE run_id=(SELECT run_id FROM reminders WHERE id=?) AND agent_id=? "
                "AND day=? AND used_sim IS NULL", (f"superseded by #{rid}", rid, agent_id, day))
    return agent_id, int(day)


def redeem_in_app(run_id: str, reminder_id: int, agent_id: str, now_t: int, sim_time: str,
                  interval_min: int = 30) -> tuple[str, int]:
    """The signed-in agent's own check-in. The agent id comes from the server-side account, never the browser."""
    with _db() as con:
        return _claim(con, "id = ? AND run_id = ? AND agent_id = ?", (reminder_id, run_id, agent_id), now_t, "in-app",
                      sim_time, interval_min)


def lateness(r: dict) -> str:
    """'on time' / 'N min after start' for a used reminder; '' otherwise. The simulated clock moves in whole
    intervals, so lateness is known only to the interval (30 min)."""
    if not r.get("used_via") or r.get("late_min") is None:
        return ""
    return "on time" if r["late_min"] == 0 else f"{r['late_min']} min after start"


def late_checkins(run_id: str, days: set[int] | None = None) -> list[dict]:
    return [r for r in log(run_id) if r["used_via"] and (r.get("late_min") or 0) > 0
            and (days is None or r["day"] in days)]


_COLS = ["id", "agent_id", "day", "shift", "recipient", "channel", "body", "status", "provider", "provider_id", "error",
         "attempt", "created", "sim_time", "sent_by", "opens_t", "expires_t", "used_sim", "used_via", "late_min"]


def log(run_id: str, agent_ids: set[str] | None = None) -> list[dict]:
    with _db() as con:
        rows = con.execute(f"SELECT {', '.join(_COLS)} FROM reminders WHERE run_id=? ORDER BY id DESC",
                           (run_id,)).fetchall()
    out = [dict(zip(_COLS, r)) for r in rows]
    return [r for r in out if agent_ids is None or r["agent_id"] in agent_ids]


def open_for(run_id: str, agent_id: str, now_t: int) -> list[dict]:
    """Unused, unexpired check-ins for one agent, one per shift (newest reminder wins)."""
    seen, out = set(), []
    for r in log(run_id, {agent_id}):
        if not r["used_sim"] and now_t <= r["expires_t"] and r["day"] not in seen:
            seen.add(r["day"])
            out.append(r)
    return out


STATE = {"created": "Created (not in inbox)", "in_inbox": "In inbox", "no_account": "Not delivered: no account",
         "simulated": "SMS (retired) · simulated, nothing sent", "not_sent": "SMS (retired) · not sent",
         "queued": "SMS (retired) · queued", "sent": "SMS (retired) · sent", "delivered": "SMS (retired) · delivered",
         "failed": "SMS (retired) · failed", "not_configured": "SMS (retired) · not configured"}


def state_text(r: dict) -> str:
    return STATE.get(r["status"], r["status"])
