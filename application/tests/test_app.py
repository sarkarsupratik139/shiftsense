"""Headless UI tests: sign-in, role screens, contextual cards and the cross-role workflow."""
import html as html_lib
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import ui_common
from shiftsense import auth, history, workflow

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 300
PASSWORDS = {name: rec["demo_password"] for name, rec in auth.config()["users"].items()}
SCREENS = {"views/agent.py": "aarav", "views/team_lead.py": "tl1", "views/planner.py": "analyst",
           "views/ops.py": "ops", "views/admin.py": "admin", "views/demo.py": "analyst"}
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


@pytest.fixture(autouse=True)
def fresh_world():
    ui_common.reset_world()             # the workspace is shared and saved; isolate tests
    yield
    ui_common.reset_world()


def run(script="app.py", user=None, state=None):
    at = AppTest.from_file(str(ROOT / script), default_timeout=TIMEOUT)
    if user:
        at.session_state["user"] = auth.authenticate(user, PASSWORDS[user])
    for k, v in (state or {}).items():
        at.session_state[k] = v
    return at.run()


def click(at, label, nth=0):
    [b for b in at.button if b.label == label][nth].click()
    return at.run()


def text(at) -> str:
    return " ".join(m.value for m in at.markdown) + " " + " ".join(t.value for t in at.title)


def ok(at):
    assert not at.exception, at.exception
    return at


def seg(at, key, value):
    next(g for g in at.get("button_group") if g.key == key).set_value(value)
    return at.run()


# ---------------------------------------------------------------- sign-in & access
def test_signed_out_sees_only_login():
    at = ok(run())
    assert [t.label for t in at.text_input] == ["Username", "Password"]


def test_wrong_password_is_rejected():
    at = run()
    at.text_input[0].set_value("tl1")
    at.text_input[1].set_value("nope")
    at = click(at, "Sign in")
    assert any("Invalid" in e.value for e in at.error)


@pytest.mark.parametrize("user,title", [("aarav", "Hi Aarav"), ("tl1", "Team TL-1"), ("analyst", "Planning"),
                                        ("ops", "Operations"), ("admin", "Administration")])
def test_login_lands_on_own_screen(user, title):
    at = run()
    at.text_input[0].set_value(user)
    at.text_input[1].set_value(PASSWORDS[user])
    at = ok(click(at, "Sign in"))
    assert title in text(at)


@pytest.mark.parametrize("script,user", SCREENS.items())
def test_screen_renders_without_emoji_markers(script, user):
    at = ok(run(script, user))
    assert not any("don't have access" in e.value for e in at.error)
    assert not EMOJI.search(text(at)), EMOJI.findall(text(at))


@pytest.mark.parametrize("script,user", [("views/ops.py", "aarav"), ("views/planner.py", "tl1"),
                                         ("views/agent.py", "admin"), ("views/team_lead.py", "ops"),
                                         ("views/admin.py", "analyst"), ("views/agent.py", None),
                                         ("views/demo.py", "admin"), ("views/demo.py", "ops"),
                                         ("views/admin.py", "demo")])
def test_screen_blocked_for_other_roles(script, user):
    at = run(script, user)
    assert any("don't have access" in e.value for e in at.error)


def test_manual_comparison_only_on_analyst_screen():
    for script, user in SCREENS.items():
        if script.endswith(("planner.py", "demo.py")):
            continue
        assert "manual baseline" not in text(run(script, user)).lower()


# ---------------------------------------------------------------- team lead: alerts workspace
def _absence(team="TL-1", n=1, queue=None, day=None):
    w = ui_common.world()
    day = w.first_day if day is None else day
    ro = w.roster[(w.roster.day == day) & ~w.roster.agent_id.isin(ui_common.DEMO_AGENTS)]
    ro = ro[ro.agent_id.map(ui_common.team_of) == team]
    if queue:
        ro = ro[ro.queue == queue]
    with ui_common.mutate():
        return workflow.report_absence(w, ro.agent_id.tolist()[:n], day, "test")


