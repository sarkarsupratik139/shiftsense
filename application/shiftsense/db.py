"""One data layer for SQLite (local demo) and PostgreSQL (pilot). Every module talks to the database through it.

    SHIFTSENSE_DATABASE_URL=postgresql://user:pass@host:5432/shiftsense   -> PostgreSQL (needs `psycopg`)
    unset                                                                   -> SQLite file (SHIFTSENSE_DB)
    SHIFTSENSE_PG_SCHEMA=public                                             -> PostgreSQL schema (default public)

Code writes SQL once with `?` placeholders and portable types; this layer translates for PostgreSQL. On PostgreSQL
every statement runs inside a savepoint, so an expected failure (for example a duplicate key that the caller
catches as IntegrityError) never poisons the rest of the transaction. Schema changes are numbered migrations in
`schema_migrations`. The URL (it may hold a password) is never logged or shown; `describe()` masks it.

Tests can give every temporary SQLite path its own PostgreSQL schema (SHIFTSENSE_PG_SCHEMA_FROM_DB=1), so the whole
suite runs isolated on one PostgreSQL server.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def url() -> str:
    return os.environ.get("SHIFTSENSE_DATABASE_URL", "").strip()


def dialect() -> str:
    return "postgres" if url().startswith(("postgres://", "postgresql://")) else "sqlite"


def _sqlite_path() -> Path:
    from .store import db_path
    return db_path()


def schema() -> str:
    if os.environ.get("SHIFTSENSE_PG_SCHEMA_FROM_DB") == "1":
        return "t_" + hashlib.sha256(str(_sqlite_path()).encode()).hexdigest()[:16]
    s = os.environ.get("SHIFTSENSE_PG_SCHEMA", "public")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,40}", s):
        raise RuntimeError("SHIFTSENSE_PG_SCHEMA must be a simple lowercase name.")
    return s


def describe() -> str:
    """Where data lives, with any password masked."""
    if dialect() == "postgres":
        return re.sub(r"//([^:/@]+):([^@]+)@", r"//\1:•••@", url()) + f" (schema {schema()})"
    return f"sqlite:{_sqlite_path()}"


def exists() -> bool:
    """Is there a database to read? (A PostgreSQL server is assumed reachable; a SQLite file may not exist yet.)"""
    return dialect() == "postgres" or _sqlite_path().exists()


try:                                     # duplicate-key errors from either database
    import psycopg as _pg
    IntegrityError = (sqlite3.IntegrityError, _pg.errors.IntegrityError)
    DatabaseError = (sqlite3.Error, _pg.Error)
except Exception:                        # noqa: BLE001  (driver not installed)
    IntegrityError = (sqlite3.IntegrityError,)
    DatabaseError = (sqlite3.Error,)

NO_ID = {"import_mappings", "schema_migrations", "workspace", "scenario_workspace", "accounts", "live_roster",
         "plan_versions"}


def translate(sql: str) -> str:
    """SQLite-flavoured SQL -> PostgreSQL. Only the constructs this codebase uses."""
    if dialect() != "postgres":
        return sql
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")
    sql = re.sub(r"\bBLOB\b", "BYTEA", sql)
    sql = re.sub(r"ADD COLUMN (?!IF NOT EXISTS)", "ADD COLUMN IF NOT EXISTS ", sql)
    sql = re.sub(r"INSERT OR IGNORE INTO (\w+)(.*)", r"INSERT INTO \1\2 ON CONFLICT DO NOTHING", sql, flags=re.S)
    sql = sql.replace("%", "%%")                  # literal % (e.g. LIKE patterns) is not a placeholder
    out, in_str = [], False
    for ch in sql:                                # ? -> %s outside string literals
        if ch == "'":
            in_str = not in_str
        out.append("%s" if ch == "?" and not in_str else ch)
    return "".join(out)


class Cursor:
    def __init__(self, cur, lastrowid=None):
        self._c, self._last = cur, lastrowid

    def fetchone(self):
        return self._c.fetchone() if self._c.description else None

    def fetchall(self):
        return self._c.fetchall() if self._c.description else []

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._c.rowcount

    @property
    def lastrowid(self):
        return self._last


class Conn:
    """The same small API on both databases; usable as `with conn:` (commit on success, close always)."""

    def __init__(self, raw, kind: str):
        self.raw, self.kind = raw, kind
        self._sp = 0

    # -- statements
    def execute(self, sql: str, args: tuple | list = ()):
        if self.kind == "sqlite":
            c = self.raw.execute(sql, tuple(args))
            return Cursor(c, c.lastrowid)
        sql = translate(sql)
        m = re.match(r"\s*INSERT\s+INTO\s+(\w+)", sql, re.I)
        returning = bool(m and m.group(1).lower() not in NO_ID and "RETURNING" not in sql.upper()
                         and "ON CONFLICT" not in sql.upper())
        if returning:
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        cur = self.raw.cursor()
        self._sp += 1
        sp = f"sp{self._sp}"
        self.raw.execute(f"SAVEPOINT {sp}")                 # own cursor: the statement's result set stays intact
        try:
            cur.execute(sql, tuple(args))
        except Exception:
            self.raw.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            raise
        last = None
        if returning:
            row = cur.fetchone()
            last = row[0] if row else None
        self.raw.execute(f"RELEASE SAVEPOINT {sp}")
        return Cursor(cur, last)

    def executemany(self, sql: str, rows):
        if self.kind == "sqlite":
            return self.raw.executemany(sql, rows)
        cur = self.raw.cursor()
        cur.executemany(translate(sql), [tuple(r) for r in rows])
        return Cursor(cur)

    def executescript(self, script: str):
        if self.kind == "sqlite":
            self.raw.executescript(script)
            return
        for stmt in [s for s in script.split(";") if s.strip()]:
            self.execute(stmt)

    def columns(self, table: str) -> set[str]:
        if self.kind == "sqlite":
            return {r[1] for r in self.raw.execute(f"PRAGMA table_info({table})")}
        return {r[0] for r in self.execute("SELECT column_name FROM information_schema.columns WHERE table_schema = "
                                           "current_schema() AND table_name = ?", (table.lower(),)).fetchall()}

    def tables(self) -> list[str]:
        if self.kind == "sqlite":
            return [r[0] for r in self.raw.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE "
                                                   "'sqlite_%' ORDER BY name")]
        return [r[0] for r in self.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = "
                                           "current_schema() ORDER BY table_name").fetchall()]

    # -- transaction
    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self.raw.commit()
            else:
                self.raw.rollback()
        finally:
            self.raw.close()
        return False


_schemas_made: set = set()


def open_conn(sqlite_path: Path | None = None) -> Conn:
    """A new connection (caller closes it, or uses it as a context manager)."""
    if dialect() == "postgres":
        try:
            import psycopg
        except ImportError as e:                    # clear message instead of a stack trace
            raise RuntimeError("SHIFTSENSE_DATABASE_URL points to PostgreSQL but the 'psycopg' package is not "
                               "installed. Run: pip install 'psycopg[binary]'") from e
        raw = psycopg.connect(url())
        sch = schema()
        if sch != "public" and (url(), sch) not in _schemas_made:
            raw.execute(f"CREATE SCHEMA IF NOT EXISTS {sch}")
            raw.commit()
            _schemas_made.add((url(), sch))
        raw.execute(f"SET search_path TO {sch}")
        return Conn(raw, "postgres")
    path = sqlite_path or _sqlite_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(path, timeout=30)          # default rollback journal: backup/restore swap whole files
    return Conn(raw, "sqlite")


@contextmanager
def connect():
    """A transaction: committed on success, rolled back on error, always closed."""
    con = open_conn()
    with con:
        yield con


def key() -> str:
    """Identifies the current database (and schema) for per-database caches."""
    return describe() if dialect() == "postgres" else f"sqlite:{_sqlite_path().resolve()}"


# ------------------------------------------------------------------ migrations
MIGRATIONS: list[tuple[int, str, str]] = []          # (version, name, sql)


def migration(version: int, name: str, sql: str):
    MIGRATIONS.append((version, name, sql))


def migrate() -> list[int]:
    """Apply pending migrations in order. Returns the versions applied now."""
    applied = []
    with connect() as con:
        con.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT, "
                    "applied TEXT)")
        done = {r[0] for r in con.execute("SELECT version FROM schema_migrations").fetchall()}
        from datetime import datetime
        for v, name, sql in sorted(MIGRATIONS):
            if v in done:
                continue
            con.executescript(sql)
            con.execute("INSERT INTO schema_migrations (version, name, applied) VALUES (?,?,?)",
                        (v, name, datetime.now().isoformat(timespec="seconds")))
            applied.append(v)
    return applied


_setup_done: set = set()


def setup_once(name: str, con: Conn, fn) -> None:
    """Run a module's table setup once per database (and schema), not on every connection."""
    k = (name, key())
    if k in _setup_done:
        return
    try:
        fn(con)
        con.commit()
    except BaseException:
        con.rollback()                  # never leave a half-done setup holding locks
        con.close()
        raise
    _setup_done.add(k)
