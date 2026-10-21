# Accessibility evidence (21 Oct 2026)

**Method.** Headless Chrome against a throwaway copy of the demo database. Every screen below was signed in as its
role and checked at **1440 px (desktop), 768 px (tablet) and 375 px (phone, mobile emulation)**: 93 checks on
31 screens. Raw results: `automated-checks.json`.

Checks: every visible button and link has an accessible name (aria-label or text content); every visible input has
a label; every image has an alt attribute; page language set (`en`); exactly one H1; no sideways page scrolling.
Colour contrast is covered by `tests/test_app.py` (muted text ≥ 4.5:1, body text ≥ 7:1, light and dark).

| Screen | Named controls | Labelled inputs | Image alt | Language | One H1 | No sideways scroll |
|---|---|---|---|---|---|---|
| sign-in | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| admin-overview | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| admin-governance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| admin-audit-integrity | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| admin-system-status | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| analyst-forecast-staffing | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| analyst-roster-optimization | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| analyst-what-if | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| analyst-review-publish | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| analyst-benchmarks | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| analyst-add-data | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| analyst-demo-scenario | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| teamlead-overview | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| teamlead-attendance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| teamlead-team-schedule | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| teamlead-requests | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| teamlead-alerts | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ops-overview | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ops-coverage-risk | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ops-alerts-escalations | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ops-recovery | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| ops-cost-staffing | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| hr-people-overview | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| hr-employees | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| hr-leave-balances | ✓ | see note | ✓ | ✓ | ✓ | ✓ |
| hr-attendance | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| hr-reviews-approvals | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| hr-policies | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| agent-home | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| agent-my-shifts | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| agent-inbox-requests | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

**Note on HR → Leave & balances.** The single unlabelled input is the hidden native `<input type="date"
tabindex="-1">` inside Streamlit's date field. Assistive technology uses the visible field, which is labelled
*Effective date* (`role="group"`, `aria-label="Effective date"`), so this is not a barrier. An earlier pass flagged
buttons inside collapsed sections; that was a measurement artefact (hidden text has no `innerText`), and the check
now uses the text content.

**Earlier work (19–21 Oct 2026).** Keyboard focus is clearly visible; swap-colleague options are read by name; the
navigation drawer closes after a choice on small screens; no sideways scrolling at 640 px (200 % zoom).

**Not covered.** A session with a real screen reader (NVDA or VoiceOver) by a person. This is still required before
pilot approval.
