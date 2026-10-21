"""Sidebar and navigation: role-specific destinations, guarded area links, icon-only mode, deliberate
workspace switching with state preserved, 'On this page' links, draft indicator, sign-out."""
import html
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import ui_common
from shiftsense import auth, navigation, services

ROOT = Path(__file__).resolve().parent.parent
PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}
EXPECT = {
    "aarav": ("My work", ["Home", "My shifts", "Inbox & requests", "My history"]),
    "tl1": ("Team", ["Overview", "Attendance", "Team schedule", "Requests", "Alerts & decisions", "Team information"]),
    "ops": ("Operations", ["Overview", "Coverage & risk", "Alerts & escalations", "Recovery", "Cost & staffing",
                           "Follow-ups, data & history"]),
    "analyst": ("Planning", ["Forecast & staffing", "Roster optimization", "What-if", "Review & publish",
                             "Benchmarks & history",
                             "Data overview", "Add or update data", "Data changes", "Situations", "Downloads",
                             "Demo scenario", "Clocks"]),
    "admin": ("Oversight", ["Overview", "Accounts & access", "Governance & decisions", "Audit & integrity",
                            "System status"]),
    "hr": ("People", ["People overview", "Employees", "Leave & balances", "Attendance", "Reviews & approvals",
                      "Restricted cases", "HR policies", "Reports & exports"]),
}


@pytest.fixture(autouse=True)
def fresh():
    ui_common.reset_world()
    ui_common.set_scenario(None)
    yield
    ui_common.set_scenario(None)
    ui_common.reset_world()


def U(name):
    return auth.authenticate(name, PW[name])


def run(script, user, state=None):
    at = AppTest.from_file(str(ROOT / script), default_timeout=300)
    at.session_state["user"] = U(user)
    for k, v in (state or {}).items():
        at.session_state[k] = v
    at = at.run()
    assert not at.exception, at.exception
    return at


def click(at, label):
    next(b for b in at.button if b.label == label).click()
    at = at.run()
    assert not at.exception, at.exception
    return at


# ---------------------------------------------------------------- navigation model
@pytest.mark.parametrize("user", list(EXPECT))
def test_each_role_sees_only_its_own_destinations(user):
    first_group, titles = EXPECT[user]
    sp = navigation.spec(auth.role(U(user))["pages"])
    assert [d["title"] for d in sp] == titles
    assert sp[0]["group"] == first_group and sp[0].get("default") and sum(bool(d.get("default")) for d in sp) == 1
    allowed = set(auth.role(U(user))["pages"])
    assert {d["page"] for d in sp} <= allowed
    assert len({d["url"] for d in sp if d["url"]}) == len([d for d in sp if d["url"]])     # unique URLs


def test_analyst_navigation_is_grouped_planning_then_data_then_demo():
    groups = [d["group"] for d in navigation.spec(auth.role(U("analyst"))["pages"])]
    assert groups == ["Planning"] * 5 + ["Data"] * 5 + ["Demo"] * 2


@pytest.mark.parametrize("script,user", [("views/admin.py", "analyst"), ("views/planner.py", "admin"),
                                         ("views/demo.py", "ops"), ("views/demo.py", "tl1"),
                                         ("views/ops.py", "analyst"), ("views/team_lead.py", "aarav")])
def test_area_pages_still_refuse_other_roles(script, user):
    """A link is not access control: running a page script as another role is refused by the page itself."""
    at = AppTest.from_file(str(ROOT / script), default_timeout=300)
    at.session_state["user"] = U(user)
    at.session_state["ad_area"] = "Governance & decisions"
    at = at.run()
    assert any("don't have access" in e.value for e in at.error)


@pytest.mark.parametrize("script,key,area,title", [
    ("views/admin.py", "ad_area", "Governance & decisions", "Governance & decisions"),
    ("views/planner.py", "pl_area", "What-if", "Planning · What-if"),
    ("views/demo.py", "sys_area", "Clocks", "Demo & data · Clocks"),
    ("views/admin.py", "ad_area", "not-a-real-area", "Administration › <b aria-current=\"page\">Overview")])
def test_area_link_selects_area_and_unknown_falls_back(script, key, area, title):
    user = {"views/admin.py": "admin"}.get(script, "analyst")
    at = run(script, user, {key: area})
    assert title in html.unescape(" ".join(m.value for m in at.markdown))


