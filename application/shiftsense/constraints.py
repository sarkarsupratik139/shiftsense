"""Independent hard-constraint checker (H5).

Deliberately does not reuse any optimizer or heuristic code: it re-derives every rule
from the policy pack and the roster rows, so a bug in a planner cannot hide itself.
"""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from .config import agent_rules, is_night, shift_window, week_of

RULES = [
    "skill_eligibility", "one_shift_per_day", "approved_leave", "night_permission",
    "min_rest", "max_daily_hours", "max_weekly_hours", "weekly_off", "max_nights_per_week",
    "ot_limit", "extension_limit", "contract_shift_type",
]


def check(policy: dict, agents: pd.DataFrame, leave: pd.DataFrame, roster: pd.DataFrame) -> list[dict]:
    """Return a list of violations; empty list means the roster is compliant."""
    floor = policy["rules"]
    contracts = dict(zip(agents.agent_id, agents["contract"])) if "contract" in agents.columns else {}
    rules_of = {}

    def R(agent):
        if agent not in rules_of:
            c = contracts.get(agent)
            rules_of[agent] = agent_rules(policy, c if isinstance(c, str) else None)
        return rules_of[agent]
    elig = dict(zip(agents.agent_id, agents.eligible))
    night_ok = dict(zip(agents.agent_id, agents.night_ok))
    on_leave = set(zip(leave.agent_id, leave.day))
    out: list[dict] = []

    def v(rule, agent, day, detail):
        out.append({"rule": rule, "agent_id": agent, "day": day, "detail": detail})

    by_agent = defaultdict(list)
    for r in roster.itertuples(index=False):
        by_agent[r.agent_id].append(r)
        if r.queue not in elig.get(r.agent_id, []):
            v("skill_eligibility", r.agent_id, r.day, f"not eligible for {r.queue}")
        if r.alt_queue and r.alt_queue not in elig.get(r.agent_id, []):
            v("skill_eligibility", r.agent_id, r.day, f"redeployed to {r.alt_queue} without eligibility")
        if (r.agent_id, r.day) in on_leave:
            v("approved_leave", r.agent_id, r.day, "scheduled on approved leave")
        rules = R(r.agent_id)
        if is_night(policy, r.shift) and not (night_ok.get(r.agent_id, False) and rules["night_allowed"]):
            v("night_permission", r.agent_id, r.day, "night shift without night permission")
        if rules.get("allowed_shifts") and r.shift not in rules["allowed_shifts"]:
            v("contract_shift_type", r.agent_id, r.day, f"{r.shift} shift not allowed by the contract")
        if r.ext_before + r.ext_after > rules["max_extension_hours"] + 1e-9:
            v("extension_limit", r.agent_id, r.day, f"extension {r.ext_before + r.ext_after:g} h")
        span = policy["shifts"][r.shift]["span"] + r.ext_before + r.ext_after
        if span > rules["max_daily_hours"] + 1e-9:
            v("max_daily_hours", r.agent_id, r.day, f"{span:g} h on duty")

    ot_days_total = defaultdict(int)
    for agent, rows in by_agent.items():
        rules = R(agent)
        days = [r.day for r in rows]
        for d in {d for d in days if days.count(d) > 1}:
            v("one_shift_per_day", agent, d, "more than one shift on the day")
        wins = sorted((shift_window(policy, r.day, r.shift, r.ext_before, r.ext_after), r.day) for r in rows)
        for ((s1, e1), d1), ((s2, _), d2) in zip(wins, wins[1:]):
            if s2 - e1 < rules["min_rest_hours"] - 1e-9:
                v("min_rest", agent, d2, f"rest {s2 - e1:g} h after day {d1}")
        weeks = defaultdict(list)
        for r in rows:
            weeks[week_of(r.day)].append(r)
        for w, wr in weeks.items():
            hours = sum(policy["shifts"][r.shift]["paid"] + r.ext_before + r.ext_after for r in wr)
            if hours > rules["max_weekly_hours"] + 1e-9:
                v("max_weekly_hours", agent, w * 7, f"{hours:g} h in week {w + 1}")
            if len({r.day for r in wr}) > rules["max_shifts_per_week"]:
                v("weekly_off", agent, w * 7, f"no weekly off in week {w + 1}")
            nights = sum(is_night(policy, r.shift) for r in wr)
            if nights > rules["max_nights_per_week"]:
                v("max_nights_per_week", agent, w * 7, f"{nights} nights in week {w + 1}")
            ot_days_total[agent] += max(0, len({r.day for r in wr}) - rules["contracted_shifts_per_week"])
        if ot_days_total[agent] > rules["max_ot_days_per_horizon"]:
            v("ot_limit", agent, None, f"{ot_days_total[agent]} overtime days")
    return out


def summary(violations: list[dict]) -> dict[str, int]:
    counts = {r: 0 for r in RULES}
    for x in violations:
        counts[x["rule"]] += 1
    return counts