def test_top_priority_card_opens_and_approves_in_place():
    inc = _absence(n=2)
    if not inc.rec["actions"]:
        pytest.skip("no fix needed")
    at = ok(run("views/team_lead.py", "tl1"))
    assert "Top priorities" in text(at)
    at = ok(click(at, "Review & decide"))
    t = text(at)
    assert "Problem" in t and "Recommended action" in t and "Expected outcome" in t
    at = ok(click(at, "Approve"))
    assert inc.status in ("fixed", "escalated")
    assert any("Approve recorded" in s.value for s in at.success)
    assert "card" not in at.session_state                       # card closed, still on the workspace


def test_reject_needs_reason_then_escalate_goes_to_ops():
    inc = _absence(n=2)
    if not inc.rec["actions"]:
        pytest.skip("no fix")
    at = run("views/team_lead.py", "tl1", {"card": ("incident", inc.id)})
    at = ok(seg(at, f"dc_{inc.id}_c", "Reject"))
    at = ok(click(at, "Reject"))
    assert any("reason is required" in e.value for e in at.error) and inc.status in workflow.DECIDABLE
    at.text_input(key=f"dc_{inc.id}_r").set_value("people on training")
    at = ok(click(at, "Reject"))
    assert inc.status in ("open", "no_gap")                      # another fix proposed, not escalated
    if inc.status == "open":
        at = run("views/team_lead.py", "tl1", {"card": ("incident", inc.id)})
        at = ok(seg(at, f"dc_{inc.id}_c", "Escalate"))
        at.text_input(key=f"dc_{inc.id}_r").set_value("no cover possible")
        at = ok(click(at, "Escalate"))
        assert inc.status == "escalated"
        ok(run("views/ops.py", "ops"))
        ops_user = auth.authenticate("ops", PASSWORDS["ops"])
        assert any(n["related_ref"] == inc.id and n["status"] == "action"
                   for n in ui_common.comms.notices_for(ops_user, ui_common.world().run_id))


def test_other_team_cannot_see_alert():
    inc = _absence()
    tl2 = ok(run("views/team_lead.py", "tl2"))
    assert ui_common.incident_title(inc) not in text(tl2)


def test_decline_brings_next_fix():
    w = ui_common.world()
    day = w.first_day + 1
    who = w.roster[(w.roster.day == day) & (w.roster.queue == "Claims-EN")].agent_id.tolist()[:3]
    with ui_common.mutate():
        inc = workflow.report_absence(w, who, day, "test")
        if inc.rec["actions"]:
            workflow.approve_fix(w, inc, "test")
    asks = [x for x in w.notifications if x["kind"] in ("confirm", "offer")]
    if not asks:
        pytest.skip("no step needed confirmation")
    first = asks[0]
    group = [x for x in asks if x["kind"] == "offer" and x.get("offer") == first.get("offer")] or [first]
    for x in group:
        with ui_common.mutate():
            workflow.respond(w, x["id"], False, x["agent_id"])
    assert inc.status in ("needs_new_fix", "no_gap")


# ---------------------------------------------------------------- agent
def test_agent_reports_absence_and_team_lead_sees_it():
    at = ok(run("views/agent.py", "aarav"))
    at = ok(click(at, "Can't come in"))
    at = ok(click(at, "Report"))
    assert any("team lead is arranging cover" in s.value for s in at.success)
    inc = next(i for i in ui_common.world().incidents if i.absent == ["A01"])
    assert inc.owner_team == "TL-1"
    tl = ok(run("views/team_lead.py", "tl1"))
    assert "Aarav" in text(tl) or inc.status == "no_gap"


