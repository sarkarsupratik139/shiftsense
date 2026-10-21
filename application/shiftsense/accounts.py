"""User accounts and contact details (SQLite table `accounts`). Contact is in-app, plus optional verified email.

Kept separate from three other things on purpose:
  * operational data (rosters, attendance, skills) lives in the workspace and is changed only by
    operational roles;
  * role definitions and permissions live in config/users.yaml and are read-only here (platform owner);
  * demo handling (scenario, clock, import) belongs to the Analyst role.

Admin may create and maintain accounts of the business roles below. Admin cannot create or promote
anyone to Admin, cannot change their own role or deactivate themselves, and cannot
touch permissions. Every change is versioned (no silent overwrite) and written to the audit chain.
The first time a database is used, the demo users from config/users.yaml are copied in.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from . import db
from .store import _con, db_path

ASSIGNABLE = ("agent", "team_lead", "analyst", "ops_head", "hr")    # roles Admin may give
PROTECTED = ("admin",)                                           # managed by the platform owner only
OBSOLETE_ROLES = {"operator": "analyst"}       # retired 'System operator' -> Analyst (five business roles only)
CONTACT = {"in_app": "In-app only", "email": "In-app and email"}
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
USER_RE = re.compile(r"^[a-z][a-z0-9._-]{2,31}$")
EMP_RE = re.compile(r"^[A-Z0-9-]{3,20}$")
FIELDS = ("name", "employee_id", "role", "team", "agent_id", "email", "contact_pref", "active")


def _db(path: Path | None = None):
    con = _con(path)
    db.setup_once("accounts", con, _setup)
    for old, new in OBSOLETE_ROLES.items():     # cheap, every time: also catches rows changed outside the app
        con.execute("UPDATE accounts SET role=?, name=CASE WHEN name='Demo Operator' THEN 'Demo Analyst' ELSE name END, "
                    "version=version+1, updated_by='role migration' WHERE role=?", (new, old))
    return con


def _setup(con):
    con.execute("""CREATE TABLE IF NOT EXISTS accounts (
        username TEXT PRIMARY KEY, name TEXT, employee_id TEXT UNIQUE, role TEXT, team TEXT, agent_id TEXT,
        phone TEXT, contact_pref TEXT, sms_consent INTEGER, active INTEGER, password_hash TEXT, version INTEGER,
        created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT)""")
    have = con.columns("accounts")
    for col, decl in (("email", "TEXT DEFAULT ''"), ("email_verified", "INTEGER DEFAULT 0"),
                      ("email_code_hash", "TEXT"), ("email_code_exp", "TEXT"), ("extra_permissions", "TEXT DEFAULT ''"),
                      ("failed_logins", "INTEGER DEFAULT 0"), ("locked_until", "TEXT"),
                      ("must_change", "INTEGER DEFAULT 0"), ("password_changed_at", "TEXT"),
                      ("theme_pref", "TEXT DEFAULT 'System'")):
        if col not in have:
            con.execute(f"ALTER TABLE accounts ADD COLUMN {col} {decl}")
    # SMS retired: 'SMS and in-app' becomes 'In-app only' (what the person still receives). The old phone and
    # consent columns are left untouched in the table but are no longer read, shown or used.
    con.execute("UPDATE accounts SET contact_pref='in_app', version=version+1, updated_by='SMS retirement' "
                "WHERE contact_pref='sms'")
    if not con.execute("SELECT 1 FROM accounts LIMIT 1").fetchone():
        _seed(con)
    else:
        _seed_new(con)
    _seed_extras(con)
    for old, new in OBSOLETE_ROLES.items():     # in-place migration; no workspace data is touched
        con.execute("UPDATE accounts SET role=?, name=CASE WHEN name='Demo Operator' THEN 'Demo Analyst' ELSE name END, "
                    "version=version+1, updated_by='role migration' WHERE role=?", (new, old))


def _seed(con):
    from . import auth
    now = datetime.now().isoformat(timespec="seconds")
    for i, (u, r) in enumerate(auth.config()["users"].items(), start=1):
        con.execute("INSERT INTO accounts (username, name, employee_id, role, team, agent_id, phone, contact_pref, "
                    "sms_consent, active, password_hash, version, created_at, created_by, updated_at, updated_by, email, "
                    "email_verified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (u, r["name"], f"E-{i:03d}", r["role"], r.get("team", ""), r.get("agent_id", ""), "", "in_app", 0,
                     1, r["password_hash"], 1, now, "seed", now, "seed", "", 0))


def _seed_extras(con):
    """Extra permissions declared for demo users in config (e.g. Senior HR) are set once, if not set yet."""
    from . import auth
    for u, r in auth.config()["users"].items():
        if r.get("extra_permissions"):
            con.execute("UPDATE accounts SET extra_permissions=? WHERE username=? AND (extra_permissions IS NULL OR "
                        "extra_permissions='')", (",".join(r["extra_permissions"]), u))


def _seed_new(con):
    """Demo users added to config/users.yaml later (e.g. the HR account) are created once; existing accounts are
    never touched."""
    from . import auth
    have = {r[0] for r in con.execute("SELECT username FROM accounts")}
    n = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    now = datetime.now().isoformat(timespec="seconds")
    for u, r in auth.config()["users"].items():
        if u in have:
            continue
        n += 1
        emp = f"E-{n:03d}"
        while con.execute("SELECT 1 FROM accounts WHERE employee_id=?", (emp,)).fetchone():
            n += 1
            emp = f"E-{n:03d}"
        con.execute("INSERT INTO accounts (username, name, employee_id, role, team, agent_id, phone, contact_pref, "
                    "sms_consent, active, password_hash, version, created_at, created_by, updated_at, updated_by, email, "
                    "email_verified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (u, r["name"], emp, r["role"], r.get("team", ""), r.get("agent_id", ""), "", "in_app", 0, 1,
                     r["password_hash"], 1, now, "seed", now, "seed", "", 0))


_COLS = ["username", "name", "employee_id", "role", "team", "agent_id", "email", "email_verified", "contact_pref",
         "active", "password_hash", "version", "created_at", "created_by", "updated_at", "updated_by",
         "extra_permissions", "failed_logins", "locked_until", "must_change", "password_changed_at", "theme_pref"]
EXTRA_PERMISSIONS = {"senior_hr": "hr"}                  # extra permission -> the only role that may hold it


def _row(r) -> dict:
    d = dict(zip(_COLS, r))
    d["email"], d["email_verified"], d["active"] = d["email"] or "", bool(d["email_verified"]), bool(d["active"])
    d["extra_permissions"] = [x for x in (d.get("extra_permissions") or "").split(",") if x]
    d["failed_logins"], d["must_change"] = int(d["failed_logins"] or 0), bool(d["must_change"])
    return d


def get(username: str, *, with_hash: bool = False) -> dict | None:
    with _db() as con:
        r = con.execute(f"SELECT {', '.join(_COLS)} FROM accounts WHERE username = ?",
                        ((username or "").strip().lower(),)).fetchone()
    if not r:
        return None
    d = _row(r)
    if not with_hash:
        d.pop("password_hash")
    return d


def all_accounts() -> list[dict]:
    with _db() as con:
        rows = con.execute(f"SELECT {', '.join(_COLS)} FROM accounts ORDER BY role, username").fetchall()
    out = [_row(r) for r in rows]
    for d in out:
        d.pop("password_hash")
    return out


def for_agent(agent_id: str) -> dict | None:
    return next((a for a in all_accounts() if a["agent_id"] == agent_id and a["active"]), None)


def mask_email(addr: str) -> str:
    if not addr or "@" not in addr:
        return "—"
    name, dom = addr.split("@", 1)
    return f"{name[:1]}•••@{dom}"


def set_email_code(username: str, code_hash: str | None, expires: str | None):
    with _db() as con:
        con.execute("UPDATE accounts SET email_code_hash=?, email_code_exp=? WHERE username=?",
                    (code_hash, expires, username))


def email_code(username: str) -> tuple[str | None, str | None]:
    with _db() as con:
        r = con.execute("SELECT email_code_hash, email_code_exp FROM accounts WHERE username=?", (username,)).fetchone()
    return (r[0], r[1]) if r else (None, None)


def mark_email_verified(username: str, address: str) -> bool:
    """Verified only if the address is still the one the code was sent to."""
    with _db() as con:
        cur = con.execute("UPDATE accounts SET email_verified=1, email_code_hash=NULL, email_code_exp=NULL, "
                          "version=version+1, updated_at=?, updated_by=? WHERE username=? AND email=?",
                          (datetime.now().isoformat(timespec="seconds"), f"{username} (email verified)", username,
                           address))
        return cur.rowcount == 1


# ------------------------------------------------------------------ validation
def validate(f: dict, *, username: str, existing: dict | None, agents, teams: set[str],
             editor: str) -> list[tuple[str, str]]:
    """Return [(field, problem)] — empty when the record can be saved. `agents` is the operational
    agent table (agent_id, team) so an account can only point at a real agent in the right team."""
    errs = []
    others = [a for a in all_accounts() if a["username"] != username]
    if existing is None:
        if not USER_RE.match(username or ""):
            errs.append(("username", "Use 3–32 characters: lowercase letters, digits, dot, dash or underscore, "
                                     "starting with a letter."))
        elif get(username):
            errs.append(("username", f"Username '{username}' already exists. Pick another or edit that account."))
    if not (f.get("name") or "").strip():
        errs.append(("name", "Enter the person's name."))
    elif len(f["name"]) > 80:
        errs.append(("name", "Name is longer than 80 characters."))
    emp = (f.get("employee_id") or "").strip().upper()
    if not EMP_RE.match(emp):
        errs.append(("employee_id", "Use 3–20 characters: capital letters, digits or dash (e.g. E-104)."))
    elif any(a["employee_id"] == emp for a in others):
        errs.append(("employee_id", f"Employee ID {emp} is already used by another account."))
    role = f.get("role")
    old_role = existing["role"] if existing else None
    if old_role in PROTECTED:
        if role != old_role:
            errs.append(("role", "The Admin role is managed by the platform owner, not here."))
    elif role not in ASSIGNABLE:
        errs.append(("role", "Choose agent, team lead, analyst, operations head or HR."))
    if existing and username == editor and role != old_role:
        errs.append(("role", "You cannot change your own role."))
    if existing and username == editor and not f.get("active", True):
        errs.append(("active", "You cannot deactivate your own account."))
    team = (f.get("team") or "").strip()
    ids = dict(zip(agents.agent_id, agents.team)) if agents is not None else {}
    if role == "agent":
        aid = (f.get("agent_id") or "").strip()
        if aid not in ids:
            errs.append(("agent_id", "Pick an agent that exists in the workforce data."))
        else:
            if team and team != ids[aid]:
                errs.append(("team", f"{aid} is in {ids[aid]} in the workforce data; the account must match."))
            if any(a["agent_id"] == aid and a["active"] for a in others):
                errs.append(("agent_id", f"Agent {aid} already has an active account."))
    elif role == "team_lead":
        if team not in teams:
            errs.append(("team", "Pick an existing team."))
        elif f.get("active", True) and any(a["role"] == "team_lead" and a["team"] == team and a["active"] for a in others):
            errs.append(("team", f"{team} already has an active team lead. Deactivate that account first."))
    email = (f.get("email") or "").strip().lower()
    if email and (len(email) > 254 or not EMAIL_RE.match(email)):
        errs.append(("email", "Enter a valid email address, e.g. name@company.com."))
    elif email and any(a["email"] == email for a in others):
        errs.append(("email", "This email address is already used by another account."))
    if f.get("contact_pref") not in CONTACT:
        errs.append(("contact_pref", "Choose a contact preference."))
    elif f["contact_pref"] == "email" and not email:
        errs.append(("email", "Email contact needs an email address."))
    return errs


def _clean(f: dict, agents) -> dict:
    out = {k: f.get(k) for k in FIELDS}
    out["name"] = (out["name"] or "").strip()
    out["employee_id"] = (out["employee_id"] or "").strip().upper()
    out["email"] = (out["email"] or "").strip().lower()
    out["active"] = True if out["active"] is None else bool(out["active"])
    if out["role"] == "agent" and agents is not None:
        out["team"] = dict(zip(agents.agent_id, agents.team)).get(out["agent_id"], out["team"])
    if out["role"] not in ("agent",):
        out["agent_id"] = ""
    if out["role"] not in ("agent", "team_lead"):
        out["team"] = ""
    return out


def create(username: str, f: dict, password: str, *, by: str, editor: str, agents, teams) -> dict:
    from . import security
    username = (username or "").strip().lower()
    errs = validate(f, username=username, existing=None, agents=agents, teams=teams, editor=editor)
    bad = security.problems(password, username, f.get("name", ""))
    if bad:
        errs.append(("password", "Initial password: " + "; ".join(bad) + "."))
    if errs:
        raise AccountError(errs)
    c = _clean(f, agents)
    now = datetime.now().isoformat(timespec="seconds")
    try:
        with _db() as con:
            con.execute("INSERT INTO accounts (username, name, employee_id, role, team, agent_id, phone, contact_pref, "
                        "sms_consent, active, password_hash, version, created_at, created_by, updated_at, updated_by, "
                        "email, email_verified) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (username, c["name"], c["employee_id"], c["role"], c["team"], c["agent_id"], "",
                         c["contact_pref"], 0, int(c["active"]), security.make_hash(password), 1, now, by, now,
                         by, c["email"], 0))
            con.execute("UPDATE accounts SET must_change=1 WHERE username=?", (username,))   # temporary password
    except db.IntegrityError as e:          # a parallel create won the race
        raise AccountError([("username", f"Could not save: {e}. Refresh and try again.")]) from e
    return get(username)


def update(username: str, f: dict, expected_version: int, *, by: str, editor: str, agents, teams) -> tuple[dict, dict]:
    """Returns (before, after). Raises AccountError on validation problems or a version conflict."""
    before = get(username)
    if before is None:
        raise AccountError([("username", "Account not found.")])
    if before["version"] != expected_version:
        raise AccountError([("version", f"Conflict: {before['updated_by']} changed this account at "
                                        f"{before['updated_at'].replace('T', ' ')}. Reload it and re-apply your edit.")])
    merged = {**{k: before[k] for k in FIELDS}, **f}
    errs = validate(merged, username=username, existing=before, agents=agents, teams=teams, editor=editor)
    if errs:
        raise AccountError(errs)
    c = _clean(merged, agents)
    now = datetime.now().isoformat(timespec="seconds")
    with _db() as con:
        changed = c["email"] != before["email"]          # a new address must be verified again
        cur = con.execute("UPDATE accounts SET name=?, employee_id=?, role=?, team=?, agent_id=?, email=?, "
                          "email_verified=CASE WHEN ? = 1 THEN 0 ELSE email_verified END, contact_pref=?, active=?, "
                          "version=version+1, updated_at=?, updated_by=? WHERE username=? AND version=?",
                          (c["name"], c["employee_id"], c["role"], c["team"], c["agent_id"], c["email"], int(changed),
                           c["contact_pref"], int(c["active"]), now, by, username, expected_version))
        if cur.rowcount != 1:
            raise AccountError([("version", "Conflict: the account changed while saving. Reload and try again.")])
    return before, get(username)


class AccountError(ValueError):
    def __init__(self, problems: list[tuple[str, str]]):
        self.problems = problems
        super().__init__("; ".join(f"{f}: {m}" for f, m in problems))



def set_extra_permission(username: str, perm: str, grant: bool, by: str) -> dict:
    """Grant or remove an extra permission (Senior HR). Only for the role it belongs to."""
    a = get(username)
    if a is None:
        raise AccountError([("username", "Account not found.")])
    role = EXTRA_PERMISSIONS.get(perm)
    if role is None:
        raise AccountError([("permission", f"Unknown permission {perm}.")])
    if a["role"] != role:
        raise AccountError([("permission", f"{perm} can only be held by the {role} role.")])
    perms = set(a["extra_permissions"])
    perms = perms | {perm} if grant else perms - {perm}
    with _db() as con:
        con.execute("UPDATE accounts SET extra_permissions=?, version=version+1, updated_at=?, updated_by=? "
                    "WHERE username=?", (",".join(sorted(perms)), datetime.now().isoformat(timespec="seconds"), by,
                                         username))
    return get(username)


def set_active(username: str, active: bool, by: str) -> tuple[dict, dict]:
    """System change of the active flag only (HR separation run). Returns (before, after); idempotent."""
    before = get(username)
    if before is None:
        raise AccountError([("username", "Account not found.")])
    if before["active"] == active:
        return before, before
    with _db() as con:
        con.execute("UPDATE accounts SET active=?, version=version+1, updated_at=?, updated_by=? WHERE username=?",
                    (int(active), datetime.now().isoformat(timespec="seconds"), by, username))
    return before, get(username)



# ------------------------------------------------------------------ sign-in state (see security.py)
def record_failure(username: str) -> str | None:
    """One more wrong password. Returns the lock time if the account is now locked."""
    from . import security
    rec = get(username)
    if rec is None:
        return None
    n, lock = security.after_failure(rec["failed_logins"])
    with _db() as con:
        con.execute("UPDATE accounts SET failed_logins=?, locked_until=COALESCE(?, locked_until) WHERE username=?",
                    (n, lock, username))
    return lock


def record_success(username: str, new_hash: str | None = None):
    with _db() as con:
        con.execute("UPDATE accounts SET failed_logins=0, locked_until=NULL WHERE username=?", (username,))
        if new_hash:                                            # upgraded storage format, same password
            con.execute("UPDATE accounts SET password_hash=? WHERE username=?", (new_hash, username))


def set_password(username: str, password: str, *, must_change: bool, by: str):
    """Policy-checked new password. must_change: the person must choose their own at the next sign-in."""
    from . import security
    rec = get(username)
    if rec is None:
        raise AccountError([("username", "Account not found.")])
    security.check(password, username, rec["name"])
    now = datetime.now().isoformat(timespec="seconds")
    with _db() as con:
        con.execute("UPDATE accounts SET password_hash=?, must_change=?, password_changed_at=?, failed_logins=0, "
                    "locked_until=NULL, version=version+1, updated_at=?, updated_by=? WHERE username=?",
                    (security.make_hash(password), int(must_change), now, now, by, username))


def unlock(username: str, by: str):
    with _db() as con:
        con.execute("UPDATE accounts SET failed_logins=0, locked_until=NULL, version=version+1, updated_at=?, "
                    "updated_by=? WHERE username=?", (datetime.now().isoformat(timespec="seconds"), by, username))


THEMES = ("System", "Light", "Dark")


def set_theme(username: str, theme: str):
    """Display preference only (no business effect, so no version bump and no audit row)."""
    if theme not in THEMES:
        raise AccountError([("theme", "Choose System, Light or Dark.")])
    with _db() as con:
        con.execute("UPDATE accounts SET theme_pref=? WHERE username=?", (theme, username))
