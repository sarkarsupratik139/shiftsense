# Test results (21 Oct 2026)

Run on the code in `application/`, on macOS with Python 3.13. Machine paths and the host name were removed from the
JUnit files; the PostgreSQL connection string was never written.

| Folder | Run | Result |
|---|---|---|
| `automated-tests/` | `pytest` on SQLite | **767 passed, 2 skipped**: `test_postgres.py` needs a PostgreSQL server |
| `database-compatibility/` | `pytest` on PostgreSQL 16 (Docker), one schema per test, plus `SHIFTSENSE_TEST_PG_URL` | **768 passed, 1 skipped**: the SQLite-file restore drill (the PostgreSQL drill ran) |
| `accessibility/` | Automated checks in headless Chrome: 31 screens × 1440/768/375 px | see `summary.md` |
| `workflow-validation/` | Ablation and hypotheses H1–H5 (`shiftsense.evidence.run`, seed 42) | H1–H5 pass (simulated) |

Reproduce: `scripts/run_tests.sh`; PostgreSQL:
`SHIFTSENSE_DATABASE_URL=<url> SHIFTSENSE_TEST_PG_URL=<url> SHIFTSENSE_PG_SCHEMA_FROM_DB=1 .venv/bin/pytest -q`
(use an empty test database: the tests create and drop schemas).
