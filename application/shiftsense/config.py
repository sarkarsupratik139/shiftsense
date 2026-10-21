"""Policy loading and time helpers shared by every engine."""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

import yaml

POLICY_PATH = Path(__file__).resolve().parent.parent / "config" / "policy.yaml"
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def load_policy(path: str | Path = POLICY_PATH) -> dict:
    """Return a fresh copy of the policy pack so callers can tweak it safely."""
    import copy
    return copy.deepcopy(_load(str(path)))


def intervals_per_day(policy: dict) -> int:
    return 24 * 60 // policy["horizon"]["interval_minutes"]


def n_intervals(policy: dict) -> int:
    return policy["horizon"]["days"] * intervals_per_day(policy)


def queue_names(policy: dict) -> list[str]:
    return list(policy["queues"].keys())


def shift_names(policy: dict) -> list[str]:
    return list(policy["shifts"].keys())


def is_night(policy: dict, shift: str) -> bool:
    return bool(policy["shifts"][shift].get("night", False))


def shift_window(policy: dict, day: int, shift: str, ext_before: float = 0, ext_after: float = 0):
    """Absolute [start, end) in hours from horizon start, including extensions."""
    s = policy["shifts"][shift]
    start = day * 24 + s["start"] - ext_before
    end = day * 24 + s["start"] + s["span"] + ext_after
    return start, end


def shift_intervals(policy: dict, day: int, shift: str, ext_before: float = 0, ext_after: float = 0) -> range:
    """Global interval indices covered by a shift (clipped to the horizon)."""
    per_h = 60 // policy["horizon"]["interval_minutes"]
    start, end = shift_window(policy, day, shift, ext_before, ext_after)
    lo = max(0, int(round(start * per_h)))
    hi = min(n_intervals(policy), int(round(end * per_h)))
    return range(lo, hi)


def day_label(policy: dict, day: int) -> str:
    d = date.fromisoformat(policy["horizon"]["start_date"]) + timedelta(days=int(day))
    return f"{DAY_NAMES[d.weekday()]} {d.strftime('%d %b')}"


def is_weekend(policy: dict, day: int) -> bool:
    d = date.fromisoformat(policy["horizon"]["start_date"]) + timedelta(days=int(day))
    return d.weekday() >= 5


def week_of(day: int) -> int:
    return day // 7


def interval_label(policy: dict, t: int) -> str:
    per_day = intervals_per_day(policy)
    mins = (t % per_day) * policy["horizon"]["interval_minutes"]
    return f"{mins // 60:02d}:{mins % 60:02d}"


def warmup_mask(policy: dict):
    """Intervals on day 0 before the first morning shift would be covered by the
    previous week's night shift, which is outside the horizon. Exclude them from
    objectives and KPIs so neither planner is penalised for them."""
    import numpy as np
    per_h = 60 // policy["horizon"]["interval_minutes"]
    first_start = min(s["start"] for s in policy["shifts"].values())
    mask = np.ones(n_intervals(policy), dtype=bool)
    mask[: first_start * per_h + per_h] = False   # 00:00 - 07:00 of day 0
    return mask


def deploy_mode() -> str:
    """'demo' (default: demo accounts listed on the sign-in screen) or 'pilot' (SHIFTSENSE_ENV=pilot: no demo
    account list, and the health check fails while any active account still has its published demo password)."""
    import os
    return "pilot" if os.environ.get("SHIFTSENSE_ENV", "demo").strip().lower() == "pilot" else "demo"


# ------------------------------------------------------------------ contract-specific working-time rules
CONTRACT_MAX_KEYS = ("max_weekly_hours", "max_shifts_per_week", "max_nights_per_week", "max_ot_days_per_horizon",
                     "max_daily_hours", "max_extension_hours")


def agent_rules(policy: dict, contract: str | None = None) -> dict:
    """The working-time rules for one agent: the company-wide floor (policy['rules']) tightened by the agent's
    contract (policy['contracts'][name]). A contract can only be stricter than the floor, never looser."""
    base = {k: (int(v) if isinstance(v, float) and v.is_integer() else v) for k, v in policy["rules"].items()}
    base.setdefault("night_allowed", True)
    base.setdefault("allowed_shifts", None)
    c = (policy.get("contracts") or {}).get(contract or "") if contract else None
    if not c:
        for k, v in (policy.get("rule_exceptions") or {}).items():
            if k in base:
                base[k] = v
        return base
    out = dict(base)
    for k in CONTRACT_MAX_KEYS:
        if c.get(k) is not None:
            v = min(base[k], c[k])
            out[k] = int(v) if isinstance(v, float) and v.is_integer() else v
    if c.get("min_rest_hours") is not None:
        out["min_rest_hours"] = max(base["min_rest_hours"], c["min_rest_hours"])
    if c.get("contracted_shifts_per_week") is not None:
        out["contracted_shifts_per_week"] = min(int(c["contracted_shifts_per_week"]), out["max_shifts_per_week"])
    if c.get("night_allowed") is not None:
        out["night_allowed"] = bool(c["night_allowed"])
    if c.get("allowed_shifts"):
        out["allowed_shifts"] = list(c["allowed_shifts"])
    for k, v in (policy.get("rule_exceptions") or {}).items():     # approved temporary exception wins while active
        if k in out:
            out[k] = v
    return out


def contract_of(agents, agent_id: str) -> str | None:
    if "contract" not in agents.columns:
        return None
    hit = agents.loc[agents.agent_id == agent_id, "contract"]
    return None if hit.empty or not isinstance(hit.iloc[0], str) else hit.iloc[0]


def shift_allowed(policy: dict, rules: dict, night_ok: bool, shift: str) -> bool:
    if is_night(policy, shift) and not (night_ok and rules.get("night_allowed", True)):
        return False
    allowed = rules.get("allowed_shifts")
    return not allowed or shift in allowed


def agent_pay(policy: dict, contract: str | None = None) -> tuple[float, float]:
    """(hourly rate, overtime multiplier) for one person: their contract's rates where set, else the defaults."""
    c = (policy.get("contracts") or {}).get(contract or "") if contract else None
    base = policy["costs"]
    rate = float((c or {}).get("hourly_rate") or base["hourly_rate"])
    mult = float((c or {}).get("ot_multiplier") or base["ot_multiplier"])
    return rate, mult


def pay_table(policy: dict, agents) -> dict:
    """agent_id -> (rate, multiplier)."""
    has = "contract" in agents.columns
    return {a: agent_pay(policy, c if has and isinstance(c, str) else None)
            for a, c in zip(agents.agent_id, agents["contract"] if has else [None] * len(agents))}
