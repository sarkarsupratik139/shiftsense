"""Move an existing SQLite database into PostgreSQL.

    SHIFTSENSE_DATABASE_URL=postgresql://... python -m shiftsense.migrate_to_pg [--from data/shiftsense.db] [--replace]

* A verified SQLite backup is taken first; the SQLite file itself is never changed.
* Every table and row is copied with its original ids (the audit hash chain depends on them), then id sequences
  are moved past the copied ids.
* The retired phone and SMS-consent values are not carried over (agreed: cleared during the move).
* The target must be empty unless --replace is given. Everything happens in one transaction: on any error,
  nothing is left half-copied.
* Afterwards the audit chain is verified in PostgreSQL and row counts are compared table by table.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from . import db


def _bytes(v):
    return bytes(v) if isinstance(v, memoryview) else v


def migrate(src: Path, replace: bool = False) -> dict:
    if db.dialect() != "postgres":
        raise RuntimeError("Set SHIFTSENSE_DATABASE_URL to the target PostgreSQL database first.")
    if not src.exists():
        raise FileNotFoundError(f"No SQLite database at {src}")
    from . import backup, comms, pgbackup
    old = os.environ.get("SHIFTSENSE_DB")
    os.environ["SHIFTSENSE_DB"] = str(src)
    url = os.environ.pop("SHIFTSENSE_DATABASE_URL")              # back up the SQLite file, untouched
    try:
        bk = backup.create(src.parent / "backups")
    finally:
        os.environ["SHIFTSENSE_DATABASE_URL"] = url
        if old is None:
            os.environ.pop("SHIFTSENSE_DB", None)
        else:
            os.environ["SHIFTSENSE_DB"] = old
    pgbackup._ensure_tables()
    report = {"sqlite_backup": str(bk), "tables": {}}
    lite = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    with db.connect() as con:
        pg_tables = set(con.tables())
        busy = [t for t in pg_tables if t != "schema_migrations" and con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]]
        if busy and not replace:
            raise RuntimeError(f"The PostgreSQL database already holds data ({', '.join(sorted(busy)[:5])}…). "
                               "Use --replace to overwrite it.")
        for t in [r[0] for r in lite.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                            "'sqlite_%' ORDER BY name")]:
            if t not in pg_tables or t == "schema_migrations":
                continue
            src_cols = [r[1] for r in lite.execute(f"PRAGMA table_info({t})")]
            cols = [c for c in src_cols if c in con.columns(t)]
            rows = lite.execute(f"SELECT {', '.join(cols)} FROM {t}").fetchall()
            if t == "accounts":                                   # retired SMS data is not carried over
                rows = [tuple("" if c == "phone" else 0 if c == "sms_consent" else v for c, v in zip(cols, r))
                        for r in rows]
            con.raw.execute(f"TRUNCATE {t}")
            if rows:
                ph = ", ".join(["%s"] * len(cols))
                con.raw.cursor().executemany(f"INSERT INTO {t} ({', '.join(cols)}) VALUES ({ph})",
                                             [tuple(_bytes(v) for v in r) for r in rows])
            if "id" in cols:
                con.raw.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                                f"COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)")
            n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            report["tables"][t] = {"sqlite": len(rows), "postgres": n}
    lite.close()
    from . import refdata, store
    refdata._ready.clear()
    store._ready.clear()
    ok, bad = comms.verify_audit()
    report["audit"] = "verified" if ok else f"broken at row {bad}"
    report["mismatch"] = [t for t, c in report["tables"].items() if c["sqlite"] != c["postgres"]]
    return report


def main(argv: list[str]) -> int:
    src = Path(argv[argv.index("--from") + 1]) if "--from" in argv else None
    if src is None:
        from .store import DEFAULT_DB
        src = DEFAULT_DB
    try:
        r = migrate(src, replace="--replace" in argv)
    except (RuntimeError, FileNotFoundError) as e:
        print(f"Not migrated: {e}")
        return 1
    print(f"SQLite backup: {r['sqlite_backup']}")
    for t, c in r["tables"].items():
        print(f"  {t:22} {c['sqlite']:>7} -> {c['postgres']:>7}")
    print(f"Audit chain in PostgreSQL: {r['audit']}")
    print("MIGRATED" if not r["mismatch"] and r["audit"] == "verified" else f"CHECK: {r['mismatch']}")
    return 0 if not r["mismatch"] and r["audit"] == "verified" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
