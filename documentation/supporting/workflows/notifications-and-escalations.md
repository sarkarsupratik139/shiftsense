# Workflow: notifications and escalations

Implementation: `comms.py` (notices, messages), `attendance.py` (reminders), `mailer.py` (optional email), `jobs.py`.

| Channel | Behaviour |
|---|---|
| In-app notices | Primary channel. Every role sees its own notices in the sidebar inbox |
| Messages | Threads between people (e.g. team lead ↔ agent) |
| Email (optional) | Copies of notices to a **verified** address when `SHIFTSENSE_EMAIL=smtp`. Idempotent. "Sent" means accepted by the relay, not delivered |
| SMS | Not used anywhere (retired) |

**Reminders.** Check-in reminders for agents; HR reminders at most once per item per day: review tasks and correction
requests waiting more than a day, case deadlines (today, tomorrow, overdue) and follow-ups (caseworker only, no case
details). They run when HR opens their screens and when live time moves.

**Escalations.**
| From | To | Trigger |
|---|---|---|
| Team lead alert | Ops | Team lead escalates, the approved fix leaves a gap, or the alert is overdue |
| Ops | Team lead | Returned with an instruction |
| Analyst risks | Ops | Planning risks flagged by the Analyst |
| Governance case | Admin → Team lead / Ops | Flagged → under review → explanation requested / referred to Ops → resolved → closed |
| Health check failure | `SHIFTSENSE_ALERT_EMAIL` | `health --alert`, at most once a day per failure |
| People-data change | Analyst | Effect on the plan (`services._tell_analyst`) |

Notices never contain confidential HR content (absence flags are announced without names).
