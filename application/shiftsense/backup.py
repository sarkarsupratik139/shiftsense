"""Backup and restore of the SQLite database (workspace, scenario, accounts, notifications, messages, cases,
reminders, change history and the audit chain).

  python -m shiftsense.backup create [--keep N] -> data/backups/shiftsense-YYYYmmdd-HHMMSS.db (verified);
                                                   --keep N removes all but the newest N backups afterwards
  With SHIFTSENSE_DATABASE_URL set, the same commands back up PostgreSQL (see pgbackup.py): a .zip per backup.
  python -m shiftsense.backup verify FILE
  python -m shiftsense.backup restore FILE      -> stop the app first; current DB is backed up before restore
  python -m shiftsense.backup drill [FILE]      -> rehearse a restore of FILE (default: newest backup) into a
                                                   temporary copy and check it; the live database is never touched

A backup is taken with SQLite's online backup API (consistent while the app runs) and then verified:
integrity check, the workspace loads, and the audit hash chain is intact.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


def _db():
    from .store import db_path
    return db_path()


def _pg() -> bool:
    from . import db
    return db.dialect() == "postgres"


def backup_dir() -> Path:
    return _db().parent / "backups"


def verify(path: Path) -> list[str]:
    """Return problems found in a backup file (empty = good)."""
    if str(path).endswith(".zip"):
        from . import pgbackup
        return pgbackup.verify(path)
    problems = []
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as con:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                problems.append("integrity check failed")
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as e:
        return [f"cannot open: {type(e).__name__}"]
    if "workspace" not in tables:
        problems.append("no workspace table")
    old = os.environ.get("SHIFTSENSE_DB")
    os.environ["SHIFTSENSE_DB"] = str(path)
    try:
        from . import comms, store
        if "workspace" in tables and store.load(path) is None:
            problems.append("workspace does not load")
        if "audit_log" in tables:
            ok, bad = comms.verify_audit()
            if not ok:
                problems.append(f"audit chain broken at row {bad}")
    finally:
        if old is None:
            os.environ.pop("SHIFTSENSE_DB", None)
        else:
            os.environ["SHIFTSENSE_DB"] = old
    return problems


def create(dest_dir: Path | None = None, keep: int | None = None) -> Path:
    if _pg():
        from . import pgbackup
        out = pgbackup.create(dest_dir or backup_dir())
        _retain(dest_dir or backup_dir(), keep, "shiftsense-pg-*.zip")
        return out
    src = _db()
    if not src.exists():
        raise FileNotFoundError(f"No database at {src}")
    dest_dir = dest_dir or src.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"shiftsense-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
    with sqlite3.connect(src) as s, sqlite3.connect(dest) as d:
        s.backup(d)
    problems = verify(dest)
    if problems:
        raise RuntimeError(f"Backup written but failed verification: {problems}")
    _retain(dest_dir, keep, "shiftsense-*.db")
    return dest


def _retain(d: Path, keep: int | None, pattern: str):
    """Keep only the newest `keep` backups (only when asked)."""
    if not keep or keep < 1:
        return
    files = sorted(d.glob(pattern))
    for f in files[:-keep]:
        f.unlink()


def restore(src: Path) -> Path:
    """Replace the live database with a verified backup. Keeps a safety copy of the current one."""
    if _pg():
        from . import pgbackup
        return pgbackup.restore(Path(src), backup_dir())
    problems = verify(src)
    if problems:
        raise RuntimeError(f"Refusing to restore an invalid backup: {problems}")
    target = _db()
    safety = None
    if target.exists():
        safety = target.with_name(f"{target.stem}.before-restore-{datetime.now():%Y%m%d-%H%M%S}.db")
        shutil.copy2(target, safety)
    tmp = target.with_suffix(".restoring")
    shutil.copy2(src, tmp)
    os.replace(tmp, target)                 # atomic rename: never a half-written database
    return safety


def latest(dest_dir: Path | None = None) -> Path | None:
    d = dest_dir or _db().parent / "backups"
    files = sorted(d.glob("shiftsense-pg-*.zip" if _pg() else "shiftsense-*.db")) if d.exists() else []
    return files[-1] if files else None


def drill(src: Path | None = None) -> dict:
    """Rehearse a restore without touching the live database: copy the backup to a temporary location exactly as
    `restore` would, then open it the way the app does and check it. Returns timings and findings."""
    import tempfile
    import time
    src = src or latest()
    if src is None:
        raise FileNotFoundError("No backup found. Run 'python -m shiftsense.backup create' first.")
    if not Path(src).exists():
        raise FileNotFoundError(f"Backup not found: {src}")
    if str(src).endswith(".zip"):
        from . import pgbackup
        return pgbackup.drill(Path(src))
    t0 = time.perf_counter()
    out = {"backup": str(src), "problems": verify(src)}
    if out["problems"]:                       # an unreadable backup is not restored, even into a copy
        out["seconds"] = round(time.perf_counter() - t0, 2)
        return out
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "shiftsense.db"
        shutil.copy2(src, target.with_suffix(".restoring"))
        os.replace(target.with_suffix(".restoring"), target)
        old = os.environ.get("SHIFTSENSE_DB")
        os.environ["SHIFTSENSE_DB"] = str(target)
        try:
            from . import accounts, comms, store
            from .constraints import check
            w = store.load(target)
            if w is None:
                out["problems"].append("restored copy: workspace does not load")
            else:
                v = check(w.policy, w.data["agents"], w.data["leave"], w.roster)
                out["workspace"] = f"run {w.run_id}, {len(w.roster)} shifts, {len(v)} rule violation(s)"
                if v:
                    out["problems"].append(f"restored copy: {len(v)} rule violation(s)")
            ok, bad = comms.verify_audit()
            out["audit"] = "verified" if ok else f"broken at row {bad}"
            if not ok:
                out["problems"].append(f"restored copy: audit chain broken at row {bad}")
            out["accounts"] = len(accounts.all_accounts())
            out["audit_rows"] = len(comms.audit_rows())
        finally:
            if old is None:
                os.environ.pop("SHIFTSENSE_DB", None)
            else:
                os.environ["SHIFTSENSE_DB"] = old
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("create", "verify", "restore", "drill"):
        print(__doc__)
        return 2
    if argv[0] == "create":
        keep = int(argv[argv.index("--keep") + 1]) if "--keep" in argv else None
        print(f"Backup written and verified: {create(keep=keep)}")
    elif argv[0] == "verify":
        problems = verify(Path(argv[1]))
        print("OK" if not problems else "\n".join(problems))
        return 1 if problems else 0
    elif argv[0] == "drill":
        r = drill(Path(argv[1]) if len(argv) > 1 else None)
        print(f"Restore drill of {r['backup']} in {r['seconds']} s (live database untouched)")
        for k in ("workspace", "audit", "accounts", "audit_rows"):
            if k in r:
                print(f"  {k:10} {r[k]}")
        print("PASSED" if not r["problems"] else "FAILED:\n  " + "\n  ".join(r["problems"]))
        return 1 if r["problems"] else 0
    else:
        safety = restore(Path(argv[1]))
        print(f"Restored. Previous database kept at {safety}. Restart the app.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