def test_agent_time_off_and_team_lead_decline_with_reason():
    w = ui_common.world()
    day = next(d for d in range(w.first_day, w.first_day + 10)
               if ((w.roster.agent_id == "A01") & (w.roster.day == d)).any()
               and workflow.leave_check(w, "A01", d)["status"] != "Green")
    with ui_common.mutate():
        r = workflow.submit_leave(w, "A01", day, "aarav")
    at = ok(run("views/team_lead.py", "tl1", {"card": ("request", r["id"])}))
    at = ok(click(at, "Decline"))
    assert any("required" in e.value for e in at.error)
    at.text_input(key=f"rq_r_{r['id']}").set_value("peak week")
    at = ok(click(at, "Decline"))
    assert r["state"] == "Rejected"
    me = ok(run("views/agent.py", "aarav", {"me_area": "Inbox & requests"}))
    assert "peak week" in text(me)


def test_agent_reviews_offer_with_before_after_and_accepts():
    w = ui_common.world()
    for day in range(w.first_day, w.first_day + 5):
        who = w.roster[(w.roster.day == day) & (w.roster.queue == "Claims-EN") & (w.roster.agent_id != "A01")].agent_id.tolist()[:3]
        with ui_common.mutate():
            inc = workflow.report_absence(w, who, day, "t")
            if inc.rec["actions"]:
                workflow.approve_fix(w, inc, "t")
        if any(n["agent_id"] == "A01" and n["kind"] in ("offer", "confirm") and n["status"] == "new"
               for n in w.notifications):
            break
    n = next((n for n in w.notifications if n["agent_id"] == "A01" and n["kind"] in ("offer", "confirm")
              and n["status"] == "new"), None)
    if n is None:
        pytest.skip("Aarav was not asked")
    at = ok(run("views/agent.py", "aarav", {"card": ("notif", n["id"])}))
    assert "Week hours after" in text(at)
    at = ok(click(at, "Accept"))
    assert n["status"] in ("accepted", "withdrawn", "expired")


# ---------------------------------------------------------------- operations
def test_ops_priorities_and_escalation_decision():
    inc = _absence(n=2)
    with ui_common.mutate():
        workflow.escalate_fix(ui_common.world(), inc, "test", "nobody suitable")
    assert "Not configured" in text(ok(run("views/ops.py", "ops", {"ops_area": "Cost & staffing"})))  # no invented budget
    at = ok(run("views/ops.py", "ops"))
    assert "Decide first" in text(at) and "Ops decision needed" in text(at)   # shown without any click or AI
    at = ok(run("views/ops.py", "ops", {"card": ("incident", inc.id)}))
    at.text_input(key=f"on_{inc.id}").set_value("agency cover booked")
    at = ok(click(at, "Record decision"))
    assert inc.status == "closed"


def test_ops_reports_spike():
    at = ok(run("views/ops.py", "ops", {"card": ("spike", True)}))
    at = ok(click(at, "Raise alert"))
    assert ui_common.world().incidents[-1].kind == "spike"


# ---------------------------------------------------------------- analyst
def test_analyst_areas_render():
    for area in ("Forecast & staffing", "Roster optimization", "What-if", "Review & publish"):
        at = ok(run("views/planner.py", "analyst", {"pl_area": area}))     # as the sidebar link does
    assert "Original plan vs live roster" in text(at)


def test_analyst_scenario_draft_validate_publish():
    w = ui_common.world()
    live = w.roster.copy()
    at = ok(run("views/planner.py", "analyst", {"pl_area": "What-if"}))
    at = ok(click(at, "Run scenario"))
    assert w.roster.equals(live)                                # scenario never touches the live roster
    at = ok(click(at, "Use as draft"))
    draft = next(p for p in w.plans if p["status"] == "draft")
    at = ok(run("views/planner.py", "analyst", {"pl_area": "Review & publish"}))
    at = ok(click(at, "Validate"))
    assert draft["status"] == "validated"
    at.checkbox(key="pub_ok").check()
    at = ok(at.run())
    at = ok(click(at, "Publish"))
    assert draft["status"] == "published"
    assert [p["status"] for p in w.plans].count("published") == 1


