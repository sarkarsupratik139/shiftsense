# ShiftSense UI redesign: audit and feature inventory (20 Oct 2026)

## Current architecture (inspected)
- **Framework:** Streamlit 1.64, server-rendered Python. There is no separate frontend, so the redesign stays in
  Streamlit; no new framework is added.
- **Shell:** `app.py` builds role navigation from `shiftsense/navigation.py` (`st.navigation`; areas are separate
  links). `ui_common.page()` checks access and draws the sidebar. `ui_common.shell()` draws the page header.
- **Design system:** `ui_kit.py`.
  - Tokens: `TOKENS` light/dark, IBM Plex Sans.
  - Components: KPI tiles, status pills, flow, stats, labels, and Plotly charts with a shared template plus
    "Summary & data".
  - Theme: Streamlit's light/dark presets in `.streamlit/config.toml`. Our CSS picks its colours on the server
    from `st.context.theme`.
- **Theme switching:** Streamlit 1.64's main menu has System/Light/Dark items (`stMainMenuItem-theme-*`), but
  `toolbarMode = "minimal"` hid them. Our CSS only followed a theme change after a rerun.
- **Employee schema:** the versioned `employees` reference domain (`shiftsense/schemas.py`, `refdata.py`):
  employee_id, name, team, contract, primary, secondary, skills, night_ok, pref_shift, pref_off, start_date,
  end_date, status… There is no image field and no file storage for people.
- **Avatars:** initials only (sidebar account block).
- **Tests:** 746 pytest tests, including AppTest screen tests that select areas by name
  (`tests/test_navigation.py`, `tests/test_app.py`).

## Constraints carried forward
- All actions go through `services.py` (server-side checks); views never call workflow directly.
- Live/demo separation; audit append-only; no invented figures; synthetic/modelled labels.
- An earlier decision removed a custom "Icons only" sidebar because it was unreliable. Streamlit's own sidebar
  collapse stays. An icon-only rail is not offered natively by Streamlit (see the report).

## Where each existing feature goes

| Role | New navigation | Existing content placed there |
|---|---|---|
| Agent | **Home** | Next shift and tiles, action buttons (time off, swap, can't come in, leave now, message, type a request, preferences, calendar, Excel), check-in |
| | **My shifts** | Week timeline (this week / next week) with record status |
| | **Inbox & requests** | Inbox (offers, swap asks, decisions), My requests table + request card, report a record problem |
| | **My history** | History view |
| Team Lead | **Overview** | Tiles, deadlines, top priorities |
| | **Attendance** | Attendance today, confirm present, ask HR to correct, reminders |
| | **Team schedule** | Team roster with avatars, team schedule card, printable schedule, team pack |
| | **Requests** | Time-off and swap requests, revoke |
| | **Alerts & decisions** | All alerts, alert card (Approve/Modify/Reject/Escalate), report absence, send home, governance questions |
| | **Team information** | Team directory with avatars, proposals to HR, training/disruption report, team history |
| Ops | **Overview** | Next 72 h, operations now, decide first |
| | **Coverage & risk** | Where to intervene, staffing variance, demand vs supply, watch list |
| | **Alerts & escalations** | Escalated issues + Ops decision, report call spike, Analyst risks, governance referrals |
| | **Recovery** | Recovery performance, options per open issue, risk carried |
| | **Cost & staffing** | Cost (simulated), budget |
| | **Follow-ups & history** | Follow-ups assigned, data approvals and situations, operations history, activity log |
| Analyst | **Forecast & staffing** | Demand & staffing (renamed) |
| | **Roster optimization** | Roster & optimization |
| | **What-if** | What-if |
| | **Review & publish** | Guided stepper Configure → Generate draft → Validate → Review impact → Publish; rollback |
| | **Benchmarks & history** | Benchmark (moved from Review & publish), roster versions, forecast revisions, saved what-if runs, planning history |
| | Data hub (5 areas), Demo & data (2 areas) | Unchanged content, restyled |
| HR | **People overview** | Tiles, headcount, joining and leaving |
| | **Employees** (new) | Directory with photo/initials, search and filters; profile with tabs (Employment, Schedule, Leave, Attendance, History); edit form with grouped sections and profile photo; update people data; separations |
| | **Leave & balances** | Leave balances, import, verify, adjust, transactions |
| | **Attendance** | Attendance correction requests, absence-pattern flags, overtime approval |
| | **Reviews & approvals** | Leave review tasks, team-lead proposals, people-data changes |
| | **Restricted cases** | Restricted cases (unchanged rules) |
| | **HR policies** | HR policy, pending register, retention |
| | **Reports & exports** | HR records, templates, people downloads |
| Admin | **Overview** | Week |
| | **Accounts & access** | People & accounts (with avatars), Senior HR, password reset, unlock |
| | **Governance & decisions** | Governance + Decisions |
| | **Audit & integrity** | Audit & integrity |
| | **System status** (new) | Health checks (read-only), email relay check, deployment mode |

Every existing button, card and field stays reachable by the same roles. Server-side permissions are unchanged.
