# Backup and restore

The same command works for SQLite and PostgreSQL (whichever is configured). Run from `application/`.

| Task | Command |
|---|---|
| Create a verified backup, keep the newest 14 | `scripts/backup.sh create --keep 14` |
| Verify a backup | `scripts/backup.sh verify <file>` |
| Restore | `scripts/backup.sh restore <file>` |
| Restore drill (into a throw-away copy/schema, checked, then dropped) | `scripts/backup.sh drill [<file>]` |

**SQLite.** `create` uses SQLite's online backup API (safe while the app runs) and writes
`data/backups/shiftsense-YYYYmmdd-HHMMSS.db`, then verifies it: integrity check, workspace loads, audit chain intact.

**PostgreSQL.** `create` writes `shiftsense-pg-*.zip`: one CSV per table and a manifest from one consistent snapshot
(no `pg_dump` needed). `restore` takes a safety backup first, then reloads everything in one transaction.

**Restore procedure**
1. Announce downtime. Stop the app (`docker compose stop app jobs`, or stop the Streamlit process).
2. `scripts/backup.sh verify <file>`; do not continue if it reports problems.
3. `scripts/backup.sh restore <file>` (a safety copy of the current data is taken automatically).
4. Start the app; run `scripts/health.sh` and confirm *audit chain verified* and *0 rule violations*.
5. Record who restored, which file and why **outside the app**: a restore also replaces the audit log with the
   backup's version.

**Schedule (Docker stack).** The `jobs` container runs the nightly backup with `--keep 14` and the restore drill.
In pilot mode the health check fails when the newest backup is older than 26 h. Copy backups off the host; they
contain accounts and contact details and must be stored as confidential.

**Moving SQLite → PostgreSQL.** `scripts/migrate_to_postgres.sh` (takes a verified SQLite backup first, refuses a
non-empty target without `--replace`, copies every table in one transaction, verifies the audit chain and row counts).
