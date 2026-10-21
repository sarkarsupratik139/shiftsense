# Workflow: forecasting and roster planning

**Owner:** Analyst. Implementation: `staffing.py`, `optimizer.py`, `baseline.py`, `constraints.py`, `planning.py`.

```mermaid
flowchart LR
  V[Call volumes<br/>history or import] --> F[Seasonal forecast<br/>same weekday average ± band]
  F --> E[Erlang C per queue<br/>productive heads]
  E --> R[Required heads<br/>÷ (1 − shrinkage)]
  R --> O[CP-SAT optimiser<br/>hard rules as constraints]
  O --> D[Draft]
  D --> C{Independent<br/>rule check}
  C -- violations --> D
  C -- 0 violations --> P[Publish<br/>previous → superseded]
```

1. **Forecast.** A transparent seasonal baseline (no ML) with low/mid/high band. Labelled *prediction*.
2. **Staffing.** Erlang C per queue gives productive heads for the SLA target; shrinkage turns it into scheduled heads.
3. **Draft.** The CP-SAT optimiser (time-limited) minimises under-staffing (weighted highest), over-staffing,
   overtime, unfairness, preference misses and changes to the published roster. Hard rules are constraints:
   skills, approved leave, night permission, shift type, rest ≥ 11 h, weekly hours, weekly off, nights, extensions.
   Other draft sources: restore an earlier version, import a roster grid, what-if scenario.
4. **Validate.** An independent checker re-tests every hard rule (H5: zero violations).
5. **Publish.** Needs a review confirmation. Refused when the live roster changed after the draft was made (roster
   fingerprint), so stale drafts are never published.
6. **Benchmarks.** The manual heuristic baseline (fill highest-demand intervals first, round-robin nights) is kept for
   comparison on the Evidence/Benchmarks screen. All figures are simulated.

Coverage is shown on the staffing coverage matrix (queues × 30 min/1 h/2 h): Covered 100 %, Tight 95–99.9 %,
Short 85–94.9 %, Critical < 85 %.
