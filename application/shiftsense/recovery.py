"""Recovery engine: Generate -> Filter -> Simulate -> Rank -> (Approve) -> Re-solve.

Local re-optimisation of the disrupted day only. Actions:
  redeploy    time-bounded cross-skill move; the donor queue must stay at or above
              its own requirement (never pushed into a new shortage)
  swap        an off-duty, same-queue-eligible agent works today and gives up a later
              shift in the same week (outside the 24 h freeze); any shortage that
              leaves on the later day (max 2 h) is priced into the ranking
  open_shift  TL-issued offer of a full shift to an off-duty eligible agent (overtime)
  extension   extend an adjacent on-shift agent by up to 2 h (overtime)
If nothing closes the gap, residual risk is escalated, never silently accepted.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from ortools.sat.python import cp_model

from .config import (day_label, interval_label, queue_names, shift_intervals, shift_names,
                     warmup_mask, week_of)
from .constraints import check
from .risk import day_window
from .roster import coverage, make_roster

KIND_LABEL = {"redeploy": "Cross-skill redeployment", "swap": "Shift swap",
              "open_shift": "Open-shift offer", "extension": "Shift extension (OT)"}
OT_KINDS = {"open_shift", "extension"}


@dataclass
class Action:
    id: str
    kind: str
    agent_id: str
    day: int
    shift: str
    queue: str                         # queue that gains coverage
    delta: dict                        # (qi, t) -> +1 / -1
    cost: float                        # actual incremental cost, simulated INR
    description: str
    from_queue: str | None = None      # donor queue (redeploy) or queue given up (swap)
    ext_before: float = 0.0
    ext_after: float = 0.0
    alt_start: int = 0
    alt_end: int = 0
    swap_day: int | None = None
    shifts_touched: int = 1
    ot_hours: float = 0.0
    gain: int = 0                      # deficit intervals closed on its own
    penalty: float = 0.0               # ranking-only (e.g. shortage left on a later day)

    @property
    def queues_touched(self) -> set:
        return {self.queue} | ({self.from_queue} if self.from_queue else set())


@dataclass
class Context:
    policy: dict
    data: dict
    req: np.ndarray
    plan_roster: pd.DataFrame          # published plan (before disruption)
    roster: pd.DataFrame               # current state (after disruption)
    day: int
    now_t: int                         # detection time as global interval index
    absent: list = field(default_factory=list)
    target_override: np.ndarray | None = None   # >= 0 where a new target applies (demand spike)

    def __post_init__(self):
        self.queues = queue_names(self.policy)
        self.cur = coverage(self.policy, self.roster)
        plan_cov = coverage(self.policy, self.plan_roster)
        self.target = np.minimum(self.req, plan_cov)
        if self.target_override is not None:
            self.target = np.where(self.target_override >= 0, self.target_override, self.target)
        mask = warmup_mask(self.policy)
        self.deficit = np.zeros_like(self.cur)
        for t in day_window(self.policy, self.day):
            if mask[t] and t >= self.now_t:
                self.deficit[:, t] = np.clip(self.target[:, t] - self.cur[:, t], 0, None)
        self.info = self.data["agents"].set_index("agent_id")
        self.on_leave = set(zip(self.data["leave"].agent_id, self.data["leave"].day))


# ------------------------------------------------------------------ apply / filter
def apply_actions(roster: pd.DataFrame, actions: list[Action]) -> pd.DataFrame:
    rows = roster.to_dict("records")
    for a in actions:
        if a.kind == "redeploy":
            for r in rows:
                if r["agent_id"] == a.agent_id and r["day"] == a.day:
                    r.update(alt_queue=a.queue, alt_start=a.alt_start, alt_end=a.alt_end, kind="redeploy")
        elif a.kind == "extension":
            for r in rows:
                if r["agent_id"] == a.agent_id and r["day"] == a.day:
                    r.update(ext_before=r["ext_before"] + a.ext_before,
                             ext_after=r["ext_after"] + a.ext_after, kind="extension")
        elif a.kind == "open_shift":
            rows.append({"agent_id": a.agent_id, "day": a.day, "shift": a.shift, "queue": a.queue,
                         "kind": "open_shift"})
        elif a.kind == "swap":
            rows = [r for r in rows if not (r["agent_id"] == a.agent_id and r["day"] == a.swap_day)]
            rows.append({"agent_id": a.agent_id, "day": a.day, "shift": a.shift, "queue": a.queue,
                         "kind": "swap"})
    return make_roster(rows)


def _feasible(ctx: Context, action: Action) -> bool:
    """Filter step: apply to the agent's own rows and run the independent checker."""
    mine = ctx.roster[ctx.roster.agent_id == action.agent_id]
    trial = apply_actions(mine, [action])
    return not check(ctx.policy, ctx.data["agents"], ctx.data["leave"], trial)


