"""Recovery performance measured from the live workspace (simulated time).

  time to cover   alert raised -> fixed/closed, in simulated minutes (median)
  acceptance      share of team-lead decisions that approved the recommendation unchanged
Forecast accuracy needs actual call volumes for plan days; it is measured once they are imported (Data hub), otherwise reported as unavailable.
"""
from __future__ import annotations

import statistics


def recovery(w) -> dict:
    mins = w.policy["horizon"]["interval_minutes"]
    # covered = the gap was actually closed in the roster; an Ops decision that leaves people-hours
    # uncovered is counted separately as carried risk, never as covered
    carried = [i for i in w.incidents if i.status == "closed" and (i.residual_ph or 0) > 0]
    done = [i for i in w.incidents if i.created_t is not None and i.resolved_t is not None and i not in carried]
    ttc = [(i.resolved_t - i.created_t) * mins for i in done]
    decisions = [r for r in w.audit.records if r.event == "Recommendation" and r.decision in ("Accept", "Modify", "Reject")]
    esc = [r for r in w.audit.records if r.decision == "Escalate"]
    active = [i for i in w.incidents if i.status in ("open", "needs_new_fix", "escalated")]
    from . import priority
    residual = sum(priority.assess(w, i)["short_ph"] for i in active) + sum(i.residual_ph for i in carried)
    offers = [o for i in w.incidents for o in i.offers]
    return {
        "alerts": len(w.incidents),
        "resolved": len(done),
        "closed_with_risk": len(carried),
        "median_ttc_min": statistics.median(ttc) if ttc else None,
        "acceptance_pct": round(100 * sum(r.decision == "Accept" for r in decisions) / len(decisions), 1) if decisions else None,
        "modified": sum(r.decision == "Modify" for r in decisions),
        "rejected": sum(r.decision == "Reject" for r in decisions),
        "escalated": len(esc),
        "residual_ph": round(residual, 1),
        "offers_filled_pct": round(100 * sum(o["status"] == "filled" for o in offers) / len(offers), 1) if offers else None,
        "forecast_accuracy": _accuracy(w),
    }


def _accuracy(w):
    """Average absolute error % of the forecast over days with imported actual calls; None when there are none."""
    if not getattr(w, "ref_linked", False):
        return None
    try:
        from . import refdata
        fa = refdata.forecast_accuracy(w.policy, w.fc, refdata.actuals(w.policy))
    except Exception:                                   # noqa: BLE001  (no reference data yet)
        return None
    if fa.empty or fa.error_pct.isna().all():
        return None
    return round(float((fa.error_pct * fa.actual).sum() / fa.actual.sum()), 1)