# ---------------------------------------------------------------- admin
def test_admin_week_agent_and_change_cards():
    inc = _absence()
    at = ok(run("views/admin.py", "admin"))
    assert "Schedule" in text(at)
    at = ok(run("views/admin.py", "admin", {"card": ("agent", inc.absent[0])}))
    assert "Absent" in " ".join(str(d.value) for d in at.dataframe)
    cid = history.changes(ui_common.world().run_id)[0]["id"]
    at = ok(run("views/admin.py", "admin", {"card": ("change", cid), "ad_area": "Audit & integrity"}))
    assert "Shifts changed" in text(at)


def test_analyst_loads_demo_without_touching_live_roster():
    live = ui_common.live_world()
    before = live.roster.copy()
    at = ok(run("views/demo.py", "analyst", {"card": ("confirm", "story")}))
    assert [b for b in at.button if b.label == "Confirm"][0].disabled
    at.checkbox(key="cf_ok").check()
    at = ok(click(ok(at.run()), "Confirm"))
    sc = ui_common.scenario()
    assert sc is not None and sc.scenario and sc.run_id.startswith("sim-")
    assert ui_common.live_world().roster.equals(before) and ui_common.live_world().incidents == []
    ui_common.set_scenario(None)


def test_analyst_clock_runs_only_in_scenario():
    at = ok(run("views/demo.py", "analyst", {"sys_area": "Clocks"}))
    assert not [b for b in at.button if b.label == "Next 08:00"]          # no scenario: no clock
    live = ui_common.live_world()
    from shiftsense import services
    sc = services.load_demo_story(live, auth.authenticate("analyst", PASSWORDS["analyst"]))
    ui_common.set_scenario(sc)
    t_live, t_sc = live.now_t, sc.now_t
    at = ok(click(ok(run("views/demo.py", "analyst", {"sys_area": "Clocks"})), "Next 08:00"))
    assert ui_common.scenario().now_t > t_sc and ui_common.live_world().now_t == t_live
    assert "Shifts started (simulated)" in text(at)
    ui_common.set_scenario(None)


def test_admin_is_oversight_only():
    forbidden = {"Approve", "Reset", "Load demo story", "+1 h", "Next 08:00", "Build roster and publish", "Publish",
                 "Record decision", "Report absence", "Raise alert"}
    for area in ("Overview", "Accounts & access", "Governance & decisions", "Audit & integrity", "System status"):
        at = ok(run("views/admin.py", "admin", {"ad_area": area}))
        assert not forbidden & {b.label for b in at.button}, area


def test_admin_audit_and_integrity():
    _absence()
    at = ok(run("views/admin.py", "admin", {"ad_area": "Audit & integrity"}))
    assert "Audit chain verified" in text(at) and "Live roster rules" in text(at)


def test_governance_case_through_ui():
    w = ui_common.world()
    workflow.load_story(w)
    at = ok(run("views/admin.py", "admin", {"ad_area": "Governance & decisions"}))
    at = ok(click(at, "Scan for discrepancies"))
    cs = ui_common.comms.cases(w.run_id)
    assert cs and all(c["status"] == "Flagged" for c in cs)
    cid = next(c["id"] for c in cs if c["subject_user"] == "tl2")
    at = ok(run("views/admin.py", "admin", {"card": ("case", cid), "ad_area": "Governance & decisions"}))
    assert "Triggered by" in " ".join(d.value.to_string() for d in at.dataframe)
    assert any("not a finding" in c.value for c in at.caption)
    at = ok(click(at, "Submit"))                                   # Acknowledge (first option)
    assert ui_common.comms.cases(w.run_id)[0]["status"] in ("Under Review", "Flagged")
    assert next(c for c in ui_common.comms.cases(w.run_id) if c["id"] == cid)["status"] == "Under Review"


