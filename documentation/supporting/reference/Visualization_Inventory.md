# ShiftSense visualization audit and redesign (21 Oct 2026)

**Library and shared code.** The app uses Plotly through `st.plotly_chart`, Streamlit tables, and HTML components
in `ui_kit.py`. All charts are built by shared functions in `ui_kit.py` and shown through one chart card,
`kit.chart`. No new dependencies were added.

**Data basis.**
- Everything is synthetic or derived from the seeded data.
- Required staffing comes from the seasonal forecast and Erlang C, so it is labelled **Modelled estimate**.
- Costs use configured rates, and recovery outcomes are simulated; both are labelled **Simulated**.
- There are no actual call volumes, so forecast accuracy shows **Unavailable**.

## Metric definitions used by the visuals (unchanged)

| Measure | Numerator / denominator | Unit / aggregation |
|---|---|---|
| Required staffing (`w.req`) | Erlang C productive heads ÷ (1 − shrinkage), rounded up | scheduled heads per 30 min |
| Productive requirement (`prod_mid`) | Erlang C heads before shrinkage | heads per 30 min (forecast basis only) |
| Scheduled | people on shift on that queue | heads per 30 min |
| Productive scheduled | scheduled × (1 − shrinkage) | heads per 30 min |
| Shortage | max(required − scheduled, 0) | people per 30 min; people-hours = Σ ÷ 2 |
| Coverage % | Σ min(scheduled, required) ÷ Σ required | over the cells or period shown |
| Matrix states | coverage 100% Covered · 95–99.9% Tight · 85–94.9% Short · <85% Critical · no requirement = No demand | thresholds of the existing `coverage_status` (95 / 85) |
| Ops queue-day coverage | Σ min ÷ Σ required for the day | % (watch list < 95%) |
| Labour cost / overtime | paid hours × contract rate; overtime hours × rate × multiplier; Total = Labour + Overtime | ₹ per day, simulated rates |

**Ambiguity found and resolved.**
- Before: the planner chart and the interval card compared *productive* staffing with the *scheduled-basis*
  requirement. The requirement already includes shrinkage, so shrinkage was in effect counted twice.
- Now: the matrix and all shortage figures compare scheduled with required, which is the app's existing shortage
  definition.
- The detail card shows productive alongside the *productive* requirement (`prod_mid`). It says "Unavailable" where
  demand was revised after the forecast, because the forecast-basis value no longer applies there.
- No calculation was changed.

## Inventory and redesign

