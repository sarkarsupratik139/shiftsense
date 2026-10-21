"""Saved what-if runs: inputs and the full metric comparison (keep plan vs re-planned), per workspace run.

What-ifs run on copies and never touch the live roster. Saving them lets an Analyst compare old runs later
instead of keeping only a one-line audit summary. Rosters themselves are not stored (they can be re-run).
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import db
from .store import _con, db_path


def _db(path: Path | None = None):
    con = _con(path)
    db.setup_once("whatif", con, _setup)
    return con


def _setup(con):
    con.execute("""CREATE TABLE IF NOT EXISTS whatif_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, created TEXT, sim_time TEXT, actor TEXT, name TEXT,
        inputs TEXT, status TEXT, as_is TEXT, after TEXT)""")


def _plain(v):
    try:
        return v.item()                      # numpy scalars
    except AttributeError:
        return str(v)


def save(run_id: str, *, actor: str, sim_time: str, name: str, inputs: dict, status: str, as_is: dict | None,
         after: dict | None) -> int:
    with _db() as con:
        cur = con.execute("INSERT INTO whatif_runs (run_id, created, sim_time, actor, name, inputs, status, as_is, "
                          "after) VALUES (?,?,?,?,?,?,?,?,?)",
                          (run_id, datetime.now().isoformat(timespec="seconds"), sim_time, actor, name,
                           json.dumps(inputs, default=_plain), status, json.dumps(as_is, default=_plain),
                           json.dumps(after, default=_plain) if after else None))
        return int(cur.lastrowid)


def runs(run_id: str) -> list[dict]:
    with _db() as con:
        rows = con.execute("SELECT id, created, sim_time, actor, name, inputs, status, as_is, after FROM whatif_runs "
                           "WHERE run_id=? ORDER BY id DESC", (run_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(zip(["id", "created", "sim_time", "actor", "name", "inputs", "status", "as_is", "after"], r))
        for k in ("inputs", "as_is", "after"):
            d[k] = json.loads(d[k]) if d[k] else None
        out.append(d)
    return out