def test_notification_reaches_team_lead_and_opens_record():
    at = ok(run("views/agent.py", "aarav"))
    at = ok(click(at, "Can't come in"))
    at = ok(click(at, "Report"))
    tl = ok(run("views/team_lead.py", "tl1"))
    tl_user = auth.authenticate("tl1", PASSWORDS["tl1"])
    assert any(n["action_required"] for n in ui_common.comms.notices_for(tl_user, ui_common.world().run_id))
    assert not ui_common.comms.notices_for(auth.authenticate("tl2", PASSWORDS["tl2"]), ui_common.world().run_id)
    opens = [b for b in tl.button if b.label == "Open"]
    if opens:
        opens[0].click()
        tl = ok(tl.run())
        assert "card" in tl.session_state


def test_agent_messages_team_lead():
    at = ok(run("views/agent.py", "aarav", {"card": ("thread", "agent:A01")}))
    at.text_area(key="tc_agent:A01_body").set_value("Can I start later on Friday?")
    at = ok(click(at, "Send message"))
    assert ui_common.comms.thread(ui_common.world().run_id, "agent:A01")
    tl = ok(run("views/team_lead.py", "tl1", {"card": ("thread", "agent:A01")}))
    assert "Can I start later" in text(tl)


# ---------------------------------------------------------------- accessibility
def test_status_colours_meet_wcag_aa_contrast_in_both_themes():
    import tomllib
    import ui_kit
    for m in ("light", "dark"):
        t = ui_kit.TOKENS[m]
        for name, (fg, bg) in ui_kit.status_colors(m).items():
            assert ui_kit.contrast(fg, bg) >= 4.5, (m, name)
            assert ui_kit.contrast(fg, t["paper"]) >= 4.5, (m, name, "on paper")
        for k in ("ink", "muted", "accent"):
            assert ui_kit.contrast(t[k], t["paper"]) >= 4.5 and ui_kit.contrast(t[k], t["bg"]) >= 4.5, (m, k)
    cfg = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text())["theme"]
    for m in ("light", "dark"):
        prim = cfg[m]["primaryColor"]
        assert ui_kit.contrast("#ffffff", prim) >= 4.5, m                    # primary button text
        assert ui_kit.contrast(prim, cfg[m]["backgroundColor"]) >= 3, m      # button against the page (non-text)
        assert cfg[m]["textColor"] == ui_kit.TOKENS[m]["ink"]                # one source of truth


@pytest.mark.parametrize("script,user", SCREENS.items())
def test_buttons_have_labels_and_charts_have_text(script, user):
    at = ok(run(script, user))
    assert all(b.label.strip() for b in at.button)
    if at.get("plotly_chart"):
        assert 'role="note"' in " ".join(m.value for m in at.markdown)


def test_workspace_survives_restart():
    inc = _absence()
    ui_common.live_world.clear()
    assert ui_common.world().incidents[-1].absent == inc.absent


# ---------------------------------------------------------------- integrity / account / attendance screens
def test_admin_week_separates_recorded_from_forecast():
    at = ok(run("views/admin.py", "admin"))
    t = text(at)
    assert "FORECAST / PREDICTION — NOT ACTUAL RESULTS" in t and "Recorded so far" in t
    assert "Worked +OT" not in t


def test_admin_people_area_and_account_form():
    at = ok(run("views/admin.py", "admin", {"ad_area": "Accounts & access"}))
    assert "Role permissions · read-only" in text(at)
    at = ok(run("views/admin.py", "admin", {"ad_area": "Accounts & access", "card": ("account", "")}))
    assert any(b.label == "Create account" for b in at.button) or at.get("form")
    at = ok(run("views/admin.py", "admin", {"ad_area": "Accounts & access", "card": ("account", "aarav")}))
    assert "Version" in " ".join(c.value for c in at.caption)


def test_admin_change_history_has_status_and_explanation():
    _absence()
    at = ok(run("views/admin.py", "admin", {"ad_area": "Audit & integrity"}))
    assert any("A logbook of every change" in c.value for c in at.caption)


