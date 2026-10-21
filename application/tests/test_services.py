"""Server-side permissions, notifications, governance cases and the tamper-evident audit."""
import sqlite3

import pytest

from shiftsense import auth, comms, governance, services, staffing, store
from shiftsense import workflow as wf


@pytest.fixture
def world(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "s.db"))
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def U(name):
    pw = auth.config()["users"][name]["demo_password"]
    return auth.authenticate(name, pw)


def team_agent(w, team, day, exclude=("A01", "A02", "A06")):
    t = dict(zip(w.data["agents"].agent_id, w.data["agents"].team))
    return next(a for a in w.roster[w.roster.day == day].agent_id if t[a] == team and a not in exclude)


# ---------------------------------------------------------------- permissions
def test_admin_cannot_change_operations(world):
    a = team_agent(world, "TL-1", world.first_day)
    inc = services.report_absence(world, U("tl1"), [a], world.first_day)
    for call in (lambda: services.decide_incident(world, U("admin"), inc.id, "Approve"),
                 lambda: services.report_absence(world, U("admin"), [a], world.first_day + 1),
                 lambda: services.publish_plan(world, U("admin"), "PLAN-0"),
                 lambda: services.ops_decide(world, U("admin"), inc.id, "Accept the risk for this window", "x"),
                 lambda: services.require_demo(U("admin"))):
        with pytest.raises(services.Denied):
            call()


def test_team_scope_and_no_self_approval(world):
    a = team_agent(world, "TL-1", world.first_day)
    inc = services.report_absence(world, U("tl1"), [a], world.first_day)
    with pytest.raises(services.Denied):
        services.decide_incident(world, U("tl2"), inc.id, "Approve")
    with pytest.raises(services.Denied):                       # TL cannot report another team's agent
        services.report_absence(world, U("tl2"), [team_agent(world, "TL-1", world.first_day + 1)], world.first_day + 1)
    with pytest.raises(services.Denied):                       # agent reports only self
        services.report_absence(world, U("aarav"), [a], world.first_day)
    day = next(d for d in range(world.first_day, 14) if ((world.roster.agent_id == "A01") & (world.roster.day == d)).any())
    r = services.submit_leave(world, U("aarav"), day)
    if r["state"] == "Pending":
        with pytest.raises(services.Denied):
            services.decide_request(world, U("aarav"), r["id"], True)
        with pytest.raises(services.Denied):
            services.decide_request(world, U("tl2"), r["id"], True)
        services.decide_request(world, U("tl1"), r["id"], False, "peak")
        assert r["decided_by"].startswith("Rohit")


def test_notifications_are_addressed_not_broadcast(world):
    a = team_agent(world, "TL-1", world.first_day)
    inc = services.report_absence(world, U("tl1"), [a], world.first_day)
    tl1 = comms.notices_for(U("tl1"), world.run_id)
    assert any(n["related_ref"] == inc.id and n["action_required"] for n in tl1)
    assert not comms.notices_for(U("tl2"), world.run_id)
    assert not comms.notices_for(U("aarav"), world.run_id)
    services.decide_incident(world, U("tl1"), inc.id, "Escalate", "no cover possible")
    assert all(n["status"] != "action" for n in comms.notices_for(U("tl1"), world.run_id) if n["related_ref"] == inc.id)
    ops = comms.notices_for(U("ops"), world.run_id)
    assert any(n["related_ref"] == inc.id and n["priority"] == "Critical" for n in ops)
    services.ops_decide(world, U("ops"), inc.id, "Return to team lead", "try a swap first")
    assert inc.status in ("open", "no_gap")
    assert any("returned" in n["title"] for n in comms.notices_for(U("tl1"), world.run_id))


def test_duplicate_notifications_are_suppressed(world):
    assert comms.notify(world.run_id, {"role": "ops_head"}, "x", dedupe="k1")
    assert comms.notify(world.run_id, {"role": "ops_head"}, "x", dedupe="k1") is None


def test_reject_proposes_new_fix_escalate_goes_to_ops(world):
    day = world.first_day
    t = dict(zip(world.data["agents"].agent_id, world.data["agents"].team))
    who = [a for a in world.roster[(world.roster.day == day) & (world.roster.queue == "Claims-EN")].agent_id
           if t[a] == "TL-1"][:2] or [team_agent(world, "TL-1", day)]
    inc = services.report_absence(world, U("tl1"), who, day)
    if not inc.rec["actions"]:
        pytest.skip("no fix")
    first = {a.id for a in inc.rec["actions"]}
    services.decide_incident(world, U("tl1"), inc.id, "Reject", "people on training")
    assert inc.status in ("open", "no_gap") and not first & {a.id for a in inc.rec["actions"]}
    with pytest.raises(ValueError):
        services.decide_incident(world, U("tl1"), inc.id, "Escalate", "")
    services.decide_incident(world, U("tl1"), inc.id, "Escalate", "nothing feasible")
    assert inc.status == "escalated"


