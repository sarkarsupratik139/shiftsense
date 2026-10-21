"""Backups for PostgreSQL, without needing pg_dump on the app server.

A backup is a zip: one CSV per table (PostgreSQL COPY, consistent snapshot) plus manifest.json with the row
count of every table and the schema migration level. Restore first takes a safety backup, then empties and
reloads every table inside one transaction (all or nothing) and moves id sequences past the restored ids.
A drill restores into a temporary schema, checks it (workspace loads, rule check, audit chain) and drops it;
the live schema is never touched.
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path

from . import db


def _ensure_tables():
    """Make sure every table exists in the current schema (each module creates its own on first use)."""
    from . import accounts, attendance, comms, hr, mailer, photos, refdata, store, whatif_store
    refdata.ensure()
    hr.ensure()
    photos.ensure()
    for mod in (accounts, attendance, comms, mailer, whatif_store):
        with mod._db():
            pass
    with store._history_con():
        pass


def _order(tables: list[str]) -> list[str]:
    first = ["schema_migrations"]
    return first + [t for t in tables if t not in first]


def create(dest_dir: Path) -> Path:
    _ensure_tables()
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"shiftsense-pg-{datetime.now():%Y%m%d-%H%M%S-%f}.zip"
    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "schema": db.schema(), "tables": {}}
    with db.connect() as con:
        con.raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")      # one consistent snapshot
        tables = con.tables()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for t in tables:
                buf = io.BytesIO()
                with con.raw.cursor().copy(f"COPY (SELECT * FROM {t}) TO STDOUT WITH (FORMAT csv, HEADER true)") as cp:
                    for chunk in cp:
                        buf.write(bytes(chunk))
                z.writestr(f"tables/{t}.csv", buf.getvalue())
                manifest["tables"][t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            z.writestr("manifest.json", json.dumps(manifest, indent=1))
    problems = verify(out)
    if problems:
        raise RuntimeError(f"Backup written but failed verification: {problems}")
    return out


def verify(path: Path) -> list[str]:
    try:
        with zipfile.ZipFile(path) as z:
            if z.testzip() is not None:
                return ["zip file is damaged"]
            man = json.loads(z.read("manifest.json"))
            problems = []
            for t, n in man["tables"].items():
                rows = z.read(f"tables/{t}.csv").decode().count("\n") if n else 0
                if n and rows < n + 1:                       # header + at least n lines (fields may hold newlines)
                    problems.append(f"table {t}: expected {n} rows")
            if "workspace" not in man["tables"]:
                problems.append("no workspace table")
            return problems
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, OSError) as e:
        return [f"cannot read backup: {type(e).__name__}"]


def restore(path: Path, dest_dir: Path) -> Path:
    problems = verify(path)
    if problems:
        raise RuntimeError(f"Refusing to restore an invalid backup: {problems}")
    safety = create(dest_dir)
    _load(path)
    return safety


def _load(path: Path):
    _ensure_tables()
    with zipfile.ZipFile(path) as z, db.connect() as con:
        man = json.loads(z.read("manifest.json"))
        have = set(con.tables())
        for t in _order([t for t in man["tables"] if t in have]):
            con.raw.execute(f"TRUNCATE {t}")
            data = z.read(f"tables/{t}.csv")
            header = data.split(b"\n", 1)[0].decode().strip()
            cols = ", ".join(f'"{c}"' for c in header.split(","))
            with con.raw.cursor().copy(f"COPY {t} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)") as cp:
                cp.write(data)
            if "id" in con.columns(t):
                con.raw.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), "
                                f"COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)")
    from . import refdata, store
    refdata._ready.clear()
    store._ready.clear()


def drill(path: Path) -> dict:
    """Restore into a temporary schema, check it, drop it. The live schema is not touched."""
    t0 = time.perf_counter()
    out = {"backup": str(path), "problems": verify(path)}
    if out["problems"]:
        out["seconds"] = round(time.perf_counter() - t0, 2)
        return out
    old = os.environ.get("SHIFTSENSE_PG_SCHEMA"), os.environ.get("SHIFTSENSE_PG_SCHEMA_FROM_DB")
    tmp = f"drill_{datetime.now():%H%M%S}_{os.getpid()}"
    os.environ["SHIFTSENSE_PG_SCHEMA"] = tmp
    os.environ.pop("SHIFTSENSE_PG_SCHEMA_FROM_DB", None)
    try:
        _load(path)
        from . import accounts, comms, store
        from .constraints import check
        w = store.load()
        if w is None:
            out["problems"].append("restored copy: workspace does not load")
        else:
            v = check(w.policy, w.data["agents"], w.data["leave"], w.roster)
            out["workspace"] = f"run {w.run_id}, {len(w.roster)} shifts, {len(v)} rule violation(s)"
        ok, bad = comms.verify_audit()
        out["audit"] = "verified" if ok else f"broken at row {bad}"
        if not ok:
            out["problems"].append(f"restored copy: audit chain broken at row {bad}")
        out["accounts"] = len(accounts.all_accounts())
    finally:
        with db.connect() as con:
            con.raw.execute(f"DROP SCHEMA IF EXISTS {tmp} CASCADE")
        for k, v in zip(("SHIFTSENSE_PG_SCHEMA", "SHIFTSENSE_PG_SCHEMA_FROM_DB"), old):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    out["seconds"] = round(time.perf_counter() - t0, 2)
    return out
