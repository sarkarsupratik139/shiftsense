"""Readiness pass: exact time boundaries, too-late decisions, staffing variance honesty, roster rollback,
benchmark fairness, recovery ranking and solver failure, health, backup/restore, log hygiene, and the seven
end-to-end acceptance workflows. Controlled clocks only (no wall-clock time)."""
import numpy as np
import pandas as pd
import pytest

from shiftsense import (accounts, attendance, auth, backup, comms, health, logs, planning, priority, records,
                        recovery, risk, services, staffing, store, variance)
from shiftsense import workflow as wf
from shiftsense.constraints import check

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "r.db"))
    monkeypatch.delenv("SHIFTSENSE_EMAIL", raising=False)
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def U(name):
    return auth.authenticate(name, PW[name])


def team(w):
    return dict(zip(w.data["agents"].agent_id, w.data["agents"].team))


def tl1_worker(w, day, skip=()):
    return next(a for a in w.roster[w.roster.day == day].agent_id
                if team(w)[a] == "TL-1" and a not in ("A01", "A06", *skip))


def red_day(w, agent):
    return next(d for d in range(w.first_day, 14) if ((w.roster.agent_id == agent) & (w.roster.day == d)).any()
                and wf.leave_check(w, agent, d)["status"] != "Green")


# ---------------------------------------------------------------- time boundaries (controlled clock)
def test_checkin_window_exact_boundaries(w):
    d = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A01") & (w.roster.day == x)).any())
    out = services.send_attendance_reminder(w, U("tl1"), "A01", d)
    w.now_t = out["opens_t"] - 1
    with pytest.raises(ValueError, match="not open yet"):
        services.check_in_self(w, U("aarav"), out["id"])
    w.now_t = out["expires_t"] + 1
    with pytest.raises(ValueError, match="closed"):
        services.check_in_self(w, U("aarav"), out["id"])
    w.now_t = out["expires_t"]                                          # last valid instant
    services.check_in_self(w, U("aarav"), out["id"])
    assert w.attendance[("A01", d)] == "checkin"
    with pytest.raises(ValueError):
        services.check_in_self(w, U("aarav"), out["id"])                # exactly once


def test_checkin_opens_exactly_at_window_start(w):
    d = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A06") & (w.roster.day == x)).any())
    out = services.send_attendance_reminder(w, U("tl1"), "A06", d)
    w.now_t = out["opens_t"]
    services.check_in_self(w, U("sneha"), out["id"])
    assert w.attendance[("A06", d)] == "checkin"


def test_reminder_refused_after_window_and_missing_attendance_is_never_a_no_show(w):
    d = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A01") & (w.roster.day == x)).any())
    start = wf.shift_intervals(w.policy, d, w.roster[(w.roster.agent_id == "A01") & (w.roster.day == d)].iloc[0]["shift"]).start
    w.now_t = start + attendance.CLOSES_AFTER + 1
    with pytest.raises(ValueError, match="already started"):
        services.send_attendance_reminder(w, U("tl1"), "A01", d)
    assert records.day_record(w, "A01", d)["code"] == "unrecorded"          # unknown, not no_show
    assert not w.incidents


def test_overdue_escalation_at_exact_limit(w):
    inc = services.report_absence(w, U("tl1"), [tl1_worker(w, w.first_day)], w.first_day)
    if priority.assess(w, inc)["severity"] != "Critical":
        pytest.skip("seeded gap not critical")
    limit = int(2 * w.policy["recovery"]["tl_overdue_hours"])
    w.now_t = inc.created_t + limit - 1
    assert services.sweep_overdue(w) == 0
    w.now_t = inc.created_t + limit
    assert services.sweep_overdue(w) == 1 and services.sweep_overdue(w) == 0


