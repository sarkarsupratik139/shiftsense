import pytest

from shiftsense import finance, planning, priority, staffing
from shiftsense import workflow as wf
from shiftsense.constraints import check


@pytest.fixture
def world(policy, data, req, optimized_plan):
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def test_world_starts_with_one_published_plan(world):
    assert [p["status"] for p in world.plans] == ["published"]
    assert world.req0 is not None


def test_metrics_basic(world):
    m = planning.metrics(world.policy, world.data["agents"], world.data["leave"], world.req, world.roster,
                         planning.open_days(world))
    assert 0 < m["Coverage %"] <= 100 and m["Violations"] == 0 and m["Labour cost ₹"] > 0


def test_scenario_is_isolated_and_compliant(world):
    before = world.roster.copy()
    res = planning.run_scenario(world, planning.Scenario(name="Busy", demand_pct={"Claims-EN": 20},
                                                         extra_absences=4), time_limit=20)
    assert world.roster.equals(before)                      # live roster untouched
    assert res["status"] in ("OPTIMAL", "FEASIBLE")
    assert res["after"]["Violations"] == 0
    assert res["after"]["Coverage %"] >= res["as_is"]["Coverage %"] - 0.5
    frozen = res["roster"][res["roster"].day <= world.first_day]
    live_frozen = before[before.day <= world.first_day]
    assert len(frozen) <= len(live_frozen)


def test_headcount_scenario_removes_agents(world):
    res = planning.run_scenario(world, planning.Scenario(headcount_change=-3), time_limit=20)
    assert len(res["removed"]) == 3
    assert not res["roster"][res["roster"].day > world.first_day].agent_id.isin(res["removed"]).any()


def test_draft_validate_publish_cycle(world):
    res = planning.run_scenario(world, planning.Scenario(name="Busy", demand_pct={"Claims-EN": 15}), time_limit=20)
    plan = planning.create_draft(world, res["roster"], "Busy week", "scenario", "analyst")
    assert plan["status"] == "draft"
    with pytest.raises(ValueError):
        planning.publish(world, plan["id"], "analyst")     # must validate first
    rv = planning.validate(world, plan["id"])
    assert not rv["violations"], rv["violations"][:3]
    assert plan["status"] == "validated"
    out = planning.publish(world, plan["id"], "analyst")
    assert plan["status"] == "published"
    assert [p["status"] for p in world.plans].count("published") == 1
    assert world.plans[0]["status"] == "superseded"
    assert check(world.policy, world.data["agents"], world.data["leave"], world.roster) == []
    if out["changes"]:
        assert any(n["text"].startswith("Schedule update") for n in world.notifications)


def test_diagnose_flags_proven_conflicts(world, policy):
    p2 = {**policy, "rules": {**policy["rules"], "max_nights_per_week": 0}}
    out = planning.diagnose(p2, world.data["agents"], world.data["leave"], world.roster, world.first_day + 1)
    assert any(d["type"] == "proven" and "nights" in d["text"] for d in out)


def test_planned_vs_actual(world):
    a = world.roster[world.roster.day == world.today].agent_id.iloc[0]
    df = planning.planned_vs_actual(world)
    assert len(df) == world.today + 1 and {"Planned shifts", "Live-roster coverage %"} <= set(df.columns)


def test_priority_ranks_and_finance(world):
    day = world.first_day
    a = wf.report_absence(world, world.roster[(world.roster.day == day) & (world.roster.queue == "Claims-EN")]
                          .agent_id.tolist()[:3], day, "tl")
    b = wf.report_absence(world, world.roster[world.roster.day == day + 3].agent_id.tolist()[:1], day + 3, "tl")
    ranked = priority.rank(world, [b, a])
    assert ranked[0][0] is a                                # bigger and sooner comes first
    s = ranked[0][1]
    assert s["severity"] in ("Critical", "Warning") and s["short_ph"] > 0 and s["hours_left"] is not None
    if a.rec["actions"]:
        wf.approve_fix(world, a, "tl")
        with pytest.raises(ValueError):
            wf.approve_fix(world, a, "tl")                  # no double decisions
        rc = finance.recovery_costs(world)
        assert rc["Cost ₹"].sum() == round(sum(x.cost for x in a.applied))
    dc = finance.day_costs(world, [day])
    assert dc["Labour ₹"].iloc[0] > 0
    assert finance.budget(world) is None                    # not configured -> not invented
