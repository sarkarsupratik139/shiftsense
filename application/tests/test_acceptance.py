"""Persona acceptance matrix: permitted AND prohibited actions for every persona, plus cross-persona
journeys, duplicate/concurrent actions, provider failure, missing credentials and reset behaviour.
All checks run against the server-side services (the same calls the screens make)."""
import threading

import pandas as pd
import pytest

from shiftsense import (accounts, assistant, attendance, auth, comms, history, importer, performance, planning,
                        records, report, services, staffing)
from shiftsense import workflow as wf
from shiftsense.constraints import check

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}


@pytest.fixture
def world(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "acc.db"))
    monkeypatch.delenv("SHIFTSENSE_EMAIL", raising=False)
    w = wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))
    import threading as th
    w.lock = th.Lock()
    return w


def U(name, pw=None):
    return auth.authenticate(name, pw or PW[name])


def team_of(w):
    return dict(zip(w.data["agents"].agent_id, w.data["agents"].team))


def worker(w, team, day, exclude=("A01", "A02", "A06")):
    t = team_of(w)
    return next(a for a in w.roster[w.roster.day == day].agent_id if t[a] == team and a not in exclude)


def shift_day(w, agent, start=None):
    start = w.first_day if start is None else start
    return next(d for d in range(start, 14) if ((w.roster.agent_id == agent) & (w.roster.day == d)).any())


def pending_day(w, agent):
    """A working day where a time-off request would go to the team lead (not auto-approved)."""
    for d in range(w.first_day, 14):
        if ((w.roster.agent_id == agent) & (w.roster.day == d)).any() and wf.leave_check(w, agent, d)["status"] != "Green":
            return d
    return None


def tl_alert(w, team="TL-1", day=None):
    day = w.first_day if day is None else day
    inc = services.report_absence(w, U("tl1" if team == "TL-1" else "tl2"), [worker(w, team, day)], day)
    assert inc.status == "open" and inc.rec["actions"], "fixture expects a fixable gap"
    return inc


def agent_account(w, agent_id, username, email="", pref="in_app"):
    """Admin creates a login for a helper agent (exercises account management)."""
    services.create_account(w, U("admin"), username, {
        "name": f"Agent {agent_id}", "employee_id": f"E-{agent_id}", "role": "agent", "agent_id": agent_id,
        "email": email, "contact_pref": pref}, "Temp-Pass-2026!")
    return U(username, "Temp-Pass-2026!")


# ============================================================ Agent
def test_agent_submit_duplicate_and_withdraw(world):
    d = pending_day(world, "A01")
    r = services.submit_leave(world, U("aarav"), d)
    assert r["state"] == "Pending"
    with pytest.raises(ValueError, match="already have an open request"):
        services.submit_leave(world, U("aarav"), d)
    off = next(x for x in range(world.first_day, 14) if not ((world.roster.agent_id == "A01") & (world.roster.day == x)).any())
    with pytest.raises(ValueError, match="no shift"):
        services.submit_leave(world, U("aarav"), off)
    with pytest.raises(services.Denied):
        services.withdraw(world, U("sneha"), r["id"])
    services.withdraw(world, U("aarav"), r["id"])
    assert r["state"] == "Withdrawn"
    with pytest.raises(ValueError):
        services.withdraw(world, U("aarav"), r["id"])


def test_agent_reports_only_own_absence_and_early_departure(world):
    d = shift_day(world, "A01")
    inc = services.report_absence(world, U("aarav"), ["A01"], d)
    assert inc.absent == ["A01"]
    with pytest.raises(services.Denied):
        services.report_absence(world, U("aarav"), [worker(world, "TL-1", d)], d)
    on_now = next(r["agent_id"] for r in world.roster.to_dict("records")
                  if r["day"] == world.today and r["shift"] == "Evening" and team_of(world)[r["agent_id"]] == "TL-1")
    with pytest.raises(services.Denied):
        services.report_left_early(world, U("aarav"), on_now)