# ------------------------------------------------------------------ generate
def _runs(ts: list[int]) -> list[list[int]]:
    runs, cur = [], []
    for t in ts:
        if cur and t == cur[-1] + 1:
            cur.append(t)
        else:
            if cur:
                runs.append(cur)
            cur = [t]
    if cur:
        runs.append(cur)
    return runs


def generate(ctx: Context) -> list[Action]:
    p, costs, rules = ctx.policy, ctx.policy["costs"], ctx.policy["rules"]
    from .config import pay_table
    pay = pay_table(p, ctx.info.reset_index())          # each person's contract rate (default rates otherwise)
    per_h = 60 // p["horizon"]["interval_minutes"]
    D = ctx.day
    today = ctx.roster[ctx.roster.day == D]
    working = set(today.agent_id)
    cands: list[Action] = []
    short_q = [qi for qi in range(len(ctx.queues)) if ctx.deficit[qi].any()]
    notice = ctx.now_t + per_h                        # 1 h notice for anyone new

    for qi in short_q:
        q = ctx.queues[qi]
        # --- cross-skill redeployment (time-bounded, donor protected)
        for r in today.itertuples(index=False):
            if (r.queue == q or r.alt_queue or r.agent_id in ctx.absent
                    or q not in ctx.info.loc[r.agent_id].eligible):
                continue
            pi = ctx.queues.index(r.queue)
            ts = [t for t in shift_intervals(p, D, r.shift, r.ext_before, r.ext_after)
                  if t >= ctx.now_t and ctx.deficit[qi, t] > 0 and ctx.cur[pi, t] - 1 >= ctx.req[pi, t]]
            for run in _runs(ts):
                if len(run) < 2:
                    continue
                delta = {**{(pi, t): -1 for t in run}, **{(qi, t): 1 for t in run}}
                cands.append(Action(
                    id=f"R-{r.agent_id}-{run[0]}", kind="redeploy", agent_id=r.agent_id, day=D,
                    shift=r.shift, queue=q, from_queue=r.queue, delta=delta, cost=costs["redeploy_cost"],
                    alt_start=run[0], alt_end=run[-1] + 1,
                    description=f"Move {r.agent_id} from {r.queue} to {q} "
                                f"{interval_label(p, run[0])}–{interval_label(p, run[-1] + 1)}"))
        # --- extensions of adjacent on-shift agents on the short queue
        for r in today.itertuples(index=False):
            if r.queue != q or r.alt_queue or r.agent_id in ctx.absent or r.ext_after < 0:
                continue
            rng = shift_intervals(p, D, r.shift, r.ext_before, r.ext_after)
            max_iv = int((rules["max_extension_hours"] - r.ext_before - r.ext_after) * per_h)
            after = []
            for t in range(rng.stop, rng.stop + max_iv):
                if t < ctx.deficit.shape[1] and ctx.deficit[qi, t] > 0:
                    after.append(t)
                else:
                    break
            before = []
            for t in range(rng.start - 1, rng.start - 1 - max_iv, -1):
                if t >= notice and ctx.deficit[qi, t] > 0:
                    before.append(t)
                else:
                    break
            for side, ts in (("after", after), ("before", before)):
                if not ts:
                    continue
                h = len(ts) / per_h
                cands.append(Action(
                    id=f"E-{r.agent_id}-{side}", kind="extension", agent_id=r.agent_id, day=D,
                    shift=r.shift, queue=q, delta={(qi, t): 1 for t in ts}, cost=h * pay[r.agent_id][0] * pay[r.agent_id][1],
                    ext_after=h if side == "after" else 0.0, ext_before=h if side == "before" else 0.0,
                    ot_hours=h, description=f"Extend {r.agent_id} {h:g} h {side} {r.shift} shift on {q}"))
        # --- off-duty agents: open-shift offer (OT) or swap (day-off exchange)
        for a in ctx.info.itertuples():
            aid = a.Index
            if aid in working or aid in ctx.absent or (aid, D) in ctx.on_leave or q not in a.eligible:
                continue
            for s in shift_names(p):
                rng = shift_intervals(p, D, s)
                if rng.start < notice or not any(ctx.deficit[qi, t] > 0 for t in rng):
                    continue
                paid = p["shifts"][s]["paid"]
                delta = {(qi, t): 1 for t in rng}
                cands.append(Action(
                    id=f"O-{aid}-{s}-{q}", kind="open_shift", agent_id=aid, day=D, shift=s, queue=q,
                    delta=delta, cost=paid * pay[aid][0] * pay[aid][1], ot_hours=paid,
                    description=f"Offer {aid} an open {s} shift on {q} (overtime)"))
                cands.extend(_swaps(ctx, aid, s, q, qi, delta, per_h))
    return cands


