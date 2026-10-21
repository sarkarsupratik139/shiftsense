"""HR records and rules (Decision Record HR-01..HR-08).

Storage and pure logic only; every user action goes through `services` (permission, scope, audit).

* Policy: versioned HR settings. Each item is "approved" or "pending confirmation"; a pending item that switches
  a feature on (absence threshold, overtime threshold) keeps that feature off. Nothing is invented: retention
  periods are not set at all (register only).
* Separations: recorded -> confirmed -> executed (account deactivated on the effective date), or rescinded /
  corrected. Unconfirmed separations never deactivate anything. Execution is idempotent.
* Leave balances: HR-entered opening balances (unverified until verified by a second HR user), a transaction
  ledger (debit on approval, credit on cancellation, adjustments with a reason). Only verified balances count.
* Review tasks: leave triggers create an HR review task; a task never approves or rejects.
* Attendance corrections after the 48 h lock: requested, then applied or rejected by an HR reviewer.
* Absence flags (confidential, non-disciplinary), overtime approval tasks, restricted HR cases.

Live and demo stay apart: run-bound records carry the run id; balance transactions made in a demo scenario
("sim-" run) count only inside that scenario.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import db

db.migration(201, "hr decision record", """
CREATE TABLE IF NOT EXISTS hr_policy (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pkey TEXT NOT NULL, value TEXT, status TEXT NOT NULL, reference TEXT,
    note TEXT, changed_by TEXT, changed_at TEXT);
CREATE TABLE IF NOT EXISTS hr_separations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL, username TEXT, effective_date TEXT NOT NULL,
    reason_category TEXT NOT NULL, note TEXT, status TEXT NOT NULL, recorded_by TEXT, recorded_by_user TEXT,
    recorded_at TEXT, confirmed_by TEXT, confirmed_at TEXT, executed_at TEXT, deactivated INTEGER DEFAULT 0,
    rescinded_by TEXT, rescinded_at TEXT, rescind_reason TEXT, correction_of INTEGER, corrected_by_id INTEGER);
CREATE TABLE IF NOT EXISTS hr_leave_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL, leave_type_id TEXT NOT NULL,
    opening_balance REAL NOT NULL, effective_date TEXT NOT NULL, unit TEXT NOT NULL, entered_by TEXT,
    entered_by_user TEXT, entered_at TEXT, verified_by TEXT, verified_at TEXT, source_reference TEXT,
    status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS hr_leave_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL, leave_type_id TEXT NOT NULL, kind TEXT NOT NULL,
    amount REAL NOT NULL, request_id TEXT, reason TEXT, by_name TEXT, at TEXT, run_id TEXT);
CREATE TABLE IF NOT EXISTS hr_review_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, request_id TEXT NOT NULL, employee_id TEXT,
    trigger_name TEXT NOT NULL, reason TEXT, status TEXT NOT NULL, assigned_to TEXT, created_at TEXT,
    recommendation TEXT, recommended_by TEXT, recommended_at TEXT, note TEXT, closed_at TEXT);
CREATE TABLE IF NOT EXISTS hr_attendance_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, employee_id TEXT NOT NULL, day INTEGER NOT NULL,
    original TEXT, requested TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, requested_by TEXT,
    requested_by_user TEXT, requested_at TEXT, reviewed_by TEXT, reviewed_at TEXT, review_reason TEXT);
CREATE TABLE IF NOT EXISTS hr_absence_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, flag_key TEXT NOT NULL UNIQUE,
    employee_id TEXT NOT NULL, window_start INTEGER, window_end INTEGER, absences INTEGER, threshold INTEGER,
    status TEXT NOT NULL, created_at TEXT, updated_by TEXT, updated_at TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS hr_overtime_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, task_key TEXT NOT NULL UNIQUE,
    employee_id TEXT NOT NULL, period TEXT, worked_hours REAL, scheduled_unconfirmed_hours REAL, threshold REAL,
    status TEXT NOT NULL, created_at TEXT, decided_by TEXT, decided_at TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS hr_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, confidentiality TEXT NOT NULL, status TEXT NOT NULL,
    caseworker TEXT, subject_employee TEXT, summary TEXT, opened_by TEXT, opened_at TEXT, updated_at TEXT,
    deadline TEXT, outcome TEXT, closed_by TEXT, closed_at TEXT);
CREATE TABLE IF NOT EXISTS hr_case_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, kind TEXT NOT NULL, body TEXT, due TEXT,
    by_name TEXT, by_user TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS hr_case_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, username TEXT, action TEXT, allowed INTEGER, at TEXT);
