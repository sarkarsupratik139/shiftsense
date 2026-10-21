"""Persist the workspace (live and demo scenario) and the schedule change history through the data layer.

The workspace is saved whole (all-or-nothing) with a version number. A save says which version it started from;
if another app server saved in between, the save is refused (WorkspaceConflict) instead of overwriting, and every
server reloads when the stored version is newer than its copy. On each live save the roster and the plan list are
also written as ordinary rows (live_roster, plan_versions) so reports and other tools can query them directly.
"""
from __future__ import annotations

import json
import os
import pickle
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "shiftsense.db"


def db_path() -> Path:
    """SHIFTSENSE_DB overrides the location (tests use a temporary file)."""
    return Path(os.environ.get("SHIFTSENSE_DB", DEFAULT_DB))


# Two separate slots: "live" = the published roster and genuine operational records;
# "scenario" = the demo sandbox (a copy). Nothing written to the scenario slot can reach the live slot.
TABLES = {"live": "workspace", "scenario": "scenario_workspace"}
_ready: set = set()


class WorkspaceConflict(RuntimeError):
    """Someone else saved the workspace after this copy was loaded."""


def _con(path: Path | None = None):
    """A connection with the workspace tables in place (path is used only for SQLite)."""
    from . import db
    con = db.open_conn(path if db.dialect() == "sqlite" else None)
    k = (db.key() if path is None or db.dialect() == "postgres" else f"sqlite:{Path(path).resolve()}")
    if k not in _ready:
      try:
        for t in TABLES.values():
            con.execute(f"CREATE TABLE IF NOT EXISTS {t} (id INTEGER PRIMARY KEY CHECK (id = 1), saved_at TEXT, "
                        f"blob BLOB)")
            if "version" not in con.columns(t):
                con.execute(f"ALTER TABLE {t} ADD COLUMN version INTEGER DEFAULT 0")
        con.execute("CREATE TABLE IF NOT EXISTS live_roster (run_id TEXT, agent_id TEXT, day INTEGER, shift TEXT, "
                    "queue TEXT, alt_queue TEXT, ext_before REAL, ext_after REAL, kind TEXT, saved_at TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS plan_versions (run_id TEXT, plan_id TEXT, name TEXT, status TEXT, "
                    "source TEXT, created_by TEXT, created TEXT, shifts INTEGER, saved_at TEXT)")
        con.commit()
      except BaseException:
        con.rollback()
        con.close()
        raise
      _ready.add(k)
    return con


def _exists(path: Path | None) -> bool:
    from . import db
    return db.dialect() == "postgres" or (path or db_path()).exists()


def version(path: Path | None = None, slot: str = "live") -> int:
    if not _exists(path):
        return 0
    with _con(path) as con:
        row = con.execute(f"SELECT version FROM {TABLES[slot]} WHERE id = 1").fetchone()
    return int(row[0] or 0) if row else 0


def save(world, path: Path | None = None, slot: str = "live", expected: int | None = None) -> int:
    """Atomic. `expected`: the version this copy was loaded at (None = overwrite, used by resets and imports).
    Returns the new version."""
    from . import db
    blob = pickle.dumps(world, protocol=pickle.HIGHEST_PROTOCOL)
    now = datetime.now().isoformat(timespec="seconds")
    t = TABLES[slot]
    with _con(path) as con:
        row = con.execute(f"SELECT version FROM {t} WHERE id = 1").fetchone()
        cur = int(row[0] or 0) if row else 0
        if expected is not None and cur != expected:
            raise WorkspaceConflict(f"The workspace was changed elsewhere (version {cur}, this copy {expected}).")
        new = cur + 1
        if row:
            n = con.execute(f"UPDATE {t} SET saved_at=?, blob=?, version=? WHERE id = 1 AND version = ?",
                            (now, blob if db.dialect() == "sqlite" else blob, new, cur)).rowcount
            if n != 1:
                raise WorkspaceConflict("The workspace was changed elsewhere while saving.")
        else:
            con.execute(f"INSERT INTO {t} (id, saved_at, blob, version) VALUES (1, ?, ?, ?)", (now, blob, new))
        if slot == "live":
            _mirror(con, world, now)
    try:
        world.db_version = new
    except AttributeError:
        pass
    return new


