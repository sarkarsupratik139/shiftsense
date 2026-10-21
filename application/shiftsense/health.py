"""Health check: `python -m shiftsense.health` (exit code 1 if anything essential fails).

Reports configuration *presence* only: secrets are never printed.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys


def run() -> list[tuple[str, bool, str]]:
    from . import accounts, auth, comms, security, store
    from .config import load_policy
    from .constraints import check
    out = []

    def add(name, ok, detail, essential=True):
        out.append((name, bool(ok), detail, essential))

    from . import db
    path = store.db_path()
    if db.dialect() == "postgres":
        try:
            with db.connect() as con:
                ver = con.execute("SELECT version()").fetchone()[0].split(",")[0]
                mig = con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] \
                    if "schema_migrations" in con.tables() else None
            add("Database", True, f"{db.describe()} · {ver} · migrations up to {mig}")
        except Exception as e:                                     # noqa: BLE001
            add("Database", False, f"PostgreSQL unreachable: {type(e).__name__}")
    else:
        add("Database file", path.exists(), str(path) if path.exists() else f"missing: {path} (created on first start)",
            essential=False)
    if db.dialect() == "sqlite" and path.exists():
        try:
            with sqlite3.connect(path) as con:
                res = con.execute("PRAGMA integrity_check").fetchone()[0]
            add("Database integrity", res == "ok", res)
        except sqlite3.Error as e:
            add("Database integrity", False, f"cannot open: {type(e).__name__}")
    try:
        p = load_policy()
        add("Policy config", True, f"seed {p['seed']}, {len(p['queues'])} queues, {p['horizon']['days']} days")
    except Exception as e:                                         # noqa: BLE001
        add("Policy config", False, f"{type(e).__name__}: {e}")
        p = None
    roles = set(auth.config()["roles"])
    add("Roles", roles == {"admin", "agent", "team_lead", "ops_head", "analyst", "hr"}, ", ".join(sorted(roles)))
    try:
        accs = accounts.all_accounts()
        admins = [a for a in accs if a["role"] == "admin" and a["active"]]
        bad = [a["username"] for a in accs if a["role"] not in roles]
        add("Accounts", admins and not bad, f"{len(accs)} accounts, {len(admins)} active admin(s)"
            + (f"; unknown roles: {bad}" if bad else ""))
    except Exception as e:                                         # noqa: BLE001
        add("Accounts", False, f"{type(e).__name__}")
    from .config import deploy_mode
    mode = deploy_mode()
    add("Deployment mode", True, mode + (" (demo accounts listed on sign-in)" if mode == "demo" else ""), essential=False)
    if mode == "pilot":
        try:
            users = auth.config()["users"]
            weak = [u for u, r in users.items() if (a := accounts.get(u, with_hash=True)) and a["active"]
                    and security.verify(u, r.get("demo_password", ""), a["password_hash"])[0]]
            add("Demo passwords retired", not weak, "none in use" if not weak else
                f"{len(weak)} active account(s) still use a published demo password: {', '.join(weak)}")
        except Exception as e:                                     # noqa: BLE001
            add("Demo passwords retired", False, type(e).__name__)
        sc_loaded = store.load(slot="scenario") is not None
        add("No demo scenario loaded", not sc_loaded, "none" if not sc_loaded else "a demo scenario is loaded",
            essential=False)
    w = store.load()
    if w is None:
        add("Live workspace", True, "not created yet (created on first start)", essential=False)
    else:
        v = check(w.policy, w.data["agents"], w.data["leave"], w.roster)
        add("Live workspace", not v, f"run {w.run_id}, {len(w.roster)} shifts, {len(v)} rule violation(s)")
    sc = store.load(slot="scenario")
    add("Demo scenario", True, f"loaded ({sc.run_id})" if sc else "not loaded", essential=False)
    ok, bad_id = comms.verify_audit()
    add("Audit chain", ok, "verified" if ok else f"broken at row {bad_id}")
    try:
        import ortools
        add("Optimiser (OR-Tools)", True, ortools.__version__)
    except Exception as e:                                         # noqa: BLE001
        add("Optimiser (OR-Tools)", False, type(e).__name__)
    from .mailer import configured
    add("Email (optional)", True, "configured (SMTP)" if configured() else "not configured · in-app messaging only",
        essential=False)
    add("AI assistant key", True, "present" if os.environ.get("ANTHROPIC_API_KEY") else "absent (rule-based answers)",
        essential=False)
    free = shutil.disk_usage(path.parent if path.parent.exists() else ".").free / 1e9
    add("Disk space", free > 0.5, f"{free:.1f} GB free")
    # ---- pilot operations: backups and sign-in security (essential in pilot mode, advisory in demo)
    pilot = mode == "pilot"
    try:
        from . import backup
        from datetime import datetime
        last = backup.latest()
        if last is None:
            add("Latest backup", False, "no backup found", essential=pilot)
        else:
            age_h = (datetime.now().timestamp() - last.stat().st_mtime) / 3600
            add("Latest backup", age_h <= 26, f"{last.name} · {age_h:.0f} h old" +
                ("" if age_h <= 26 else " (older than a day: check the nightly job)"), essential=pilot)
    except Exception as e:                                         # noqa: BLE001
        add("Latest backup", False, type(e).__name__, essential=pilot)
    am = security.auth_mode()
    add("Sign-in", am != "password" or not pilot,
        f"mode {am}; lockout after {security.max_failures()} wrong passwords for {security.lockout_minutes()} min; "
        f"idle sign-out {security.idle_minutes()} min" + (" (pilot: single sign-on recommended)"
                                                          if pilot and am == "password" else ""), essential=False)
    add("Password hashing", security.iterations() >= security.DEFAULT_ITERATIONS,
        f"PBKDF2-SHA256, {security.iterations():,} iterations, random salt", essential=pilot)
    return out


def alert(rows) -> str | None:
    """Email the operations contact (SHIFTSENSE_ALERT_EMAIL) when an essential check fails, at most once per
    failure set per day. Returns the email status, or None when nothing was sent."""
    failed = [r for r in rows if not r[1] and r[3]]
    to = os.environ.get("SHIFTSENSE_ALERT_EMAIL", "")
    if not failed or not to:
        return None
    from datetime import date
    from . import mailer
    names = ", ".join(r[0] for r in failed)
    res = mailer.send("health", f"health|{date.today()}|{names}", kind="health", ref=names, account={},
                      subject=f"ShiftSense health: {len(failed)} check(s) failing",
                      body="\n".join(f"{r[0]}: {r[2]}" for r in failed), address=to, require_verified=False)
    return res["status"]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    rows = run()
    failed = [r for r in rows if not r[1] and r[3]]
    if "--json" in argv:
        import json
        print(json.dumps({"healthy": not failed, "checks": [
            {"name": n, "ok": ok, "detail": d, "essential": e} for n, ok, d, e in rows]}))
    else:
        for name, ok, detail, essential in rows:
            print(f"{'OK ' if ok else ('FAIL' if essential else 'WARN')}  {name:22} {detail}")
        print("\nHealthy" if not failed else f"\n{len(failed)} essential check(s) failed")
    if "--alert" in argv:
        st = alert(rows)
        if st and "--json" not in argv:
            print(f"Alert email: {st}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
