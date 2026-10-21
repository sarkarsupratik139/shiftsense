"""Manual heuristic baseline: mimics a competent WFM analyst building the roster by hand.

Published rules (shown in the Evidence appendix so the baseline is not a strawman):
1. Satisfy hard constraints (eligibility, leave, rest, weekly offs, nights, weekly hours).
2. Build day by day; within a day fill required heads, highest-demand intervals first,
   choosing the standard shift that best covers the remaining shortfall.
3. Distribute skills: primary-queue agents first, then cross-skilled agents.
4. Rotate nights and weekends round-robin (fewest nights/weekends so far goes next).
5. Top up every agent to their contracted shifts where the remaining shortfall is largest.
6. Recovery (see recovery.baseline_recover): exceptions one at a time, first eligible
   off-duty agent from the same queue (call-in on overtime), then shift extensions.
"""
from __future__ import annotations

import time
from collections import defaultdict

import numpy as np
import pandas as pd

from .config import (agent_rules, intervals_per_day, is_night, shift_allowed, is_weekend, n_intervals, queue_names,
                     shift_intervals, shift_names, shift_window, warmup_mask, week_of)
from .roster import make_roster


class _State:
    """Per-agent bookkeeping for the greedy analyst."""

    def __init__(self, policy, agents, leave):
        self.p = policy
        self.rules = policy["rules"]
        self.agents = agents
        self.elig = dict(zip(agents.agent_id, agents.eligible))
        self.primary = dict(zip(agents.agent_id, agents.primary))
        self.night_ok = dict(zip(agents.agent_id, agents.night_ok))
        cons = dict(zip(agents.agent_id, agents["contract"])) if "contract" in agents.columns else {}
        self.R = {a: agent_rules(policy, cons.get(a) if isinstance(cons.get(a), str) else None) for a in agents.agent_id}
        self.on_leave = set(zip(leave.agent_id, leave.day))
        self.assign: dict[tuple[str, int], tuple[str, str]] = {}
        self.week_count = defaultdict(int)
        self.week_nights = defaultdict(int)
        self.burden = defaultdict(int)          # nights + weekend shifts
        days = policy["horizon"]["days"]
        self.contract = {}
        for a in agents.agent_id:
            for w in range((days + 6) // 7):
                wdays = [d for d in range(w * 7, min(days, w * 7 + 7))]
                lv = sum((a, d) in self.on_leave for d in wdays)
                self.contract[(a, w)] = max(0, min(self.R[a]["contracted_shifts_per_week"], len(wdays)) - lv)

    def rest_ok(self, a, d, s):
        start, end = shift_window(self.p, d, s)
        for nd in (d - 1, d + 1):
            other = self.assign.get((a, nd))
            if other:
                os_, oe = shift_window(self.p, nd, other[0])
                gap = start - oe if nd < d else os_ - end
                if gap < self.R[a]["min_rest_hours"]:
                    return False
        return True

    def can(self, a, d, s, q):
        w = week_of(d)
        return (q in self.elig[a]
                and (a, d) not in self.on_leave
                and (a, d) not in self.assign
                and shift_allowed(self.p, self.R[a], bool(self.night_ok[a]), s)
                and self.week_count[(a, w)] < self.contract[(a, w)]
                and (not is_night(self.p, s) or self.week_nights[(a, w)] < self.R[a]["max_nights_per_week"])
                and self.rest_ok(a, d, s))

    def put(self, a, d, s, q):
        w = week_of(d)
        self.assign[(a, d)] = (s, q)
        self.week_count[(a, w)] += 1
        if is_night(self.p, s):
            self.week_nights[(a, w)] += 1
            self.burden[a] += 1
        if is_weekend(self.p, d):
            self.burden[a] += 1


def plan(policy: dict, agents: pd.DataFrame, leave: pd.DataFrame, req: np.ndarray) -> dict:
    t0 = time.perf_counter()
    st = _State(policy, agents, leave)
    queues = queue_names(policy)
    shifts = shift_names(policy)
    days = policy["horizon"]["days"]
    per_day = intervals_per_day(policy)
    mask = warmup_mask(policy)
    cov = np.zeros((len(queues), n_intervals(policy)), dtype=int)
    order = list(agents.agent_id)

    def add(a, d, s, q):
        st.put(a, d, s, q)
        rng = shift_intervals(policy, d, s)
        cov[queues.index(q), rng.start:rng.stop] += 1

    def candidates(d, s, q):
        cands = [a for a in order if st.can(a, d, s, q)]
        heavy = is_night(policy, s) or is_weekend(policy, d)
        key = (lambda a: (st.primary[a] != q, st.burden[a], st.week_count[(a, week_of(d))], order.index(a))) if heavy \
            else (lambda a: (st.primary[a] != q, st.week_count[(a, week_of(d))], order.index(a)))
        return sorted(cands, key=key)

    for d in range(days):
        lo, hi = d * per_day, min(n_intervals(policy), (d + 1) * per_day + 14)
        blocked = set()
        while True:
            short = (req[:, lo:hi] - cov[:, lo:hi]) * mask[lo:hi]
            # highest-demand intervals first, among those still short
            cells = [(req[qi, lo + j], qi, lo + j) for qi, j in zip(*np.nonzero(short > 0))
                     if (qi, lo + j) not in blocked]
            if not cells:
                break
            _, qi, t = max(cells)
            q = queues[qi]
            covering = [s for s in shifts if t in shift_intervals(policy, d, s)]
            covering.sort(key=lambda s: -sum(max(0, req[qi, i] - cov[qi, i]) * mask[i]
                                             for i in shift_intervals(policy, d, s)))
            placed = False
            for s in covering:
                c = candidates(d, s, q)
                if c:
                    add(c[0], d, s, q)
                    placed = True
                    break
            if not placed:
                blocked.add((qi, t))

    # top up contracted shifts where the remaining shortfall is largest
    for a in order:
        for w in range((days + 6) // 7):
            while st.week_count[(a, w)] < st.contract[(a, w)]:
                best = None
                for d in range(w * 7, min(days, w * 7 + 7)):
                    for s in shifts:
                        for q in st.elig[a]:
                            if not st.can(a, d, s, q):
                                continue
                            rng = shift_intervals(policy, d, s)
                            qi = queues.index(q)
                            gain = sum(max(0, req[qi, i] - cov[qi, i]) * mask[i] for i in rng)
                            idle = sum(max(0, cov[qi, i] + 1 - req[qi, i]) for i in rng)
                            score = (gain, -idle, q == st.primary[a])
                            if best is None or score > best[0]:
                                best = (score, d, s, q)
                if best is None:
                    break
                add(a, best[1], best[2], best[3])

    roster = make_roster([{"agent_id": a, "day": d, "shift": s, "queue": q}
                          for (a, d), (s, q) in st.assign.items()])
    return {"roster": roster, "build_seconds": time.perf_counter() - t0, "method": "Manual heuristic"}