def test_team_lead_schedule_card_sends_in_app_reminder():
    w = ui_common.world()
    at = ok(run("views/team_lead.py", "tl1", {"card": ("schedule", True)}))
    assert len(at.selectbox(key="rem_pick").options) > 1
    assert any("not configured" in c.value for c in at.caption)           # email status is stated, not implied
    pick = next(x for x in [(a, d) for d in (w.first_day, w.today) for a in ("A01", "A06")]
                if ui_common.shift_row(*x) is not None)
    at = ok(run("views/team_lead.py", "tl1", {"card": ("schedule", True), "rem_pick": pick}))
    at = ok(click(at, "Send reminder"))
    assert "In inbox" in text(at) and "SMS" not in text(at)
    assert any("in inbox ≠ opened ≠ checked in" in c.value for c in at.caption)


def test_agent_opens_reminder_from_notification_and_checks_in_once():
    from shiftsense import comms, services, auth as _a
    w = ui_common.world()
    d = next(d for d in range(w.first_day, 14) if ui_common.shift_row("A01", d) is not None)
    out = services.send_attendance_reminder(w, _a.authenticate("tl1", PASSWORDS["tl1"]), "A01", d)
    assert ("A01", d) not in w.attendance
    with ui_common.mutate():
        w.now_t = out["opens_t"]
    at = ok(run("views/agent.py", "aarav", {"card": ("checkin", str(out["id"]))}))
    assert comms.related_notice(w.run_id, "checkin", out["id"])["read_by"] == ["aarav"]     # opened, recorded apart
    assert ("A01", d) not in ui_common.world().attendance                  # opening is not checking in
    at = ok(click(at, "Check in now"))
    assert ui_common.world().attendance[("A01", d)] == "checkin"
    with pytest.raises(ValueError, match="[Aa]lready"):
        services.check_in_self(ui_common.world(), _a.authenticate("aarav", PASSWORDS["aarav"]), out["id"])


def test_old_sms_link_no_longer_checks_anyone_in():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT)
    at.query_params["checkin"] = "abcd1234abcd1234"
    at = ok(at.run())
    assert not [b for b in at.button if b.label == "Check in now"]
    assert any(b.label == "Sign in" for b in at.button)


def test_demo_areas_render_and_story_explains_itself():
    for area in ("Demo scenario", "Clocks"):
        ok(run("views/demo.py", "analyst", {"sys_area": area}))
    at = ok(run("views/demo.py", "analyst", {"sys_area": "Demo scenario"}))
    assert "separate sandbox" in text(at)


def test_live_clock_ui_moves_live_time_only():
    live = ui_common.live_world()
    t = live.now_t
    at = ok(run("views/demo.py", "analyst", {"sys_area": "Clocks"}))
    at = ok(click(at, "+1 day"))
    assert ui_common.live_world().now_t == t + 48 and ui_common.live_world().attendance == {}
    assert ui_common.live_world().incidents == []


def test_team_schedule_never_calls_a_started_shift_upcoming_and_hides_closed_checkins():
    w = ui_common.live_world()
    at = ok(run("views/team_lead.py", "tl1", {"card": ("schedule", True)}))
    opts = [o for o in at.selectbox(key="rem_pick").options if o != "Choose…"]
    assert opts and not any(ui_common.dlabel(w.today) in o and "Morning" in o for o in opts)   # ended this morning
    grid = at.dataframe[0].value
    started = [a for a in ui_common.team_members("TL-1") if ui_common.shift_row(a, w.today) is not None
               and ui_common.shift_row(a, w.today)["shift"] in ("Morning", "Day")]
    names = {ui_common.name(a) for a in started}
    assert started and set(grid[grid.Agent.isin(names)].Today) <= {"Started · no record", "Absent", "Leave"}


# ---------------------------------------------------------------- history (all five roles) and new cards
@pytest.mark.parametrize("script,user,state", [
    ("views/agent.py", "aarav", {"me_area": "My history"}), ("views/team_lead.py", "tl1", {"tl_area": "Team information"}),
    ("views/ops.py", "ops", {"ops_area": "Follow-ups, data & history"}),
    ("views/planner.py", "analyst", {"pl_area": "Benchmarks & history"}), ("views/admin.py", "admin", {"ad_area": "Audit & integrity"})])
