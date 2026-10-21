"""Six-role permission matrix (every protected server action x every role, called directly - no UI) and
rollback of publish / import when something fails part-way."""
import pandas as pd
import pytest

from shiftsense import accounts, auth, comms, planning, services, staffing, store
from shiftsense import workflow as wf

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}
ROLES = {"admin": "admin", "agent": "aarav", "team_lead": "tl1", "ops": "ops", "analyst": "analyst", "hr": "hr"}


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "m.db"))
    monkeypatch.delenv("SHIFTSENSE_EMAIL", raising=False)
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def U(name):
    return auth.authenticate(name, PW[name])


def _tl1_agent(w, day):
    t = dict(zip(w.data["agents"].agent_id, w.data["agents"].team))
    return next(a for a in w.roster[w.roster.day == day].agent_id if t[a] == "TL-1" and a not in ("A01", "A06"))


def _setup(w):
    inc = services.report_absence(w, U("tl1"), [_tl1_agent(w, w.first_day)], w.first_day)
    d = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A01") & (w.roster.day == x)).any()
             and wf.leave_check(w, "A01", x)["status"] != "Green")
    req = services.submit_leave(w, U("aarav"), d)
    esc = services.report_absence(w, U("tl1"), [_tl1_agent(w, w.first_day + 1)], w.first_day + 1)
    services.decide_incident(w, U("tl1"), esc.id, "Escalate", "no one free")
    risk = services.escalate_risk(w, U("analyst"), "setup risk", "evidence")
    from shiftsense import refdata
    services.load_base_pack(w, U("analyst"))                          # people data for the HR / team-lead actions
    emps = refdata.current("employees")
    t = dict(zip(w.data["agents"].agent_id, w.data["agents"].team))
    own = emps[next(a for a in emps if t.get(a) == "TL-1" and a not in ("A01", "A06"))]
    other = emps[next(a for a in emps if t.get(a) == "TL-2")]
    proposal = services.submit_change(w, U("tl1"), "employees", [{**own, "night_ok": not own.get("night_ok", True)}],
                                      reason="setup proposal")
    from shiftsense import hr
    case = services.open_hr_case(w, U("hrlead"), "Wellbeing", "restricted", "A07", "support", "hr")
    task = hr.review_tasks(w.run_id, req["id"])[0]["id"]
    return {"inc": inc, "req": req, "esc": esc, "risk": risk, "day": d, "own": own, "other": other,
            "proposal": proposal["id"], "case": case["id"], "task": task}


