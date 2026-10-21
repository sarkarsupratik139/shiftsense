"""The independent checker must catch each rule, and both planners must pass it."""
import pandas as pd

from shiftsense import constraints, kpis, optimizer
from shiftsense.roster import make_roster


def rules_hit(policy, data, rows):
    v = constraints.check(policy, data["agents"], data["leave"], make_roster(rows))
    return {x["rule"] for x in v}


def agent(data, **cond):
    df = data["agents"]
    for k, val in cond.items():
        df = df[df[k] == val]
    return df.iloc[0]


def test_eligibility_violation_detected(policy, data):
    a = agent(data)
    bad_q = next(q for q in policy["queues"] if q not in a.eligible)
    assert "skill_eligibility" in rules_hit(policy, data, [
        {"agent_id": a.agent_id, "day": 3, "shift": "Day", "queue": bad_q}])


def test_rest_violation_detected(policy, data):
    a = agent(data, night_ok=True)
    rows = [{"agent_id": a.agent_id, "day": 3, "shift": "Evening", "queue": a.primary},
            {"agent_id": a.agent_id, "day": 4, "shift": "Morning", "queue": a.primary}]
    assert "min_rest" in rules_hit(policy, data, rows)


def test_night_permission_detected(policy, data):
    a = agent(data, night_ok=False)
    assert "night_permission" in rules_hit(policy, data, [
        {"agent_id": a.agent_id, "day": 3, "shift": "Night", "queue": a.primary}])


def test_leave_detected(policy, data):
    lv = data["leave"].iloc[0]
    a = data["agents"].set_index("agent_id").loc[lv.agent_id]
    assert "approved_leave" in rules_hit(policy, data, [
        {"agent_id": lv.agent_id, "day": int(lv.day), "shift": "Day", "queue": a.primary}])


def test_weekly_off_and_hours_detected(policy, data):
    a = agent(data)
    rows = [{"agent_id": a.agent_id, "day": d, "shift": "Day", "queue": a.primary} for d in range(7)]
    hit = rules_hit(policy, data, rows)
    assert {"weekly_off", "max_weekly_hours", "ot_limit"} <= hit


def test_double_shift_and_extension_detected(policy, data):
    a = agent(data)
    rows = [{"agent_id": a.agent_id, "day": 2, "shift": "Morning", "queue": a.primary, "ext_after": 3},
            {"agent_id": a.agent_id, "day": 2, "shift": "Evening", "queue": a.primary}]
    hit = rules_hit(policy, data, rows)
    assert {"one_shift_per_day", "extension_limit", "max_daily_hours"} <= hit


def test_clean_roster_passes(policy, data):
    a = agent(data)
    rows = [{"agent_id": a.agent_id, "day": d, "shift": "Day", "queue": a.primary} for d in (8, 9, 10)]
    assert rules_hit(policy, data, rows) == set()


def test_heuristic_plan_has_zero_violations(policy, data, heuristic_plan):
    assert constraints.check(policy, data["agents"], data["leave"], heuristic_plan["roster"]) == []


def test_optimizer_plan_has_zero_violations(policy, data, optimized_plan):
    assert optimized_plan["status"] in ("OPTIMAL", "FEASIBLE")
    assert constraints.check(policy, data["agents"], data["leave"], optimized_plan["roster"]) == []


def test_every_assignment_is_skill_eligible(data, heuristic_plan, optimized_plan):
    elig = dict(zip(data["agents"].agent_id, data["agents"].eligible))
    for plan in (heuristic_plan, optimized_plan):
        for r in plan["roster"].itertuples():
            assert r.queue in elig[r.agent_id]


def test_optimizer_beats_heuristic_on_coverage(policy, data, req, heuristic_plan, optimized_plan):
    h = kpis.plan_kpis(policy, data, req, heuristic_plan["roster"])
    o = kpis.plan_kpis(policy, data, req, optimized_plan["roster"])
    assert o["Under-covered workload %"] < h["Under-covered workload %"]
    assert o["Paid hours"] <= h["Paid hours"]


def test_optimizer_is_deterministic(policy, data, req, optimized_plan):
    again = optimizer.plan(policy, data["agents"], data["leave"], req)
    pd.testing.assert_frame_equal(again["roster"], optimized_plan["roster"])


def test_stability_keeps_published_roster(policy, data, req, optimized_plan):
    """Re-planning against its own published roster with a frozen first week changes nothing there."""
    opts = optimizer.PlanOptions(stability_ref=optimized_plan["roster"], freeze_before_day=7)
    res = optimizer.plan(policy, data["agents"], data["leave"], req, opts)
    first = lambda df: df[df.day < 7].reset_index(drop=True)
    pd.testing.assert_frame_equal(first(res["roster"]), first(optimized_plan["roster"]))
