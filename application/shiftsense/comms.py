"""Persistent, role-aware notifications, threaded messages, governance cases and a
tamper-evident audit log (SQLite, separate tables; never cleared by a workspace reset).

Recipients are addressed, never broadcast:
    {"user": "tl1"} · {"role": "team_lead", "team": "TL-1"} · {"role": "ops_head"} · {"agent": "A01"}
Audit rows are hash-chained: each row stores sha256(previous hash + its own content), so any
edit or deletion of an earlier row is detected by verify_audit(). There is no update/delete API.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from . import db
from .store import _con, db_path

PRIORITY_ORDER = {"Critical": 0, "Warning": 1, "Normal": 2, "Info": 3}


SCHEMA = """
    CREATE TABLE IF NOT EXISTS notices (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, dedupe TEXT UNIQUE, created TEXT, sim_time TEXT,
        priority TEXT, title TEXT, body TEXT, sender TEXT, recipient TEXT, related_kind TEXT, related_ref TEXT,
        action_required INTEGER, due_t INTEGER, status TEXT, read_by TEXT);
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, thread TEXT, created TEXT, sim_time TEXT,
        sender TEXT, sender_role TEXT, body TEXT, read_by TEXT);
    CREATE TABLE IF NOT EXISTS cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, flag_key TEXT UNIQUE, created TEXT, sim_time TEXT,
        kind TEXT, title TEXT, evidence TEXT, team TEXT, subject_user TEXT, reviewer TEXT, status TEXT,
        due_t INTEGER, outcome TEXT);
    CREATE TABLE IF NOT EXISTS case_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, created TEXT, sim_time TEXT, actor TEXT,
        role TEXT, action TEXT, from_status TEXT, to_status TEXT, note TEXT);
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, created TEXT, sim_time TEXT, actor TEXT, role TEXT,
        action TEXT, record TEXT, before TEXT, after TEXT, reason TEXT, status TEXT, prev_hash TEXT, hash TEXT);
    """


def _db(path: Path | None = None):
    con = _con(path)
    db.setup_once("comms", con, _setup)
    return con


def _setup(con):
    con.executescript(SCHEMA)
    if con.kind == "postgres":
        for stmt in AUDIT_GUARD_PG:            # one at a time: the function body contains semicolons
            con.raw.execute(stmt)
    else:
        con.executescript(AUDIT_GUARD_SQLITE)


AUDIT_GUARD_SQLITE = """
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'the audit trail is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'the audit trail is append-only'); END
"""
AUDIT_GUARD_PG = [
    "CREATE OR REPLACE FUNCTION audit_append_only() RETURNS trigger LANGUAGE plpgsql AS "
    "$$ BEGIN RAISE EXCEPTION 'the audit trail is append-only'; END $$",
    "DROP TRIGGER IF EXISTS audit_no_change ON audit_log",
    "CREATE TRIGGER audit_no_change BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW "
    "EXECUTE FUNCTION audit_append_only()",
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ notifications
def notify(run_id: str, recipient: dict, title: str, *, body: str = "", priority: str = "Info",
           sender: str = "system", related: tuple[str, str] | None = None, action_required: bool = False,
           due_t: int | None = None, sim_time: str = "", dedupe: str | None = None) -> int | None:
    """Store one notification; returns its id, or None if an identical one (same dedupe key) exists."""
    with _db() as con:
        try:
            cur = con.execute(
                "INSERT INTO notices (run_id, dedupe, created, sim_time, priority, title, body, sender, recipient, "
                "related_kind, related_ref, action_required, due_t, status, read_by) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, dedupe, _now(), sim_time, priority, title, body, sender, json.dumps(recipient, sort_keys=True),
                 related[0] if related else None, str(related[1]) if related else None, int(action_required), due_t,
                 "action" if action_required else "unread", "[]"))
            return int(cur.lastrowid)
        except db.IntegrityError:
            return None


def _matches(rec: dict, user: dict) -> bool:
    if "user" in rec:
        return rec["user"] == user.get("username")
    if "agent" in rec:
        return rec["agent"] == user.get("agent_id")
    if rec.get("role") != user.get("role"):
        return False
    return rec.get("team") in (None, user.get("team"))


def notices_for(user: dict, run_id: str, now_t: int | None = None) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT id, created, sim_time, priority, title, body, sender, recipient, related_kind, "
                           "related_ref, action_required, due_t, status, read_by FROM notices WHERE run_id = ? "
                           "ORDER BY id DESC", (run_id,)).fetchall()
    keys = ["id", "created", "sim_time", "priority", "title", "body", "sender", "recipient", "related_kind",
            "related_ref", "action_required", "due_t", "status", "read_by"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["recipient"] = json.loads(d["recipient"])
        if not _matches(d["recipient"], user):
            continue
        d["read_by"] = json.loads(d["read_by"])
        d["read"] = user.get("username") in d["read_by"]
        d["overdue"] = bool(d["action_required"] and d["status"] == "action" and now_t is not None
                            and d["due_t"] is not None and now_t > d["due_t"])
        out.append(d)
    return sorted(out, key=lambda d: (d["status"] != "action", PRIORITY_ORDER.get(d["priority"], 9), -d["id"]))


def all_notices(run_id: str, title_prefix: str = "") -> list[dict]:
    """Every notice of a run (for oversight lists such as Ops follow-ups), newest first."""
    with _db() as con:
        rows = con.execute("SELECT id, created, sim_time, priority, title, body, sender, recipient, related_kind, "
                           "related_ref, action_required, due_t, status FROM notices WHERE run_id = ? AND title LIKE ? "
                           "ORDER BY id DESC", (run_id, title_prefix + "%")).fetchall()
    keys = ["id", "created", "sim_time", "priority", "title", "body", "sender", "recipient", "related_kind",
            "related_ref", "action_required", "due_t", "status"]
    out = [dict(zip(keys, r)) for r in rows]
    for d in out:
        d["recipient"] = json.loads(d["recipient"])
    return out


def complete(notice_id: int, username: str, recipient_check) -> bool:
    """Recipient marks an action-required notice (e.g. a follow-up) as done."""
    with _db() as con:
        row = con.execute("SELECT recipient, status FROM notices WHERE id = ?", (notice_id,)).fetchone()
        if not row or not recipient_check(json.loads(row[0])):
            return False
        con.execute("UPDATE notices SET status = 'resolved' WHERE id = ? AND status IN ('action', 'unread')",
                    (notice_id,))
    mark_read(notice_id, username)
    return True


def delete_notices(run_id: str, ids: list[int]) -> int:
    """Remove specific notifications (used only by the targeted legacy-demo cleanup; never audit rows)."""
    if not ids:
        return 0
    with _db() as con:
        cur = con.execute(f"DELETE FROM notices WHERE run_id = ? AND id IN ({','.join('?' * len(ids))})",
                          (run_id, *ids))
        return cur.rowcount


def mark_read(notice_id: int, username: str):
    with _db() as con:
        row = con.execute("SELECT read_by FROM notices WHERE id = ?", (notice_id,)).fetchone()
        if row:
            rb = set(json.loads(row[0])) | {username}
            con.execute("UPDATE notices SET read_by = ? WHERE id = ?", (json.dumps(sorted(rb)), notice_id))


def acknowledge(notice_id: int, username: str):
    mark_read(notice_id, username)
    with _db() as con:
        con.execute("UPDATE notices SET status = 'acknowledged' WHERE id = ? AND status = 'unread'", (notice_id,))


def related_notice(run_id: str, kind: str, ref) -> dict | None:
    """The notice attached to one record (e.g. an attendance reminder), with who has opened it."""
    with _db() as con:
        r = con.execute("SELECT id, recipient, status, read_by FROM notices WHERE run_id=? AND related_kind=? AND "
                        "related_ref=? ORDER BY id DESC LIMIT 1", (run_id, kind, str(ref))).fetchone()
    return {"id": r[0], "recipient": json.loads(r[1]), "status": r[2], "read_by": json.loads(r[3])} if r else None


def resolve_related(run_id: str, kind: str, ref) -> int:
    """Close action-required notices once their record has been acted on."""
    with _db() as con:
        cur = con.execute("UPDATE notices SET status = 'resolved' WHERE run_id = ? AND related_kind = ? "
                          "AND related_ref = ? AND status IN ('action', 'unread')", (run_id, kind, str(ref)))
        return cur.rowcount


# ------------------------------------------------------------------ messages
def post(run_id: str, thread: str, sender: str, sender_role: str, body: str, sim_time: str = "") -> int:
    if not body.strip():
        raise ValueError("Message is empty.")
    with _db() as con:
        cur = con.execute("INSERT INTO messages (run_id, thread, created, sim_time, sender, sender_role, body, read_by) "
                          "VALUES (?,?,?,?,?,?,?,?)", (run_id, thread, _now(), sim_time, sender, sender_role,
                                                       body.strip(), "[]"))
        return int(cur.lastrowid)


def thread(run_id: str, thread_key: str) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT id, created, sim_time, sender, sender_role, body FROM messages "
                           "WHERE run_id = ? AND thread = ? ORDER BY id", (run_id, thread_key)).fetchall()
    return [dict(zip(["id", "created", "sim_time", "sender", "sender_role", "body"], r)) for r in rows]


# ------------------------------------------------------------------ governance cases
CASE_FLOW = {   # allowed transitions
    "Flagged": {"Under Review", "Dismissed"},
    "Under Review": {"Explanation Requested", "Referred to Operations", "Action Required", "Resolved", "Dismissed"},
    "Explanation Requested": {"Under Review"},
    "Referred to Operations": {"Action Required", "Resolved", "Under Review"},
    "Action Required": {"Resolved"},
    "Resolved": {"Closed"},
    "Dismissed": set(),
    "Closed": set(),
}


def open_case(run_id: str, flag: dict, sim_time: str = "") -> int | None:
    """Open one review case per pattern per run (the key is scoped to the run, so a demo scenario can
    never block or duplicate a live case)."""
    with _db() as con:
        try:
            cur = con.execute(
                "INSERT INTO cases (run_id, flag_key, created, sim_time, kind, title, evidence, team, subject_user, "
                "reviewer, status, due_t, outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, f"{run_id}|{flag['key']}", _now(), sim_time, flag["type"], flag["title"], json.dumps(flag, default=str),
                 flag.get("team"), flag.get("subject_user"), None, "Flagged", None, ""))
            cid = int(cur.lastrowid)
            con.execute("INSERT INTO case_events (case_id, created, sim_time, actor, role, action, from_status, "
                        "to_status, note) VALUES (?,?,?,?,?,?,?,?,?)",
                        (cid, _now(), sim_time, "system", "system", "Flagged", None, "Flagged", flag["threshold"]))
            return cid
        except db.IntegrityError:
            return None


def cases(run_id: str) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT id, created, sim_time, kind, title, evidence, team, subject_user, reviewer, status, "
                           "due_t, outcome FROM cases WHERE run_id = ? ORDER BY id DESC", (run_id,)).fetchall()
    keys = ["id", "created", "sim_time", "kind", "title", "evidence", "team", "subject_user", "reviewer", "status",
            "due_t", "outcome"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["evidence"] = json.loads(d["evidence"])
        out.append(d)
    return out


def case_events(case_id: int) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT created, sim_time, actor, role, action, from_status, to_status, note FROM case_events "
                           "WHERE case_id = ? ORDER BY id", (case_id,)).fetchall()
    return [dict(zip(["created", "sim_time", "actor", "role", "action", "from_status", "to_status", "note"], r))
            for r in rows]


def case_event(case_id: int, actor: str, role: str, action: str, note: str, to_status: str | None = None,
               sim_time: str = "", reviewer: str | None = None, due_t: int | None = None, outcome: str | None = None):
    """Record an action on a case; validates the status transition when one is requested."""
    with _db() as con:
        cur = con.execute("SELECT status FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not cur:
            raise ValueError("Unknown case.")
        frm = cur[0]
        if to_status and to_status != frm and to_status not in CASE_FLOW.get(frm, set()):
            raise ValueError(f"A case that is '{frm}' cannot move to '{to_status}'.")
        con.execute("INSERT INTO case_events (case_id, created, sim_time, actor, role, action, from_status, to_status, "
                    "note) VALUES (?,?,?,?,?,?,?,?,?)", (case_id, _now(), sim_time, actor, role, action, frm,
                                                         to_status or frm, note))
        sets, args = ["status = ?"], [to_status or frm]
        if reviewer is not None:
            sets.append("reviewer = ?")
            args.append(reviewer)
        if due_t is not None:
            sets.append("due_t = ?")
            args.append(due_t)
        if outcome is not None:
            sets.append("outcome = ?")
            args.append(outcome)
        con.execute(f"UPDATE cases SET {', '.join(sets)} WHERE id = ?", (*args, case_id))
    return frm, to_status or frm


# ------------------------------------------------------------------ tamper-evident audit
def _hash(prev: str, row: tuple) -> str:
    return hashlib.sha256((prev + json.dumps(row, default=str, sort_keys=True)).encode()).hexdigest()


def audit(run_id: str, actor: str, role: str, action: str, record: str, before: str = "", after: str = "",
          reason: str = "", status: str = "", sim_time: str = "") -> int:
    with _db() as con:
        last = con.execute("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        prev = last[0] if last else "genesis"
        created = _now()
        row = (run_id, created, sim_time, actor, role, action, record, before, after, reason, status)
        cur = con.execute("INSERT INTO audit_log (run_id, created, sim_time, actor, role, action, record, before, after, "
                          "reason, status, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (*row, prev, _hash(prev, row)))
        return int(cur.lastrowid)


def audit_rows(run_id: str | None = None) -> list[dict]:
    with _db() as con:
        q = "SELECT id, run_id, created, sim_time, actor, role, action, record, before, after, reason, status FROM audit_log"
        rows = con.execute(q + (" WHERE run_id = ?" if run_id else "") + " ORDER BY id DESC",
                           (run_id,) if run_id else ()).fetchall()
    keys = ["id", "run_id", "created", "sim_time", "actor", "role", "action", "record", "before", "after", "reason",
            "status"]
    return [dict(zip(keys, r)) for r in rows]


def verify_audit() -> tuple[bool, int | None]:
    """(True, None) if the chain is intact, else (False, first broken row id)."""
    with _db() as con:
        rows = con.execute("SELECT id, run_id, created, sim_time, actor, role, action, record, before, after, reason, "
                           "status, prev_hash, hash FROM audit_log ORDER BY id").fetchall()
    prev = "genesis"
    for r in rows:
        if r[12] != prev or _hash(prev, tuple(r[1:12])) != r[13]:
            return False, r[0]
        prev = r[13]
    return True, None
