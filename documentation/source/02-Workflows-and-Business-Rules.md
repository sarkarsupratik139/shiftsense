# ShiftSense — Workflows and Business Rules

*Version 1.0 · October 2026. All data synthetic.*

This document describes every business workflow, who may act at each step, and the rules the server enforces.
State tables are in `supporting/governance/workflow-state-and-decision-tables.xlsx`; diagrams are in the Markdown
files under `supporting/workflows/`.

## 1. Rules that apply everywhere

- **Server-side permission** on every action (`shiftsense/services.py`): role, own team scope, account still active,
  no self-approval, no acting on your own HR record. Hiding a button is never the protection.
- **All-or-nothing changes** (`workflow.transaction`). A change that would add a hard-rule violation is rolled back
  at commit time.
- **Hard rules are never relaxed:** skills eligibility, approved leave, night permission, contract shift type,
  rest ≥ 11 h, maximum weekly hours, weekly off, maximum nights, extension and daily-hour limits. Contract limits can
  never be looser than the company-wide floor.
- **Shifts that have started are never changed** by imports, separations or recovery.
- **Reasons are mandatory** for modify, reject, escalate, revoke, decline, reset, unlock and policy changes.
- **Audit:** one append-only, hash-chained row per action.
- **Truthfulness:** an unknown value is shown as unknown (*No attendance record*, *Unavailable*, *Not configured*).

## 2. Key decision rules

| Rule | Value |
|---|---|
| Leave coverage check | Green: team stays covered · Amber: thin · Red: short (alternatives offered) |
| Auto-approval of leave | Only Green, no HR trigger, and a verified sufficient balance (or unpaid) |
| HR leave-review triggers | unverified or insufficient balance; > 5 consecutive calendar days; sick > 2 days |
| Attendance lock | 48 h after the shift ends; later changes only through HR correction |
| Check-in window | 30 min before to 1 h after the shift start; once; signed-in agent only |
| Separation timing | Access ends at 00:00 Asia/Kolkata on the day after the last working day |
| Absence flags | 3 qualifying unplanned absences in any 30 days; **off pending validation** |
| Overtime approval | **Off** until a threshold is approved |
| Overdue alert | A critical alert undecided for 2 h (simulated) is reported to Ops once |
| Data alert window | After a data change: today + 1 day get alerts; later days a re-plan notice (0–3 configurable) |
| Coverage states | Covered 100 % · Tight 95–99.9 % · Short 85–94.9 % · Critical < 85 % |
| Recovery ranking | incremental cost + residual SLA risk + blast radius; minimum necessary change |
| Cross-skill redeployment | donor queue must stay at or above its own requirement |
| Swap | partner must be trained for the same queue; rules re-checked for both |

{{include: ../supporting/workflows/forecasting-and-roster-planning.md}}
{{include: ../supporting/workflows/absence-and-recovery.md}}
{{include: ../supporting/workflows/leave-review.md}}
{{include: ../supporting/workflows/attendance-and-corrections.md}}
{{include: ../supporting/workflows/employee-onboarding.md}}
{{include: ../supporting/workflows/HR-case-management.md}}
{{include: ../supporting/workflows/notifications-and-escalations.md}}
{{include: ../supporting/workflows/data-import-and-validation.md}}
{{include: ../supporting/workflows/authentication-and-account-lifecycle.md}}
{{include: ../supporting/workflows/administration-and-audit.md}}