def test_agent_cannot_see_other_private_requests(world):
    d = pending_day(world, "A01")
    r = services.submit_leave(world, U("aarav"), d)
    assert services.get_request(world, U("aarav"), r["id"]) is r
    assert services.get_request(world, U("tl1"), r["id"]) is r
    assert services.get_request(world, U("ops"), r["id"]) is r
    for who in ("priya", "sneha", "tl2"):
        with pytest.raises(services.Denied):
            services.get_request(world, U(who), r["id"])


def test_agent_responds_only_to_own_proposal(world):
    inc = tl_alert(world)
    services.decide_incident(world, U("tl1"), inc.id, "Approve")
    n = next(x for x in world.notifications if x["incident"] == inc.id and x["kind"] in ("confirm", "offer"))
    helper = agent_account(world, n["agent_id"], "helper")
    with pytest.raises(services.Denied):
        services.respond(world, U("aarav"), n["id"], True)
    services.respond(world, helper, n["id"], True)
    assert n["status"] in ("accepted", "withdrawn")
    with pytest.raises(ValueError):
        services.respond(world, helper, n["id"], True)                  # duplicate answer refused


def test_in_app_checkin_window_single_use_and_attendance(world):
    d = shift_day(world, "A01")
    out = services.send_attendance_reminder(world, U("tl1"), "A01", d)
    assert out["status"] == "in_inbox" and out["channel"] == "in_app" and "token" not in out
    assert out["email"]["status"] == "not_sent"                       # in-app preference: no email attempted
    assert ("A01", d) not in world.attendance                          # a reminder never records attendance
    with pytest.raises(ValueError, match="already sent"):
        services.send_attendance_reminder(world, U("tl1"), "A01", d)
    with pytest.raises(ValueError, match="not open yet"):
        services.check_in_self(world, U("aarav"), out["id"])
    world.now_t = out["opens_t"]
    assert records.day_record(world, "A01", d)["code"] == "scheduled"
    services.check_in_self(world, U("aarav"), out["id"])
    assert world.attendance[("A01", d)] == "checkin"          # genuine check-in, not simulated "in"
    with pytest.raises(ValueError, match="[Aa]lready"):
        services.check_in_self(world, U("aarav"), out["id"])
    with pytest.raises(ValueError):
        services.send_attendance_reminder(world, U("tl1"), "A01", d, resend=True)
    assert any(r["action"] == "Check-in" for r in comms.audit_rows(world.run_id))


def test_checkin_expires_and_in_app_checkin_is_owner_only(world):
    d = shift_day(world, "A06")
    out = services.send_attendance_reminder(world, U("tl1"), "A06", d)
    world.now_t = out["opens_t"]
    with pytest.raises(ValueError):
        services.check_in_self(world, U("aarav"), out["id"])            # not Aarav's reminder
    world.now_t = out["expires_t"] + 1
    with pytest.raises(ValueError, match="closed"):
        services.check_in_self(world, U("sneha"), out["id"])


# ============================================================ messaging delivery honesty
def test_reminder_without_account_is_not_delivered(world):
    other = worker(world, "TL-1", world.first_day)
    out = services.send_attendance_reminder(world, U("tl1"), other, world.first_day)
    assert out["status"] == "no_account" and "No active ShiftSense account" in out["error"]


def test_operator_and_others_cannot_send_reminders(world):
    d = shift_day(world, "A01")
    for who in ("demo", "admin", "ops", "analyst", "aarav", "tl2"):
        with pytest.raises(services.Denied):
            services.send_attendance_reminder(world, U(who), "A01", d)


# ============================================================ Team lead
def test_team_lead_modify_reject_reasons_and_no_repeat(world):
    inc = tl_alert(world)
    with pytest.raises(services.Denied):
        services.decide_incident(world, U("tl2"), inc.id, "Approve")
    with pytest.raises(ValueError):
        services.decide_incident(world, U("tl1"), inc.id, "Reject", "")
    rejected = {(a.agent_id, a.kind) for a in inc.rec["actions"]}
    services.decide_incident(world, U("tl1"), inc.id, "Reject", "agents are in training")
    assert inc.status in wf.DECIDABLE or inc.status == "no_gap"
    if inc.rec["actions"]:
        assert not rejected & {(a.agent_id, a.kind) for a in inc.rec["actions"]}
    assert any(r["action"] == "Reject fix" and r["reason"] == "agents are in training"
               for r in comms.audit_rows(world.run_id))


