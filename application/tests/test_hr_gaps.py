"""Phase 2 gaps: separation reaches the roster and employee record, reminders, notices, agent status, reassign,
HR download, leave type from free text."""
import io
from datetime import datetime, timedelta

import pandas as pd
import pytest

from shiftsense import assistant, auth, comms, hr, refdata, services, staffing
from shiftsense import workflow as wf
from shiftsense.services import Denied

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}
U = lambda n: auth.authenticate(n, PW[n])      # noqa: E731


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "gaps.db"))
    monkeypatch.delenv("SHIFTSENSE_EMAIL", raising=False)
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def test_confirmed_separation_alerts_near_days_and_releases_later_shifts(w):
    a = "A05"
    eff = refdata.iso_of_day(w.policy, w.today)                       # last working day = today
    have = sorted(int(d) for d in w.roster[(w.roster.agent_id == a) & (w.roster.day > w.today)].day)
    assert have
    s = services.record_separation(w, U("hr"), a, eff, "Resignation")
    services.confirm_separation(w, U("hrlead"), s["id"])
    left = w.roster[(w.roster.agent_id == a) & (w.roster.day > w.today)]
    assert left.empty                                                 # no future shift left for the leaver
    inc = [i for i in w.incidents if i.absent == [a]]
    near = [d for d in have if d <= w.today + 1]
    assert len(inc) == len(near)                                      # near days: staffing alerts with a proposal
    lv = w.data["leave"]
    assert ((lv.agent_id == a) & lv.reason.str.contains("left the company")).sum() >= len(have)
    assert hr.absence_events(w, a) == {}                              # leaving is not an unplanned absence
    from shiftsense.constraints import check
    assert check(w.policy, w.data["agents"], w.data["leave"], w.roster) == []
    services.rescind_separation(w, U("hrlead"), s["id"], "stays after all")
    lv = w.data["leave"]
    assert not ((lv.agent_id == a) & (lv.reason == "left the company")).any()


def test_separation_updates_employee_record(w):
    services.load_base_pack(w, U("analyst"))
    a = next(iter(refdata.current("employees")))
    s = services.record_separation(w, U("hr"), a, "2026-10-31", "End of contract")
    services.confirm_separation(w, U("hrlead"), s["id"])
    rec = refdata.current("employees", hr.local_today())[a]
    assert rec["status"] == "On notice" and rec["end_date"] == "2026-10-31"
    services.rescind_separation(w, U("hrlead"), s["id"], "renewed")
    assert refdata.current("employees", hr.local_today())[a]["status"] == "Active"


def test_reminders_for_overdue_hr_work_without_case_details(w, monkeypatch):
    days = sorted(int(d) for d in w.roster[w.roster.agent_id == "A01"].day if d >= w.first_day)
    r = services.submit_leave(w, U("aarav"), days[0])                 # unverified balance -> review task
    t = hr.review_tasks(w.run_id, r["id"])[0]
    with hr._db() as con:
        con.execute("UPDATE hr_review_tasks SET created_at=? WHERE id=?",
                    ((datetime.now() - timedelta(days=2)).isoformat(timespec="seconds"), t["id"]))
    c = services.open_hr_case(w, U("hrlead"), "Grievance", "restricted", "A05", "secret summary", "hr", "2020-01-01")
    services.add_case_entry(w, U("hr"), c["id"], "followup", "call back", "2020-01-02")
    n = services.hr_reminders(w)
    assert n >= 3
    assert services.hr_reminders(w) == 0                              # once per item per day
    mine = comms.notices_for(U("hr"), w.run_id)
    case_notes = [x for x in mine if "case" in x["title"].lower() and "overdue" in x["title"] or "follow-up" in x["title"]]
    assert case_notes and all("Grievance" not in x["title"] and "secret" not in (x["body"] or "") for x in mine)
    assert not [x for x in comms.notices_for(U("tl1"), w.run_id) if "Restricted" in x["title"]]