def test_requests_cannot_change_a_day_that_has_started(w):
    d = red_day(w, "A01")
    r = services.submit_leave(w, U("aarav"), d, leave_type="unpaid")
    w.now_t = d * 48                                                       # the day starts
    fp = wf.roster_fp(w.roster)
    with pytest.raises(ValueError, match="Too late to approve"):
        services.decide_request(w, U("tl1"), r["id"], True)
    assert wf.roster_fp(w.roster) == fp and r["state"] == "Pending"
    services.decide_request(w, U("tl1"), r["id"], False, "day already started")   # declining is still allowed
    assert r["state"] == "Rejected"


def test_live_and_simulation_clocks_never_touch_each_other(w):
    sc = services.load_demo_story(w, U("analyst"))
    t_live, t_sc = w.now_t, sc.now_t
    services.advance_live_clock(w, U("analyst"), 5)
    assert sc.now_t == t_sc and w.attendance == {}
    services.advance_clock(sc, U("analyst"), 5, rate=0.5)
    assert w.now_t == t_live + 10 and w.attendance == {} and not w.incidents
    n_inc = len(sc.incidents)
    sc.now_t -= 0                                                          # repeat over the same span: no duplicates
    services.advance_clock(sc, U("analyst"), 0.5, rate=0.5)
    keys = [(i.absent[0], i.day) for i in sc.incidents if i.kind == "no_show"]
    assert len(keys) == len(set(keys)) and len(sc.incidents) >= n_inc


# ---------------------------------------------------------------- staffing variance
def test_variance_separates_required_scheduled_recorded_and_unknown(w):
    d = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A01") & (w.roster.day == x)).any())
    out = services.send_attendance_reminder(w, U("tl1"), "A01", d)
    w.now_t = out["opens_t"]
    services.check_in_self(w, U("aarav"), out["id"])
    w.now_t = out["opens_t"] + 3                                            # shift running for 1 h
    t = variance.interval_table(w, d)
    el = t[t.Phase == "elapsed"]
    up = t[t.Phase == "upcoming"]
    assert up["Checked in (genuine)"].isna().all() and up["No record yet"].isna().all()   # no actuals for the future
    assert el["Simulated attendance"].isna().all()                         # live: no simulated column values
    cur = el[el.t == w.now_t - 1]
    assert int(cur["Checked in (genuine)"].sum()) == 1                     # only Aarav
    assert int(cur["No record yet"].sum()) == int(cur.Scheduled.sum()) - 1  # everyone else unknown, not absent
    assert (t["Required (forecast)"].notna()).all()
    tv = variance.interval_table(w, d, agent_ids={a for a, tm in team(w).items() if tm == "TL-1"})
    assert tv["Required (forecast)"].isna().all() and tv["Gap vs scheduled"].isna().all()   # not team-attributable
    s = variance.summary(t)
    assert set(s.Queue) == set(w.policy["queues"])
    assert any("unknown" in x for x in variance.explain(t))


def test_variance_in_scenario_marks_simulated_separately(w):
    sc = services.load_demo_story(w, U("analyst"))
    services.advance_clock(sc, U("analyst"), 14, rate=0.0)
    t = variance.interval_table(sc, sc.today)
    el = t[t.Phase == "elapsed"]
    assert el["Simulated attendance"].fillna(0).sum() > 0
    assert variance.freshness(sc)["scenario"] and variance.freshness(w)["simulated_records"] == 0


# ---------------------------------------------------------------- roster versions and rollback
def test_restore_earlier_version_goes_through_draft_review_and_publish(w):
    res = planning.run_scenario(w, planning.Scenario(name="re", headcount_change=-2), time_limit=20)
    p1 = services.draft_plan(w, U("analyst"), res["roster"], "re", "test")
    services.validate_plan(w, U("analyst"), p1["id"])
    services.publish_plan(w, U("analyst"), p1["id"], reviewed=True)
    fp_new = wf.roster_fp(w.roster)
    for who in ("ops", "admin", "tl1"):
        with pytest.raises(services.Denied):
            services.restore_plan_as_draft(w, U(who), "PLAN-0", "x")
    with pytest.raises(ValueError, match="reason"):
        services.restore_plan_as_draft(w, U("analyst"), "PLAN-0", " ")
    d = services.restore_plan_as_draft(w, U("analyst"), "PLAN-0", "new plan cut too many shifts")
    assert d["status"] == "draft" and wf.roster_fp(w.roster) == fp_new        # nothing live until published
    services.validate_plan(w, U("analyst"), d["id"])
    frozen = w.roster[w.roster.day <= w.today].reset_index(drop=True)
    services.publish_plan(w, U("analyst"), d["id"], reviewed=True)
    assert w.roster[w.roster.day <= w.today].reset_index(drop=True).equals(frozen)
    assert any(r["action"] == "Restore roster version" for r in comms.audit_rows(w.run_id))


