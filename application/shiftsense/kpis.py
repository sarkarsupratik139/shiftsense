"""Plan and recovery metrics. All figures are computed on simulated data."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import interval_label, intervals_per_day, is_night, is_weekend, queue_names, warmup_mask, week_of
from .constraints import check
from .roster import coverage

HOURS_PER_INTERVAL = 0.5


def burden(policy: dict, roster: pd.DataFrame, agents: pd.DataFrame) -> pd.Series:
    """Nights + weekend shifts per agent (the unpopular-shift burden)."""
    b = pd.Series(0, index=agents.agent_id, dtype=float)
    for r in roster.itertuples(index=False):
        b[r.agent_id] += is_night(policy, r.shift) + is_weekend(policy, r.day)
    return b


def ot_hours(policy: dict, roster: pd.DataFrame) -> float:
    """Extension hours + paid hours of shifts beyond the weekly contract."""
    rules = policy["rules"]
    ext = float((roster.ext_before + roster.ext_after).sum()) if len(roster) else 0.0
    extra = 0.0
    if len(roster):
        wk = roster.assign(week=roster.day.map(week_of)).groupby(["agent_id", "week"]).day.nunique()
        extra_days = (wk - rules["contracted_shifts_per_week"]).clip(lower=0).sum()
        extra = float(extra_days) * policy["shifts"]["Day"]["paid"]
    return ext + extra


def plan_kpis(policy: dict, data: dict, req: np.ndarray, roster: pd.DataFrame,
              build_seconds: float | None = None, ref: pd.DataFrame | None = None) -> dict:
    from .roster import diff_count
    mask = warmup_mask(policy)
    cov = coverage(policy, roster)
    r, c = req[:, mask], cov[:, mask]
    under = np.clip(r - c, 0, None)
    over = np.clip(c - r, 0, None)
    agents = data["agents"]
    prim = dict(zip(agents.agent_id, agents.primary))
    pref = dict(zip(agents.agent_id, agents.pref_shift))
    b = burden(policy, roster, agents)
    paid = sum(policy["shifts"][s]["paid"] for s in roster["shift"]) + float((roster.ext_before + roster.ext_after).sum())
    violations = check(policy, agents, data["leave"], roster)
    out = {
        "Coverage %": 100 * np.minimum(r, c).sum() / max(r.sum(), 1),
        "Under-covered workload %": 100 * under.sum() / max(r.sum(), 1),
        "Understaffed intervals": int((under > 0).sum()),
        "Under-staffed head-hours": under.sum() * HOURS_PER_INTERVAL,
        "Over-staffed head-hours": over.sum() * HOURS_PER_INTERVAL,
        "Paid hours": paid,
        "OT hours": ot_hours(policy, roster),
        "Fairness (burden std)": float(b.std(ddof=0)),
        "Max nights+weekends": int(b.max()) if len(b) else 0,
        "Preference met %": 100 * np.mean([pref[a] == s for a, s in zip(roster.agent_id, roster["shift"])]) if len(roster) else 0,
        "Primary-queue share %": 100 * np.mean([prim[a] == q for a, q in zip(roster.agent_id, roster.queue)]) if len(roster) else 0,
        "Hard violations": len(violations),
    }
    if build_seconds is not None:
        out["Build time (s)"] = build_seconds
    if ref is not None:
        out["Schedule changes"] = diff_count(ref, roster)
    return out


def coverage_frame(policy: dict, req: np.ndarray, roster: pd.DataFrame) -> pd.DataFrame:
    """Long frame: queue, t, day, time, required, scheduled, gap."""
    cov = coverage(policy, roster)
    per_day = intervals_per_day(policy)
    rows = []
    for qi, q in enumerate(queue_names(policy)):
        for t in range(req.shape[1]):
            rows.append((q, t, t // per_day, interval_label(policy, t), int(req[qi, t]), int(cov[qi, t])))
    df = pd.DataFrame(rows, columns=["queue", "t", "day", "time", "required", "scheduled"])
    df["gap"] = df.scheduled - df.required
    return df


def queue_coverage_table(policy: dict, req: np.ndarray, roster: pd.DataFrame) -> pd.DataFrame:
    mask = warmup_mask(policy)
    cov = coverage(policy, roster)
    rows = []
    for qi, q in enumerate(queue_names(policy)):
        r, c = req[qi, mask], cov[qi, mask]
        rows.append({
            "Queue": q,
            "Required head-hours": r.sum() * HOURS_PER_INTERVAL,
            "Covered %": 100 * np.minimum(r, c).sum() / max(r.sum(), 1),
            "Understaffed intervals": int((c < r).sum()),
            "Short head-hours": np.clip(r - c, 0, None).sum() * HOURS_PER_INTERVAL,
            "Idle head-hours": np.clip(c - r, 0, None).sum() * HOURS_PER_INTERVAL,
        })
    return pd.DataFrame(rows)