def test_agent_messages_team_lead_only(world):
    services.post_message(world, U("aarav"), "agent:A01", "Can I start an hour later on Friday?")
    assert comms.thread(world.run_id, "agent:A01")[0]["body"].startswith("Can I")
    assert any(n["related_ref"] == "agent:A01" for n in comms.notices_for(U("tl1"), world.run_id))
    with pytest.raises(services.Denied):
        services.post_message(world, U("tl2"), "agent:A01", "hello")
    with pytest.raises(services.Denied):
        services.post_message(world, U("sneha"), "agent:A01", "hello")


def test_withdraw_and_revoke_keep_history(world):
    day = next(d for d in range(world.first_day + 1, 14) if ((world.roster.agent_id == "A01") & (world.roster.day == d)).any())
    r = services.submit_leave(world, U("aarav"), day)
    if r["state"] == "Pending":
        services.withdraw(world, U("aarav"), r["id"])
        assert r["state"] == "Withdrawn"
        r = services.submit_leave(world, U("aarav"), day, leave_type="unpaid")
        services.decide_request(world, U("tl1"), r["id"], True)
    assert r["state"] == "Approved"
    assert not ((world.roster.agent_id == "A01") & (world.roster.day == day)).any()
    services.revoke(world, U("tl1"), r["id"], "client launch moved")
    assert r["state"] == "Revoked"
    assert ((world.roster.agent_id == "A01") & (world.roster.day == day)).any()
    actions = [x["action"] for x in comms.audit_rows(world.run_id)]
    assert "Revoke approval" in actions and ("Approve request" in actions or "Request time off" in actions)


# ---------------------------------------------------------------- governance
def test_governance_flags_show_evidence_and_threshold(world):
    wf.load_story(world)
    flags = governance.detect(world)
    gap = next(f for f in flags if f["type"] == "Approval-rate difference")
    assert gap["agent"] == "A02" and gap["subject_user"] == "tl2"
    assert {"Requests", "Approved", "Rejected", "Approval rate", "Peer approval rate"} <= set(gap["metrics"])
    assert gap["threshold"] and gap["comparison"] and "not a finding" in gap["note"]


def test_case_lifecycle_with_explanation_and_ops_authorisation(world):
    wf.load_story(world)
    ids = services.scan(world, U("admin"))
    assert ids and not services.scan(world, U("admin"))       # deduplicated
    cid = next(c["id"] for c in comms.cases(world.run_id) if c["subject_user"] == "tl2")
    with pytest.raises(ValueError):                             # must acknowledge first
        services.case_action(world, U("admin"), cid, "Request explanation", "please explain")
    services.case_action(world, U("admin"), cid, "Acknowledge", "")
    services.case_action(world, U("admin"), cid, "Request explanation", "Please explain approvals for A02")
    assert any(n["related_ref"] == str(cid) and n["action_required"] for n in comms.notices_for(U("tl2"), world.run_id))
    with pytest.raises(services.Denied):
        services.case_action(world, U("tl1"), cid, "Explain", "not me")
    services.case_action(world, U("tl2"), cid, "Explain", "A02 had approved medical leave documents.")
    services.case_action(world, U("admin"), cid, "Request corrective action", "Pattern persists after explanation")
    with pytest.raises(services.Denied):
        services.case_action(world, U("admin"), cid, "Authorise corrective action", "x")
    services.case_action(world, U("ops"), cid, "Authorise corrective action", "Coaching plan agreed; review in 30 days")
    c = next(x for x in comms.cases(world.run_id) if x["id"] == cid)
    assert c["status"] == "Resolved"
    assert any("Formal corrective action" in n["title"] for n in comms.notices_for(U("tl2"), world.run_id))
    services.case_action(world, U("admin"), cid, "Close", "Coaching plan in place")
    assert [e["to_status"] for e in comms.case_events(cid)][-1] == "Closed"


def test_audit_is_tamper_evident_and_survives_reset(world, tmp_path):
    a = team_agent(world, "TL-1", world.first_day)
    services.report_absence(world, U("tl1"), [a], world.first_day)
    services.post_message(world, U("aarav"), "agent:A01", "hi")
    assert comms.verify_audit() == (True, None)
    store.clear()                                               # workspace reset
    assert comms.audit_rows(world.run_id)
    from shiftsense import db
    with pytest.raises(db.DatabaseError, match="append-only"):          # the database itself refuses edits
        with db.connect() as con:
            con.execute("UPDATE audit_log SET reason = 'edited' WHERE id = 1")
    with db.connect() as con:                                            # someone with admin rights drops the guard…
        con.execute("DROP TRIGGER audit_no_change ON audit_log" if db.dialect() == "postgres"
                    else "DROP TRIGGER audit_no_update")
        con.execute("UPDATE audit_log SET reason = 'edited' WHERE id = 1")
    ok, bad = comms.verify_audit()                                       # …and the hash chain still exposes it
    assert not ok and bad == 1