def test_every_role_has_a_scoped_history_view(script, user, state):
    _absence()
    at = ok(run(script, user, state))
    t = text(at) + " ".join(c.value for c in at.caption) + " ".join(i.value for i in at.info)
    assert "only stored records are shown" in t
    assert "event(s) · page" in t or "No recorded events in your scope yet." in t


def test_team_lead_correction_card_and_agent_discrepancy_card_render():
    at = ok(run("views/agent.py", "aarav", {"card": ("disc", True)}))
    assert "Send to team lead" in {b.label for b in at.button}
    at = ok(run("views/team_lead.py", "tl1", {"tl_area": "Team information"}))
    assert "Team history" in text(at)


def test_ops_decide_first_opens_the_alert_in_one_click():
    inc = _absence(n=2)
    at = ok(run("views/ops.py", "ops"))
    btn = next(b for b in at.button if b.key == f"prio_{inc.id}")
    at = ok(btn.click().run())
    assert at.session_state["card"] == ("incident", inc.id)


def test_pilot_mode_sign_in_hides_demo_accounts(monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_ENV", "pilot")
    at = ok(AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT).run())
    assert not at.expander and not at.dataframe
    assert any("administrator" in c.value for c in at.caption)
    monkeypatch.delenv("SHIFTSENSE_ENV")
    at = ok(AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT).run())
    assert at.expander and at.expander[0].label == "Demo accounts"


# ---------------------------------------------------------------- data hub
@pytest.mark.parametrize("area", ["Data overview", "Add or update data", "Data changes", "Situations", "Downloads"])
def test_data_hub_areas_render(area):
    at = ok(run("views/data.py", "analyst", {"data_area": area}))
    assert f"Data hub · {area}" in text(at)


HR_AREAS = ["People overview", "Employees", "Leave & balances", "Attendance", "Reviews & approvals",
            "Restricted cases", "HR policies", "Reports & exports"]


@pytest.mark.parametrize("who", ["hr", "hrlead"])
@pytest.mark.parametrize("area", HR_AREAS)
def test_hr_areas_render(area, who):
    at = ok(run("views/hr.py", who, {"hr_area": area}))
    assert html_lib.unescape(f"People data › <b aria-current=\"page\">{area}") in html_lib.unescape(text(at))
    if who == "hrlead":
        assert "Senior HR" in text(at)


def test_hr_policy_shows_pending_register_and_no_retention_periods():
    at = ok(run("views/hr.py", "hr", {"hr_area": "HR policies"}))
    t = text(at) + " ".join(c.value for c in at.caption)
    assert "Pending decisions register" in t and "nothing is deleted automatically" in t
    assert "absence.flags_enabled" in t and "not a decision to keep records indefinitely" in t


def test_data_hub_is_analyst_only():
    for who in ("aarav", "tl1", "ops", "admin", "hr"):
        at = run("views/data.py", who)
        assert any("don't have access" in e.value for e in at.error)


def test_data_hub_load_base_pack_then_paste_leave_and_submit(tmp_path, monkeypatch):
    from shiftsense import refdata
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "dh.db"))
    ui_common.reset_world()
    at = ok(run("views/data.py", "analyst", {"card": ("dh_confirm", "load")}))
    at.checkbox(key="dh_cf_ok").check()
    at = ok(click(ok(at.run()), "Confirm"))
    assert refdata.counts()["employees"] == 50                      # small pack by default
    at = ok(run("views/hr.py", "hr", {"hr_area": "Employees", "hr_emp_tab": "Update data", "dh_dom": "leave", "dh_how": "Paste"}))
    at.text_area(key="dh_txt_leave").set_value("Emp ID\tLeave from\tTill\nA03\t15/10/2026\t16/10/2026")
    at = ok(click(ok(at.run()), "Read and check"))
    assert "1 ready" in text(at)
    at = ok(click(at, "Submit and apply"))
    assert any(r["employee_id"] == "A03" and r["date"] == "2026-10-15" for r in refdata.current("leave").values())


