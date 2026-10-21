# ShiftSense — Installation, Deployment and Operations

*Version 1.0 · October 2026. Commands run from `application/`.*

## 1. Local installation (SQLite)

Requirements: Python 3.11+ (tested on 3.13), about 1 GB disk.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
scripts/run_app.sh            # = .venv/bin/python -m streamlit run app.py
```

Open http://localhost:8501. The first start builds the synthetic dataset and the published roster (about 10 s) and
saves it to `data/shiftsense.db`. Streamlit runs with `fileWatcherType = "none"`: **restart the app after any code
change.**

Tests: `scripts/run_tests.sh` (about 770 tests, 15 minutes on a laptop).

## 2. Pilot deployment (Docker)

```bash
cp .env.example .env                                   # fill in; never commit it
mkdir -p .streamlit && cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # SSO settings
docker compose --env-file .env up -d --build
```

| Service | Role |
|---|---|
| `db` | PostgreSQL 16, no public port, volume `pgdata` |
| `app` | ShiftSense (Streamlit) |
| `jobs` | nightly verified backup (keep 14) and restore drill; health check every 15 min with alert email |
| `proxy` | Caddy, automatic HTTPS for `SHIFTSENSE_DOMAIN` |

**Before go-live:** set `SHIFTSENSE_ENV=pilot`, configure SSO (`SHIFTSENSE_AUTH=sso` or `both`), set the SMTP relay
and `SHIFTSENSE_ALERT_EMAIL`, remove or change the demo accounts, and run `scripts/health.sh` until every essential
check passes. Copy backups off the host.

## 3. Configuration

| Variable | Purpose | Default |
|---|---|---|
| `SHIFTSENSE_ENV` | `demo` (demo accounts listed at sign-in) or `pilot` | `demo` |
| `SHIFTSENSE_DB` | SQLite file | `data/shiftsense.db` |
| `SHIFTSENSE_DATABASE_URL` | PostgreSQL URL (PostgreSQL used when set) | not set |
| `SHIFTSENSE_PG_SCHEMA` | PostgreSQL schema | `public` |
| `SHIFTSENSE_AUTH` | `password`, `sso` or `both` | `password` |
| `SHIFTSENSE_BREAKGLASS` | emergency password account(s) in SSO mode | not set |
| `SHIFTSENSE_IDLE_MINUTES` / `_LOCKOUT_ATTEMPTS` / `_LOCKOUT_MINUTES` | session and lockout | 30 / 5 / 15 |
| `SHIFTSENSE_PBKDF2_ITERATIONS` | password hashing cost | 600000 |
| `SHIFTSENSE_EMAIL`, `SMTP_*`, `SHIFTSENSE_EMAIL_FROM` | optional email copies | off |
| `SHIFTSENSE_ALERT_EMAIL` | health alert recipient | not set |
| `SHIFTSENSE_PHOTO_MAX_MB` | profile photo limit | 5 |
| `ANTHROPIC_API_KEY`, `SHIFTSENSE_AI`, `SHIFTSENSE_AI_CONVERT` | optional AI | off without a key |
| `SHIFTSENSE_LOG` | log file | `data/shiftsense.log` |

Business configuration (read-only in the app): `config/policy.yaml` (rules, queues, shifts, costs, thresholds) and
`config/users.yaml` (roles, permissions, demo accounts).

## 4. Moving from SQLite to PostgreSQL

`scripts/migrate_to_postgres.sh` takes a verified SQLite backup first, refuses a non-empty target unless `--replace`
is given, copies every table with its ids in one transaction, and verifies the audit chain and row counts.

## 5. Scheduled jobs (without Docker)

`scripts/ops-examples/cron.example` and `scripts/ops-examples/systemd/` contain the nightly backup and drill, the
drop-folder staging and the 15-minute health check. Secrets go in `/etc/shiftsense/env`, never on command lines.

{{include: ../../operations/backup-and-restore.md}}
{{include: ../../operations/monitoring-and-health-checks.md}}
{{include: ../../operations/troubleshooting.md}}
{{include: ../../operations/incident-response.md}}
