"""Staffing variance by 30-minute interval: required (forecast) vs scheduled (published roster) vs confirmed
present (genuine check-ins; in the demo scenario also simulated attendance, kept separate) vs unknown.

Honesty rules:
  * required comes from the forecast; scheduled from the roster; neither is an actual
  * "confirmed" counts only shifts with an attendance record; a started shift without one is UNKNOWN,
    never assumed present and never assumed absent
  * intervals that have not started yet have no actual value at all (shown as planned only)
  * required staffing is per queue (shared by all teams), so a team view has no required value
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import interval_label, intervals_per_day, queue_names
from .roster import coverage


def _status_mask(w, roster: pd.DataFrame, *values: str) -> pd.Series:
    return pd.Series([w.attendance.get((a, int(d))) in values for a, d in zip(roster.agent_id, roster.day)],
                     index=roster.index, dtype=bool)


def interval_table(w, day: int, agent_ids: set[str] | None = None) -> pd.DataFrame:
    """One row per queue x interval of `day`."""
    p = w.policy
    per_day = intervals_per_day(p)
    queues = queue_names(p)
    ro = w.roster if agent_ids is None else w.roster[w.roster.agent_id.isin(agent_ids)]
    sched = coverage(p, ro)
    genuine = coverage(p, ro[_status_mask(w, ro, "checkin", "confirmed", "feed")])   # recorded: self check-in or TL-confirmed
    simulated = coverage(p, ro[_status_mask(w, ro, "in")])
    sl = slice(day * per_day, (day + 1) * per_day)
    rows = []
    for qi, q in enumerate(queues):
        for t in range(sl.start, sl.stop):
            elapsed = t < w.now_t
            s, g, m = int(sched[qi, t]), int(genuine[qi, t]), int(simulated[qi, t])
            req = None if agent_ids is not None else int(w.req[qi, t])
            rows.append({
                "Queue": q, "t": t, "Time": interval_label(p, t), "Phase": "elapsed" if elapsed else "upcoming",
                "Required (forecast)": req, "Scheduled": s,
                "Checked in (genuine)": g if elapsed else None,
                "Simulated attendance": m if elapsed and getattr(w, "scenario", False) else None,
                "No record yet": max(s - g - m, 0) if elapsed else None,
                "Gap vs scheduled": None if req is None else max(req - s, 0),
                "Surplus vs scheduled": None if req is None else max(s - req, 0),
                "Gap vs confirmed": None if req is None or not elapsed else max(req - g - m, 0),
            })
    return pd.DataFrame(rows)


def summary(df: pd.DataFrame) -> pd.DataFrame:
    """People-hours per queue (30-min intervals / 2). Unknown is never folded into present or absent."""
    def ph(col, mask=None):
        x = df if mask is None else df[mask]
        vals = x.groupby("Queue")[col].apply(lambda s: None if s.isna().all() else float(s.fillna(0).sum()) / 2)
        return vals
    el = df.Phase == "elapsed"
    out = pd.DataFrame({
        "Required (forecast)": ph("Required (forecast)"),
        "Scheduled": ph("Scheduled"),
        "Checked in (genuine, so far)": ph("Checked in (genuine)", el),
        "Simulated attendance (so far)": ph("Simulated attendance", el),
        "No record yet (so far)": ph("No record yet", el),
        "Gap vs scheduled": ph("Gap vs scheduled"),
        "Gap vs confirmed (so far)": ph("Gap vs confirmed", el),
    })
    return out.reset_index()


def explain(df: pd.DataFrame, limit: int = 4) -> list[str]:
    """Plain sentences for the worst windows, built only from the table's own numbers."""
    out, unk_line = [], None
    unk0 = df[(df.Phase == "elapsed") & (df["No record yet"].fillna(0) > 0)]
    if len(unk0):
        n0 = unk0.groupby("Queue")["No record yet"].max()
        unk_line = ("Started shifts without an attendance record (unknown, not assumed absent): "
                    + ", ".join(f"{q} up to {int(v)}" for q, v in n0.items()) + ".")
    if df["Required (forecast)"].notna().any():
        g = df[df["Gap vs scheduled"].fillna(0) > 0]
        for q, x in g.groupby("Queue"):
            worst = x.loc[x["Gap vs scheduled"].idxmax()]
            out.append(f"{q}: the plan is short of the forecast in {len(x)} half-hours; worst at {worst.Time} "
                       f"({int(worst['Required (forecast)'])} required, {int(worst.Scheduled)} scheduled).")
    if unk_line:
        out.insert(0, unk_line)                 # never let plan gaps crowd out the unknown-attendance warning
    return out[:limit] or ["No gap against the forecast and no unrecorded started shifts for this day."]


def freshness(w) -> dict:
    """What the actual figures are based on."""
    genuine = sum(v in ("checkin", "confirmed", "feed") for v in w.attendance.values())
    sim = sum(v == "in" for v in w.attendance.values())
    return {"as_of": w.now_t, "genuine_checkins": genuine, "simulated_records": sim,
            "scenario": bool(getattr(w, "scenario", False))}


def busiest_day(w) -> int:
    return int(min(w.today, w.policy["horizon"]["days"] - 1))


def _nan(x):
    return x is None or (isinstance(x, float) and np.isnan(x))