def _swaps(ctx: Context, aid: str, s: str, q: str, qi: int, delta: dict, per_h: int) -> list[Action]:
    """Day-off exchange: work shift s today, give up a later same-week shift outside the
    24 h freeze. Up to 4 h of shortage left on that later day is allowed and priced in
    (half weight: there are days to re-plan it)."""
    p, costs, D = ctx.policy, ctx.policy["costs"], ctx.day
    later = ctx.roster[(ctx.roster.agent_id == aid) & (ctx.roster.day > D)
                       & (ctx.roster.day.map(week_of) == week_of(D))]
    options = []
    for r in later.itertuples(index=False):
        give = shift_intervals(p, r.day, r.shift)
        if give.start < ctx.now_t + 24 * per_h:
            continue
        ri = ctx.queues.index(r.queue)
        harm = int(sum(ctx.cur[ri, t] - 1 < ctx.req[ri, t] for t in give))
        if harm <= 4 * per_h:
            options.append((harm, r.day, r, ri, give))
    if not options:
        return []
    harm, _, r, ri, give = min(options, key=lambda o: (o[0], o[1]))
    penalty = harm * costs["shortfall_penalty"] * p["queues"][r.queue]["sensitivity"] * 0.5
    note = "" if not harm else f" ({day_label(p, r.day)} is then {harm * 60 // per_h} min short; the planner can fix that)"
    return [Action(
        id=f"S-{aid}-{s}-{q}-{r.day}", kind="swap", agent_id=aid, day=D, shift=s, queue=q,
        delta={**delta, **{(ri, t): -1 for t in give}}, cost=costs["swap_cost"], penalty=penalty,
        from_queue=r.queue if r.queue != q else None, swap_day=r.day, shifts_touched=2,
        description=f"{aid} works {s} on {q} on {day_label(p, D)} and takes {day_label(p, r.day)} off instead{note}")]


# ------------------------------------------------------------------ simulate + rank
def _gain(ctx: Context, a: Action) -> int:
    return sum(1 for (qi, t), v in a.delta.items() if v > 0 and ctx.deficit[qi, t] > 0)