# action -> (callable(w, user, ctx), roles allowed)
ACTIONS = {
    "report absence (TL-1 agent)": (lambda w, u, c: services.report_absence(w, u, [_tl1_agent(w, w.first_day + 2)], w.first_day + 2), {"team_lead"}),
    "decide alert": (lambda w, u, c: services.decide_incident(w, u, c["inc"].id, "Approve"), {"team_lead"}),
    "decide request": (lambda w, u, c: services.decide_request(w, u, c["req"]["id"], False, "short"), {"team_lead", "ops"}),
    "send attendance reminder": (lambda w, u, c: services.send_attendance_reminder(w, u, "A01", c["day"]), {"team_lead"}),
    "ops decision on escalation": (lambda w, u, c: services.ops_decide(w, u, c["esc"].id, "Return to team lead", "retry"), {"ops"}),
    "assign follow-up": (lambda w, u, c: services.assign_followup(w, u, "TL-1", "call back", 2), {"ops"}),
    "report call spike": (lambda w, u, c: services.report_spike(w, u, "Claims-EN", 30, 3), {"ops"}),
    "answer Analyst risk": (lambda w, u, c: services.answer_risk(w, u, c["risk"], "Decision recorded", "ok"), {"ops"}),
    "flag risk to Ops": (lambda w, u, c: services.escalate_risk(w, u, "matrix risk", "evidence"), {"analyst"}),
    "create draft": (lambda w, u, c: services.draft_plan(w, u, w.roster, "m", "matrix"), {"analyst"}),
    "publish plan": (lambda w, u, c: services.publish_plan(w, u, "PLAN-0", reviewed=True), {"analyst"}),
    "load demo story": (lambda w, u, c: services.load_demo_story(w, u), {"analyst"}),
    "advance live clock": (lambda w, u, c: services.advance_live_clock(w, u, 0.5), {"analyst"}),
    "commit import": (lambda w, u, c: services.commit_import(w, u, None, None, {}, {}), {"analyst"}),
    "legacy cleanup (dry run)": (lambda w, u, c: services.cleanup_legacy_story(w, u), {"analyst"}),
    "governance scan": (lambda w, u, c: services.scan(w, u), {"admin"}),
    "create account": (lambda w, u, c: services.create_account(w, u, "zz", {"role": "analyst"}, "x"), {"admin"}),
    "request time off": (lambda w, u, c: services.submit_leave(w, u, c["day"] + 0), {"agent"}),
    "check in (own reminder)": (lambda w, u, c: services.check_in_self(w, u, 999999), {"agent"}),
    "message on alert thread": (lambda w, u, c: services.post_message(w, u, f"incident:{c['inc'].id}", "hi"), {"team_lead", "ops"}),
    "read Aarav's request": (lambda w, u, c: services.get_request(w, u, c["req"]["id"]), {"agent", "team_lead", "ops", "analyst", "admin", "hr"}),
    # ---- attendance and team operations (Team Lead) vs people data (HR)
    "confirm attendance (TL-1 agent)": (lambda w, u, c: services.correct_attendance(w, u, _tl1_agent(w, w.first_day), w.first_day, "seen"), {"team_lead"}),
    "message an agent's private thread": (lambda w, u, c: services.post_message(w, u, "agent:A01", "hi"), {"agent", "team_lead"}),
    "propose own-team agent change": (lambda w, u, c: services.submit_change(w, u, "employees", [{**c["own"], "secondary": []}], reason="x"), {"team_lead", "hr"}),
    "change another team's employee": (lambda w, u, c: services.submit_change(w, u, "employees", [{**c["other"], "secondary": []}], reason="x"), {"hr"}),
    "change a contract": (lambda w, u, c: services.submit_change(w, u, "contracts", [{"name": "Full-time 40h", "contracted_hours_per_week": 40, "contracted_shifts_per_week": 5, "max_nights_per_week": 3}], reason="x"), {"hr"}),
    "record HR leave file": (lambda w, u, c: services.submit_change(w, u, "leave", [{"employee_id": "A03", "date": "2026-10-15"}], reason="x"), {"hr"}),
    "decide a team-lead proposal": (lambda w, u, c: services.decide_change(w, u, c["proposal"], False, "not now"), {"hr"}),
    "report a team situation (training)": (lambda w, u, c: services.report_situation(w, u, {"type": "training", "title": "t", "start": "2026-10-08 09:00", "end": "2026-10-08 11:00", "teams": [u.get("team") or "TL-1"]}), {"team_lead", "analyst"}),
    "load base data": (lambda w, u, c: services.load_base_pack(w, u, replace=True), {"analyst"}),
    # ---- HR decision record (HR-01..HR-08): server-side, not navigation
    "record a separation": (lambda w, u, c: services.record_separation(w, u, "A09", "2026-10-30", "Resignation"), {"hr"}),
    "run due separations": (lambda w, u, c: services.run_separations(w, u), {"hr"}),
    "enter opening leave balance": (lambda w, u, c: services.enter_leave_balance(w, u, "A09", "annual", 5, "2026-10-01", "HRIS"), {"hr"}),
    "adjust a leave balance": (lambda w, u, c: services.adjust_leave_balance(w, u, "A09", "annual", 1, "carry-over"), {"hr"}),
    "recommend on leave review": (lambda w, u, c: services.recommend_on_review(w, u, c["task"], "approve", "ok"), {"hr"}),
    "list leave review tasks": (lambda w, u, c: services.review_tasks(w, u), {"hr"}),
    "list attendance corrections": (lambda w, u, c: services.attendance_corrections(w, u), {"hr"}),
    "see absence-pattern flags": (lambda w, u, c: services.absence_flags(w, u), {"hr"}),
    "see overtime approval": (lambda w, u, c: services.overtime_view(w, u), {"hr"}),
    "change HR policy (Senior HR only)": (lambda w, u, c: services.set_hr_policy(w, u, "leave.sick_days", 3, "approved", "ref"), set()),
    "grant Senior HR": (lambda w, u, c: services.set_senior_hr(w, u, "hr", True, "cover"), {"admin"}),
    "open a restricted HR case": (lambda w, u, c: services.open_hr_case(w, u, "Other", "restricted", "A08", "s", "hr"), {"hr"}),
    "view a restricted HR case (assigned)": (lambda w, u, c: services.view_hr_case(w, u, c["case"]), {"hr"}),
    "list restricted HR cases": (lambda w, u, c: services.hr_cases(w, u), {"hr"}),
    "see another agent's leave balance": (lambda w, u, c: services.leave_balance(w, u, "A09", "annual"), {"hr"}),
    "reassign a leave review": (lambda w, u, c: services.reassign_review(w, u, c["task"], "hrlead", "cover"), {"hr"}),
    "download HR records": (lambda w, u, c: services.hr_records_workbook(w, u), {"hr"}),
    "import leave balances": (lambda w, u, c: services.import_leave_balances(w, u, b"Emp ID,Leave type,Balance,As of\nA09,Annual,3,01/10/2026\n", "f.csv"), {"hr"}),
    # ---- sign-in administration and email check (Admin)
    "reset someone's password": (lambda w, u, c: services.reset_password(w, u, "priya", "Temp-Reset-2026!", "forgot"), {"admin"}),
    "unlock an account": (lambda w, u, c: services.unlock_account(w, u, "priya", "called"), {"admin"}),
    "send a test email": (lambda w, u, c: services.send_test_email(w, u, "x@example.test"), {"admin"}),
}