""")

_ready: dict = {}


def ensure():
    if not _ready.get(db.key()):
        db.migrate()
        _ready[db.key()] = True


def _db():
    ensure()
    return db.connect()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _q(sql: str, args=()) -> list[dict]:
    with _db() as con:
        cur = con.execute(sql, args)
        rows = cur.fetchall()
        cols = [c[0] for c in cur._c.description] if cur._c.description else []
        return [dict(zip(cols, tuple(r))) for r in rows]


def _insert(con, table: str, rec: dict) -> int:
    cols = list(rec)
    cur = con.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                      tuple(rec[c] for c in cols))
    return cur.lastrowid


# ================================================================== policy (versioned)
LEAVE_TYPES = {                       # id -> (label, paid, unit)
    "annual": ("Annual leave", True, "days"),
    "casual": ("Casual leave", True, "days"),
    "sick": ("Sick leave", True, "days"),
    "unpaid": ("Unpaid leave", False, "days"),
}

# key -> (default value, default status, what it means). "pending" = awaiting HR policy confirmation.
POLICY = {
    "separation.timezone": ("Asia/Kolkata", "approved", "Business timezone in which the separation date is read."),
    "separation.effective_time": ("end_of_day", "approved",
                                  "end_of_day: access ends at 00:00 (business timezone) on the day after the last "
                                  "working day; start_of_day: access ends when the effective date begins."),
    "separation.second_person_confirms": (True, "approved",
                                          "A different HR user must confirm a separation before it can take effect."),
    "leave.trigger.unverified_balance": (True, "approved", "Paid leave with no verified (or too small) balance goes "
                                         "to HR review."),
    "leave.trigger.long_leave": (True, "approved", "Leave longer than the day limit goes to HR review."),
    "leave.long_leave_days": (5, "approved", "More than this many consecutive days of leave triggers review."),
    "leave.consecutive_basis": ("calendar_days", "approved",
                                "calendar_days: every date counts, weekends and holidays included; working_days: "
                                "days the person is not rostered and has no leave neither count nor break the run."),
    "leave.trigger.sick_leave": (True, "approved", "Sick leave longer than the sick-day limit goes to HR review."),
    "leave.sick_days": (2, "approved", "More than this many consecutive sick days triggers review."),
    "attendance.lock_hours": (48, "approved", "Attendance is locked this many hours after the shift ends; changes "
                              "after that need an HR-reviewed correction request."),
    "absence.flags_enabled": (False, "pending", "Confidential absence-pattern flags for HR. Off until the counting "
                              "rules are validated against the attendance model."),
    "absence.threshold": (3, "approved", "Distinct qualifying unplanned absences within the window that raise a flag "
                          "(a prompt for HR review, not a disciplinary finding)."),
    "absence.window_days": (30, "approved", "Rolling window in calendar days."),
    "overtime.hr_approval_enabled": (False, "approved", "HR approval of overtime. Off by default."),
    "overtime.monthly_threshold": (None, "pending", "Worked overtime above this per month needs HR approval."),
    "overtime.threshold_unit": ("hours", "pending", "Unit of the overtime threshold."),
    "overtime.threshold_scope": ("per_employee_per_month", "pending", "What the threshold is counted over."),
    "overtime.approval_policy_reference": ("", "pending", "Reference to the approved overtime policy."),
    "overtime.effective_from": ("", "pending", "Date the overtime approval rule starts."),
    "overtime.effective_to": ("", "pending", "Date it ends (blank = open-ended)."),
    "retention.policy": (None, "pending", "Retention periods for HR records. Not set: no automatic deletion until "
                         "EXL HR and legal approve periods. This is not a decision to keep records indefinitely."),
}

DECISION_REF = "HR policy decisions, 19 Oct 2026"
DEFAULT_REFS = {k: DECISION_REF for k in ("separation.timezone", "separation.effective_time",
                                          "leave.consecutive_basis", "leave.long_leave_days", "absence.threshold",
                                          "absence.window_days")}

RETENTION_REGISTER = [            # HR-08: shown as pending; nothing is deleted automatically
    ("Separation records", "Not set — awaiting retention policy"),
    ("Leave balances and transactions", "Not set — awaiting retention policy"),
    ("Leave review tasks", "Not set — awaiting retention policy"),
    ("Attendance corrections", "Not set — awaiting retention policy"),
    ("Absence-pattern flags", "Not set — awaiting retention policy"),
    ("Overtime approval tasks", "Not set — awaiting retention policy"),
    ("Restricted HR cases and evidence references", "Not set — awaiting retention policy"),
    ("Audit trail", "Kept; append-only (never deleted by the application)"),
]


def policy_rows() -> list[dict]:
    """Current value of every policy item (latest version) plus its history count."""
    hist = _q("SELECT * FROM hr_policy ORDER BY id")
    out = []
    for k, (val, status, meaning) in POLICY.items():
        mine = [h for h in hist if h["pkey"] == k]
        if mine:
            last = mine[-1]
            out.append({"key": k, "value": json.loads(last["value"]), "status": last["status"], "meaning": meaning,
                        "reference": last["reference"] or "", "changed_by": last["changed_by"],
                        "changed_at": last["changed_at"], "versions": len(mine) + 1})
        else:
            out.append({"key": k, "value": val, "status": status, "meaning": meaning,
                        "reference": DEFAULT_REFS.get(k, ""),
                        "changed_by": "default", "changed_at": "", "versions": 1})
    return out


def pol(key: str):
    r = next(x for x in policy_rows() if x["key"] == key)
    return r["value"], r["status"]


def value(key: str):
    return pol(key)[0]


def set_policy(key: str, val, status: str, reference: str, note: str, by: str) -> dict:
    if key not in POLICY:
        raise ValueError(f"Unknown HR policy item {key}.")
    if status not in ("approved", "pending"):
        raise ValueError("Status must be approved or pending.")
    if status == "approved" and not reference.strip():
        raise ValueError("Approving a policy value needs the policy reference (document or decision).")
    if key == "absence.threshold" and val is not None and (not isinstance(val, int) or val < 1):
        raise ValueError("The absence threshold must be a whole number of at least 1.")
    if key == "absence.flags_enabled" and val and pol("absence.threshold")[1] != "approved":
        raise ValueError("Approve the absence threshold first; flags stay off until it is approved.")
    if key == "overtime.hr_approval_enabled" and val and pol("overtime.monthly_threshold")[1] != "approved":
        raise ValueError("Approve the monthly overtime threshold first.")
    if key == "leave.consecutive_basis" and val not in ("calendar_days", "working_days"):
        raise ValueError("Basis must be calendar_days or working_days.")
    if key == "separation.effective_time" and val not in ("end_of_day", "start_of_day"):
        raise ValueError("Effective time must be end_of_day or start_of_day.")
    if key == "retention.policy" and val is not None:
        raise ValueError("Retention periods are deferred (HR-08); record the decision before setting them.")
    with _db() as con:
        _insert(con, "hr_policy", {"pkey": key, "value": json.dumps(val), "status": status, "reference": reference,
                                   "note": note, "changed_by": by, "changed_at": _now()})
    return next(x for x in policy_rows() if x["key"] == key)


def pending_items() -> list[dict]:
    return [r for r in policy_rows() if r["status"] == "pending"]


# ================================================================== HR-01 separations
SEPARATION_REASONS = ["Resignation", "End of contract", "Retirement", "Termination", "Transfer out", "Other"]


def separations(employee_id: str | None = None) -> list[dict]:
    if employee_id:
        return _q("SELECT * FROM hr_separations WHERE employee_id=? ORDER BY id", (employee_id,))
    return _q("SELECT * FROM hr_separations ORDER BY id")


def separation(sid: int) -> dict:
    r = _q("SELECT * FROM hr_separations WHERE id=?", (sid,))
    if not r:
        raise ValueError(f"Separation {sid} not found.")
    return r[0]


def record_separation(employee_id: str, username: str | None, effective_date: str, reason_category: str,
                      note: str, by: str, by_user: str, correction_of: int | None = None) -> dict:
    datetime.strptime(effective_date, "%Y-%m-%d")
    if reason_category not in SEPARATION_REASONS:
        raise ValueError(f"Reason category must be one of: {', '.join(SEPARATION_REASONS)}.")
    open_ = [s for s in separations(employee_id) if s["status"] in ("recorded", "confirmed")
             and s["id"] != correction_of]
    if open_:
        raise ValueError(f"A separation for {employee_id} is already open (#{open_[0]['id']}). Correct or rescind "
                         "it instead.")
    with _db() as con:
        sid = _insert(con, "hr_separations", {
            "employee_id": employee_id, "username": username, "effective_date": effective_date,
            "reason_category": reason_category, "note": note, "status": "recorded", "recorded_by": by,
            "recorded_by_user": by_user, "recorded_at": _now(), "correction_of": correction_of})
        if correction_of:
            con.execute("UPDATE hr_separations SET status='corrected', corrected_by_id=? WHERE id=?",
                        (sid, correction_of))
    return separation(sid)


def confirm_separation(sid: int, by: str, by_user: str) -> dict:
    s = separation(sid)
    if s["status"] != "recorded":
        raise ValueError(f"Only a recorded separation can be confirmed (this one is {s['status']}).")
    if value("separation.second_person_confirms") and s["recorded_by_user"] == by_user:
        raise ValueError("A different HR user must confirm this separation.")
    with _db() as con:
        con.execute("UPDATE hr_separations SET status='confirmed', confirmed_by=?, confirmed_at=? WHERE id=? AND "
                    "status='recorded'", (by, _now(), sid))
    return separation(sid)


def local_now() -> datetime:
    """Now in the business timezone (separation timing follows real time, not the workspace clock)."""
    return datetime.now(ZoneInfo(value("separation.timezone")))


def local_today() -> str:
    return local_now().date().isoformat()


def access_ended(username: str) -> bool:
    """True once a confirmed separation for this account has reached its effective time, even before the
    bookkeeping run has deactivated the account: access ends exactly on time."""
    if not username:
        return False
    rows = _q("SELECT * FROM hr_separations WHERE username=? AND status IN ('confirmed','executed')", (username,))
    today = local_today()
    return any(r["status"] == "executed" and r["deactivated"] or is_due(r, today) for r in rows)


def is_due(s: dict, today_iso: str) -> bool:
    """Confirmed separations whose effective time has arrived (business-timezone date)."""
    if s["status"] != "confirmed":
        return False
    if value("separation.effective_time") == "start_of_day":
        return today_iso >= s["effective_date"]
    return today_iso > s["effective_date"]


def mark_executed(sid: int, deactivated: bool) -> bool:
    """Idempotent: only the first call changes the row. Returns True if this call executed it."""
    with _db() as con:
        cur = con.execute("UPDATE hr_separations SET status='executed', executed_at=?, deactivated=? WHERE id=? AND "
                          "status='confirmed'", (_now(), int(deactivated), sid))
        return cur.rowcount == 1


def rescind_separation(sid: int, reason: str, by: str) -> dict:
    s = separation(sid)
    if s["status"] not in ("recorded", "confirmed", "executed"):
        raise ValueError(f"This separation is {s['status']}; nothing to rescind.")
    if not reason.strip():
        raise ValueError("A reason is required to rescind a separation.")
    with _db() as con:
        con.execute("UPDATE hr_separations SET status='rescinded', rescinded_by=?, rescinded_at=?, rescind_reason=? "
                    "WHERE id=?", (by, _now(), reason, sid))
    return {**s, "status": "rescinded"}


# ================================================================== HR-02 leave balances
def balances(employee_id: str | None = None) -> list[dict]:
    q, a = "SELECT * FROM hr_leave_balances", ()
    if employee_id:
        q, a = q + " WHERE employee_id=?", (employee_id,)
    return _q(q + " ORDER BY id", a)


def enter_opening_balance(employee_id: str, leave_type_id: str, opening: float, effective_date: str,
                          source_reference: str, by: str, by_user: str) -> dict:
    if leave_type_id not in LEAVE_TYPES:
        raise ValueError(f"Unknown leave type {leave_type_id}.")
    if opening is None or float(opening) < 0:
        raise ValueError("Opening balance must be zero or more.")
    datetime.strptime(effective_date, "%Y-%m-%d")
    if not source_reference.strip():
        raise ValueError("A source reference (where the figure comes from) is required.")
    with _db() as con:
        con.execute("UPDATE hr_leave_balances SET status='superseded' WHERE employee_id=? AND leave_type_id=? AND "
                    "status IN ('unverified','verified')", (employee_id, leave_type_id))
        bid = _insert(con, "hr_leave_balances", {
            "employee_id": employee_id, "leave_type_id": leave_type_id, "opening_balance": float(opening),
            "effective_date": effective_date, "unit": LEAVE_TYPES[leave_type_id][2], "entered_by": by,
            "entered_by_user": by_user, "entered_at": _now(), "source_reference": source_reference,
            "status": "unverified"})
    return _q("SELECT * FROM hr_leave_balances WHERE id=?", (bid,))[0]


def verify_balance(bid: int, by: str, by_user: str) -> dict:
    b = _q("SELECT * FROM hr_leave_balances WHERE id=?", (bid,))
    if not b:
        raise ValueError("Balance not found.")
    b = b[0]
    if b["status"] != "unverified":
        raise ValueError(f"This balance is {b['status']}.")
    if b["entered_by_user"] == by_user:
        raise ValueError("A different HR user must verify a balance they did not enter.")
    with _db() as con:
        con.execute("UPDATE hr_leave_balances SET status='verified', verified_by=?, verified_at=? WHERE id=? AND "
                    "status='unverified'", (by, _now(), bid))
    return _q("SELECT * FROM hr_leave_balances WHERE id=?", (bid,))[0]


def balance(employee_id: str, leave_type_id: str, run_id: str | None = None) -> dict:
    """{'state': 'verified'|'unverified'|'missing'|'not_tracked', 'available': float|None, ...}.
    Only a verified opening balance gives an available figure; otherwise it is unresolved (never assumed)."""
    if leave_type_id in LEAVE_TYPES and not LEAVE_TYPES[leave_type_id][1]:
        return {"state": "not_tracked", "available": None, "unit": "days"}
    cur = [b for b in balances(employee_id) if b["leave_type_id"] == leave_type_id
           and b["status"] in ("unverified", "verified")]
    if not cur:
        return {"state": "missing", "available": None, "unit": "days"}
    b = cur[-1]
    if b["status"] != "verified":
        return {"state": "unverified", "available": None, "unit": b["unit"], "balance_id": b["id"]}
    # live transactions always count; a demo scenario's own transactions count only inside that scenario
    moved = sum(t["amount"] for t in transactions(employee_id, run_id) if t["leave_type_id"] == leave_type_id
                and t["at"] >= b["entered_at"])
    return {"state": "verified", "available": round(float(b["opening_balance"]) + moved, 2), "unit": b["unit"],
            "balance_id": b["id"], "opening": b["opening_balance"], "effective_date": b["effective_date"]}


def add_transaction(con, employee_id: str, leave_type_id: str, kind: str, amount: float, request_id: str | None,
                    reason: str, by: str, run_id: str | None) -> int:
    if kind not in ("debit", "credit", "adjustment"):
        raise ValueError("Unknown transaction kind.")
    if kind == "adjustment" and not reason.strip():
        raise ValueError("An adjustment needs a reason.")
    return _insert(con, "hr_leave_transactions", {
        "employee_id": employee_id, "leave_type_id": leave_type_id, "kind": kind, "amount": float(amount),
        "request_id": request_id, "reason": reason, "by_name": by, "at": _now(), "run_id": run_id})


def transactions(employee_id: str | None = None, run_id: str | None = None) -> list[dict]:
    rows = _q("SELECT * FROM hr_leave_transactions" + (" WHERE employee_id=?" if employee_id else "") + " ORDER BY id",
              (employee_id,) if employee_id else ())
    return [r for r in rows if not (r["run_id"] or "").startswith("sim-") or r["run_id"] == run_id]


def debited(request_id: str, run_id: str) -> dict | None:
    """The debit a request produced (if any) and not yet credited back."""
    rows = [t for t in transactions(run_id=run_id) if t["request_id"] == request_id and t["run_id"] == run_id]
    net = sum(t["amount"] for t in rows)
    deb = next((t for t in rows if t["kind"] == "debit"), None)
    return deb if deb and net < 0 else None


# ================================================================== HR-03 leave review triggers
def consecutive_days(w, agent_id: str, day: int, sick_only: bool = False) -> int:
    """Length of the leave run containing `day` (approved leave + open requests + this day), on the configured
    basis. Sick-only counts only sick leave requests."""
    basis = value("leave.consecutive_basis")
    leave_days = set()
    for r in w.requests:
        if r.get("kind") == "leave" and r["agent_id"] == agent_id and r["state"] in ("Pending", "Approved") \
                and r.get("alternative") is None and (not sick_only or r.get("leave_type") == "sick"):
            leave_days.add(int(r["day"]))
    if not sick_only:
        lv = w.data["leave"]
        leave_days |= {int(d) for d in lv[lv.agent_id == agent_id].day}
    leave_days.add(int(day))
    rostered = {int(d) for d in w.roster[w.roster.agent_id == agent_id].day}

    def neutral(d):          # working-days basis: a day off (not rostered, no leave) neither counts nor breaks
        return basis == "working_days" and d not in leave_days and d not in rostered

    n = 1
    for step in (-1, 1):
        d = day + step
        while d in leave_days or neutral(d):
            if d in leave_days:
                n += 1
            d += step
            if abs(d - day) > 60:
                break
    return n


def evaluate_triggers(w, agent_id: str, day: int, leave_type: str) -> list[dict]:
    """Which review triggers fire for this leave request. A trigger that cannot be evaluated is reported as
    'unresolved' (it still routes to review). Returns [{'trigger','reason'}]."""
    out = []
    paid = LEAVE_TYPES.get(leave_type, ("", True, ""))[1]
    if value("leave.trigger.unverified_balance") and paid:
        b = balance(agent_id, leave_type, w.run_id)
        if b["state"] != "verified":
            out.append({"trigger": "unverified_balance",
                        "reason": f"No verified {LEAVE_TYPES[leave_type][0].lower()} balance (unresolved: "
                                  f"{b['state']})."})
        elif b["available"] < 1:
            out.append({"trigger": "unverified_balance",
                        "reason": f"Verified balance is {b['available']:g} {b['unit']}; not enough for this day."})
    if value("leave.trigger.long_leave"):
        n, lim = consecutive_days(w, agent_id, day), int(value("leave.long_leave_days"))
        if n > lim:
            out.append({"trigger": "long_leave", "reason": f"{n} consecutive days of leave "
                        f"({value('leave.consecutive_basis').replace('_', ' ')}); limit {lim}."})
    if value("leave.trigger.sick_leave") and leave_type == "sick":
        n, lim = consecutive_days(w, agent_id, day, sick_only=True), int(value("leave.sick_days"))
        if n > lim:
            out.append({"trigger": "sick_leave", "reason": f"{n} consecutive sick days; limit {lim}."})
    return out


def create_review_task(run_id: str, request_id: str, employee_id: str, trigger: str, reason: str,
                       assigned_to: str | None) -> int:
    with _db() as con:
        dup = con.execute("SELECT id FROM hr_review_tasks WHERE run_id=? AND request_id=? AND trigger_name=? AND "
                          "status IN ('open','recommended')", (run_id, request_id, trigger)).fetchone()
        if dup:
            return dup[0]
        return _insert(con, "hr_review_tasks", {
            "run_id": run_id, "request_id": request_id, "employee_id": employee_id, "trigger_name": trigger,
            "reason": reason, "status": "open", "assigned_to": assigned_to, "created_at": _now()})


def review_tasks(run_id: str, request_id: str | None = None, status: tuple | None = None) -> list[dict]:
    rows = _q("SELECT * FROM hr_review_tasks WHERE run_id=?" + (" AND request_id=?" if request_id else "") +
              " ORDER BY id", (run_id, request_id) if request_id else (run_id,))
    return [r for r in rows if status is None or r["status"] in status]


def review_task(tid: int) -> dict:
    r = _q("SELECT * FROM hr_review_tasks WHERE id=?", (tid,))
    if not r:
        raise ValueError("Review task not found.")
    return r[0]


def recommend(tid: int, recommendation: str, note: str, by: str) -> dict:
    if recommendation not in ("approve", "reject", "no objection", "needs more information"):
        raise ValueError("Recommendation must be approve, reject, no objection or needs more information.")
    if not note.strip():
        raise ValueError("A note is required with the recommendation.")
    with _db() as con:
        cur = con.execute("UPDATE hr_review_tasks SET status='recommended', recommendation=?, recommended_by=?, "
                          "recommended_at=?, note=? WHERE id=? AND status IN ('open','recommended')",
                          (recommendation, by, _now(), note, tid))
        if cur.rowcount != 1:
            raise ValueError("This review task is closed.")
    return review_task(tid)


def assign_task(tid: int, username: str):
    with _db() as con:
        con.execute("UPDATE hr_review_tasks SET assigned_to=? WHERE id=?", (username, tid))


def close_tasks(run_id: str, request_id: str, note: str):
    with _db() as con:
        con.execute("UPDATE hr_review_tasks SET status='closed', closed_at=?, note=COALESCE(note,'') || ? WHERE "
                    "run_id=? AND request_id=? AND status IN ('open','recommended')",
                    (_now(), f" [closed: {note}]", run_id, request_id))


def blocking_review(run_id: str, request_id: str) -> list[dict]:
    """Open (not yet recommended) review tasks: approval waits for these."""
    return review_tasks(run_id, request_id, ("open",))


# ================================================================== HR-04 attendance lock + corrections
def lock_intervals(policy: dict) -> int:
    from .config import intervals_per_day
    return int(round(float(value("attendance.lock_hours")) * intervals_per_day(policy) / 24))


def shift_end_t(w, agent_id: str, day: int) -> int | None:
    from .config import shift_intervals
    ro = w.roster[(w.roster.agent_id == agent_id) & (w.roster.day == day)]
    if ro.empty:
        return None
    r = ro.iloc[0]
    return shift_intervals(w.policy, int(day), r["shift"], r["ext_before"], r["ext_after"]).stop


def is_locked(w, agent_id: str, day: int) -> bool:
    end = shift_end_t(w, agent_id, day)
    return end is not None and w.now_t >= end + lock_intervals(w.policy)


def request_correction(run_id: str, employee_id: str, day: int, original: str | None, requested: str, reason: str,
                       by: str, by_user: str) -> dict:
    if requested not in ("present", "absent", "no record"):
        raise ValueError("Requested value must be present, absent or no record.")
    if not reason.strip():
        raise ValueError("A reason is required for a correction request.")
    with _db() as con:
        dup = con.execute("SELECT id FROM hr_attendance_corrections WHERE run_id=? AND employee_id=? AND day=? AND "
                          "status='pending'", (run_id, employee_id, int(day))).fetchone()
        if dup:
            raise ValueError("A correction request for this shift is already waiting for review.")
        cid = _insert(con, "hr_attendance_corrections", {
            "run_id": run_id, "employee_id": employee_id, "day": int(day), "original": original or "no record",
            "requested": requested, "reason": reason, "status": "pending", "requested_by": by,
            "requested_by_user": by_user, "requested_at": _now()})
    return correction(cid)


def correction(cid: int) -> dict:
    r = _q("SELECT * FROM hr_attendance_corrections WHERE id=?", (cid,))
    if not r:
        raise ValueError("Correction request not found.")
    return r[0]


def corrections(run_id: str, status: str | None = None) -> list[dict]:
    rows = _q("SELECT * FROM hr_attendance_corrections WHERE run_id=? ORDER BY id", (run_id,))
    return [r for r in rows if status is None or r["status"] == status]


def close_correction(cid: int, applied: bool, reason: str, by: str):
    with _db() as con:
        cur = con.execute("UPDATE hr_attendance_corrections SET status=?, reviewed_by=?, reviewed_at=?, "
                          "review_reason=? WHERE id=? AND status='pending'",
                          ("applied" if applied else "rejected", by, _now(), reason, cid))
        if cur.rowcount != 1:
            raise ValueError("This correction request was already reviewed.")


# ================================================================== HR-05 absence-pattern flags
def absence_active() -> tuple[bool, str]:
    en, en_s = pol("absence.flags_enabled")
    th, th_s = pol("absence.threshold")
    if th is None or th_s != "approved":
        return False, "Inactive: the absence threshold is not approved yet (pending HR policy)."
    if not en or en_s != "approved":
        return False, (f"Inactive: rule set ({th} qualifying absences in any {value('absence.window_days')} "
                       "calendar days) awaiting validation against the attendance model; Senior HR switches it on.")
    return True, f"Active: {th} qualifying absences in {value('absence.window_days')} days."


def absence_events(w, agent_id: str) -> dict[int, list[str]]:
    """Qualifying unplanned absence events per day, with their sources. Validated against the attendance model:
    * an explicit no-show attendance record ("no-show");
    * a reported unplanned absence (team lead or agent "can't come in", stored as unavailable "absent (...)").
    Never counted: approved leave, a missing check-in (unknown, not an absence), a day whose attendance shows
    presence (e.g. after a correction). Two records for the same day are one event (no duplicates)."""
    lv = w.data["leave"]
    mine = lv[lv.agent_id == agent_id]
    leave_days = {int(d) for d in mine[mine.reason == "approved leave"].day}
    out: dict[int, list[str]] = {}
    for (a, d), v in w.attendance.items():
        if a == agent_id and v == "no-show":
            out.setdefault(int(d), []).append("no-show record")
    for r in mine.itertuples():
        if str(r.reason).startswith("absent (") and "left the company" not in str(r.reason):
            out.setdefault(int(r.day), []).append(f"reported {r.reason}")
    return {d: src for d, src in sorted(out.items()) if d not in leave_days and d <= w.today
            and w.attendance.get((agent_id, d)) not in PRESENT}


def qualifying_absences(w, agent_id: str, start_day: int, end_day: int) -> list[int]:
    return [d for d in absence_events(w, agent_id) if start_day <= d <= end_day]


def first_window(days: list[int], threshold: int, window: int) -> tuple[int, int, int] | None:
    """Any rolling `window`-calendar-day span holding at least `threshold` events: (start, end, count)."""
    for i in range(len(days)):
        inside = [d for d in days[i:] if d < days[i] + window]
        if len(inside) >= threshold:
            return days[i], days[i] + window - 1, len(inside)
    return None


def absence_validation(w) -> list[dict]:
    """Dry run: what the rules would flag today, with every counted event and its source. Creates nothing."""
    th, win = value("absence.threshold"), int(value("absence.window_days"))
    out = []
    for a in sorted(set(w.data["agents"].agent_id)):
        ev = absence_events(w, a)
        if not ev:
            continue
        hit = first_window(list(ev), int(th), win) if th else None
        out.append({"employee_id": a, "events": ev, "would_flag": bool(hit), "window": hit})
    return out


def evaluate_absence_flags(w) -> dict:
    """Create flags for employees at/above the approved threshold (idempotent per window) and re-count open
    flags (corrections may lower the count). Returns a summary; does nothing while inactive."""
    active, why = absence_active()
    if not active:
        return {"active": False, "note": why, "created": 0, "updated": 0}
    th, win = int(value("absence.threshold")), int(value("absence.window_days"))
    created = updated = 0
    for a in sorted(set(w.data["agents"].agent_id)):
        hit = first_window(list(absence_events(w, a)), th, win)
        if hit:
            start, end, n = hit
            days = qualifying_absences(w, a, start, end)
            key = f"{w.run_id}|{a}|{start}"              # one flag per pattern start: no duplicates on re-runs
            with _db() as con:
                hit = con.execute("SELECT id FROM hr_absence_flags WHERE flag_key=?", (key,)).fetchone()
                open_ = con.execute("SELECT id FROM hr_absence_flags WHERE run_id=? AND employee_id=? AND status IN "
                                    "('open','under_review')", (w.run_id, a)).fetchone()
                if not hit and not open_:
                    _insert(con, "hr_absence_flags", {
                        "run_id": w.run_id, "flag_key": key, "employee_id": a, "window_start": start, "window_end": end,
                        "absences": len(days), "threshold": th, "status": "open", "created_at": _now(),
                        "note": "Confidential, non-disciplinary: for an HR wellbeing / support conversation."})
                    created += 1
    for f in absence_flags(w.run_id, ("open", "under_review")):
        n = len(qualifying_absences(w, f["employee_id"], f["window_start"], f["window_end"]))
        if n != f["absences"]:
            with _db() as con:
                con.execute("UPDATE hr_absence_flags SET absences=?, updated_at=?, updated_by='ShiftSense (re-count)', "
                            "note=? WHERE id=?", (n, _now(), f"Re-counted after a correction: {n} (threshold "
                                                  f"{f['threshold']}).", f["id"]))
            updated += 1
    return {"active": True, "note": why, "created": created, "updated": updated}


def absence_flags(run_id: str, status: tuple | None = None) -> list[dict]:
    rows = _q("SELECT * FROM hr_absence_flags WHERE run_id=? ORDER BY id", (run_id,))
    return [r for r in rows if status is None or r["status"] in status]


FLAG_MOVES = {"open": {"under_review", "dismissed"}, "under_review": {"resolved", "dismissed"}}


def move_flag(fid: int, to: str, note: str, by: str) -> dict:
    f = _q("SELECT * FROM hr_absence_flags WHERE id=?", (fid,))
    if not f:
        raise ValueError("Flag not found.")
    f = f[0]
    if to not in FLAG_MOVES.get(f["status"], set()):
        raise ValueError(f"A flag that is {f['status']} cannot move to {to}.")
    if not note.strip():
        raise ValueError("A note is required.")
    with _db() as con:
        con.execute("UPDATE hr_absence_flags SET status=?, updated_by=?, updated_at=?, note=? WHERE id=?",
                    (to, by, _now(), note, fid))
    return {**f, "status": to}


# ================================================================== HR-06 overtime approval
PRESENT = {"checkin", "confirmed", "feed", "in"}


def overtime_hours(w, agent_id: str) -> dict:
    """Extension hours on the agent's shifts in the current month. Worked = shift with a presence record;
    a scheduled extension without a presence record is NOT counted as worked."""
    from .refdata import iso_of_day
    month = iso_of_day(w.policy, w.today)[:7]
    worked = sched = 0.0
    for r in w.roster[w.roster.agent_id == agent_id].itertuples():
        if iso_of_day(w.policy, int(r.day))[:7] != month or int(r.day) > w.today:
            continue
        h = float(r.ext_before) + float(r.ext_after)
        if not h:
            continue
        if w.attendance.get((agent_id, int(r.day))) in PRESENT and (w.scenario or
                                                                    w.attendance.get((agent_id, int(r.day))) != "in"):
            worked += h
        else:
            sched += h
    return {"period": month, "worked": round(worked, 2), "scheduled_unconfirmed": round(sched, 2)}


def overtime_active() -> tuple[bool, str]:
    en = value("overtime.hr_approval_enabled")
    th, th_s = pol("overtime.monthly_threshold")
    if not en:
        return False, "Off (default): HR overtime approval is not enabled. Ops keeps coverage decisions."
    if th is None or th_s != "approved":
        return False, "Off: the monthly overtime threshold is not approved."
    return True, f"On: worked overtime above {th} {value('overtime.threshold_unit')} a month needs HR approval."


def evaluate_overtime(w) -> dict:
    active, why = overtime_active()
    if not active:
        return {"active": False, "note": why, "created": 0}
    th = float(value("overtime.monthly_threshold"))
    created = 0
    for a in sorted(set(w.data["agents"].agent_id)):
        o = overtime_hours(w, a)
        if o["worked"] > th:
            key = f"{w.run_id}|{a}|{o['period']}"
            with _db() as con:
                if not con.execute("SELECT id FROM hr_overtime_tasks WHERE task_key=?", (key,)).fetchone():
                    _insert(con, "hr_overtime_tasks", {
                        "run_id": w.run_id, "task_key": key, "employee_id": a, "period": o["period"],
                        "worked_hours": o["worked"], "scheduled_unconfirmed_hours": o["scheduled_unconfirmed"],
                        "threshold": th, "status": "pending", "created_at": _now()})
                    created += 1
    return {"active": True, "note": why, "created": created}


def overtime_tasks(run_id: str, status: str | None = None) -> list[dict]:
    rows = _q("SELECT * FROM hr_overtime_tasks WHERE run_id=? ORDER BY id", (run_id,))
    return [r for r in rows if status is None or r["status"] == status]


def decide_overtime(tid: int, approve: bool, reason: str, by: str) -> dict:
    if not reason.strip():
        raise ValueError("A reason is required.")
    with _db() as con:
        cur = con.execute("UPDATE hr_overtime_tasks SET status=?, decided_by=?, decided_at=?, reason=? WHERE id=? "
                          "AND status='pending'", ("approved" if approve else "rejected", by, _now(), reason, tid))
        if cur.rowcount != 1:
            raise ValueError("This overtime task was already decided.")
    return _q("SELECT * FROM hr_overtime_tasks WHERE id=?", (tid,))[0]


# ================================================================== HR-07 restricted cases
CASE_CATEGORIES = ["Grievance", "Conduct", "Wellbeing", "Accommodation", "Attendance support", "Other"]
CASE_STATUSES = ["open", "in progress", "awaiting information", "closed"]


def log_access(case_id: int | None, username: str, action: str, allowed: bool):
    with _db() as con:
        _insert(con, "hr_case_access", {"case_id": case_id, "username": username, "action": action,
                                        "allowed": int(allowed), "at": _now()})


def access_log(case_id: int) -> list[dict]:
    return _q("SELECT * FROM hr_case_access WHERE case_id=? ORDER BY id", (case_id,))


def open_case(category: str, confidentiality: str, subject_employee: str, summary: str, caseworker: str,
              deadline: str, by: str, by_user: str) -> dict:
    if category not in CASE_CATEGORIES:
        raise ValueError(f"Category must be one of: {', '.join(CASE_CATEGORIES)}.")
    if confidentiality not in ("restricted", "highly restricted"):
        raise ValueError("Confidentiality must be restricted or highly restricted.")
    if deadline:
        datetime.strptime(deadline, "%Y-%m-%d")
    with _db() as con:
        cid = _insert(con, "hr_cases", {
            "category": category, "confidentiality": confidentiality, "status": "open", "caseworker": caseworker,
            "subject_employee": subject_employee, "summary": summary, "opened_by": by, "opened_at": _now(),
            "updated_at": _now(), "deadline": deadline or None})
        _insert(con, "hr_case_events", {"case_id": cid, "kind": "assignment", "body": f"Assigned to {caseworker}",
                                        "by_name": by, "by_user": by_user, "at": _now()})
    return case(cid)


def case(cid: int) -> dict:
    r = _q("SELECT * FROM hr_cases WHERE id=?", (cid,))
    if not r:
        raise ValueError("Case not found.")
    return r[0]


def all_cases() -> list[dict]:
    return _q("SELECT * FROM hr_cases ORDER BY id")


def case_events(cid: int) -> list[dict]:
    return _q("SELECT * FROM hr_case_events WHERE case_id=? ORDER BY id", (cid,))


def add_case_event(cid: int, kind: str, body: str, due: str, by: str, by_user: str):
    if kind not in ("comment", "evidence", "followup"):
        raise ValueError("Unknown case entry type.")
    if not body.strip():
        raise ValueError("The entry is empty.")
    if case(cid)["status"] == "closed":
        raise ValueError("The case is closed.")
    with _db() as con:
        _insert(con, "hr_case_events", {"case_id": cid, "kind": kind, "body": body, "due": due or None,
                                        "by_name": by, "by_user": by_user, "at": _now()})
        con.execute("UPDATE hr_cases SET updated_at=? WHERE id=?", (_now(), cid))


def reassign_case(cid: int, caseworker: str, by: str, by_user: str):
    with _db() as con:
        con.execute("UPDATE hr_cases SET caseworker=?, updated_at=? WHERE id=?", (caseworker, _now(), cid))
        _insert(con, "hr_case_events", {"case_id": cid, "kind": "assignment", "body": f"Assigned to {caseworker}",
                                        "by_name": by, "by_user": by_user, "at": _now()})


def set_case_status(cid: int, status: str, outcome: str, by: str, by_user: str):
    if status not in CASE_STATUSES:
        raise ValueError("Unknown status.")
    c = case(cid)
    if c["status"] == "closed":
        raise ValueError("The case is closed.")
    if status == "closed" and not outcome.strip():
        raise ValueError("An outcome is required to close a case.")
    with _db() as con:
        con.execute("UPDATE hr_cases SET status=?, updated_at=?, outcome=?, closed_by=?, closed_at=? WHERE id=?",
                    (status, _now(), outcome or c["outcome"], by if status == "closed" else None,
                     _now() if status == "closed" else None, cid))
        _insert(con, "hr_case_events", {"case_id": cid, "kind": "decision" if status == "closed" else "status",
                                        "body": f"{status}" + (f": {outcome}" if outcome else ""), "by_name": by,
                                        "by_user": by_user, "at": _now()})


# ================================================================== HR system feed: opening balances in bulk
BAL_HEADERS = {
    "employee_id": ("employee id", "emp id", "employee", "emp no", "employee number", "staff id", "agent id", "id"),
    "leave_type_id": ("leave type", "type", "leave", "category", "leave code", "code"),
    "opening_balance": ("opening balance", "balance", "days", "available", "entitlement", "opening", "days left"),
    "effective_date": ("effective date", "as of", "as on", "date", "effective", "balance date"),
    "source_reference": ("source", "source reference", "reference", "ref"),
}
TYPE_WORDS = {"annual": ("annual", "al", "el", "earned", "privilege", "pl", "vacation"), "casual": ("casual", "cl"),
              "sick": ("sick", "sl", "medical"), "unpaid": ("unpaid", "lwp", "lop", "leave without pay")}


def _col(columns, field) -> str | None:
    from .convert import norm
    names = {norm(c): c for c in columns}
    for want in BAL_HEADERS[field]:
        if want in names:
            return names[want]
    return None


def read_balance_file(data: bytes, filename: str, known_employees: set[str]) -> dict:
    """An HR-system export (Excel, CSV, pasted table) -> {'rows': [...], 'problems': [(row, message)]}.
    Headers are matched by common names; a row with any missing or unreadable mandatory value is held back,
    never guessed. Rows are entered as UNVERIFIED balances."""
    from .convert import read_bytes, to_date
    grids = read_bytes(data, filename)
    if not grids:
        return {"rows": [], "problems": [(0, "The file has no table.")]}
    grid = next(iter(grids.values()))
    if grid.empty:
        return {"rows": [], "problems": [(0, "The file has no rows.")]}
    df = grid.iloc[1:].copy()
    df.columns = [str(c) for c in grid.iloc[0]]
    cols = {f: _col(df.columns, f) for f in BAL_HEADERS}
    missing = [f for f in ("employee_id", "leave_type_id", "opening_balance", "effective_date") if not cols[f]]
    if missing:
        return {"rows": [], "problems": [(1, "Missing column(s): " + ", ".join(m.replace("_", " ") for m in missing))]}
    rows, problems = [], []
    for i, rec in enumerate(df.to_dict("records"), start=2):
        emp = str(rec[cols["employee_id"]] or "").strip().upper()
        raw_t = str(rec[cols["leave_type_id"]] or "").strip().lower()
        lt = next((k for k, words in TYPE_WORDS.items() if raw_t in words or raw_t.startswith(k)), None)
        try:
            bal = float(str(rec[cols["opening_balance"]]).strip())
        except (TypeError, ValueError):
            bal = None
        eff, _ = to_date(rec[cols["effective_date"]]) if rec[cols["effective_date"]] not in (None, "") else (None, None)
        src = str(rec.get(cols["source_reference"]) or "").strip() if cols["source_reference"] else ""
        why = [m for ok, m in ((emp in known_employees, f"unknown employee '{emp}'"),
                               (lt is not None, f"unknown leave type '{raw_t}'"),
                               (bal is not None and bal >= 0, "balance missing or negative"),
                               (eff is not None, "effective date missing or unreadable")) if not ok]
        if why:
            problems.append((i, "; ".join(why)))
            continue
        rows.append({"employee_id": emp, "leave_type_id": lt, "opening_balance": bal, "effective_date": eff,
                     "source_reference": src})
    return {"rows": rows, "problems": problems}
