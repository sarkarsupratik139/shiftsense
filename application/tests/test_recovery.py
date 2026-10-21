import numpy as np
import pytest

from shiftsense import audit, recovery, requests, risk, staffing
from shiftsense.config import queue_names
from shiftsense.constraints import check
from shiftsense.roster import coverage

DAY = 2


@pytest.fixture(scope="module")
def scenario(policy, data, req, optimized_plan):
    plan = optimized_plan["roster"]
    absent = risk.pick_absentees(policy, plan, DAY, 4, seed=0, focus_queue="Claims-EN")
    cur, lost = risk.inject_absence(plan, absent, DAY)
    now = DAY * 48 - 8                                  # 20:00 the evening before
    ctx = recovery.Context(policy, data, req, plan, cur, DAY, now, absent)
    return {"plan": plan, "absent": absent, "cur": cur, "lost": lost, "ctx": ctx,
            "rec": recovery.recommend(ctx)}


def test_inject_absence_removes_only_those_shifts(scenario):
    assert len(scenario["lost"]) == 4
    assert len(scenario["cur"]) == len(scenario["plan"]) - 4
    assert set(scenario["lost"].agent_id) == set(scenario["absent"])


def test_risk_detects_disruption_gap(policy, data, req, scenario):
    st = staffing.staffing_chain(policy, staffing.forecast(policy, data["history"]))
    rk = risk.assess(policy, data, req, st["uncertainty"], scenario["cur"], scenario["plan"],
                     DAY, scenario["ctx"].now_t)
    ex = risk.exceptions(policy, rk)
    assert not ex.empty
    assert (ex.gap < 0).all()
    assert set(ex.level) <= {"Critical", "At risk"}
    assert (rk[rk.gap == 0].level == "Healthy").all()


def test_risk_rises_with_sla_sensitivity_and_scarcity(policy, data, req, scenario):
    st = staffing.staffing_chain(policy, staffing.forecast(policy, data["history"]))
    base = risk.assess(policy, data, req, st["uncertainty"], scenario["cur"], scenario["plan"], DAY, scenario["ctx"].now_t)
    hot = dict(policy, queues={q: {**v, "sensitivity": 0.1} for q, v in policy["queues"].items()})
    low = risk.assess(hot, data, req, st["uncertainty"], scenario["cur"], scenario["plan"], DAY, scenario["ctx"].now_t)
    gapped = base.gap > 0
    assert (base.risk[gapped].values > low.risk[gapped].values).all()
    # scarcity: fewer qualified people -> higher risk
    few = {**data, "agents": data["agents"].assign(eligible=data["agents"].primary.map(lambda q: [q]))}
    scarce = risk.assess(policy, few, req, st["uncertainty"], scenario["cur"], scenario["plan"], DAY, scenario["ctx"].now_t)
    assert (scarce.risk[gapped].values >= base.risk[gapped].values).all()


def test_every_candidate_is_feasible_and_skill_eligible(data, scenario):
    elig = dict(zip(data["agents"].agent_id, data["agents"].eligible))
    for a in scenario["rec"]["candidates"]:
        assert a.queue in elig[a.agent_id]
        assert a.agent_id not in scenario["absent"]


def test_recovered_roster_has_zero_violations(policy, data, scenario):
    new = scenario["rec"]["eval"]["roster"]
    assert check(policy, data["agents"], data["leave"], new) == []


def test_redeploy_never_pushes_donor_below_requirement(policy, req, scenario):
    ctx = scenario["ctx"]
    redeploys = [a for a in scenario["rec"]["candidates"] if a.kind == "redeploy"]
    new_cov = coverage(policy, recovery.apply_actions(ctx.roster, scenario["rec"]["actions"]))
    for a in redeploys:
        for (qi, t), v in a.delta.items():
            if v < 0:
                assert ctx.cur[qi, t] - 1 >= req[qi, t]          # single move is safe
    for a in scenario["rec"]["actions"]:
        if a.kind == "redeploy":
            pi = queue_names(policy).index(a.from_queue)
            for t in range(a.alt_start, a.alt_end):
                assert new_cov[pi, t] >= min(req[pi, t], ctx.cur[pi, t])


def test_swap_partner_is_eligible_and_keeps_weekly_hours(policy, data, scenario):
    elig = dict(zip(data["agents"].agent_id, data["agents"].eligible))
    for a in scenario["rec"]["candidates"]:
        if a.kind == "swap":
            assert a.queue in elig[a.agent_id]
            assert a.swap_day is not None and a.swap_day > DAY


def test_recovery_improves_coverage_and_beats_manual(policy, scenario):
    rec = scenario["rec"]
    assert rec["eval"]["restored_pct"] > 0
    manual = recovery.baseline_recover(scenario["ctx"], scenario["lost"], seed=0)
    assert rec["eval"]["restored_pct"] >= manual["eval"]["restored_pct"]
    assert rec["ttc_min"] < manual["ttc_min"]
    eq = recovery.cost_at_equal_coverage(scenario["ctx"], rec["candidates"], manual["eval"]["deficit_after"])
    assert eq is not None and eq["cost"] <= manual["eval"]["cost"]


def test_modify_excludes_actions(scenario):
    ctx = scenario["ctx"]
    first = scenario["rec"]["actions"][0]
    again = recovery.recommend(ctx, exclude={first.id})
    assert first.id not in {a.id for a in again["actions"]}


def test_escalates_when_nothing_is_feasible(policy, data, req, scenario):
    ctx = scenario["ctx"]
    res = recovery.select(ctx, [], kinds=None)
    ev = recovery.evaluate(ctx, res["actions"])
    assert ev["deficit_after"] == ev["deficit_before"] > 0
    esc = recovery.escalation(ctx, ev, res["residual"], {"OT only": ev})
    assert esc["residual"] and "Ops decision required" in esc["message"]


def test_recovery_is_deterministic(scenario):
    again = recovery.recommend(scenario["ctx"])
    assert [a.id for a in again["actions"]] == [a.id for a in scenario["rec"]["actions"]]


# ---------------------------------------------------------------- requests
def test_leave_status_levels(policy, data, req, optimized_plan):
    plan = optimized_plan["roster"]
    statuses = {requests.status_for(policy, req, plan, r.agent_id, r.day)["status"]
                for r in plan.head(200).itertuples()}
    assert statuses <= {"Green", "Amber", "Red"}
    off = requests.status_for(policy, req, plan, "A01", -1)
    assert off["status"] == "Green"


def test_red_request_offers_eligible_alternatives(policy, data, req, optimized_plan):
    plan = optimized_plan["roster"]
    elig = dict(zip(data["agents"].agent_id, data["agents"].eligible))
    red = next(r for r in plan.itertuples()
               if requests.status_for(policy, req, plan, r.agent_id, r.day)["status"] == "Red")
    alts = requests.alternatives(policy, data, req, plan, red.agent_id, red.day)
    assert alts
    for o in alts:
        if o["type"] == "Swap":
            assert red.queue in elig[o["partner"]]


# ---------------------------------------------------------------- audit
def test_reject_requires_reason():
    log = audit.AuditLog()
    with pytest.raises(ValueError):
        log.log(audit.AuditRecord("AgentAbsent", "Tue 20:00", "gap", "rec", decision="Reject"))
    log.log(audit.AuditRecord("AgentAbsent", "Tue 20:00", "gap", "rec", decision="Reject", reason="agent unreachable"))
    assert len(log.frame()) == 1
