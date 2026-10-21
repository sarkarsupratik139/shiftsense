"""Demo isolation, publication safety, five-role model and commit-time rule guard.

The demo scenario is a separate copy (own run id, own storage slot). These tests prove that nothing done in
it - loading, simulating, exploring, drafting, unloading - changes the live published roster or live records,
and that only an explicitly published, valid Analyst draft does."""
import sqlite3

import pandas as pd
import pytest

from shiftsense import accounts, attendance, auth, comms, history, planning, records, report, services, staffing, store
from shiftsense import workflow as wf

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}


@pytest.fixture
def live(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "iso.db"))
    monkeypatch.delenv("SHIFTSENSE_EMAIL", raising=False)
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def U(name):
    return auth.authenticate(name, PW[name])


def snapshot(w):
    return {"fp": wf.roster_fp(w.roster), "requests": len(w.requests), "incidents": len(w.incidents),
            "attendance": dict(w.attendance), "now": w.now_t, "leave": len(w.data["leave"]),
            "plans": [(p["id"], p["status"]) for p in w.plans], "notices": len(comms.all_notices(w.run_id)),
            "audit": len(comms.audit_rows(w.run_id))}


# ---------------------------------------------------------------- isolation
def test_loading_demo_preserves_live_and_is_identifiable(live):
    before = snapshot(live)
    sc = services.load_demo_story(live, U("analyst"))
    after = snapshot(live)
    assert after["audit"] == before["audit"] + 1                        # only the 'Load demo story' audit row
    assert {k: v for k, v in after.items() if k != "audit"} == {k: v for k, v in before.items() if k != "audit"}
    assert sc.scenario and not live.scenario and sc.run_id.startswith("sim-") and sc.run_id != live.run_id
    assert sc.incidents and any(r.get("simulated_history") for r in sc.requests)
    assert wf.roster_fp(sc.roster) != before["fp"]                      # the story changed the COPY only
    assert history.context_of(sc.run_id).startswith("Demo scenario")
    assert report.weekly_report(sc, 0)["summary"]["Context"].startswith("Demo scenario")
    assert all(n["title"] for n in comms.all_notices(sc.run_id)) and not comms.all_notices(live.run_id)


def test_demo_clock_and_no_shows_never_reach_live_records(live):
    sc = services.load_demo_story(live, U("analyst"))
    before = snapshot(live)
    res = services.advance_clock(sc, U("analyst"), 20, rate=0.5)
    assert res["no_shows"] and sc.now_t > live.now_t
    assert snapshot(live) == before
    for inc in res["no_shows"]:
        a, d = inc.absent[0], inc.day
        assert records.day_record(sc, a, d)["code"] == "no_show"
        assert records.day_record(live, a, d)["code"] != "no_show"          # simulated outcome stays in the sandbox
    with pytest.raises(ValueError, match="only in the demo scenario"):
        services.advance_clock(live, U("analyst"), 1)


def test_what_if_and_forecast_exploration_do_not_mutate(live):
    sc = services.load_demo_story(live, U("analyst"))
    fp_live, fp_sc = wf.roster_fp(live.roster), wf.roster_fp(sc.roster)
    planning.run_scenario(sc, planning.Scenario(name="spike", demand_pct={"Claims-EN": 30}, extra_absences=5),
                          time_limit=10)
    planning.run_scenario(live, planning.Scenario(name="x", headcount_change=-3), time_limit=10)
    planning.metrics(live.policy, live.data["agents"], live.data["leave"], live.req, live.roster, planning.open_days(live))
    assert wf.roster_fp(live.roster) == fp_live and wf.roster_fp(sc.roster) == fp_sc


def test_unload_and_storage_slots_keep_live_and_history(live):
    store.save(live)                                                   # live slot
    sc = services.load_demo_story(live, U("analyst"))
    store.save(sc, slot="scenario")
    assert store.load(slot="scenario").run_id == sc.run_id and store.load().run_id == live.run_id
    services.unload_demo(live, U("analyst"), sc)
    store.clear(slot="scenario")
    assert store.load(slot="scenario") is None
    reloaded = store.load()
    assert reloaded.run_id == live.run_id and wf.roster_fp(reloaded.roster) == wf.roster_fp(live.roster)
    assert comms.audit_rows(sc.run_id) and comms.verify_audit()[0]


