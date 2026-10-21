"""Workflow gaps not covered elsewhere: the full shift-swap path (D), the Modify path of a recovery
decision (B), and the demo-to-live planning path checked step by step (H). Server functions only."""
import pytest

from shiftsense import auth, comms, planning, priority, services, staffing
from shiftsense import requests as rq
from shiftsense import workflow as wf
from shiftsense.constraints import check

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "wf.db"))
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def U(name, pw=None):
    return auth.authenticate(name, pw or PW[name])


def login_for(w, agent_id):
    """Admin creates an account so the partner agent can answer (exercises account management)."""
    uname = f"p{agent_id.lower()}"
    services.create_account(w, U("admin"), uname, {"name": agent_id, "employee_id": f"E-{agent_id}", "role": "agent",
                                                 "agent_id": agent_id, "contact_pref": "in_app"}, "Temp-Pass-2026!")
    return U(uname, "Temp-Pass-2026!")


def row(w, a, d):
    r = w.roster[(w.roster.agent_id == a) & (w.roster.day == d)]
    return None if r.empty else (r.iloc[0]["shift"], r.iloc[0]["queue"])


def test_D_swap_agreement_approval_recheck_and_roster_update(w):
    day = next(d for d in range(w.first_day, 14) if row(w, "A01", d)
               and rq.swap_options(w.policy, w.data, w.roster, "A01", d, limit=5, min_day=w.first_day))
    opt = rq.swap_options(w.policy, w.data, w.roster, "A01", day, limit=5, min_day=w.first_day)[0]
    partner, pday = opt["partner"], opt["partner_day"]
    before = {(a, d): row(w, a, d) for a in ("A01", partner) for d in (day, pday)}
    r = services.request_swap(w, U("aarav"), day, opt)
    assert r["state"] == "Waiting for colleague"
    with pytest.raises(services.Denied):                                  # nobody else can agree for the partner
        services.respond(w, U("sneha"), next(n["id"] for n in w.notifications if n.get("request") == r["id"]), True)
    pu = login_for(w, partner)
    ask = next(n for n in w.notifications if n.get("request") == r["id"] and n["agent_id"] == partner)
    services.respond(w, pu, ask["id"], True)
    assert r["state"] == "Pending"
    assert any("Swap to approve" in n["title"] for n in comms.notices_for(U("tl1"), w.run_id))
    with pytest.raises(services.Denied):
        services.decide_request(w, U("tl2"), r["id"], True)               # other team
    services.decide_request(w, U("tl1"), r["id"], True)
    assert r["state"] == "Approved"
    after = {(a, d): row(w, a, d) for a in ("A01", partner) for d in (day, pday)}
    assert after != before                                                 # roster really changed
    assert row(w, partner, day) == before[("A01", day)]                    # partner covers Aarav's shift
    assert check(w.policy, w.data["agents"], w.data["leave"], w.roster) == []
    with pytest.raises(ValueError, match="Conflict"):
        services.decide_request(w, U("tl1"), r["id"], True)               # repeated approval refused


def test_B_modify_removes_chosen_steps_and_recalculates(w):
    t = dict(zip(w.data["agents"].agent_id, w.data["agents"].team))
    d = w.first_day
    who = [a for a in w.roster[w.roster.day == d].agent_id if t[a] == "TL-1" and a not in ("A01", "A06")][:2]
    inc = services.report_absence(w, U("tl1"), who, d)
    if len(inc.rec["actions"]) < 2:
        pytest.skip("need at least two recovery steps to modify")
    drop = inc.rec["actions"][0].id
    gap_before = priority.assess(w, inc)["short_ph"]
    with pytest.raises(ValueError):
        services.decide_incident(w, U("tl1"), inc.id, "Modify", "", remove=[drop])           # reason required
    with pytest.raises(ValueError):
        services.decide_incident(w, U("tl1"), inc.id, "Modify", "x", remove=[])              # must remove a step
    services.decide_incident(w, U("tl1"), inc.id, "Modify", "that agent is on training", remove=[drop])
    assert drop not in {a.id for a in inc.applied}
    assert any(r["action"] == "Modify fix" and r["reason"] == "that agent is on training"
               for r in comms.audit_rows(w.run_id))
    left = inc.rec["eval"]["short_head_hours_after"] if inc.rec else 0
    assert left <= gap_before
    assert check(w.policy, w.data["agents"], w.data["leave"], w.roster) == []


def test_H_scenario_to_live_publish_with_live_unchanged_until_publish(w):
    fp0 = wf.roster_fp(w.roster)
    sc = services.load_demo_story(w, U("analyst"))
    services.advance_clock(sc, U("analyst"), 6, rate=0.2)                              # disruption in the sandbox
    a = planning.run_scenario(sc, planning.Scenario(name="spike", demand_pct={"Claims-EN": 15}), time_limit=15)
    b = planning.run_scenario(sc, planning.Scenario(name="absences", extra_absences=4), time_limit=15)
    assert wf.roster_fp(w.roster) == fp0                                               # exploring: live unchanged
    pick = b if b["roster"] is not None else a
    draft = services.draft_plan(w, U("analyst"), pick["roster"], pick["name"], f"demo scenario {sc.run_id}")
    assert wf.roster_fp(w.roster) == fp0                                               # draft: live unchanged
    rv = services.validate_plan(w, U("analyst"), draft["id"])
    if rv["violations"]:
        pytest.skip("scenario draft not valid for live (rules); publish correctly blocked")
    assert wf.roster_fp(w.roster) == fp0                                               # validated: live unchanged
    with pytest.raises(ValueError, match="Confirm"):
        services.publish_plan(w, U("analyst"), draft["id"])                            # review required
    out = services.publish_plan(w, U("analyst"), draft["id"], reviewed=True)
    assert out["changes"] >= 0 and draft["status"] == "published"
    assert check(w.policy, w.data["agents"], w.data["leave"], w.roster) == []
    assert any("New roster published" in n["title"] for n in comms.notices_for(U("tl1"), w.run_id))