def test_flag_and_correction_notices(w):
    for d in range(3):
        w.attendance[("A03", d)] = "no-show"
    w.now_t = 5 * 48
    services.set_hr_policy(w, U("hrlead"), "absence.flags_enabled", True, "approved", "validated")
    services.check_absence_flags(w, U("hr"))
    flags = [x for x in comms.notices_for(U("hr"), w.run_id) if "absence-pattern" in x["title"]]
    assert flags and "A03" not in flags[0]["title"] + (flags[0]["body"] or "")
    r = w.roster[w.roster.day == w.today + 1].iloc[0]
    a, d = r.agent_id, int(r.day)
    w.now_t = hr.shift_end_t(w, a, d) + 1
    tl = next(u for u, x in auth.config()["users"].items() if x["role"] == "team_lead"
              and x.get("team") == w.data["agents"].set_index("agent_id").loc[a, "team"])
    c = services.request_attendance_correction(w, U(tl), a, d, "present", "badge reader down")
    services.review_attendance_correction(w, U("hr"), c["id"], False, "no evidence")
    got = [x for x in comms.notices_for(U(tl), w.run_id) if "Attendance correction" in x["title"]]
    assert got and "not applied" in got[0]["body"]


def test_agent_sees_hr_review_step_and_can_ask_for_correction(w):
    days = sorted(int(d) for d in w.roster[w.roster.agent_id == "A01"].day if d >= w.first_day)
    r = services.submit_leave(w, U("aarav"), days[0])
    assert services.request_hr_status(w, U("aarav"), r["id"]) == "HR review, then team lead"
    t = hr.review_tasks(w.run_id, r["id"])[0]
    services.recommend_on_review(w, U("hr"), t["id"], "approve", "ok")
    assert services.request_hr_status(w, U("aarav"), r["id"]) == "Team lead"
    started = [int(d) for d in w.roster[w.roster.agent_id == "A01"].day if d <= w.today]
    if started:
        w.now_t = hr.shift_end_t(w, "A01", started[0]) + 1
        c = services.request_attendance_correction(w, U("aarav"), "A01", started[0], "present", "I was there")
        assert c["status"] == "pending"
        with pytest.raises(Denied):
            services.request_attendance_correction(w, U("aarav"), "A02", started[0], "present", "x")


def test_reassign_review_task(w):
    days = sorted(int(d) for d in w.roster[w.roster.agent_id == "A01"].day if d >= w.first_day)
    r = services.submit_leave(w, U("aarav"), days[0])
    t = hr.review_tasks(w.run_id, r["id"])[0]
    with pytest.raises(ValueError):
        services.reassign_review(w, U("hr"), t["id"], "hrlead", "")
    services.reassign_review(w, U("hr"), t["id"], "hrlead", "leave cover")
    assert hr.review_task(t["id"])["assigned_to"] == "hrlead"
    with pytest.raises(Denied):
        services.reassign_review(w, U("tl1"), t["id"], "hr", "x")


def test_hr_records_download(w):
    data = services.hr_records_workbook(w, U("hr"))
    sheets = pd.read_excel(io.BytesIO(data), sheet_name=None)
    assert {"Read me", "Leave balances", "Transactions", "Separations", "Review tasks",
            "Attendance corrections"} <= set(sheets)
    assert not any("case" in s.lower() or "flag" in s.lower() for s in sheets)
    assert (sheets["Leave balances"]["Available"] == "Unresolved").all()
    with pytest.raises(Denied):
        services.hr_records_workbook(w, U("tl1"))


def test_leave_type_from_free_text():
    d = ["Fri 09 Oct"]
    assert assistant.parse_request_rules("I need sick leave on Friday", d, "")["intent"] == "time_off"
    assert assistant.parse_request_rules("I need sick leave on Friday", d, "")["leave_type"] == "sick"
    assert assistant.parse_request_rules("I'm sick, can't come Friday", d, "")["intent"] == "absence"
    assert assistant.parse_request_rules("Friday off for a wedding", d, "")["leave_type"] == ""
    assert assistant.parse_request_rules("unpaid leave on Friday", d, "")["leave_type"] == "unpaid"
