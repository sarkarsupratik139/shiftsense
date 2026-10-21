# Pilot acceptance checklist

Status words: **Done**: verified by automated tests and/or in a browser. **Client**: needs something only the
client can provide. **Open**: not done yet.

## A. Functional (PoC scope)
| # | Criterion | Evidence | Status |
|---|---|---|---|
| A1 | Roster built with zero hard-rule violations | H5; `tests/test_constraints.py`; health check | Done |
| A2 | Optimised plan beats the manual heuristic on coverage at equal paid hours | H1, H2 (simulated) | Done |
| A3 | Absence → ranked fix → team-lead decision → roster updated, residual risk escalated | H3, H4; `test_workflows_e2e.py` | Done |
| A4 | Leave Green/Amber/Red with alternatives; HR review triggers | `test_workflow.py`, `test_hr_record.py` | Done |
| A5 | Every action permission-checked on the server (6 roles) | `test_matrix.py`; role-permission-matrix.xlsx | Done |
| A6 | Demo scenario never changes live data | `test_isolation.py` | Done |
| A7 | Audit trail append-only and hash-chained | `test_services.py`; health check | Done |
| A8 | HR decision record HR-01..HR-08 | `test_hr_record.py`, `test_hr_gaps.py` | Done |
| A9 | Imports validate and never invent mandatory fields | `test_data_system.py`, `test_importer.py` | Done |
| A10 | Light and dark themes, role navigation, calendars, coverage matrix | `test_ui_overhaul.py`; screenshots | Done |

## B. Quality
| # | Criterion | Evidence | Status |
|---|---|---|---|
| B1 | Full suite passes on SQLite | 767 passed, 2 skipped (PostgreSQL-only) | Done |
| B2 | Full suite passes on PostgreSQL 16 | 768 passed, 1 skipped (SQLite-only drill) | Done |
| B3 | Automated accessibility checks (names, labels, alt, lang, headings, no sideways scroll at 1440/768/375 px) | `evidence/test-results/accessibility` | Done |
| B4 | Screen-reader session (NVDA / VoiceOver) | needs a person | Open |
| B5 | Manual browser runs: publish, rollback, swap, separation, HR policy, governance | READINESS pass 19 Oct 2026 (throwaway copy) | Done |

## C. Before go-live (client)
| # | Item | Status |
|---|---|---|
| C1 | Host for the Docker stack; DNS name for HTTPS (`SHIFTSENSE_DOMAIN`) | Client |
| C2 | Identity provider (OIDC) client ID and secret; `SHIFTSENSE_AUTH=sso` or `both` | Client |
| C3 | SMTP relay host and credentials; send the test email and check the inbox | Client |
| C4 | Remove demo accounts or change their passwords; create real accounts | Client |
| C5 | Validate assumptions: pay rates, shrinkage, SLA targets, recovery timings, governance and HR thresholds | Client |
| C6 | Approve retention periods (HR-08) and absence-flag / overtime thresholds | Client |
| C7 | Real data: employee export, call-volume history, HRIS leave-balance export | Client |
| C8 | Restore rehearsal on the pilot host (`scripts/backup.sh drill`, then a real restore test) | Client |
| C9 | Security review / penetration test | Client |
| C10 | `scripts/health.sh` passes in pilot mode (`SHIFTSENSE_ENV=pilot`) | Open until C1–C4 |

**Sign-off**

| Role | Name | Date | Signature |
|---|---|---|---|
| Business owner (EXL) | | | |
| Operations lead | | | |
| HR | | | |
| IT / security | | | |