def select(ctx: Context, cands: list[Action], kinds: set | None = None,
           exclude: set | None = None, residual_cap: int | None = None,
           time_limit: float = 10.0) -> dict:
    """Local re-solve: choose the subset of feasible actions minimising incremental cost +
    blast radius + residual SLA risk, with donor protection and one action per agent.
    With `residual_cap`, minimise cost subject to leaving at most that many short
    head-intervals (used to price 'equal coverage' against the manual process)."""
    p = ctx.policy
    costs = p["costs"]
    blast_w = p["recovery"]["blast_weight"]
    pool = [a for a in cands if (kinds is None or a.kind in kinds) and (not exclude or a.id not in exclude)]
    m = cp_model.CpModel()
    y = {a.id: m.NewBoolVar(a.id) for a in pool}
    by_agent: dict[str, list] = {}
    cells: dict[tuple, list] = {}
    for a in pool:
        by_agent.setdefault(a.agent_id, []).append(y[a.id])
        for c, v in a.delta.items():
            cells.setdefault(c, []).append((v, y[a.id]))
    for vs in by_agent.values():
        m.AddAtMostOne(vs)
    obj = []
    short = {}
    deficit_cells = {(qi, t) for qi, t in zip(*np.nonzero(ctx.deficit))}
    today = set(day_window(p, ctx.day))
    for c in sorted(set(cells) | deficit_cells):
        qi, t = c
        new = int(ctx.cur[qi, t]) + sum(v * yy for v, yy in cells.get(c, []))
        if t in today and any(v < 0 for v, _ in cells.get(c, [])):   # donor protection
            m.Add(new >= int(min(ctx.req[qi, t], ctx.cur[qi, t])))
        if c in deficit_cells:
            s = m.NewIntVar(0, int(ctx.deficit[qi, t]), f"s_{qi}_{t}")
            m.Add(s >= int(ctx.target[qi, t]) - new)
            short[c] = s
            if residual_cap is None:
                obj.append(int(costs["shortfall_penalty"] * p["queues"][ctx.queues[qi]]["sensitivity"]) * s)
    if residual_cap is not None:
        m.Add(sum(short.values()) <= residual_cap)
    for a in pool:
        obj.append(int(a.cost + a.penalty + blast_w * a.shifts_touched) * y[a.id])
    m.Minimize(sum(obj))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = 1                          # small model; deterministic
    solver.parameters.random_seed = int(p["seed"])
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    chosen = [a for a in pool if solver.Value(y[a.id])]
    residual = {c: int(solver.Value(s)) for c, s in short.items() if solver.Value(s) > 0}
    return {"actions": chosen, "residual": residual}


def evaluate(ctx: Context, actions: list[Action]) -> dict:
    """Simulate a set of actions: coverage restored, cost, blast radius, OT."""
    new_roster = apply_actions(ctx.roster, actions)
    new_cov = coverage(ctx.policy, new_roster)
    mask_cells = ctx.deficit > 0
    before = int(ctx.deficit.sum())
    after = int(np.clip(ctx.target - new_cov, 0, None)[mask_cells].sum())
    minutes = ctx.policy["horizon"]["interval_minutes"]
    return {
        "roster": new_roster,
        "deficit_before": before, "deficit_after": after,
        "restored_pct": 100.0 * (before - after) / before if before else 100.0,
        "short_head_hours_before": before * minutes / 60, "short_head_hours_after": after * minutes / 60,
        "cost": float(sum(a.cost for a in actions)),
        "ot_hours": float(sum(a.ot_hours for a in actions)),
        "agents": len({a.agent_id for a in actions}),
        "shifts": int(sum(a.shifts_touched for a in actions)),
        "queues": len(set().union(*[a.queues_touched for a in actions])) if actions else 0,
        "violations": check(ctx.policy, ctx.data["agents"], ctx.data["leave"], new_roster),
    }