@pytest.mark.parametrize("action", list(ACTIONS))
@pytest.mark.parametrize("role", list(ROLES))
def test_permission_matrix(w, role, action):
    fn, allowed = ACTIONS[action]
    ctx = _setup(w)
    user = U(ROLES[role])
    if role in allowed:
        try:
            fn(w, user, ctx)
        except services.Denied as e:                      # authorised roles must never be refused for permission
            pytest.fail(f"{role} wrongly denied '{action}': {e}")
        except Exception:                                 # a business-rule refusal (bad input/state) is fine here
            pass
    else:
        with pytest.raises(services.Denied):
            fn(w, user, ctx)


def test_forged_identity_cannot_reach_another_role(w):
    ctx = _setup(w)
    for fake in (dict(U("aarav"), role="ops_head"), dict(U("ops"), role="analyst"), dict(U("tl1"), team="TL-2"),
                 dict(U("analyst"), username="admin"), dict(U("hr"), role="team_lead", team="TL-1"),
                 dict(U("tl1"), role="hr")):
        for fn, _ in ACTIONS.values():
            try:
                fn(w, fake, ctx)
            except services.Denied:
                continue
            except Exception:
                continue
            # anything that succeeded must be allowed for the account's REAL stored role
            real = accounts.get(fake["username"])
            assert real and real["role"] == fake["role"], f"forged {fake['role']} succeeded"


# ---------------------------------------------------------------- rollback
def test_publish_failure_part_way_rolls_back_everything(w, monkeypatch):
    res = planning.run_scenario(w, planning.Scenario(name="re"), time_limit=20)
    plan = services.draft_plan(w, U("analyst"), res["roster"], "re", "test")
    services.validate_plan(w, U("analyst"), plan["id"])
    fp, plans = wf.roster_fp(w.roster), [(p["id"], p["status"]) for p in w.plans]
    inbox = len(w.notifications)

    def boom(*a, **k):
        raise RuntimeError("network dropped while notifying team leads")
    real_note = services._note
    monkeypatch.setattr(services, "_note", boom)
    with pytest.raises(RuntimeError):
        with wf.transaction(w):
            services.publish_plan(w, U("analyst"), plan["id"], reviewed=True)
    assert wf.roster_fp(w.roster) == fp                                   # roster back
    assert [(p["id"], p["status"]) for p in w.plans] == plans             # draft still validated, PLAN-0 still live
    assert len(w.notifications) == inbox                                  # no agent told about a change that didn't happen
    monkeypatch.setattr(services, "_note", real_note)
    services.publish_plan(w, U("analyst"), plan["id"], reviewed=True)     # retry works once
    assert [p["status"] for p in w.plans if p["id"] == plan["id"]] == ["published"]
    assert [p["status"] for p in w.plans if p["id"] == "PLAN-0"] == ["superseded"]
    with pytest.raises(ValueError):
        services.publish_plan(w, U("analyst"), plan["id"], reviewed=True)  # and a second retry is refused


def test_import_failures_never_touch_the_live_workspace(w, data, req, monkeypatch):
    from shiftsense import importer, optimizer
    store.save(w)
    agents = importer.validate_agents(importer.template(data["agents"]), w.policy)["agents"]
    base = {"data": data, "req": req, "fc": None}
    monkeypatch.setattr(optimizer, "plan", lambda *a, **k: {"roster": pd.DataFrame(), "status": "UNKNOWN"})
    with pytest.raises(ValueError, match="Nothing was changed"):
        services.commit_import(w, U("analyst"), agents, pd.DataFrame(columns=["agent_id", "day"]), base, {"files": "x"})
    assert store.load().run_id == w.run_id
    assert any(r["action"] == "Import failed" for r in comms.audit_rows(w.run_id))


def test_interrupted_live_swap_keeps_the_old_workspace(w, monkeypatch):
    """ui_common.reset_world (used by import) is one atomic write: if it fails, the old workspace stays saved
    AND in memory - there is no moment with an empty or half-written live workspace."""
    import ui_common
    store.save(w)
    ui_common.live_world.clear()
    old = ui_common.live_world().run_id
    new = wf.new_world(w.policy, w.data, w.req, w.roster)

    def failing(world, path=None, slot="live"):
        raise OSError("disk full")
    real = store.save
    monkeypatch.setattr(store, "save", failing)
    with pytest.raises(OSError):
        ui_common.reset_world(new, event="Roster imported")
    monkeypatch.setattr(store, "save", real)
    assert ui_common.live_world().run_id == old
    ui_common.live_world.clear()
    assert ui_common.live_world().run_id == old                          # also after a "restart"
    ui_common.live_world.clear()