def test_decline_returns_to_team_lead_with_new_fix(world):
    inc = tl_alert(world)
    services.decide_incident(world, U("tl1"), inc.id, "Approve")
    n = next(x for x in world.notifications if x["incident"] == inc.id and x["kind"] == "confirm")
    helper = agent_account(world, n["agent_id"], "helper2")
    services.respond(world, helper, n["id"], False)
    assert inc.status == "needs_new_fix"
    tl_notes = comms.notices_for(U("tl1"), world.run_id)
    assert any("declined · new fix ready" in x["title"] for x in tl_notes)


def test_overdue_critical_alert_escalates_to_ops_once(world):
    inc = tl_alert(world)
    from shiftsense import priority
    if priority.assess(world, inc)["severity"] != "Critical":
        pytest.skip("seeded gap is not critical")
    world.now_t += 2 * world.policy["recovery"]["tl_overdue_hours"]
    assert services.sweep_overdue(world) == 1
    assert services.sweep_overdue(world) == 0
    assert sum("Overdue at team lead" in n["title"] for n in comms.notices_for(U("ops"), world.run_id)) == 1


def test_double_click_and_concurrent_decisions_apply_once(world):
    inc = tl_alert(world)
    results = []

    def decide():
        try:
            with world.lock, wf.transaction(world):
                services.decide_incident(world, U("tl1"), inc.id, "Approve")
            results.append("ok")
        except ValueError as e:
            results.append(str(e))
    threads = [threading.Thread(target=decide) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 1
    assert all("Conflict" in r for r in results if r != "ok")
    assert sum(r["action"] == "Approve fix" for r in comms.audit_rows(world.run_id)) == 1


def test_failed_operation_leaves_no_partial_change(world):
    inc = tl_alert(world)
    before = world.roster.copy()
    n_req = len(world.requests)
    with pytest.raises(RuntimeError):
        with wf.transaction(world):
            wf.approve_fix(world, inc, "tl", "")
            world.requests.append({"id": "X"})
            raise RuntimeError("simulated crash mid-update")
    assert next(i for i in world.incidents if i.id == inc.id).status == "open"    # restored record
    assert len(world.requests) == n_req
    assert history.diff(world.policy, before, world.roster) == []


# ============================================================ Operations head
def test_escalation_to_ops_and_carried_risk_is_not_covered(world):
    inc = tl_alert(world)
    services.decide_incident(world, U("tl1"), inc.id, "Escalate", "both helpers unavailable")
    assert any("Escalation from TL-1" in n["title"] for n in comms.notices_for(U("ops"), world.run_id))
    with pytest.raises(services.Denied):
        services.ops_decide(world, U("tl1"), inc.id, "Accept the risk for this window", "x")
    with pytest.raises(ValueError):
        services.ops_decide(world, U("ops"), inc.id, "Accept the risk for this window", "")
    services.ops_decide(world, U("ops"), inc.id, "Accept the risk for this window", "low volume expected")
    assert inc.status == "closed" and inc.residual_ph and inc.residual_ph > 0
    perf = performance.recovery(world)
    assert perf["closed_with_risk"] == 1 and perf["resolved"] == 0 and perf["residual_ph"] >= inc.residual_ph
    with pytest.raises(ValueError, match="Conflict"):
        services.ops_decide(world, U("ops"), inc.id, "Accept the risk for this window", "again")


def test_ops_return_and_follow_up_ownership(world):
    inc = tl_alert(world)
    services.decide_incident(world, U("tl1"), inc.id, "Escalate", "no one free")
    services.ops_decide(world, U("ops"), inc.id, "Return to team lead", "try the open-shift offer first")
    assert inc.status == "open"
    services.assign_followup(world, U("ops"), "TL-1", "confirm cover by 20:00", 2, related=("incident", inc.id))
    fu = next(n for n in comms.notices_for(U("tl1"), world.run_id) if n["title"].startswith("Follow-up"))
    assert fu["due_t"] == world.now_t + 4 and fu["action_required"]
    with pytest.raises(services.Denied):
        services.complete_followup(world, U("tl2"), fu["id"], "done")
    services.complete_followup(world, U("tl1"), fu["id"], "Sneha covers 18-20")
    assert any(n["title"].startswith("Follow-up done") for n in comms.notices_for(U("ops"), world.run_id))
    assert comms.all_notices(world.run_id, "Follow-up from Operations")[0]["status"] == "resolved"


# ============================================================ Workforce planner
def test_planner_manual_vs_optimised_on_identical_inputs(world, heuristic_plan, optimized_plan):
    days = list(range(14))
    m_man = planning.metrics(world.policy, world.data["agents"], world.data["leave"], world.req, heuristic_plan["roster"], days)
    m_opt = planning.metrics(world.policy, world.data["agents"], world.data["leave"], world.req, optimized_plan["roster"], days)
    assert m_opt["Violations"] == 0
    assert m_opt["Uncovered people-hours"] <= m_man["Uncovered people-hours"]


def test_planner_infeasible_plan_keeps_live_roster_and_explains(world):
    """Frozen days already contain night shifts, so 'max 0 nights per week' cannot be met: the plan must
    fail, name the blocking rule, and leave the live roster untouched (hard rules are never relaxed)."""
    live = world.roster.copy()
    res = planning.run_scenario(world, planning.Scenario(name="no nights", max_nights_per_week=0), time_limit=10)
    assert res["roster"] is None
    assert any(d["type"] == "proven" and "nights" in d["text"] for d in res["diagnosis"])
    assert "Nothing was changed" in planning.explain_status(res["status"])
    assert world.roster.equals(live)


def test_publish_needs_review_blocks_violations_and_keeps_frozen_days(world):
    res = planning.run_scenario(world, planning.Scenario(name="re"), time_limit=20)
    plan = services.draft_plan(world, U("analyst"), res["roster"], "re", "test")
    services.validate_plan(world, U("analyst"), plan["id"])
    with pytest.raises(ValueError, match="Confirm"):
        services.publish_plan(world, U("analyst"), plan["id"])
    with pytest.raises(services.Denied):
        services.publish_plan(world, U("tl1"), plan["id"], reviewed=True)
    frozen = world.roster[world.roster.day <= world.today].copy()
    services.publish_plan(world, U("analyst"), plan["id"], reviewed=True)
    assert world.roster[world.roster.day <= world.today].reset_index(drop=True).equals(frozen.reset_index(drop=True))
    assert any("New roster published" in n["title"] for n in comms.notices_for(U("tl1"), world.run_id))
    # a draft that breaks a rule cannot be validated or published
    bad = world.roster.copy()
    dup = bad[bad.day == world.first_day + 1].iloc[[0]].assign(shift="Night")
    bad = pd.concat([bad, dup], ignore_index=True)
    plan2 = services.draft_plan(world, U("analyst"), bad, "bad", "test")
    rv = services.validate_plan(world, U("analyst"), plan2["id"])
    assert rv["violations"] and plan2["status"] == "draft"
    with pytest.raises(ValueError):
        services.publish_plan(world, U("analyst"), plan2["id"], reviewed=True)


def test_planner_cannot_decide_alerts_or_requests(world):
    inc = tl_alert(world)
    with pytest.raises(services.Denied):
        services.decide_incident(world, U("analyst"), inc.id, "Approve")
    r = services.submit_leave(world, U("aarav"), pending_day(world, "A01"))
    with pytest.raises(services.Denied):
        services.decide_request(world, U("analyst"), r["id"], True)


# ============================================================ Admin
def test_admin_calendar_shows_only_recorded_events(world):
    """At Tue 06 Oct 18:00 no attendance has been recorded: nothing may show as worked."""
    assert wf.now_label(world).startswith("Tue 06 Oct 18:00")
    codes = {records.day_record(world, a, d)["code"] for a in world.data["agents"].agent_id for d in (0, 1)}
    assert "worked" not in codes and "unrecorded" in codes
    rep = report.weekly_report(world, 0)
    assert rep["summary"]["Shifts worked (recorded)"] == 0
    assert rep["summary"]["Attendance %"] == "Unavailable"
    assert rep["summary"]["Started shifts without attendance record"] > 0


def test_admin_scan_does_not_duplicate_and_needs_ops_authorisation(world):
    wf.load_story(world)
    first = services.scan(world, U("admin"))
    assert first and services.scan(world, U("admin")) == []
    cid = first[0]
    services.case_action(world, U("admin"), cid, "Acknowledge", "")
    services.case_action(world, U("admin"), cid, "Request explanation", "please explain the approvals")
    services.case_action(world, U("tl2"), cid, "Explain", "all approvals were family emergencies")
    services.case_action(world, U("admin"), cid, "Request corrective action", "pattern persists")
    assert any("Authorise corrective action" in n["title"] for n in comms.notices_for(U("ops"), world.run_id))
    with pytest.raises(services.Denied):
        services.case_action(world, U("admin"), cid, "Authorise corrective action", "self-authorise")
    services.case_action(world, U("ops"), cid, "Authorise corrective action", "coaching session")
    assert next(c for c in comms.cases(world.run_id) if c["id"] == cid)["status"] == "Resolved"


def test_admin_account_management_boundaries(world):
    admin = U("admin")
    a10 = next(a for a in world.data["agents"].agent_id if a not in ("A01", "A02", "A06"))
    services.create_account(world, admin, "newagent", {"name": "New Agent", "employee_id": "E-900", "role": "agent",
                                                       "agent_id": a10, "email": "new.agent@example.com",
                                                       "contact_pref": "email"}, "Temp-Pass-2026!")
    assert U("newagent", "Temp-Pass-2026!")["agent_id"] == a10
    bad = [("newagent", {"employee_id": "E-901"}),                                  # duplicate username
           ("other", {"employee_id": "E-900"}),                                     # duplicate employee id
           ("boss", {"role": "admin"}),                                             # no admin creation
           ("dup", {"role": "agent", "agent_id": a10, "employee_id": "E-902"}),     # agent already has account
           ("mailless", {"contact_pref": "email", "email": "", "employee_id": "E-903"}),
           ("badmail", {"email": "not-an-address", "employee_id": "E-904"}),
           ("dupmail", {"email": "NEW.agent@example.com", "employee_id": "E-906"}),
           ("smspref", {"contact_pref": "sms", "employee_id": "E-907"})]                # SMS no longer offered
    base = {"name": "X", "employee_id": "E-905", "role": "analyst", "contact_pref": "in_app"}
    for username, f in bad:
        with pytest.raises(accounts.AccountError):
            services.create_account(world, admin, username, {**base, **f}, "Temp-Pass-2026!")
    me = accounts.get("admin")
    with pytest.raises(accounts.AccountError):                                     # no self-promotion / demotion
        services.update_account(world, admin, "admin", {"role": "ops_head"}, me["version"], "x")
    acc = accounts.get("newagent")
    with pytest.raises(ValueError, match="reason"):
        services.update_account(world, admin, "newagent", {"active": False}, acc["version"])
    services.update_account(world, admin, "newagent", {"email": "agent.new@example.com"}, acc["version"])
    with pytest.raises(accounts.AccountError, match="Conflict"):                   # stale version, no silent overwrite
        services.update_account(world, admin, "newagent", {"email": "x.y@example.com"}, acc["version"])
    for who in ("tl1", "ops", "analyst", "demo", "aarav"):
        with pytest.raises(services.Denied):
            services.create_account(world, U(who), "zz", base, "Temp-Pass-2026!")
    acts = [r["action"] for r in comms.audit_rows(world.run_id)]
    assert acts.count("Create account") == 1 and acts.count("Update account") == 1
    assert "agent.new@example.com" not in str(comms.audit_rows(world.run_id))     # addresses are masked in the audit
    assert accounts.get("newagent")["email_verified"] is False                     # admin entry is never "verified"


def test_deactivated_account_is_refused_on_next_action(world):
    tl = U("tl1")
    acc = accounts.get("tl1")
    services.update_account(world, U("admin"), "tl1", {"active": False}, acc["version"], "left the company")
    with pytest.raises(services.Denied, match="not active"):
        services.report_absence(world, tl, [worker(world, "TL-1", world.first_day)], world.first_day)
    assert auth.authenticate("tl1", PW["tl1"]) is None


def test_admin_prohibited_actions_are_denied(world):
    inc = tl_alert(world)
    r = services.submit_leave(world, U("aarav"), pending_day(world, "A01"))
    admin = U("admin")
    for call in (lambda: services.decide_incident(world, admin, inc.id, "Approve"),
                 lambda: services.decide_request(world, admin, r["id"], True),
                 lambda: services.revoke(world, admin, r["id"], "x"),
                 lambda: services.publish_plan(world, admin, "PLAN-0", reviewed=True),
                 lambda: services.draft_plan(world, admin, world.roster, "x", "x"),
                 lambda: services.ops_decide(world, admin, inc.id, "Accept the risk for this window", "x"),
                 lambda: services.advance_clock(world, admin, 1),
                 lambda: services.load_demo_story(world, admin),
                 lambda: services.unload_demo(world, admin, None),
                 lambda: services.send_attendance_reminder(world, admin, "A01", world.first_day)):
        with pytest.raises(services.Denied):
            call()
    assert not hasattr(comms, "delete_audit") and not hasattr(comms, "update_audit")


# ============================================================ Analyst: demo handling and import
def test_import_maps_headers_reports_rows_and_never_partially_applies(world, data):
    t = importer.template(data["agents"]).rename(columns={"agent_id": "Agent ID", "primary": "Primary Queue",
                                                          "pref_off": "Day off"})
    t = t[list(reversed(t.columns))]                                             # column order does not matter
    m = importer.suggest_mapping(t.columns, list(importer.REQUIRED) + list(importer.OPTIONAL))
    assert m["agent_id"] == "Agent ID" and m["primary"] == "Primary Queue" and m["pref_off"] == "Day off"
    ok = importer.validate_agents(importer.apply_mapping(t, m), world.policy, set(world.data["agents"].team))
    assert ok["agents"] is not None and ok["summary"]["rejected"] == 0
    bad = t.astype(object)
    bad.loc[0, "team"] = "TL-99"
    bad.loc[1, "pref_shift"] = "Graveyard"
    bad.loc[2, "night_ok"] = "maybe"
    res = importer.validate_agents(importer.apply_mapping(bad, m), world.policy, set(world.data["agents"].team))
    assert res["agents"] is None and res["summary"]["rejected"] == 3
    probs = " ".join(x["problems"] for x in res["rows"])
    assert "unknown team" in probs and "not a shift" in probs and "not yes/no" in probs
    lv = importer.validate_leave(pd.DataFrame({"agent_id": ["A01", "A01", "A02", "ZZ"],
                                               "date": ["2026-10-08", "2026-10-08", "31/31/2026", "2026-10-09"]}),
                                 ok["agents"], world.policy)
    assert lv["leave"] is None and lv["summary"] == {"accepted": 1, "rejected": 2, "skipped": 1}


def test_import_commit_is_audited_and_analyst_only(world, data, req):
    agents = importer.validate_agents(importer.template(data["agents"]).head(40), world.policy)["agents"]
    base = {"data": data, "req": req, "fc": None}
    with pytest.raises(services.Denied):
        services.commit_import(world, U("admin"), agents, pd.DataFrame(columns=["agent_id", "day"]), base, {})
    for who in ("ops", "tl1", "aarav"):
        with pytest.raises(services.Denied):
            services.commit_import(world, U(who), agents, pd.DataFrame(columns=["agent_id", "day"]), base, {})
    new = services.commit_import(world, U("analyst"), agents, pd.DataFrame(columns=["agent_id", "day"]), base,
                                 {"files": "agents.csv", "agents": 40})
    assert len(new.data["agents"]) == 40 and check(new.policy, new.data["agents"], new.data["leave"], new.roster) == []
    assert any(r["action"] == "Import committed" for r in comms.audit_rows(world.run_id))


def test_analyst_demo_access_grants_no_tl_or_ops_authority(world):
    inc = tl_alert(world)
    r = services.submit_leave(world, U("aarav"), pending_day(world, "A01"))
    for who in ("analyst", "demo"):
        an = U(who)
        for call in (lambda: services.decide_incident(world, an, inc.id, "Approve"),
                     lambda: services.decide_request(world, an, r["id"], True),
                     lambda: services.ops_decide(world, an, inc.id, "Accept the risk for this window", "x"),
                     lambda: services.scan(world, an),
                     lambda: services.post_message(world, an, f"incident:{inc.id}", "hello"),
                     lambda: services.send_attendance_reminder(world, an, "A01", world.first_day),
                     lambda: services.create_account(world, an, "x1", {}, "Temp-Pass-2026!")):
            with pytest.raises(services.Denied):
                call()


def test_demo_story_is_labelled_and_unload_keeps_history(world):
    story = services.load_demo_story(world, U("analyst"))
    sim = [r for r in story.requests if r.get("simulated_history")]
    assert len(sim) == 7 and all("simulated" in r["detail"] for r in sim)
    assert not [r for r in world.requests if r.get("simulated_history")]           # live untouched
    services.unload_demo(world, U("analyst"), story)
    assert comms.audit_rows(story.run_id) and comms.verify_audit()[0]


def test_clock_no_shows_alert_team_lead_once(world):
    with pytest.raises(ValueError, match="only in the demo scenario"):
        services.advance_clock(world, U("analyst"), 1)
    world = services.load_demo_story(world, U("analyst"))
    res = services.advance_clock(world, U("analyst"), 14, rate=0.5)
    assert res["no_shows"]
    inc = res["no_shows"][0]
    services.notify_alert(world, inc, "Attendance system")                       # a repeat does not duplicate
    tl = {"TL-1": "tl1", "TL-2": "tl2"}.get(inc.owner_team)
    if tl:
        hits = [n for n in comms.notices_for(U(tl), world.run_id) if n["related_ref"] == inc.id]
        assert len(hits) == 1
    for n in res["no_shows"]:
        assert records.day_record(world, n.absent[0], n.day)["code"] == "no_show"


# ============================================================ cross-persona journey
def test_absence_to_cover_journey_across_personas(world):
    d = world.first_day
    absent = worker(world, "TL-1", d)
    inc = services.report_absence(world, U("tl1"), [absent], d)
    assert any(n["related_ref"] == inc.id for n in comms.notices_for(U("tl1"), world.run_id))
    assert not any(n["related_ref"] == inc.id for n in comms.notices_for(U("tl2"), world.run_id))
    before = world.roster.copy()
    services.decide_incident(world, U("tl1"), inc.id, "Approve")
    asks = [n for n in world.notifications if n["incident"] == inc.id and n["kind"] in ("confirm", "offer")]
    assert asks
    helper = agent_account(world, asks[0]["agent_id"], "journey")
    services.respond(world, helper, asks[0]["id"], True)
    assert history.diff(world.policy, before, world.roster)                      # roster really changed
    assert check(world.policy, world.data["agents"], world.data["leave"], world.roster) == []
    # TL alert notice resolved once the TL decided
    assert all(n["status"] == "resolved" for n in comms.notices_for(U("tl1"), world.run_id)
               if n["related_ref"] == inc.id and n["action_required"])
    acts = [r["action"] for r in comms.audit_rows(world.run_id)]
    assert "Report absence" in " ".join(acts) or any("absence" in a.lower() for a in acts)
    assert "Approve fix" in acts and "Accept" in acts
    assert comms.verify_audit()[0]
    # Admin oversight sees the decision in the log; governance scan runs without error
    assert services.scan(world, U("admin")) is not None


# ============================================================ AI optional, never authoritative
def test_ai_absent_or_broken_falls_back_to_rules():
    out = assistant.parse_request("I need Friday off for a wedding", ["Fri 09 Oct"], "Tue 06 Oct", None)
    assert out["source"] == "rules" and out["intent"] == "time_off"

    class Broken:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    raise RuntimeError("malformed response")
    out = assistant.parse_request("I need Friday off", ["Fri 09 Oct"], "Tue 06 Oct", Broken())
    assert out["source"] == "rules"

    class Garbage:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    class R:
                        stop_reason = "end_turn"
                        content = [type("B", (), {"type": "text", "text": '{"intent": "approve_everything"}'})()]
                    return R()
    out = assistant.parse_request("approve my leave", ["Fri 09 Oct"], "Tue 06 Oct", Garbage())
    assert out["source"] == "rules"                                              # unknown intent is rejected
