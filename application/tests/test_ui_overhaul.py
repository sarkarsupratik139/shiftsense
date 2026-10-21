"""Enterprise UI overhaul: employee directory and profile, photos in team views, theme preference, system status,
role areas, provenance and design tokens."""
import html
import io
from pathlib import Path

import pytest
from PIL import Image
from streamlit.testing.v1 import AppTest

import ui_common
import ui_kit
from shiftsense import accounts, auth, photos, refdata, services

ROOT = Path(__file__).resolve().parent.parent
PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}
U = lambda n: auth.authenticate(n, PW[n])      # noqa: E731


def img():
    b = io.BytesIO()
    Image.new("RGB", (200, 200), (30, 90, 160)).save(b, "PNG")
    return b.getvalue()


@pytest.fixture(autouse=True)
def fresh():
    ui_common.reset_world()
    yield
    ui_common.reset_world()


def run(script, user, state=None):
    at = AppTest.from_file(str(ROOT / script), default_timeout=300)
    at.session_state["user"] = U(user)
    for k, v in (state or {}).items():
        at.session_state[k] = v
    at = at.run()
    assert not at.exception, at.exception
    return at


def text(at):
    return html.unescape(" ".join(m.value for m in at.markdown))


def test_employee_directory_and_profile_with_photo_and_initials():
    w = ui_common.live_world()
    services.set_employee_photo(w, U("hr"), "A01", img())
    at = run("views/hr.py", "hr", {"hr_area": "Employees", "hr_emp": "A01"})
    df = at.dataframe[0].value
    assert {"Photo", "Name", "Employee ID", "Team", "Reports to", "Status"} <= set(df.columns)
    row = df[df["Employee ID"] == "A01"].iloc[0]
    assert row["Photo"].startswith("data:image/jpeg")                     # photo on file
    other = df[df["Employee ID"] == "A02"].iloc[0]
    assert other["Photo"].startswith("data:image/svg+xml")                # initials badge
    t = text(at)
    assert "Aarav K." in t and 'class="ss-profile"' in t and "<img" in t   # profile header with the photo
    assert [x.label for x in at.tabs] == ["Employment", "Schedule", "Leave", "Attendance", "History",
                                          "Profile photo", "Edit details"]


def test_directory_search_and_filters():
    at = run("views/hr.py", "hr", {"hr_area": "Employees"})
    at.text_input(key="hr_q").set_value("Aarav")
    at = at.run()
    assert list(at.dataframe[0].value["Employee ID"]) == ["A01"]


def test_profile_edit_saves_a_new_employee_version():
    services.load_base_pack(ui_common.live_world(), U("analyst"))        # contracts exist once base data is loaded
    at = run("views/hr.py", "hr", {"hr_area": "Employees", "hr_emp": "A03"})
    next(t for t in at.text_input if t.label == "Reason for the change (required)").set_value("night shifts agreed")
    cb = next(c for c in at.checkbox if c.label == "Night shifts allowed")
    cb.set_value(not cb.value)
    at = next(b for b in at.button if b.label == "Save new version").click().run()
    assert not at.exception, at.exception
    assert refdata.current("employees", "2099-01-01")["A03"]["night_ok"] == cb.value


def test_team_lead_sees_team_photos_other_roles_scoped():
    w = ui_common.live_world()
    services.set_employee_photo(w, U("hr"), "A01", img())
    at = run("views/team_lead.py", "tl1", {"tl_area": "Team schedule"})
    t = text(at)
    assert 'class="ss-wg"' in t and 'aria-label="Aarav K."' in t                 # week roster grid
    grid = t[t.index('class="ss-wg"'):]
    aarav = grid[grid.index('aria-label="Aarav K."'):][:300]
    assert "<img" in aarav                                                       # photo for Aarav
    assert ">DP<" in grid                                                        # initials for Deepak P.
    tl2 = next(u for u, r in auth.config()["users"].items() if r["role"] == "team_lead" and r.get("team") != "TL-1")
    assert "A01" not in services.photo_uris(w, U(tl2), ["A01"])


def test_theme_preference_is_saved_on_the_account():
    at = run("views/ops.py", "ops")
    at.button_group(key="theme_pick").set_value("Dark")
    at = at.run()
    assert not at.exception
    assert accounts.get("ops")["theme_pref"] == "Dark"


def test_system_status_area_is_read_only():
    at = run("views/admin.py", "admin", {"ad_area": "System status"})
    t = text(at)
    assert "Health" in t and "Email relay check" in t
    assert set(at.dataframe[0].value.columns) == {"Check", "Result", "Detail", "Essential"}


def test_publish_stepper_and_breadcrumb():
    t = text(run("views/planner.py", "analyst", {"pl_area": "Review & publish"}))
    assert 'class="ss-steps"' in t and 'aria-current="step"' in t
    assert 'aria-label="Breadcrumb"' in t and "Planning ›" in t


def test_tokens_follow_the_active_theme():
    css = ui_kit.css()
    assert "light-dark(" in css and "--accent: light-dark(#1f5f8b, #7cb3dc)" in css
    for m in ("light", "dark"):
        t = ui_kit.TOKENS[m]
        assert ui_kit.contrast(t["muted"], t["paper"]) >= 4.5 and ui_kit.contrast(t["ink"], t["bg"]) >= 7