| # | Where (role) | Business question | Before | After |
|---|---|---|---|---|
| 1 | Planning → Forecast & staffing (Analyst) | Are the queues staffed to requirement, hour by hour? | Line chart for one queue + 30-min heatmap | **Staffing coverage matrix** (queues × 30 min/1 h/2 h): scheduled/required per cell, state in colour and words, summary (coverage, peak shortfall, short half-hours), filters (day, queues, interval), click → detail card. The trend line is an optional drill-down. The duplicate heatmap was removed. |
| 2 | Planning → interval detail card | What is happening in this window and who can help? | KPIs (mixed bases) + tables | Shared `interval_detail`: matching bases, forecast range, photo/initials people grid, spare trained people, link to recovery alerts |
| 3 | Planning → Gaps this day | Which gaps first? | Table | Kept (exception table), opens the same detail |
| 4 | Planning → Roster optimization comparison | Which roster leaves least uncovered work? | Grouped bars | Kept as grouped bars (neutral second series), titled |
| 5 | Planning → Roster optimization gaps | Where is the live roster short by day? | Heatmap | Shortfall heatmap (people-h short, 0 = neutral, values in cells) |
| 6 | Planning → Roster calendar (new) | Who works when, by team? | Assignments table only | Week calendar (team, week, queue filters) |
| 7 | Planning → What-if | Which scenario leaves least uncovered work? | Bars | Bars, titled, simulated label |
| 8 | Planning → Review & publish, past days | Did approved changes help coverage? | Bars | Bars, titled; "scheduled coverage, not measured service" |
| 9 | Planning → Benchmarks | Does the approach beat the manual heuristic? | Tables | Kept (tables communicate exact values best) |
| 10 | Ops → Overview | What needs a decision now? | 6 + 4 KPIs, priority cards | 5 KPIs + compact line; priority cards kept |
| 11 | Ops → Coverage & risk | Where and when are we short in the next 3 days? | 72 h line chart + queue×day heatmap | **Coverage matrix** (day picker over the 72 h window) + **gap timeline** with a ranked list of the most urgent gaps + **watch list** table. The line chart and heatmap were removed as duplicates. |
| 12 | Ops → Where to intervene | Which issue first? | Table + scatter | Ranked exception table (primary) + compact risk matrix (shapes as well as colour, click → alert) |
| 13 | Ops → Staffing variance | Did people actually turn up? | Lines + hatched bars | Recorded presence vs "no record (unknown)" as soft bars, required/scheduled lines, shortage tint, summary strip |
| 14 | Ops → Recovery | Is recovery working and at what cost? | KPIs + options table | Kept (tables for exact trade-offs, including overtime) |
| 15 | Ops → Cost & staffing | What does the week cost? | Grouped bars + horizontal bars | **Stacked** daily cost (Total = Labour + Overtime), horizontal recovery-spend bars, ₹ tooltips |
| 16 | Alert card (TL/Ops) | Does the proposed fix close the gap? | Before/after line chart | **Coverage now vs after the proposed fix** (2-row matrix by hour); steps marked "Proposed · not approved"; chart kept as drill-down |
| 17 | Team Lead → Overview / Alerts | What to decide first? | Cards + table | Kept |
| 18 | Team Lead → Attendance | Who is in, who has no record? | Stats + table | **Agenda** (time order, unknown never called absent) + table option |
| 19 | Team Lead → Team schedule | What does my team work this week/today? | Grid table | **Week calendar** + **Day time grid** with "now" line and gap rows, detail panel, list alternative |
| 20 | Team Lead → Requests | Do leave requests overlap? | Table | Table + **14-day team leave calendar** (type hidden from TL) |
| 21 | Agent → My shifts | When do I work? | Timeline chart | **Week calendar** (default) with record status; timeline as an option |
| 22 | HR → Leave & balances | Who is off, and does leave overlap? | Tables | **Leave calendar** (team filter, type visible to HR, "people off" row) + tables |
| 23 | HR → Employee profile → Schedule | When does this person work? | Table | Two-week calendar |
| 24 | Admin → Overview | What happened in the week? | Coloured table | **Calendar** (default) with record status + table option (row → agent card) |
| 25 | Admin → Audit, decisions, accounts; HR records; Data hub | Find a specific record | Tables | Kept as searchable/filterable tables (no chart is better here) |

## Components

- **Created:**
  - `kit.coverage_matrix`, `kit.matrix_bins`, `kit.matrix_state`, `kit.matrix_legend`
  - `ui.coverage_matrix_section` (filters, summary, matrix, click-through, trend drill-down)
  - `ui.interval_detail`
  - `kit.week_grid`, `ui.roster_calendar`, `ui.schedule_cell`
  - `kit.timeline`, `kit.agenda`, `ui.leave_calendar`
  - `kit.gap_stats`
  - chart tokens `CHART`
  - `kit.chart` card (title, subtitle, provenance, summary strip, Data & export, minimal toolbar)
- **Reused:**
  - `kit.supply_demand` (drill-down)
  - `kit.variance_chart`, `kit.bars`, `kit.gap_heatmap`, `kit.risk_matrix`, `kit.week_timeline`
  - `kit.kpis`, `kit.stats`, `kit.empty`, `kit.person`, `kit.avatar`
- **Removed from screens:** the Forecast & staffing 30-min heatmap and the Ops 72 h line chart and queue×day
  heatmap. They duplicated the matrix. The functions remain, used elsewhere or as drill-downs.

## Assumptions and limits

- The matrix compares scheduled with required people (the existing shortage basis). Productive figures appear in the
  detail card against the productive requirement.
- Forecast accuracy (MAE/MAPE/WAPE) is not shown. The app has actual-volume import and an accuracy function, but
  there are no real volumes, so it stays "Unavailable".
- Screenshots: no "before" set was saved for every screen. The comparisons are described above, and the "after"
  screens were checked in the browser.
- The roster calendars are view-only. Roster changes stay explicit, permission-checked actions; there is no
  drag-and-drop.
