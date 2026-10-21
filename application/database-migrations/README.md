# Database schema and migrations

ShiftSense creates and upgrades its own tables. There is no separate migration step to run.

* **Where migrations live.** Versioned migrations are registered in code with `db.migration(version, name, sql)`:
  * 101: reference data, in `shiftsense/refdata.py`;
  * 201: the HR decision record, in `shiftsense/hr.py`;
  * 301: employee photos, in `shiftsense/photos.py`.

  `db.migrate()` applies the pending ones in order and records them in `schema_migrations`. Older modules create
  their tables idempotently on first use (`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN` if missing).
* **Same code, both databases.** The same code runs on SQLite (default, file `data/shiftsense.db`) and on
  PostgreSQL 16 (set `SHIFTSENSE_DATABASE_URL`). `shiftsense/db.py` translates the SQL dialect.
* **Reference files.**
  * `schema_sqlite.sql`: the exact table definitions, generated from a freshly initialised SQLite database.
  * `schema_postgresql.sql`: the same tables as created on PostgreSQL 16, generated from a temporary schema.

  Both are for reference and review; the application does not read them.
* **Moving to PostgreSQL.** Moving an existing SQLite database to PostgreSQL:
  `python -m shiftsense.migrate_to_pg` (see `scripts/migrate_to_postgres.sh`). It takes a verified backup first,
  copies everything in one transaction with the original ids, and then verifies the audit chain.
* **The audit table is append-only.** A database trigger refuses UPDATE and DELETE on `audit_log`, and each row is
  hash-chained to the previous one.