def test_week_and_day_calendar_navigation():
    at = run("views/team_lead.py", "tl1", {"tl_area": "Team schedule"})
    labels = {b.label for b in at.button}
    assert {"Previous", "Today", "Next"} <= labels
    at = next(b for b in at.button if b.label == "Next").click().run()
    assert not at.exception
    at.button_group(key="tl_cal_view").set_value("Day")
    at = at.run()
    assert not at.exception
    assert "Day schedule and coverage gaps" in text(at) or "No shifts this day" in text(at)


def test_leave_calendar_hides_leave_type_from_team_lead_not_hr():
    w = ui_common.live_world()
    days = sorted(int(d) for d in w.roster[w.roster.agent_id == "A01"].day if d >= w.first_day)
    with ui_common.mutate(w):
        services.submit_leave(w, U("aarav"), days[0], leave_type="sick")
    tl = text(run("views/team_lead.py", "tl1", {"tl_area": "Requests"}))
    assert "Team leave calendar" in tl and "Sick leave" not in tl and "Requested" in tl
    hr_ = text(run("views/hr.py", "hr", {"hr_area": "Leave & balances"}))
    assert "Leave calendar" in hr_ and "Sick leave" in hr_


def test_attendance_agenda_never_calls_missing_records_absent():
    t = text(run("views/team_lead.py", "tl1", {"tl_area": "Attendance"}))
    assert 'class="ss-agenda"' in t
    assert "No record (unknown)" in t or "Still to start" in t


def test_chart_cards_have_titles_actions_and_provenance():
    at = run("views/ops.py", "ops", {"ops_area": "Coverage & risk"})
    t = text(at)
    assert "Staffing coverage" in t and "each cell: scheduled / required people" in t and "Watch list" in t
    assert "Coverage vs. required staffing · next 72 hours" not in t          # replaced by the matrix
    assert "Modelled estimate" in t
    assert any(getattr(p, "proto", None) is not None and "Data & export" in str(p.proto) for p in at.get("popover"))
    assert "When the gaps happen" in t


def test_heatmap_modes_and_gap_stats():
    import numpy as np
    f = ui_kit.gap_heatmap(np.array([[-3.0, 0.0], [-1.0, -0.5]]), ["d1", "d2"], ["Q1", "Q2"],
                           unit="pp below full coverage", mode_="shortfall", fmt=".1f")
    hm = f.data[0]
    assert hm.zmin == 0 and hm.zmax == 3.0 and "pp below full coverage" in hm.colorbar.title.text
    f2 = ui_kit.gap_heatmap(np.array([[-2.0, 4.0]]), ["a", "b"], ["Q"], unit="people")
    assert f2.data[0].zmin == -4 and f2.data[0].zmax == 4                    # symmetric, neutral midpoint at 0
    assert ui_kit.gap_stats(["09:00", "09:30", "10:00"], [5, 6, 4], [5, 3, 4]) == [
        ("Peak gap", "3 people"), ("Half-hours short", "1"), ("Highest risk", "09:30")]
    assert ui_kit.gap_stats(["a"], [1], [2])[0] == ("Peak gap", "None")



def test_coverage_matrix_values_match_existing_definitions():
    import numpy as np
    req = np.array([[4, 4, 6, 6], [0, 0, 2, 2]])
    sch = np.array([[4, 3, 6, 4], [0, 1, 2, 2]])
    a = ui_kit.matrix_bins(req, sch, 2)
    assert a["req"].tolist() == [[4, 6], [0, 2]] and a["sch"].tolist() == [[3.5, 5], [0.5, 2]]
    assert round(a["cov"][0, 0], 1) == 87.5 and np.isnan(a["cov"][1, 0]) and a["peak"].tolist() == [[1, 2], [0, 0]]
    assert [ui_kit.matrix_state(v) for v in (100, 96, 90, 50, float("nan"))] == [0, 1, 2, 3, 4]
    f = ui_kit.coverage_matrix(["Q1", "Q2"], ["09:00", "10:00"], a)
    assert f.data[0].text[0][0] == "3.5/4" and f.data[0].text[1][0] == "—"
    many = ui_kit.matrix_bins(np.tile(req, 7), np.tile(sch, 7), 1)               # 28 columns: dense view
    f2 = ui_kit.coverage_matrix(["Q1", "Q2"], [str(i) for i in range(28)], many)
    assert f2.data[0].text[0][1] == "−1" and f2.data[0].text[0][0] == ""        # only short cells are marked
    assert f2.data[0].customdata[0][1][5] == "3/4"                                 # full value stays in the tooltip
    assert "Short" in ui_kit.matrix_legend() and "Covered" in ui_kit.matrix_legend()


def test_planner_matrix_and_interval_detail_use_matching_bases():
    at = run("views/planner.py", "analyst", {"pl_area": "Forecast & staffing"})
    t = text(at)
    assert "Staffing coverage" in t and "Gaps this day" in t
    at = run("views/planner.py", "analyst", {"pl_area": "Forecast & staffing", "card": ("interval", ("Claims-EN", 96 + 20, 96 + 24))})
    t = text(at)
    assert "Productive required (peak)" in t and "scheduled heads, incl. shrinkage" in t


@pytest.mark.parametrize("script,user,state", [
    ("views/agent.py", "aarav", {"me_area": "My shifts"}),
    ("views/admin.py", "admin", {"ad_area": "Overview"}),
    ("views/planner.py", "analyst", {"pl_area": "Roster optimization"}),
    ("views/hr.py", "hr", {"hr_area": "Employees", "hr_emp": "A01"})])
def test_schedules_are_shown_as_calendars(script, user, state):
    assert 'class="ss-wg"' in text(run(script, user, state))