def test_scenario_draft_publishes_only_by_explicit_valid_analyst_action(live):
    sc = services.load_demo_story(live, U("analyst"))
    with pytest.raises(ValueError, match="never published"):
        services.publish_plan(sc, U("analyst"), "PLAN-0", reviewed=True)
    with pytest.raises(ValueError, match="live workspace"):
        services.draft_plan(sc, U("analyst"), sc.roster, "x", "x")
    res = planning.run_scenario(sc, planning.Scenario(name="from demo"), time_limit=20)
    fp = wf.roster_fp(live.roster)
    plan = services.draft_plan(live, U("analyst"), res["roster"], "from demo", f"demo scenario {sc.run_id}")
    assert wf.roster_fp(live.roster) == fp                                # a draft is not the published roster
    services.validate_plan(live, U("analyst"), plan["id"])
    assert wf.roster_fp(live.roster) == fp
    for who in ("ops", "admin", "tl1", "aarav"):
        with pytest.raises(services.Denied):
            services.publish_plan(live, U(who), plan["id"], reviewed=True)
    services.publish_plan(live, U("analyst"), plan["id"], reviewed=True)
    assert wf.roster_fp(live.roster) != fp


def test_stale_draft_and_invalid_draft_cannot_publish(live):
    res = planning.run_scenario(live, planning.Scenario(name="re"), time_limit=20)
    plan = services.draft_plan(live, U("analyst"), res["roster"], "re", "test")
    services.validate_plan(live, U("analyst"), plan["id"])
    services.report_absence(live, U("tl1"), [next(a for a in live.roster[live.roster.day == live.first_day].agent_id
                                                  if a not in ("A01", "A06") and dict(zip(live.data["agents"].agent_id,
                                                  live.data["agents"].team))[a] == "TL-1")], live.first_day)
    before = wf.roster_fp(live.roster)
    with pytest.raises(ValueError, match="Conflict: the published roster changed"):
        services.publish_plan(live, U("analyst"), plan["id"], reviewed=True)
    assert wf.roster_fp(live.roster) == before                          # last valid roster preserved
    bad = live.roster.copy()
    q_bad = bad.iloc[0]
    elig = dict(zip(live.data["agents"].agent_id, live.data["agents"].eligible))
    wrong = next(q for q in live.policy["queues"] if q not in elig[q_bad.agent_id])
    bad.loc[bad.index[0], "queue"] = wrong                              # untrained queue
    plan2 = services.draft_plan(live, U("analyst"), bad, "bad", "test")
    rv = services.validate_plan(live, U("analyst"), plan2["id"])
    assert rv["violations"] and plan2["status"] == "draft"
    with pytest.raises(ValueError):
        services.publish_plan(live, U("analyst"), plan2["id"], reviewed=True)
    assert wf.roster_fp(live.roster) == before


# ---------------------------------------------------------------- commit-time rule guard
def test_untrained_queue_or_rule_break_is_rejected_at_commit(live):
    elig = dict(zip(live.data["agents"].agent_id, live.data["agents"].eligible))
    fp = wf.roster_fp(live.roster)
    with pytest.raises(ValueError, match="Blocked: this change would break"):
        with wf.transaction(live):
            ro = live.roster.copy()
            a = ro.iloc[5].agent_id
            ro.loc[ro.index[5], "queue"] = next(q for q in live.policy["queues"] if q not in elig[a])
            live.roster = ro
    assert wf.roster_fp(live.roster) == fp
    with pytest.raises(ValueError, match="Blocked"):
        with wf.transaction(live):                                   # rest-rule break: night then morning
            ro = live.roster.copy()
            a = ro[ro.day == 9].agent_id.iloc[0]
            ro = ro[~((ro.agent_id == a) & ro.day.isin([9, 10]))]
            extra = pd.DataFrame([dict(ro.iloc[0], agent_id=a, day=9, shift="Night", queue=elig[a][0]),
                                  dict(ro.iloc[0], agent_id=a, day=10, shift="Morning", queue=elig[a][0])])
            live.roster = pd.concat([ro, extra], ignore_index=True)
    assert wf.roster_fp(live.roster) == fp


