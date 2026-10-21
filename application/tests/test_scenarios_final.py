"""Acceptance scenarios A-E: each persona completes its job and then finds the outcome in its own history."""
import pytest

from shiftsense import auth, comms, planning, records, services, staffing, timeline
from shiftsense import workflow as wf

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "sc.db"))
    monkeypatch.delenv("SHIFTSENSE_EMAIL", raising=False)
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def U(name):
    return auth.authenticate(name, PW[name])


def test_A_agent_next_shift_checkin_and_personal_history(w):
    d = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A01") & (w.roster.day == x)).any())
    out = services.send_attendance_reminder(w, U("tl1"), "A01", d)
    w.now_t = out["opens_t"] - 1
    with pytest.raises(ValueError, match="not open yet"):                    # clear reason when not eligible
        services.check_in_self(w, U("aarav"), out["id"])
    w.now_t = out["opens_t"]
    services.check_in_self(w, U("aarav"), out["id"])
    w.now_t += 2
    assert records.day_record(w, "A01", d)["code"] == "worked"
    hist = services.history(w, U("aarav"))
    assert any(e["Event"] == "Check-in" and "A01" in e["Record"] for e in hist)
    assert any(e["Type"] == "Attendance" and "checked in" in e["Outcome"] for e in hist)


def test_B_team_lead_reminder_decision_and_team_history(w):
    rd = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A01") & (w.roster.day == x)).any()
              and wf.leave_check(w, "A01", x)["status"] != "Green")
    r = services.submit_leave(w, U("aarav"), rd)
    services.send_attendance_reminder(w, U("tl1"), "A06", next(x for x in range(w.first_day, 14) if (
        (w.roster.agent_id == "A06") & (w.roster.day == x)).any()))
    services.decide_request(w, U("tl1"), r["id"], False, "peak day")
    hist = services.history(w, U("tl1"))
    assert any(e["Record"] == r["id"] and e["Event"] == "Reject request" and e["Reason"] == "peak day" for e in hist)
    assert any(e["Type"] == "Attendance" and "A06" in e["Agents"] for e in hist)


def test_C_ops_decision_outcome_and_residual_in_history(w):
    t = dict(zip(w.data["agents"].agent_id, w.data["agents"].team))
    a = next(x for x in w.roster[w.roster.day == w.first_day].agent_id if t[x] == "TL-1" and x not in ("A01", "A06"))
    inc = services.report_absence(w, U("tl1"), [a], w.first_day)
    alts = inc.rec["alternatives"]                                             # recovery options visible to Ops
    assert inc.rec["eval"] is not None and isinstance(alts, dict)
    services.decide_incident(w, U("tl1"), inc.id, "Escalate", "no one free")
    services.ops_decide(w, U("ops"), inc.id, "Accept the risk for this window", "low volume")
    hist = services.history(w, U("ops"))
    ev = [e for e in hist if e["Record"] == inc.id and e["Event"].startswith("Ops:")]
    assert ev and ev[0]["After"] == "closed"
    assert inc.residual_ph > 0
    assert any(e["Record"] == inc.id and "still uncovered (risk carried)" in e["Event"] for e in hist)


def test_D_analyst_whatif_validate_publish_and_version_history(w):
    fp = wf.roster_fp(w.roster)
    res = planning.run_scenario(w, planning.Scenario(name="re", headcount_change=-2), time_limit=20)
    services.record_whatif(w, U("analyst"), "re", "analysis")
    assert wf.roster_fp(w.roster) == fp
    p = services.draft_plan(w, U("analyst"), res["roster"], "re", "what-if")
    services.validate_plan(w, U("analyst"), p["id"])
    services.publish_plan(w, U("analyst"), p["id"], reviewed=True)
    hist = services.history(w, U("analyst"))
    for action in ("What-if run", "Create draft", "Validate plan", "Publish plan"):
        assert any(e["Event"] == action for e in hist), action
    assert [x["status"] for x in w.plans] == ["superseded", "published"]


def test_E_admin_governance_audit_and_no_operational_power(w):
    sc = services.load_demo_story(w, U("analyst"))
    cid = services.scan(sc, U("admin"))[0]
    services.case_action(sc, U("admin"), cid, "Acknowledge", "")
    acc = __import__("shiftsense.accounts", fromlist=["x"]).get("priya")
    services.update_account(w, U("admin"), "priya", {"email": "priya.n@example.com"}, acc["version"])
    assert any(e["Type"] == "Governance" and f"Case {cid}" in e["Event"] for e in services.history(sc, U("admin")))
    assert any(e["Type"] == "Account" for e in services.history(w, U("admin")))
    inc = next(i for i in sc.incidents if i.status in wf.DECIDABLE)
    for call in (lambda: services.decide_incident(sc, U("admin"), inc.id, "Approve"),
                 lambda: services.publish_plan(w, U("admin"), "PLAN-0", reviewed=True),
                 lambda: services.correct_attendance(w, U("admin"), "A01", w.first_day, "x"),
                 lambda: services.record_whatif(w, U("admin"), "x", "y")):
        with pytest.raises(services.Denied):
            call()
    df, n = timeline.query(services.history(w, U("admin")), types=["Account"], size=50)
    assert n >= 1 and "priya.n@example.com" not in df.to_csv()               # addresses masked in history