def recommend(ctx: Context, exclude: set | None = None) -> dict:
    """Full pipeline; returns the recommended plan plus alternatives for 'why not'."""
    t0 = time.perf_counter()
    raw = generate(ctx)
    feasible = [a for a in raw if _feasible(ctx, a)]
    for a in feasible:
        a.gain = _gain(ctx, a)
    feasible = [a for a in feasible if a.gain > 0]
    rec = select(ctx, feasible, exclude=exclude)
    solver_ok = rec is not None
    if not solver_ok:                       # timeout / internal error: change nothing, keep the whole gap visible
        rec = {"actions": [], "residual": {(int(qi), int(t)): int(ctx.deficit[qi, t])
                                           for qi, t in zip(*np.nonzero(ctx.deficit))}}
    latency = time.perf_counter() - t0
    variants = {
        "OT only": select(ctx, feasible, kinds=OT_KINDS, exclude=exclude),
        "No cross-skill moves": select(ctx, feasible, kinds={"swap", "open_shift", "extension"}, exclude=exclude),
    }
    ev = evaluate(ctx, rec["actions"])
    ttc = time_to_cover(ctx.policy, latency, rec["actions"])
    alts = {k: {**evaluate(ctx, v["actions"]), "actions": v["actions"]} for k, v in variants.items() if v is not None}
    counts = pd.Series([a.kind for a in raw]).value_counts().to_dict() if raw else {}
    fcounts = pd.Series([a.kind for a in feasible]).value_counts().to_dict() if feasible else {}
    return {
        "actions": _order(rec["actions"]), "eval": ev, "alternatives": alts,
        "latency_s": latency, "ttc_min": ttc, "generated": counts, "feasible": fcounts, "n_feasible": len(feasible),
        "escalation": escalation(ctx, ev, rec["residual"], alts) if ev["deficit_after"] else None,
        "confidence": confidence(len(feasible), len(rec["actions"]), ev),
        "why_not": why_not(ev, alts, rec["actions"]),
        "candidates": feasible,
        "solver_ok": solver_ok,
    }


def cost_at_equal_coverage(ctx: Context, candidates: list[Action], residual: int) -> dict | None:
    """Cheapest ShiftSense action set that leaves no more shortage than `residual`
    head-intervals (the manual outcome) - the H4 'equal coverage' comparison."""
    res = select(ctx, candidates, residual_cap=residual)
    if res is None:
        return None
    return {**evaluate(ctx, res["actions"]), "actions": res["actions"]}


def _order(actions):
    rank = {"redeploy": 0, "swap": 1, "extension": 2, "open_shift": 3}
    return sorted(actions, key=lambda a: (rank[a.kind], a.agent_id))


def confidence(n_feasible: int, n_chosen: int, ev: dict) -> str:
    if ev["deficit_after"] == 0 and n_feasible >= 2 * max(1, n_chosen):
        return "High"
    if ev["restored_pct"] >= 75:
        return "Medium"
    return "Low"


def why_not(ev: dict, alts: dict, chosen: list[Action]) -> str:
    if not chosen:
        return "No feasible action was found (or the solver did not finish); the whole gap is escalated."
    ot = alts.get("OT only")
    kinds = {a.kind for a in chosen}
    if ot is None:
        return "Overtime-only comparison unavailable; recommended option chosen on cost, risk and change size."
    if kinds <= OT_KINDS:
        return ("Overtime is needed: nobody trained for this queue has spare time elsewhere, "
                "and no one could swap a day without leaving another gap.")
    diff = ot["cost"] - ev["cost"]
    if diff > 0 and ot["restored_pct"] >= ev["restored_pct"] - 0.5:
        return (f"Uses people who are already working or have a spare day — ₹{diff:,.0f} cheaper than "
                f"covering the same gap with overtime (₹{ot['cost']:,.0f}).")
    if ot["restored_pct"] < ev["restored_pct"] - 0.5:
        return (f"Overtime alone would cover only {ot['restored_pct']:.0f}% of the gap; "
                f"this covers {ev['restored_pct']:.0f}%.")
    return "Lowest-cost option that keeps the change to people's schedules small."


def escalation(ctx: Context, ev: dict, residual: dict, alts: dict) -> dict:
    p = ctx.policy
    minutes = p["horizon"]["interval_minutes"]
    by_q: dict[str, list] = {}
    for (qi, t), v in residual.items():
        by_q.setdefault(ctx.queues[qi], []).append((t, v))
    lines = []
    for q, items in by_q.items():
        items.sort()
        ts = [t for t, _ in items]
        worst = max(v for _, v in items)
        lines.append(f"{q}: −{worst} heads, {interval_label(p, ts[0])}–{interval_label(p, ts[-1] + 1)} "
                     f"({len(ts) * minutes} min, SLA sensitivity {p['queues'][q]['sensitivity']:.1f})")
    return {
        "message": "No feasible recovery closes the full gap. Ops decision required.",
        "residual": lines,
        "residual_head_hours": ev["short_head_hours_after"],
        "remaining_alternatives": {k: f"restores {v['restored_pct']:.0f}% for ₹{v['cost']:,.0f}"
                                   for k, v in alts.items()},
    }


