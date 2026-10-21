"""Global planner (OR-Tools CP-SAT).

Decision variable x[a, d, s, q] = 1 if agent a works shift s on day d on queue q.
Variables exist only where the agent is eligible for q, is not on approved leave and
(for night shifts) holds night permission, so skill eligibility is a hard constraint by
construction; the independent checker verifies it again after every solve.

Minimise   under-staffing (x SLA sensitivity, weighted highest) + over-staffing
         + fairness spread (nights, weekends) + preference violations
         + secondary-queue assignments + changes vs. a published roster (stability)
Subject to one shift/day, rest >= 11 h, contracted shifts per week (exact, no planned OT),
           weekly off, max nights/week, leave, night permission, frozen days.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from ortools.sat.python import cp_model

from .config import (agent_rules, is_night, is_weekend, n_intervals, queue_names, shift_allowed,
                     shift_intervals, shift_names, shift_window, warmup_mask, week_of)
from .roster import make_roster


@dataclass
class PlanOptions:
    fairness: bool = True
    preferences: bool = True
    stability_ref: pd.DataFrame | None = None     # published roster to stay close to
    freeze_before_day: int = 0                    # days < this are fixed to stability_ref
    time_limit: float | None = None
    label: str = "ShiftSense optimizer"
    weights: dict = field(default_factory=dict)


def rest_conflicts(policy: dict, min_rest: float | None = None) -> list[tuple[str, str]]:
    """(s1, s2) pairs that break min rest when s1 is on day d and s2 on day d+1."""
    out = set()
    min_rest = policy["rules"]["min_rest_hours"] if min_rest is None else min_rest
    for s1 in shift_names(policy):
        for s2 in shift_names(policy):
            _, e1 = shift_window(policy, 0, s1)
            s2_start, _ = shift_window(policy, 1, s2)
            if s2_start - e1 < min_rest:
                out.add((s1, s2))
    return sorted(out)          # stable order: model build must not depend on hash seed


def plan(policy: dict, agents: pd.DataFrame, leave: pd.DataFrame, req: np.ndarray,
         opts: PlanOptions | None = None) -> dict:
    opts = opts or PlanOptions()
    t0 = time.perf_counter()
    w = {**policy["optimizer"]["weights"], **opts.weights}
    rules = policy["rules"]
    queues, shifts = queue_names(policy), shift_names(policy)
    days, T = policy["horizon"]["days"], n_intervals(policy)
    mask = warmup_mask(policy)
    on_leave = set(zip(leave.agent_id, leave.day))
    A = list(agents.agent_id)
    info = agents.set_index("agent_id")
    has_contract = "contract" in agents.columns
    AR = {a: agent_rules(policy, info.loc[a, "contract"] if has_contract and isinstance(info.loc[a, "contract"], str)
                         else None) for a in A}

    m = cp_model.CpModel()
    x = {}
    for a in A:
        ag = info.loc[a]
        for d in range(days):
            if (a, d) in on_leave:
                continue
            for s in shifts:
                if not shift_allowed(policy, AR[a], bool(ag.night_ok), s):
                    continue
                for q in ag.eligible:
                    x[a, d, s, q] = m.NewBoolVar(f"x_{a}_{d}_{s}_{q}")

    # index for speed
    by_ad: dict[tuple, list] = {}
    by_ads: dict[tuple, list] = {}
    for (a, d, s, q), v in x.items():
        by_ad.setdefault((a, d), []).append(v)
        by_ads.setdefault((a, d, s), []).append(v)

    work = {}
    for a in A:
        for d in range(days):
            vs = by_ad.get((a, d), [])
            m.AddAtMostOne(vs)
            work[a, d] = sum(vs)

    # rest between consecutive days
    for rest in sorted({AR[a]["min_rest_hours"] for a in A}):     # same constraint order as before contracts
        group = [a for a in A if AR[a]["min_rest_hours"] == rest]
        for s1, s2, a in ((s1, s2, a) for s1, s2 in rest_conflicts(policy, rest) for a in group):
            for d in range(days - 1):
                v1, v2 = by_ads.get((a, d, s1), []), by_ads.get((a, d + 1, s2), [])
                if v1 and v2:
                    m.Add(sum(v1) + sum(v2) <= 1)

    # weekly: contracted shifts exactly (salaried, no planned OT), nights cap
    for a in A:
        contracted = AR[a]["contracted_shifts_per_week"]
        for wk in range((days + 6) // 7):
            wdays = [d for d in range(wk * 7, min(days, wk * 7 + 7))]
            lv = sum((a, d) in on_leave for d in wdays)
            target = max(0, min(contracted, len(wdays)) - lv)
            if opts.stability_ref is not None and opts.freeze_before_day > wk * 7:
                # frozen days may already hold recovery overtime: never force more shifts than that
                ref = opts.stability_ref
                frozen = int(((ref.agent_id == a) & ref.day.isin(wdays) & (ref.day < opts.freeze_before_day)).sum())
                target = max(target, frozen)
            m.Add(sum(work[a, d] for d in wdays) == target)
            nights = [v for d in wdays for s in shifts if is_night(policy, s) for v in by_ads.get((a, d, s), [])]
            if nights:
                m.Add(sum(nights) <= AR[a]["max_nights_per_week"])

    # coverage
    cover_terms: dict[tuple[int, int], list] = {}
    for (a, d, s, q), v in x.items():
        qi = queues.index(q)
        for t in shift_intervals(policy, d, s):
            cover_terms.setdefault((qi, t), []).append(v)
    obj = []
    under_vars = {}
    for qi, q in enumerate(queues):
        sens = policy["queues"][q]["sensitivity"]
        for t in range(T):
            if not mask[t]:
                continue
            r = int(req[qi, t])
            cov = sum(cover_terms.get((qi, t), []))
            u = m.NewIntVar(0, r, f"u_{qi}_{t}")
            o = m.NewIntVar(0, len(A), f"o_{qi}_{t}")
            m.Add(cov + u - o == r)
            under_vars[qi, t] = u
            obj.append(int(round(w["under"] * sens)) * u)
            obj.append(w["over"] * o)

    # fairness: spread of nights (night-permitted agents) and weekend shifts
    if opts.fairness:
        night_cnt, wkend_cnt = {}, {}
        for a in A:
            night_cnt[a] = sum(v for (aa, d, s, q), v in x.items() if aa == a and is_night(policy, s))
            wkend_cnt[a] = sum(work[a, d] for d in range(days) if is_weekend(policy, d))
        n_max, n_min = m.NewIntVar(0, days, "n_max"), m.NewIntVar(0, days, "n_min")
        w_max, w_min = m.NewIntVar(0, days, "w_max"), m.NewIntVar(0, days, "w_min")
        for a in A:
            ag = info.loc[a]
            if ag.night_ok:
                m.Add(night_cnt[a] <= n_max)
                m.Add(night_cnt[a] >= n_min)
            m.Add(wkend_cnt[a] <= w_max)
            if not any((a, d) in on_leave for d in range(days) if is_weekend(policy, d)):
                m.Add(wkend_cnt[a] >= w_min)
        obj.append(w["fairness"] * 18 * (n_max - n_min + w_max - w_min))

    # preferences + primary-queue preference
    for (a, d, s, q), v in x.items():
        ag = info.loc[a]
        pen = 0
        if opts.preferences:
            pen += w["preference"] * (s != ag.pref_shift) + w["preference"] * (d % 7 == ag.pref_off)
        pen += w["secondary_queue"] * (q != ag.primary)
        if pen:
            obj.append(pen * v)

    # stability vs. a published roster, and frozen days
    if opts.stability_ref is not None:
        ref = {(r.agent_id, r.day): (r.shift, r.queue) for r in opts.stability_ref.itertuples(index=False)}
        for a in A:
            for d in range(days):
                cur = ref.get((a, d))
                keep = x.get((a, d, *cur)) if cur else None
                if d < opts.freeze_before_day:
                    if cur and keep is not None:
                        m.Add(keep == 1)
                    elif not cur:
                        m.Add(work[a, d] == 0)
                    continue
                if cur:
                    change = (1 - keep) if keep is not None else 1
                else:
                    change = work[a, d]
                if not isinstance(change, int):
                    obj.append(w["change"] * change)

    m.Minimize(sum(obj))
    solver = cp_model.CpSolver()
    cfg = policy["optimizer"]
    solver.parameters.max_time_in_seconds = float(opts.time_limit or cfg["time_limit_sec"])
    solver.parameters.max_deterministic_time = float(cfg["deterministic_time"])
    solver.parameters.num_workers = int(cfg["workers"])
    solver.parameters.interleave_search = True
    solver.parameters.random_seed = int(policy["seed"])
    status = solver.Solve(m)
    elapsed = time.perf_counter() - t0
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"roster": make_roster([]), "status": solver.StatusName(status),
                "build_seconds": elapsed, "method": opts.label}
    rows = [{"agent_id": a, "day": d, "shift": s, "queue": q}
            for (a, d, s, q), v in x.items() if solver.Value(v)]
    return {
        "roster": make_roster(rows),
        "status": solver.StatusName(status),
        "objective": solver.ObjectiveValue(),
        "bound": solver.BestObjectiveBound(),
        "build_seconds": elapsed,
        "method": opts.label,
    }
