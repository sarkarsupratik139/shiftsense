# ShiftSense: application

ShiftSense is a workforce operations decision layer for SLA-driven contact-centre (BPO) operations. It forecasts
demand, plans a roster with a CP-SAT optimiser, detects coverage gaps, recommends the least-disruptive recovery,
and runs the people processes around it: leave, attendance, HR and governance. Six roles each sign in to their own
screens.

> **All data is synthetic** (seeded generator). Figures labelled *Simulated* or *Modelled estimate* are not
> measured results.

## Quick start (local, SQLite)

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m streamlit run app.py          # or: scripts/run_app.sh
```

Open http://localhost:8501. In demo mode the sign-in screen lists the demo accounts:

| Username | Role |
|---|---|
| `admin` | Admin: oversight, accounts, governance (no operational decisions) |
| `analyst`, `demo` | Analyst: planning, publishing, data hub, demo scenario |
| `tl1`, `tl2` | Team Lead (TL-1, TL-2) |
| `ops` | Ops: escalations, coverage and cost decisions |
| `hr` | HR |
| `hrlead` | HR with the Senior HR permission |
| `aarav`, `sneha`, `priya` | Agents |

The demo passwords are published on purpose. They exist for the demonstration only and **must be changed or
the accounts removed before any pilot**. The health check fails in pilot mode while a demo password is in use.

## Tests

```bash
.venv/bin/pytest -q            # 769 tests, about 15 minutes (scripts/run_tests.sh)
```

PostgreSQL: `SHIFTSENSE_DATABASE_URL=postgresql://... SHIFTSENSE_PG_SCHEMA_FROM_DB=1 .venv/bin/pytest -q`. Use an
empty test database, because the tests create and drop schemas. `SHIFTSENSE_TEST_PG_URL` additionally runs the
backup, restore, drill and migration tests.

## Pilot deployment (Docker)

```bash
cp .env.example .env                      # fill in; never commit it
mkdir -p .streamlit && touch .streamlit/secrets.toml   # single sign-on settings (see secrets.toml.example)
docker compose --env-file .env up -d --build
```

The stack has four services:
- **db:** PostgreSQL 16, with no public port.
- **app:** the ShiftSense application.
- **jobs:** the nightly verified backup and restore drill, and a health check every 15 minutes with an alert email.
- **proxy:** Caddy for HTTPS, at `SHIFTSENSE_DOMAIN`.

Full instructions: `../documentation/04-Installation-Deployment-and-Operations.pdf`.

## Code layout

ShiftSense is one Python codebase served by Streamlit. The browser UI and the server logic run in the same
process, so the suggested `frontend/` and `backend/` folders map to modules rather than separate builds.

| Part | Where | Notes |
|---|---|---|
| Frontend: screens | `app.py` (entry, sign-in, navigation), `views/*.py` (one screen per role) | Read areas from `shiftsense/navigation.py` |
| Frontend: shared UI | `ui_common.py` (shell, sidebar, cards, calendars, matrix), `ui_kit.py` (design tokens, charts, avatars), `ui_data.py` (data-input screens), `assets/`, `.streamlit/config.toml` | Light/dark tokens in `ui_kit.TOKENS` and `ui_kit.CHART` |
| Backend: server actions | `shiftsense/services.py` | **Every user action goes through here**: permission, team scope, no self-approval, audit row |
| Backend: domain | `workflow.py` (requests, incidents, transactions), `optimizer.py` (CP-SAT), `baseline.py`, `staffing.py` (forecast and Erlang C), `recovery.py`, `risk.py`, `priority.py`, `planning.py`, `hr.py` (HR decision record), `photos.py`, `security.py`, `auth.py`, `accounts.py` | Pure functions where possible |
| Backend: data | `db.py` (SQLite/PostgreSQL), `store.py` (versioned workspace), `refdata.py` (versioned reference data), `dataflow.py` (change engine), `convert.py` (imports), `schemas.py` (field definitions), `comms.py` (notices, audit chain) | Migrations are in code; see `database-migrations/` |
| Operations | `health.py`, `backup.py`, `pgbackup.py`, `jobs.py`, `migrate_to_pg.py`, `dropfolder.py`, `logs.py` | Wrappers in `scripts/` |
| Configuration | `config/policy.yaml` (rules, queues, costs), `config/users.yaml` (roles and demo accounts), `basedata/` (base data pack) | Read-only in the app |
| Tests | `tests/` (pytest + Streamlit AppTest) | `pytest.ini` |

## Configuration (environment variables)

| Variable | Purpose | Default |
|---|---|---|
| `SHIFTSENSE_ENV` | `demo` (demo accounts listed on sign-in) or `pilot` | `demo` |
| `SHIFTSENSE_DB` | SQLite file | `data/shiftsense.db` |
| `SHIFTSENSE_DATABASE_URL` | PostgreSQL URL; when set, PostgreSQL is used | not set |
| `SHIFTSENSE_PG_SCHEMA`, `SHIFTSENSE_PG_SCHEMA_FROM_DB` | PostgreSQL schema / one schema per test | `public` |
| `SHIFTSENSE_AUTH` | `password`, `sso` or `both` | `password` |
| `SHIFTSENSE_BREAKGLASS` | emergency password account(s) when `SHIFTSENSE_AUTH=sso` | not set |
| `SHIFTSENSE_IDLE_MINUTES`, `SHIFTSENSE_LOCKOUT_ATTEMPTS`, `SHIFTSENSE_LOCKOUT_MINUTES` | idle sign-out, lockout | 30, 5, 15 |
| `SHIFTSENSE_PBKDF2_ITERATIONS` | password hashing cost | 600000 |
| `SHIFTSENSE_EMAIL`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_TLS`, `SHIFTSENSE_EMAIL_FROM` | optional email copies (`SHIFTSENSE_EMAIL=smtp`) | off |
| `SHIFTSENSE_ALERT_EMAIL` | health alert recipient | not set |
| `SHIFTSENSE_PHOTO_MAX_MB` | profile photo size limit | 5 |
| `ANTHROPIC_API_KEY`, `SHIFTSENSE_AI`, `SHIFTSENSE_AI_CONVERT` | optional AI explanations / import help (`off` disables AI; `nonpersonal` by default) | AI off without a key |
| `SHIFTSENSE_LOG` | log file | `data/shiftsense.log` |

Single sign-on (OIDC) settings go in `.streamlit/secrets.toml`; see `.streamlit/secrets.toml.example`.

## After changing code

Streamlit runs with `fileWatcherType = "none"`. **Restart the app after code changes.**