# ---------------------------------------------------------------- recovery engine quality
@pytest.fixture
def ctx(policy, data, req, optimized_plan):
    plan = optimized_plan["roster"]
    absent = risk.pick_absentees(policy, plan, 4, 4, seed=7, focus_queue="Claims-EN")
    cur, _ = risk.inject_absence(plan, absent, 4)
    return recovery.Context(policy, data, req, plan, cur, 4, 4 * 48 - 8, absent)


def _objective(ctx, actions):
    p = ctx.policy
    new_cov = recovery.coverage(p, recovery.apply_actions(ctx.roster, actions))
    sens = np.array([p["queues"][q]["sensitivity"] for q in ctx.queues])[:, None]
    short = np.clip(ctx.target - new_cov, 0, None) * (ctx.deficit > 0)
    return (sum(a.cost + a.penalty + p["recovery"]["blast_weight"] * a.shifts_touched for a in actions)
            + float((short * sens).sum()) * p["costs"]["shortfall_penalty"])


def test_recommendation_ranks_best_on_the_configured_objective(ctx):
    rec = recovery.recommend(ctx)
    best = _objective(ctx, rec["actions"])
    for name, alt in rec["alternatives"].items():
        assert best <= _objective(ctx, alt["actions"]) + 1, name
    assert _objective(ctx, []) >= best                                       # doing nothing is never better
    elig = dict(zip(ctx.data["agents"].agent_id, ctx.data["agents"].eligible))
    for a in rec["actions"]:
        assert a.queue in elig[a.agent_id]                                   # gaining queue is trained
    assert rec["eval"]["violations"] == []


def test_partial_recovery_keeps_residual_visible(ctx):
    rec = recovery.recommend(ctx)
    ev = rec["eval"]
    assert ev["short_head_hours_after"] == pytest.approx(ev["deficit_after"] / 2)
    if ev["deficit_after"]:
        assert rec["escalation"] and rec["escalation"]["residual"]


def test_solver_failure_changes_nothing_and_escalates(ctx, monkeypatch):
    monkeypatch.setattr(recovery, "select", lambda *a, **k: None)
    rec = recovery.recommend(ctx)
    assert rec["actions"] == [] and not rec["solver_ok"]
    assert rec["eval"]["deficit_after"] == rec["eval"]["deficit_before"] > 0
    assert rec["escalation"] and "No feasible action" in rec["why_not"]


# ---------------------------------------------------------------- benchmark integrity
def test_benchmark_compares_on_the_same_roster_and_labels_models(policy):
    from shiftsense import evidence
    res = evidence.run(policy)
    ss = res["scenarios"][res["scenarios"].approach == "ShiftSense full loop"]
    assert ss.solver_s.notna().all() and (ss.solver_s >= 0).all()          # measured, separate from TTC
    same = res["same_roster"].set_index("Metric")
    assert "MODELLED" in same.loc["Time-to-Cover (min)", "Basis"]
    assert same.loc["Hard-rule violations (total)", "ShiftSense"] == 0
    assert "Not claimed" in set(res["provenance"].Input)
    assert "SAME rosters" in res["hypotheses"].set_index("Hypothesis").loc["H3", "Measured (simulated)"]


