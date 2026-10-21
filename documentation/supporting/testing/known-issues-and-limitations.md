# Known issues and limitations

Status as of 21 Oct 2026. Release status: **demonstration-ready and PoC-verified; pilot needs the items in §2.**

## 1. By design (PoC scope)
- All data is synthetic (seeded). Costs use configured simulated rates; recovery outcomes are simulated.
- No trained ML: the forecast is a seasonal average; plans come from the CP-SAT optimiser.
- Forecast accuracy and actual service level show "Unavailable" until real call volumes are imported.
- No real integrations (payroll, WFM, telephony); data comes in by file or form.
- One application server. Workspace saves are versioned (a conflicting save is refused), but multi-server
  deployment has not been tested.
- Retention periods are not set (HR-08 deferred); nothing is deleted automatically.
- Absence flags and overtime approval are built but **off** until thresholds are approved.

## 2. Needs the client / a person before a pilot
| Item | What is needed |
|---|---|
| Single sign-on | Identity-provider client ID and secret; built and unit-tested, not run against a real provider |
| Email delivery | A real SMTP relay; tested only against a local SMTP server. "Sent" = accepted by relay, not delivered |
| Hosting | A host for the Docker stack (PostgreSQL 16, app, jobs, Caddy). Compose file validated; full stack not run on a server |
| Screen-reader session | NVDA / VoiceOver session by a person; automated accessibility checks pass |
| Demo accounts | Change passwords or remove accounts (health check fails in pilot mode otherwise) |
| Assumptions | Validate costs, recovery timings, governance thresholds, HR thresholds with EXL |
| Restore rehearsal | Restore on a production-like environment (automated drill passes on PostgreSQL) |
| Real HRIS export | Leave-balance feed handles flexible headers; the real file format has not been seen |

## 3. Known technical limits
- **Restart after code changes**: Streamlit runs with `fileWatcherType = "none"`.
- **Theme switch** relies on Streamlit 1.64 menu identifiers (`stMainMenuItem-theme-*`); pinned in
  `requirements.txt`, re-check on upgrade.
- No icon-only sidebar rail (Streamlit's own collapse control is used).
- The coverage matrix is dense on very small phones at 1-hour interval; the 2-hour interval shows full cell text.
- Roster calendars are view-only (no drag-and-drop); roster changes are explicit, permission-checked actions.
- Screens do not auto-refresh; use **Refresh** in the sidebar.
- Larger sites than the synthetic set (50 people, 4 queues; full pack 120 people, 6 queues) were not load-tested.