def test_quick_situation_report_fills_the_form(tmp_path, monkeypatch):
    at = ok(run("views/data.py", "analyst", {"data_area": "Situations"}))
    at.text_area(key="dh_qr_qr").set_value("Planned maintenance on Claims-EN from 01:00, 40% capacity lost")
    at = ok(click(ok(at.run()), "Read it"))
    assert at.session_state["dh_qr_f_type"] == "planned_outage"
    assert at.session_state["dh_qr_f_capacity_lost_pct"] == "40"
    assert any("Read from your text" in c.value for c in at.caption)



@pytest.mark.parametrize("script", ["views/team_lead.py", "views/ops.py", "views/planner.py", "views/data.py",
                                    "views/admin.py", "views/agent.py"])
def test_hr_cannot_open_other_roles_screens(script):
    at = run(script, "hr")
    assert any("don't have access" in e.value for e in at.error)


def test_team_lead_cannot_open_hr_screen():
    at = run("views/hr.py", "tl1")
    assert any("don't have access" in e.value for e in at.error)


def test_sick_leave_ui_flow_hr_review_then_team_lead_decides():
    w = ui_common.world()
    at = ok(run("views/agent.py", "aarav", {"card": ("timeoff", True)}))
    at.selectbox(key="to_type").set_value("sick")
    at = ok(at.run())
    assert any("stay with HR" in c.value for c in at.caption)
    at.text_input(key="to_note").set_value("fever and doctor visit")
    at = ok(click(at, "Send"))
    r = next(x for x in ui_common.world().requests if x["agent_id"] == "A01" and x.get("leave_type") == "sick")
    assert r["state"] == "Pending"                                  # unverified balance -> HR review first
    tl = ok(run("views/team_lead.py", "tl1", {"card": ("request", r["id"])}))
    shown = text(tl) + " ".join(c.value for c in tl.caption)
    assert "fever" not in shown and "details held by HR" in shown
    tl = ok(click(tl, "Approve"))
    assert any("HR review is in progress" in e.value for e in tl.error)
    hr_at = ok(run("views/hr.py", "hr", {"hr_area": "Reviews & approvals"}))
    t = next(x for x in __import__("shiftsense.hr", fromlist=["x"]).review_tasks(ui_common.world().run_id, r["id"]))
    hr_at.text_input(key=f"rv_note_{t['id']}").set_value("fit note seen")
    hr_at = ok(click(hr_at, "Send"))
    tl = ok(run("views/team_lead.py", "tl1", {"card": ("request", r["id"])}))
    assert "HR recommends" in " ".join(c.value for c in tl.caption)


def test_admin_can_grant_senior_hr_from_account_card():
    at = ok(run("views/admin.py", "admin", {"ad_area": "Accounts & access", "card": ("account", "hr")}))
    at.text_input(key="shr_why_hr").set_value("covering for Anita")
    at = ok(click(at, "Grant Senior HR"))
    from shiftsense import accounts
    assert "senior_hr" in accounts.get("hr")["extra_permissions"]
    at = ok(run("views/admin.py", "admin", {"ad_area": "Accounts & access", "card": ("account", "hr")}))
    at.text_input(key="shr_why_hr").set_value("cover ended")
    ok(click(at, "Remove Senior HR"))
    assert "senior_hr" not in accounts.get("hr")["extra_permissions"]


def test_temporary_password_must_be_changed_before_anything_else():
    at = run(user="aarav")
    at.session_state["user"] = {**at.session_state["user"], "must_change": True}
    at = ok(at.run())
    assert any("Choose your password" == t.value for t in at.title)


def test_idle_session_is_signed_out():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT)
    at.session_state["user"] = auth.authenticate("aarav", PASSWORDS["aarav"])
    at.session_state["last_seen"] = "2000-01-01T00:00:00"
    at = ok(at.run())
    assert [t.label for t in at.text_input] == ["Username", "Password"]
    assert any("without activity" in i.value for i in at.info)
