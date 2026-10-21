# Workflow validation

## 1. Automated end-to-end workflows (part of both suite runs, all passing)
| File | What it walks through |
|---|---|
| `tests/test_scenarios_final.py` | A agent: next shift, check-in, personal history · B team lead: reminder, decision, team history · C Ops: decision, outcome, residual risk in history · D Analyst: what-if, validate, publish, version history · E Admin: governance, audit, no operational power |
| `tests/test_workflows_e2e.py` | swap agreement → approval → re-check → roster update · modify removes chosen steps and recalculates · scenario → live publish with live unchanged until publish |
| `tests/test_acceptance.py` | agent, team lead, Ops, planner and admin acceptance criteria (duplicates, withdrawals, check-in window, escalation, concurrency, infeasible plans, publish review) |
| `tests/test_hr_record.py`, `tests/test_hr_gaps.py` | HR-01..HR-08: separations, balances, review triggers, lock and corrections, flags, overtime, restricted cases |

## 2. Browser runs (real browser, throwaway copy of the database, temporary test passwords)
| Journey | Result |
|---|---|
| Publish a new plan (draft → validate → confirm → publish) | Passed |
| Rollback (restore earlier version → validate → publish) | Passed, after fixing: the button is now disabled until a reason is entered |
| Swap: agent asks → colleague accepts → team lead approves | Passed; roster changed, 0 rule breaks |
| HR separation: record → same-user confirm refused → Senior HR confirms | Passed |
| HR policy versions; Admin governance scan | Passed, after fixing: empty scans are now audited and shown |
| Theme switch System/Light/Dark; dialogs follow the theme | Passed |
| Coverage matrix and calendars at 1000 px and phone width | Passed, after fixing overlapping text at narrow widths |

## 3. Evidence study (simulated)
`ablation-simulated.csv` and `hypotheses-H1-H5-simulated.csv`: manual heuristic vs optimiser vs + fairness/stability
vs full loop, on seed 42. H1–H5 all pass. Build effort and Time-to-Cover are modelled from configured minutes;
costs use configured simulated rates.
