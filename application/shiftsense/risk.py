"""Disruption injection and coverage-risk scoring.

Coverage is a state; risk is its consequence. Two equal gaps can carry different risk:
risk = f(gap, SLA sensitivity, forecast uncertainty, skill scarcity, time to interval,
recovery availability) -> Critical / At risk / Healthy.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import interval_label, intervals_per_day, queue_names, warmup_mask
from .roster import coverage

LEVELS = ["Critical", "At risk", "Healthy"]
WEIGHTS = {"gap": 0.35, "sla": 0.20, "scarcity": 0.20, "urgency": 0.15, "uncertainty": 0.10}
CRITICAL_AT = 0.60


def inject_absence(roster: pd.DataFrame, agent_ids: list[str], day: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Remove the absent agents' shifts on `day`. Returns (disrupted roster, lost shifts)."""
    hit = roster.agent_id.isin(agent_ids) & (roster.day == day)
    return roster[~hit].reset_index(drop=True), roster[hit].reset_index(drop=True)


def pick_absentees(policy: dict, roster: pd.DataFrame, day: int, n: int, seed: int,
                   focus_queue: str | None = None) -> list[str]:
    """Deterministic 'who calls in sick': prefers daytime shifts on the focus queue."""
    rng = np.random.default_rng(seed)
    pool = roster[(roster.day == day) & (roster["shift"] != "Night")]
    if focus_queue:
        main = pool[pool.queue == focus_queue]
        k = min(len(main), max(1, n - 1))
        chosen = list(rng.choice(main.agent_id, size=k, replace=False)) if k else []
        rest = pool[~pool.agent_id.isin(chosen)]
        chosen += list(rng.choice(rest.agent_id, size=n - len(chosen), replace=False))
        return sorted(chosen)
    return sorted(rng.choice(pool.agent_id, size=n, replace=False).tolist())


def day_window(policy: dict, day: int) -> range:
    """Intervals of the operational day: 00:00 of `day` to 07:00 next day (night spill)."""
    per_day = intervals_per_day(policy)
    return range(day * per_day, min(req_len(policy), (day + 1) * per_day + 14))


def req_len(policy):
    return policy["horizon"]["days"] * intervals_per_day(policy)


def supply(policy: dict, data: dict, roster: pd.DataFrame, day: int) -> dict[str, int]:
    """Qualified people who could still help on `day` per queue: off-duty eligible agents
    (not on leave) plus on-shift agents from other queues who are cross-skilled for it."""
    agents = data["agents"]
    on_leave = set(zip(data["leave"].agent_id, data["leave"].day))
    working = roster[roster.day == day]
    work_q = dict(zip(working.agent_id, working.queue))
    out = {}
    for q in queue_names(policy):
        n = 0
        for a in agents.itertuples():
            if q not in a.eligible or (a.agent_id, day) in on_leave:
                continue
            if a.agent_id not in work_q or work_q[a.agent_id] != q:
                n += 1
        out[q] = n
    return out


def assess(policy: dict, data: dict, req: np.ndarray, uncertainty: np.ndarray,
           roster: pd.DataFrame, plan_roster: pd.DataFrame, day: int, now_t: int,
           absent: list[str] | None = None) -> pd.DataFrame:
    """Per queue x interval risk on the operational day. `gap` is shortage vs requirement;
    `target` = min(requirement, what the published plan delivered) is what recovery restores."""
    queues = queue_names(policy)
    cov = coverage(policy, roster)
    plan_cov = coverage(policy, plan_roster)
    mask = warmup_mask(policy)
    sup = supply(policy, data, roster, day)
    minutes = policy["horizon"]["interval_minutes"]
    rows = []
    for qi, q in enumerate(queues):
        sens = policy["queues"][q]["sensitivity"]
        for t in day_window(policy, day):
            if not mask[t] or t < now_t:
                continue
            r, c = int(req[qi, t]), int(cov[qi, t])
            target = min(r, int(plan_cov[qi, t]))
            dgap = max(0, target - c)          # shortage caused by the disruption
            gap = max(0, r - c)
            hours_to = (t - now_t) * minutes / 60
            urgency = 1.0 if hours_to <= 2 else 0.7 if hours_to <= 6 else 0.4 if hours_to <= 24 else 0.2
            scarcity = 1 - min(1.0, sup[q] / max(1, 3 * max(gap, 1)))
            score = 0.0
            if gap > 0:
                score = (WEIGHTS["gap"] * min(1.0, 2 * gap / max(r, 1))
                         + WEIGHTS["sla"] * sens
                         + WEIGHTS["scarcity"] * scarcity
                         + WEIGHTS["urgency"] * urgency
                         + WEIGHTS["uncertainty"] * min(1.0, float(uncertainty[qi, t])))
            level = "Healthy" if gap == 0 else ("Critical" if score >= CRITICAL_AT else "At risk")
            rows.append({"queue": q, "t": t, "time": interval_label(policy, t), "required": r,
                         "available": c, "planned": int(plan_cov[qi, t]), "target": target,
                         "gap": gap, "disruption_gap": dgap, "risk": round(score, 3), "level": level,
                         "sensitivity": sens, "qualified_supply": sup[q],
                         "hours_to_interval": round(hours_to, 1)})
    return pd.DataFrame(rows)


def exceptions(policy: dict, risk_df: pd.DataFrame, disruption_only: bool = True) -> pd.DataFrame:
    """Group contiguous short intervals per queue into exception windows."""
    col = "disruption_gap" if disruption_only else "gap"
    short = risk_df[risk_df[col] > 0].sort_values(["queue", "t"])
    minutes = policy["horizon"]["interval_minutes"]
    out = []
    for q, g in short.groupby("queue"):
        ts = g.t.to_list()
        start = prev = ts[0]
        block = [ts[0]]
        for t in ts[1:] + [None]:
            if t is not None and t == prev + 1:
                block.append(t)
                prev = t
                continue
            b = g[g.t.isin(block)]
            worst = b.loc[b[col].idxmax()]
            level = "Critical" if (b.level == "Critical").any() else "At risk"
            out.append({
                "queue": q, "start_t": block[0], "end_t": block[-1] + 1,
                "window": f"{interval_label(policy, block[0])}–{interval_label(policy, block[-1] + 1)}",
                "required": int(worst.required), "available": int(worst.available),
                "gap": -int(worst[col]), "short_head_hours": b[col].sum() * minutes / 60,
                "level": level, "risk": float(b.risk.max()), "sensitivity": float(worst.sensitivity),
                "qualified_supply": int(worst.qualified_supply),
                "hours_to_start": float(b.hours_to_interval.min()),
            })
            if t is not None:
                block, prev = [t], t
    df = pd.DataFrame(out)
    if df.empty:
        return df
    df["_lvl"] = df.level.map({"Critical": 0, "At risk": 1})
    return df.sort_values(["_lvl", "risk"], ascending=[True, False]).drop(columns="_lvl").reset_index(drop=True)


def explain(ex: dict) -> str:
    parts = []
    parts.append("high SLA sensitivity" if ex["sensitivity"] >= 0.9 else "moderate SLA sensitivity")
    parts.append(f"{ex['qualified_supply']} qualified people could still help")
    h = ex["hours_to_start"]
    parts.append("starts now" if h <= 0 else f"{h:g} h to interval")
    return ", ".join(parts)
