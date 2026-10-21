"""Cost figures from configured assumptions only (config/policy.yaml -> costs). Simulated.

  base labour  = scheduled paid hours x hourly_rate
  overtime     = (extension hours + extra-shift paid hours) x hourly_rate x ot_multiplier
  recovery     = the actual cost of each approved recovery action (recovery engine)
Unresolved gaps are reported in people-hours: no penalty rate is configured, so no rupee value is shown.
"""
from __future__ import annotations

import pandas as pd

from .config import day_label

KIND = {"redeploy": "Redeployment", "swap": "Shift swap", "open_shift": "Extra shift (OT)", "extension": "Extension (OT)"}


def rates(policy: dict) -> tuple[float, float]:
    c = policy["costs"]
    return float(c["hourly_rate"]), float(c["ot_multiplier"])


def day_costs(w, days) -> pd.DataFrame:
    """Per day: paid hours and overtime, priced at each person's contract rate (default rates without one)."""
    from .config import pay_table
    pay = pay_table(w.policy, w.data["agents"])
    default = rates(w.policy)
    rows = []
    for d in days:
        ro = w.roster[w.roster.day == d]
        base_h = ot_h = labour = overtime = 0.0
        for r in ro.itertuples():
            rate, mult = pay.get(r.agent_id, default)
            paid = w.policy["shifts"][r.shift]["paid"]
            ot = max(r.ext_before, 0) + max(r.ext_after, 0)
            if r.kind == "open_shift":
                ot += paid
            else:
                b = paid + min(r.ext_after, 0)          # left early shortens paid time
                base_h += b
                labour += b * rate
            ot_h += ot
            overtime += ot * rate * mult
        rows.append({"day": d, "Date": day_label(w.policy, d), "Paid hours": base_h, "OT hours": ot_h,
                     "Labour ₹": round(labour), "Overtime ₹": round(overtime)})
    df = pd.DataFrame(rows)
    df["Total ₹"] = df["Labour ₹"] + df["Overtime ₹"]
    return df


def recovery_costs(w, days=None) -> pd.DataFrame:
    rows = []
    for inc in w.incidents:
        if days is not None and inc.day not in days:
            continue
        for a in inc.applied:
            rows.append({"Type": KIND.get(a.kind, a.kind), "Cost ₹": round(a.cost), "OT hours": a.ot_hours,
                         "Alert": inc.id, "Date": day_label(w.policy, inc.day)})
    df = pd.DataFrame(rows, columns=["Type", "Cost ₹", "OT hours", "Alert", "Date"])
    return df


def budget(w) -> float | None:
    """Weekly budget, only if configured (costs.weekly_budget)."""
    b = w.policy["costs"].get("weekly_budget")
    return float(b) if b else None