def test_approval_rechecks_rules_at_decision_time(live, monkeypatch):
    d = next(x for x in range(live.first_day, 14) if ((live.roster.agent_id == "A01") & (live.roster.day == x)).any()
             and wf.leave_check(live, "A01", x)["status"] != "Green")
    r = services.submit_leave(live, U("aarav"), d, leave_type="unpaid")
    fp = wf.roster_fp(live.roster)
    import shiftsense.requests as rq
    real = rq.apply_alternative

    def corrupt(roster, *a, **k):                                   # roster changed underneath since the request
        out = real(roster, *a, **k).copy()
        elig = dict(zip(live.data["agents"].agent_id, live.data["agents"].eligible))
        i = out.index[0]
        out.loc[i, "queue"] = next(q for q in live.policy["queues"] if q not in elig[out.loc[i, "agent_id"]])
        return out
    monkeypatch.setattr(rq, "apply_alternative", corrupt)
    with pytest.raises(ValueError, match="Blocked"):
        services.decide_request(live, U("tl1"), r["id"], True)
    assert wf.roster_fp(live.roster) == fp
    assert next(x for x in live.requests if x["id"] == r["id"])["state"] == "Pending"


# ---------------------------------------------------------------- scenario side effects
def test_scenario_never_sends_real_email(live, monkeypatch):
    from shiftsense import mailer
    for k, v in {"SHIFTSENSE_EMAIL": "smtp", "SMTP_HOST": "mail.example.test",
                 "SHIFTSENSE_EMAIL_FROM": "shiftsense@example.test"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(mailer.SmtpTransport, "send", lambda *a, **k: pytest.fail("real email from a demo"))
    sc = services.load_demo_story(live, U("analyst"))
    acc = accounts.get("aarav")
    services.update_account(live, U("admin"), "aarav", {"email": "aarav.k@example.com", "contact_pref": "email"},
                            acc["version"])
    accounts.mark_email_verified("aarav", "aarav.k@example.com")
    d = next(x for x in range(sc.first_day, 14) if ((sc.roster.agent_id == "A01") & (sc.roster.day == x)).any())
    out = services.send_attendance_reminder(sc, U("tl1"), "A01", d)
    assert out["status"] == "in_inbox" and out["email"]["status"] == "not_sent"
    assert "Demo scenario" in out["email"]["detail"]


def test_governance_cases_do_not_leak_between_scenario_and_live(live):
    sc = services.load_demo_story(live, U("analyst"))
    sim_cases = services.scan(sc, U("admin"))
    assert sim_cases and {c["id"] for c in comms.cases(sc.run_id)} == set(sim_cases)
    assert comms.cases(live.run_id) == []                                # live sees no simulated cases
    sc2 = services.load_demo_story(live, U("analyst"))                    # reload: flags can be raised again
    assert services.scan(sc2, U("admin"))


# ---------------------------------------------------------------- five roles
def test_obsolete_operator_role_is_migrated_not_kept(live, tmp_path):
    accounts.all_accounts()                                              # seed
    from shiftsense import db
    with db.connect() as con:
        con.execute("UPDATE accounts SET role='operator' WHERE username='demo'")
    assert accounts.get("demo")["role"] == "analyst"
    assert auth.authenticate("demo", PW["demo"])["role"] == "analyst"
    assert {a["role"] for a in accounts.all_accounts()} <= {"admin", "agent", "team_lead", "ops_head", "analyst", "hr"}
    with pytest.raises(accounts.AccountError):
        services.create_account(live, U("admin"), "op2", {"name": "x", "employee_id": "E-777", "role": "operator",
                                                          "contact_pref": "in_app"}, "Temp-Pass-2026!")


def test_analyst_risk_goes_to_ops_and_ops_keeps_authority(live):
    nid = services.escalate_risk(live, U("analyst"), "Claims-EN short Thursday", "6 people-h; no trained spare")
    with pytest.raises(ValueError, match="already sent"):
        services.escalate_risk(live, U("analyst"), "Claims-EN short Thursday", "again")
    assert any(n["id"] == nid for n in comms.notices_for(U("ops"), live.run_id))
    for who in ("analyst", "admin", "tl1"):
        with pytest.raises(services.Denied):
            services.answer_risk(live, U(who), nid, "Decision recorded", "x")
    with pytest.raises(ValueError):
        services.answer_risk(live, U("ops"), nid, "Decision recorded", "")
    services.answer_risk(live, U("ops"), nid, "Returned to Analyst", "re-run with Friday demand")
    assert any(n["title"] == "Ops: Returned to Analyst" for n in comms.notices_for(U("analyst"), live.run_id))
    for who in ("ops", "tl1", "aarav", "admin"):
        with pytest.raises(services.Denied):
            services.escalate_risk(live, U(who), "x", "y")


def test_ops_cannot_publish_edit_accounts_or_run_demo(live):
    ops = U("ops")
    for call in (lambda: services.publish_plan(live, ops, "PLAN-0", reviewed=True),
                 lambda: services.draft_plan(live, ops, live.roster, "x", "x"),
                 lambda: services.create_account(live, ops, "x", {}, "Temp-Pass-2026!"),
                 lambda: services.load_demo_story(live, ops),
                 lambda: services.advance_clock(live, ops, 1),
                 lambda: services.commit_import(live, ops, None, None, {}, {})):
        with pytest.raises(services.Denied):
            call()


def test_substituted_identity_is_refused(live):
    """A forged user dict (another person's id, or an elevated role) is re-checked against the account store."""
    forged = dict(U("aarav"), role="ops_head")
    with pytest.raises(services.Denied):
        services.escalate_risk(live, forged, "x", "y")
    forged2 = dict(U("tl1"), team="TL-2")
    with pytest.raises(services.Denied):
        services.report_absence(live, forged2, [live.roster[live.roster.day == live.first_day].agent_id.iloc[0]],
                                live.first_day)


def test_demo_loads_even_when_live_has_open_requests(live):
    """Regression (found in the browser): the story must not fail on agents who already have open requests."""
    d = next(x for x in range(live.first_day + 2, 14) if ((live.roster.agent_id == "A06") & (live.roster.day == x)).any()
             and wf.leave_check(live, "A06", x)["status"] != "Green")
    services.submit_leave(live, U("sneha"), d)
    before = wf.roster_fp(live.roster)
    sc = services.load_demo_story(live, U("analyst"))
    assert sc.incidents and wf.roster_fp(live.roster) == before


# ---------------------------------------------------------------- live clock vs simulation clock
def test_live_clock_moves_time_only_and_checkins_are_genuine(live):
    sc = services.load_demo_story(live, U("analyst"))
    sc_before = snapshot(sc)
    d = next(x for x in range(live.first_day, 14) if ((live.roster.agent_id == "A01") & (live.roster.day == x)).any())
    out = services.send_attendance_reminder(live, U("tl1"), "A01", d)
    t0, fp = live.now_t, wf.roster_fp(live.roster)
    for who in ("ops", "admin", "tl1", "aarav"):
        with pytest.raises(services.Denied):
            services.advance_live_clock(live, U(who), 1)
    with pytest.raises(ValueError, match="live clock"):
        services.advance_live_clock(sc, U("analyst"), 1)
    hours = (out["opens_t"] - live.now_t) / 2
    services.advance_live_clock(live, U("analyst"), hours)
    assert live.now_t == out["opens_t"] > t0
    assert live.attendance == {} and wf.roster_fp(live.roster) == fp      # no simulated attendance, no events
    assert len(live.incidents) == 0
    services.check_in_self(live, U("aarav"), out["id"])
    assert live.attendance[("A01", d)] == "checkin"
    assert snapshot(sc) == sc_before                                     # the scenario's clock and records untouched
    res = services.advance_clock(sc, U("analyst"), 14, rate=0.3)
    assert all(v in ("in", "no-show") for v in sc.attendance.values())
    assert live.attendance == {("A01", d): "checkin"} and live.now_t == out["opens_t"]
    services.advance_live_clock(live, U("analyst"), 1)                   # shift has now started
    rec = records.day_record(live, "A01", d)
    assert rec["code"] == "worked" and "checked in" in rec["detail"]
    assert live.attendance == {("A01", d): "checkin"}                      # still no simulated records
    rep = report.weekly_report(live, d // 7)["summary"]
    assert rep["Genuine check-ins"] == 1 and rep["Simulated attendance records"] == 0
    assert res is not None


# ---------------------------------------------------------------- legacy demo-story cleanup
def _old_flow_world(live):
    """Reproduce the pre-isolation bug: the story written straight into the live workspace."""
    wf.load_story(live)
    return live


def test_cleanup_dry_run_changes_nothing_and_execute_restores_published(live):
    w = _old_flow_world(live)
    genuine = services.report_absence(w, U("tl1"), [next(a for a in w.roster[w.roster.day == w.first_day + 4].agent_id
                                                         if a not in ("A01", "A06") and dict(zip(w.data["agents"].agent_id,
                                                         w.data["agents"].team))[a] == "TL-1")], w.first_day + 4)
    before = snapshot(w)
    p = services.cleanup_legacy_story(w, U("analyst"))
    assert snapshot(w) == before                                        # dry run is read-only
    assert p["incidents"] and genuine.id not in {x["id"] for x in p["incidents"]}
    assert any(x["why"] == "simulated prior-period record" for x in p["requests"])
    for who in ("ops", "admin", "tl1"):
        with pytest.raises(services.Denied):
            services.cleanup_legacy_story(w, U(who))
    audit_n = len(comms.audit_rows(w.run_id))
    services.cleanup_legacy_story(w, U("analyst"), execute=True)
    assert [i.id for i in w.incidents] == [genuine.id]                  # genuine alert preserved
    assert not any(r.get("simulated_history") for r in w.requests)
    pub = next(x for x in w.plans if x["status"] == "published")["roster"]
    diff = history.diff(w.policy, pub, w.roster)
    assert {(x["agent_id"], x["day"]) for x in diff} <= {(a, genuine.day) for a in genuine.absent} | \
        {(a.agent_id, a.day) for a in genuine.applied}                    # only the genuine change differs
    assert len(comms.audit_rows(w.run_id)) == audit_n + 1 and comms.verify_audit()[0]
    left = services.cleanup_legacy_story(w, U("analyst"))
    assert not left["incidents"] and not left["requests"] and not left["leave_rows"] and not left["roster_restore"]


def test_cleanup_leaves_story_records_touched_by_genuine_actions(live):
    w = _old_flow_world(live)
    req = next(r for r in w.requests if r.get("note") == "family function" and r["state"] == "Pending")
    tl = "tl1" if dict(zip(w.data["agents"].agent_id, w.data["agents"].team))[req["agent_id"]] == "TL-1" else "tl2"
    services.decide_request(w, U(tl), req["id"], False, "team short")   # a real person acted on it
    p = services.cleanup_legacy_story(w, U("analyst"))
    assert req["id"] not in {x["id"] for x in p["requests"]}
    assert any(req["id"] in u for u in p["uncertain"])
    services.cleanup_legacy_story(w, U("analyst"), execute=True)
    assert any(r["id"] == req["id"] for r in w.requests)                # uncertain record left untouched


def test_cleanup_refuses_scenario(live):
    sc = services.load_demo_story(live, U("analyst"))
    with pytest.raises(ValueError, match="live workspace only"):
        services.cleanup_legacy_story(sc, U("analyst"))