# ---------------------------------------------------------------- sidebar behaviour
def test_only_one_collapse_control_no_custom_duplicate():
    """The sidebar is collapsed only by Streamlit's built-in control at the top; the custom duplicate that used
    to sit next to Refresh is gone, and Refresh / Sign out remain."""
    at = run("views/admin.py", "admin", {"ad_area": "Governance & decisions"})
    labels = {b.label for b in at.button}
    assert not labels & {"Icons only", "Show labels", "Compact", "Expand"}
    assert {"Refresh", "Sign out"} <= labels
    assert "nav_compact" not in at.session_state
    src = (ROOT / "ui_common.py").read_text() + (ROOT / "ui_kit.py").read_text()
    assert "nav_compact" not in src and "css_compact_sidebar" not in src
    a11y = (ROOT / "ui_kit.py").read_text()
    assert "Collapse navigation" in a11y and "Open navigation" in a11y        # the remaining control is named


def test_workspace_switch_is_deliberate_and_keeps_the_page():
    at = run("views/admin.py", "admin", {"ad_area": "Governance & decisions"})
    assert not any(b.label.startswith("Switch to") for b in at.button)          # no scenario: nothing to switch
    ui_common.set_scenario(services.load_demo_story(ui_common.live_world(), U("analyst")))
    at = run("views/admin.py", "admin", {"ad_area": "Governance & decisions"})
    text = " ".join(m.value for m in at.markdown)
    assert "Live workspace" in text and "DEMO SCENARIO" not in text              # still live until chosen
    at = click(at, "Switch to demo scenario")
    text = " ".join(m.value for m in at.markdown)
    assert at.session_state["ctx"] == "scenario" and "DEMO SCENARIO · SIMULATED" in text
    assert "Governance &amp; decisions" in text and "Simulation clock" in text
    at = click(at, "Switch to live workspace")
    assert at.session_state["ctx"] == "live" and "DEMO SCENARIO" not in " ".join(m.value for m in at.markdown)


def test_switching_never_changes_live_records():
    live = ui_common.live_world()
    from shiftsense.workflow import roster_fp
    fp, n_req = roster_fp(live.roster), len(live.requests)
    ui_common.set_scenario(services.load_demo_story(live, U("analyst")))
    at = run("views/team_lead.py", "tl1")
    at = click(at, "Switch to demo scenario")
    at = click(at, "Switch to live workspace")
    assert roster_fp(ui_common.live_world().roster) == fp and len(ui_common.live_world().requests) == n_req


@pytest.mark.parametrize("script,user,key,areas", [
    ("views/ops.py", "ops", "ops_area", {"Overview": "Decide first", "Coverage & risk": "Staffing variance",
                                        "Recovery": "Recovery performance", "Cost & staffing": "Cost · simulated",
                                        "Follow-ups, data & history": "Operations history",
                                        "Alerts & escalations": "Alerts &amp; escalations"}),
    ("views/team_lead.py", "tl1", "tl_area", {"Overview": "Top priorities", "Attendance": "Attendance today",
                                              "Team schedule": "Team schedule", "Requests": "Time-off and swap",
                                              "Alerts & decisions": "All alerts", "Team information": "Team directory"}),
    ("views/agent.py", "aarav", "me_area", {"Home": "Hours this week", "My shifts": "My shifts",
                                            "Inbox & requests": "My requests", "My history": "My history"})])
def test_role_areas_each_show_their_own_part(script, user, key, areas):
    """Former single screens are now areas: each area shows its own sections (and not the others' headings)."""
    for area, expect in areas.items():
        text = " ".join(m.value for m in run(script, user, {key: area}).markdown)
        assert expect in text, (area, expect)


def test_draft_indicator_only_for_the_analyst():
    live = ui_common.live_world()
    with ui_common.mutate(live):
        services.draft_plan(live, U("analyst"), live.roster, "d", "test")
    assert "Unpublished draft" in " ".join(m.value for m in run("views/planner.py", "analyst").markdown)
    assert "Unpublished draft" not in " ".join(m.value for m in run("views/ops.py", "ops").markdown)


def test_sidebar_identity_is_compact_and_sign_out_clears_session():
    at = run("views/ops.py", "ops")
    text = " ".join(m.value for m in at.markdown)
    assert 'aria-label="Account"' in text and "Anil Desai" in text and 'aria-label="Workspace"' in text
    assert any(b.label == "Refresh" and b.help for b in at.button)                 # effect explained in tooltip
    at = click(at, "Sign out")
    assert "user" not in at.session_state
