"""Rank open problems by urgency, SLA exposure, cost and time remaining.

All inputs come from the recovery engine's own gap calculation; nothing is estimated beyond it.
  short_ph    people-hours still uncovered for this alert (current roster)
  exposure    short_ph x the queue's configured SLA sensitivity
  hours_left  hours from now until the first uncovered interval
"""
from __future__ import annotations

from .config import interval_label

SEVERITY_ORDER = {"Critical": 0, "Warning": 1, "Normal": 2}


def assess(w, inc) -> dict:
    from .workflow import _ctx
    ctx = _ctx(w, inc)
    p = w.policy
    per_h = 60 // p["horizon"]["interval_minutes"]
    deficit = ctx.deficit
    short_ph = float(deficit.sum()) / per_h
    exposure = 0.0
    queues, first_t = [], None
    for qi, q in enumerate(ctx.queues):
        if deficit[qi].any():
            queues.append(q)
            exposure += float(deficit[qi].sum()) / per_h * p["queues"][q]["sensitivity"]
            t0 = int(deficit[qi].nonzero()[0][0])
            first_t = t0 if first_t is None else min(first_t, t0)
    hours_left = max(0.0, (first_t - w.now_t) / per_h) if first_t is not None else None
    rec = inc.rec or {}
    ev = rec.get("eval", {})
    if short_ph == 0:
        severity = "Normal"
    elif exposure >= 4 or (hours_left is not None and hours_left <= 6):
        severity = "Critical"
    else:
        severity = "Warning"
    score = exposure / max(hours_left if hours_left is not None else 24, 0.5)
    if inc.status == "needs_new_fix":
        score += 1
    return {
        "id": inc.id, "severity": severity, "short_ph": round(short_ph, 1), "exposure": round(exposure, 1),
        "hours_left": None if hours_left is None else round(hours_left, 1),
        "starts": interval_label(p, first_t) if first_t is not None else "—",
        "queues": queues, "fix_cost": float(ev.get("cost", 0.0)),
        "fix_covers_pct": float(ev.get("restored_pct", 0.0)), "fix_people": int(ev.get("agents", 0)),
        "residual_ph": float(ev.get("short_head_hours_after", 0.0)), "score": score,
    }


def rank(w, incidents) -> list[tuple]:
    """[(incident, assessment)] most urgent first."""
    scored = [(i, assess(w, i)) for i in incidents]
    return sorted(scored, key=lambda x: (SEVERITY_ORDER[x[1]["severity"]], -x[1]["score"]))