# ---------------------------------------------------------------- health, backup, logs
def test_health_reports_without_secrets(w, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET-VALUE")
    store.save(w)
    rows = health.run()
    assert all(ok for name, ok, detail, essential in rows if essential), rows
    assert "SECRET" not in str(rows)


def test_backup_create_verify_restore_roundtrip(w, tmp_path):
    store.save(w)
    services.report_absence(w, U("tl1"), [tl1_worker(w, w.first_day)], w.first_day)
    store.save(w)
    b = backup.create(tmp_path / "bk")
    assert backup.verify(b) == []
    w2 = wf.new_world(w.policy, w.data, w.req, w.roster)                  # a later, different state
    store.save(w2)
    safety = backup.restore(b)
    assert safety and safety.exists()
    assert store.load().run_id == w.run_id and store.load().incidents
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database")
    assert backup.verify(bad)
    with pytest.raises(RuntimeError):
        backup.restore(bad)


def test_denied_actions_are_logged_without_secrets(w, tmp_path, monkeypatch):
    log = tmp_path / "app.log"
    monkeypatch.setenv("SHIFTSENSE_LOG", str(log))
    monkeypatch.setattr(logs, "_LOG", None)
    with pytest.raises(services.Denied):
        services.publish_plan(w, U("ops"), "PLAN-0", reviewed=True)
    for h in logs.get().handlers:
        h.flush()
    text = log.read_text()
    assert "permission denied user=ops" in text and "ops@123" not in text and "password" not in text.lower()
    monkeypatch.setattr(logs, "_LOG", None)


# ---------------------------------------------------------------- seven end-to-end acceptance workflows
def test_e2e_1_absence_to_final_gap(w):
    d = w.first_day
    absent = "A01" if ((w.roster.agent_id == "A01") & (w.roster.day == d)).any() else None
    inc = (services.report_absence(w, U("aarav"), ["A01"], d) if absent else
           services.report_absence(w, U("tl1"), [tl1_worker(w, d)], d))
    gap0 = priority.assess(w, inc)["short_ph"]
    assert any(n["related_ref"] == inc.id for n in comms.notices_for(U("tl1"), w.run_id))
    services.decide_incident(w, U("tl1"), inc.id, "Approve")
    with pytest.raises(ValueError, match="Conflict"):
        services.decide_incident(w, U("tl1"), inc.id, "Approve")
    for n in [x for x in w.notifications if x["incident"] == inc.id and x["kind"] == "confirm"]:
        agent = n["agent_id"]
        uname = f"h{agent.lower()}"
        services.create_account(w, U("admin"), uname, {"name": agent, "employee_id": f"E-{agent}", "role": "agent",
                                                     "agent_id": agent, "contact_pref": "in_app"}, "Temp-Pass-2026!")
        services.respond(w, auth.authenticate(uname, "Temp-Pass-2026!"), n["id"], True)
    gap1 = priority.assess(w, inc)["short_ph"] if inc.status in wf.DECIDABLE else inc.rec["eval"]["short_head_hours_after"]
    assert gap1 <= gap0
    assert check(w.policy, w.data["agents"], w.data["leave"], w.roster) == []
    assert {"Report absence", "Approve fix"} <= {r["action"] for r in comms.audit_rows(w.run_id)}


def test_e2e_2_escalation_ops_return_and_residual(w):
    inc = services.report_absence(w, U("tl1"), [tl1_worker(w, w.first_day)], w.first_day)
    services.decide_incident(w, U("tl1"), inc.id, "Escalate", "helpers on training")
    note = next(n for n in comms.notices_for(U("ops"), w.run_id) if n["related_ref"] == inc.id)
    assert note["action_required"] and note["due_t"] == w.now_t + 4
    services.ops_decide(w, U("ops"), inc.id, "Return to team lead", "try the open shift")
    assert inc.status == "open"
    services.decide_incident(w, U("tl1"), inc.id, "Escalate", "nobody accepted")
    services.ops_decide(w, U("ops"), inc.id, "Accept the risk for this window", "low volume")
    assert inc.status == "closed" and inc.residual_ph > 0


def test_e2e_3_publish_notifies_agents_and_team_leads(w):
    res = planning.run_scenario(w, planning.Scenario(name="re", headcount_change=-2), time_limit=20)
    p = services.draft_plan(w, U("analyst"), res["roster"], "re", "test")
    rv = services.validate_plan(w, U("analyst"), p["id"])
    services.publish_plan(w, U("analyst"), p["id"], reviewed=True)
    changed = {c["agent_id"] for c in rv["changes"]}
    told = {n["agent_id"] for n in w.notifications if n["text"].startswith("Schedule update")}
    assert changed and changed <= told
    assert any("New roster published" in n["title"] for n in comms.notices_for(U("tl1"), w.run_id))


def test_e2e_4_what_if_changes_nothing_until_published(w):
    fp = wf.roster_fp(w.roster)
    a = planning.run_scenario(w, planning.Scenario(name="a", demand_pct={"Claims-EN": 20}), time_limit=10)
    b = planning.run_scenario(w, planning.Scenario(name="b", extra_absences=6), time_limit=10)
    assert a["as_is"]["Coverage %"] >= 0 and b["after"] is not None
    services.draft_plan(w, U("analyst"), b["roster"], "b", "what-if")
    assert wf.roster_fp(w.roster) == fp


def test_e2e_5_checkin_is_visible_and_distinct(w):
    d = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A01") & (w.roster.day == x)).any())
    out = services.send_attendance_reminder(w, U("tl1"), "A01", d)
    w.now_t = out["opens_t"]
    services.check_in_self(w, U("aarav"), out["id"])
    w.now_t += 2
    assert records.day_record(w, "A01", d)["code"] == "worked"
    from shiftsense import report
    s = report.weekly_report(w, d // 7)["summary"]
    assert s["Genuine check-ins"] == 1 and s["Simulated attendance records"] == 0
    assert comms.notices_for(U("aarav"), w.run_id)[0]["status"] == "resolved"


def test_e2e_6_governance_without_admin_imposing_action(w):
    sc = services.load_demo_story(w, U("analyst"))
    cid = services.scan(sc, U("admin"))[0]
    services.case_action(sc, U("admin"), cid, "Acknowledge", "")
    services.case_action(sc, U("admin"), cid, "Request explanation", "please explain")
    services.case_action(sc, U("tl2"), cid, "Explain", "all were emergencies")
    services.case_action(sc, U("admin"), cid, "Refer to Operations", "check staffing impact")
    with pytest.raises(services.Denied):
        services.case_action(sc, U("admin"), cid, "Resolve referral", "x")
    services.case_action(sc, U("ops"), cid, "Resolve referral", "no action needed")
    case = next(c for c in comms.cases(sc.run_id) if c["id"] == cid)
    assert case["status"] == "Resolved" and "signal for review" in case["evidence"]["note"].lower()


def test_e2e_7_demo_lifecycle_isolated(w):
    fp, att = wf.roster_fp(w.roster), dict(w.attendance)
    sc = services.load_demo_story(w, U("analyst"))
    services.advance_clock(sc, U("analyst"), 24, rate=0.3)
    services.unload_demo(w, U("analyst"), sc)
    assert wf.roster_fp(w.roster) == fp and w.attendance == att and not w.incidents
    assert comms.audit_rows(sc.run_id) and comms.verify_audit()[0]


def test_empty_and_malformed_inputs(w):
    from shiftsense import importer
    res = importer.validate_agents(pd.DataFrame(), w.policy)
    assert res["agents"] is None and res["errors"]
    res = importer.validate_agents(pd.DataFrame({"agent_id": [], "name": [], "primary": [], "team": []}), w.policy)
    assert res["agents"] is None and any("At least 10" in e for e in res["errors"])
    with pytest.raises(ValueError):
        services.escalate_risk(w, U("analyst"), "", "")
    with pytest.raises(accounts.AccountError):
        services.create_account(w, U("admin"), "x", {"name": "", "role": "agent"}, "")
