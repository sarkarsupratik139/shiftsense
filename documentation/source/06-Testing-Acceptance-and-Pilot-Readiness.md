# ShiftSense — Testing, Acceptance and Pilot Readiness

*Version 1.0 · October 2026.*

## 1. Release status

| Level | Status |
|---|---|
| Demonstration-ready | **Yes** |
| PoC-verified | **Yes**: H1–H5 pass on synthetic data; full suite passes on SQLite and PostgreSQL 16 |
| Pilot-ready | **Ready for a supervised pilot once the client items in §6 are provided** (hosting, SSO, email relay, real data, validated assumptions, screen-reader session) |

## 2. Automated tests

| Run | Result | Files |
|---|---|---|
| Full suite, SQLite | **767 passed, 2 skipped** (the 2 PostgreSQL-server tests) | `evidence/test-results/automated-tests/` |
| Full suite, PostgreSQL 16 | **768 passed, 1 skipped** (SQLite-file drill; the PostgreSQL drill ran) | `evidence/test-results/database-compatibility/` |

769 test cases in 32 files cover hard rules, Erlang C and forecasting, optimiser and baseline, recovery, workflows end
to end, the permission matrix (every action × 6 roles), live/demo isolation, HR decision record, security, photos,
imports, PostgreSQL backup/restore/migration, email over SMTP, and Streamlit screen tests for every role. Catalogue:
`supporting/testing/workflow-test-catalog.xlsx`. Traceability from requirement to test:
`supporting/testing/requirements-traceability.xlsx`.

## 3. Evidence: hypotheses H1–H5 (simulated)

| | Hypothesis | Result | Measured |
|---|---|---|---|
| H1 | Planning effort falls ≥ 70 % | Pass | 92 % less (400 → 30 min, modelled); coverage 90.9 % vs 75.4 % |
| H2 | Under-covered workload falls ≥ 20 % at equal or lower paid hours | Pass | 24.6 % → 9.1 %; paid hours 3,824 vs 3,824 |
| H3 | Median Time-to-Cover < 10 min | Pass | 7.0 min vs 58 min manual (modelled) |
| H4 | Equal coverage at lower cost than overtime-first | Pass | ₹4,950 vs ₹9,500 median; cheaper in 8/8 |
| H5 | Zero hard-rule violations | Pass | 0 across 3 plans, 1 re-plan, 32 recoveries |

**Ablation (simulated):**

| Approach | Coverage % | Under-covered % | Fairness std | Re-plan changes | Time-to-Cover (min) | Recovery cost ₹ | Violations |
|---|---|---|---|---|---|---|---|
| Manual heuristic | 75.4 | 24.6 | 1.85 | 2 | 48 | 4,500 | 0 |
| Optimiser only | 90.9 | 9.1 | 2.22 | 475 | 60 | 9,375 | 0 |
| + fairness + stability | 90.9 | 9.1 | 1.60 | 23 | 58 | 9,500 | 0 |
| ShiftSense full loop | 90.9 | 9.1 | 1.60 | 23 | 7.0 | 8,800 | 0 |

The manual heuristic's lower recovery cost restores only 54 % of the gap (median); the full loop restores 96 %. At
equal restored coverage, ShiftSense is cheaper (H4). CSVs: `evidence/test-results/workflow-validation/`.

## 4. Accessibility

Automated checks in headless Chrome on 31 screens of all six roles at 1440, 768 and 375 px (93 checks): accessible
names for every button, link and input; images with alt text; page language set; one H1 per screen; no sideways page
scrolling. Results: `evidence/test-results/accessibility/`. Contrast is tested in `test_app.py` (muted text ≥ 4.5:1,
body text ≥ 7:1, both themes). **Not done:** a session with a real screen reader (NVDA / VoiceOver).

## 5. Workflow validation

- End-to-end workflows for every role are automated in `test_scenarios_final.py`, `test_workflows_e2e.py`, `test_acceptance.py` and the HR suites (see `evidence/test-results/workflow-validation/README.md`).
- In a real browser, on a throwaway copy of the database, these were exercised: publish, rollback, swap (agent →
  colleague → team lead), HR separation with second-user confirmation, HR policy, governance scan, theme switching,
  and the coverage matrix and calendars at desktop and phone widths.
- Screenshots for every role in light and dark themes: `evidence/screenshots/`.

{{include: ../supporting/testing/known-issues-and-limitations.md}}
{{include: ../../evidence/acceptance/pilot-acceptance-checklist.md}}