def time_to_cover(policy: dict, latency_s: float, actions: list[Action]) -> float:
    """Simulated minutes from detection until the recovered coverage is in place: system
    latency + simulated TL approval + slowest agent confirmation (offers go out in parallel).
    If the gap is only partly closed this is the time to the best achievable state and the
    residual is escalated."""
    r = policy["recovery"]
    confirm = max((r["confirm_minutes"][a.kind] for a in actions), default=0)
    return latency_s / 60 + r["approval_minutes"] + confirm


# ------------------------------------------------------------------ manual baseline
def baseline_recover(ctx: Context, lost: pd.DataFrame, seed: int) -> dict:
    """Analyst/TL process: exceptions one at a time in arrival order. For each lost shift,
    phone off-duty agents whose primary queue is the same (roster order), give the first
    who accepts the shift on overtime; if nobody, extend adjacent same-queue agents."""
    p = ctx.policy
    r = p["recovery"]
    rng = np.random.default_rng(seed)
    per_h = 60 // p["horizon"]["interval_minutes"]
    clock = r["manual_detect_minutes"]
    actions: list[Action] = []
    roster = ctx.roster
    used = set()
    close_times = []
    for lr in lost.itertuples(index=False):
        q, s = lr.queue, lr.shift
        qi = ctx.queues.index(q)
        rng_t = shift_intervals(p, ctx.day, s)
        placed = False
        if rng_t.start >= ctx.now_t + per_h:          # enough notice to call someone in
            for a in ctx.info.itertuples():
                aid = a.Index
                if (a.primary != q or aid in used or aid in ctx.absent
                        or ((roster.agent_id == aid) & (roster.day == ctx.day)).any()
                        or (aid, ctx.day) in ctx.on_leave):
                    continue
                act = Action(id=f"M-{aid}", kind="open_shift", agent_id=aid, day=ctx.day, shift=s, queue=q,
                             delta={(qi, t): 1 for t in rng_t}, cost=p["shifts"][s]["paid"] * p["costs"]["hourly_rate"]
                             * p["costs"]["ot_multiplier"], ot_hours=p["shifts"][s]["paid"],
                             description=f"Call in {aid} for {s} on {q} (overtime)")
                trial = Context(p, ctx.data, ctx.req, ctx.plan_roster, roster, ctx.day, ctx.now_t, ctx.absent)
                if not _feasible(trial, act):
                    continue
                clock += r["manual_call_minutes"]
                used.add(aid)
                if rng.random() < r["manual_accept_prob"]:
                    actions.append(act)
                    roster = apply_actions(roster, [act])
                    placed = True
                    close_times.append(clock + r["confirm_minutes"]["open_shift"])
                    break
        if not placed:
            # OT extensions of same-queue agents adjacent to the lost shift
            trial = Context(p, ctx.data, ctx.req, ctx.plan_roster, roster, ctx.day, ctx.now_t, ctx.absent)
            exts, seen = [], set()
            for c in generate(trial):
                if (c.kind == "extension" and c.queue == q and c.agent_id not in used | seen
                        and _feasible(trial, c)):
                    exts.append(c)
                    seen.add(c.agent_id)
            for c in exts[:2]:
                clock += r["manual_call_minutes"]
                used.add(c.agent_id)
                actions.append(c)
                roster = apply_actions(roster, [c])
            if exts:
                close_times.append(clock + r["confirm_minutes"]["extension"])
    ev = evaluate(ctx, actions)
    return {"actions": actions, "eval": ev, "ttc_min": max(close_times) if close_times else clock}