def _mirror(con, w, now: str):
    """Roster and plan list as plain rows, replaced on every live save (read model for reports)."""
    con.execute("DELETE FROM live_roster")
    ro = w.roster
    con.executemany("INSERT INTO live_roster (run_id, agent_id, day, shift, queue, alt_queue, ext_before, ext_after, "
                    "kind, saved_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [(w.run_id, r.agent_id, int(r.day), r.shift, r.queue, r.alt_queue if isinstance(r.alt_queue, str)
                      else None, float(r.ext_before), float(r.ext_after), r.kind, now) for r in ro.itertuples()])
    con.execute("DELETE FROM plan_versions WHERE run_id = ?", (w.run_id,))
    con.executemany("INSERT INTO plan_versions (run_id, plan_id, name, status, source, created_by, created, shifts, "
                    "saved_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    [(w.run_id, p["id"], p["name"], p["status"], p.get("source", ""), p.get("created_by", ""),
                      p.get("created", ""), len(p["roster"]), now) for p in w.plans])


def load(path: Path | None = None, slot: str = "live"):
    if not _exists(path):
        return None
    with _con(path) as con:
        row = con.execute(f"SELECT blob, version FROM {TABLES[slot]} WHERE id = 1").fetchone()
    if not row:
        return None
    try:
        w = pickle.loads(bytes(row[0]))
    except Exception:            # stale or incompatible save: start fresh
        return None
    w.db_version = int(row[1] or 0)
    return w


def clear(path: Path | None = None, slot: str = "live") -> None:
    if _exists(path):
        with _con(path) as con:
            con.execute(f"DELETE FROM {TABLES[slot]}")


# ------------------------------------------------------------------ append-only change history
# Kept in its own table: never cleared by Reset / Load demo story / import.
def _history_con(path: Path | None = None):
    con = _con(path)
    con.execute("CREATE TABLE IF NOT EXISTS schedule_changes ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, saved_at TEXT, sim_time TEXT, "
                "actor TEXT, event TEXT, summary TEXT, reason TEXT, n_changes INTEGER, "
                "details TEXT, snapshot TEXT)")
    have = con.columns("schedule_changes")
    for col in ("role", "status", "related"):            # added later: migrate older databases in place
        if col not in have:
            con.execute(f"ALTER TABLE schedule_changes ADD COLUMN {col} TEXT DEFAULT ''")
    return con


def append_change(entry: dict, path: Path | None = None) -> int:
    with _history_con(path) as con:
        cur = con.execute(
            "INSERT INTO schedule_changes (run_id, saved_at, sim_time, actor, event, summary, reason, "
            "n_changes, details, snapshot, role, status, related) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry["run_id"], datetime.now().isoformat(timespec="seconds"), entry["sim_time"], entry["actor"],
             entry["event"], entry["summary"], entry.get("reason", ""), len(entry["details"]),
             json.dumps(entry["details"]), entry["snapshot"], entry.get("role", ""), entry.get("status", "Applied"),
             entry.get("related", "")))
        return int(cur.lastrowid)


def list_changes(path: Path | None = None) -> list[dict]:
    """All saved changes, newest first (without the bulky snapshot)."""
    if not _exists(path):
        return []
    with _history_con(path) as con:
        rows = con.execute("SELECT id, run_id, saved_at, sim_time, actor, event, summary, reason, n_changes, "
                           "details, role, status, related FROM schedule_changes ORDER BY id DESC").fetchall()
    keys = ["id", "run_id", "saved_at", "sim_time", "actor", "event", "summary", "reason", "n_changes", "details",
            "role", "status", "related"]
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["details"] = json.loads(d["details"])
        out.append(d)
    return out


def change_snapshot(change_id: int, path: Path | None = None) -> str | None:
    with _history_con(path) as con:
        row = con.execute("SELECT snapshot FROM schedule_changes WHERE id = ?", (change_id,)).fetchone()
    return row[0] if row else None
