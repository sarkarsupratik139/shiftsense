"""HR Decision Record HR-01..HR-08: server-side rules, called directly (no UI)."""
import pytest

from shiftsense import accounts, auth, comms, hr, refdata, services, staffing
from shiftsense import workflow as wf
from shiftsense.services import Denied

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}


def U(name):
    return auth.authenticate(name, PW[name])


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "hr.db"))
    monkeypatch.delenv("SHIFTSENSE_EMAIL", raising=False)
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


@pytest.fixture
def green(monkeypatch):
    """Coverage check says Green (team stays covered), so only the HR rules decide whether it auto-approves."""
    monkeypatch.setattr(wf, "leave_check", lambda w, a, d: {"status": "Green", "detail": "Team stays covered.",
                                                           "alternatives": []})


def _run(w, n, agent="A01"):
    """First n consecutive rostered days from tomorrow."""
    have = {int(d) for d in w.roster[w.roster.agent_id == agent].day}
    return next(list(range(d, d + n)) for d in range(w.first_day, 14) if all(x in have for x in range(d, d + n)))


def _green_day(w, agent="A01"):
    return _run(w, 1, agent)[0]


def _verified(w, agent="A01", lt="annual", n=10):
    b = services.enter_leave_balance(w, U("hr"), agent, lt, n, "2026-10-01", "HRIS export 2026-10")
    return services.verify_leave_balance(w, U("hrlead"), b["id"])


# ------------------------------------------------------------------ Senior HR permission
def test_senior_hr_is_an_hr_only_extra_permission(w):
    assert auth.can(U("hrlead"), "senior_hr") and not auth.can(U("hr"), "senior_hr")
    forged = {**U("hr"), "extra_permissions": ["senior_hr"]}
    assert not auth.can(forged, "senior_hr")                      # read from the stored account, not the session
    with pytest.raises(Exception):
        accounts.set_extra_permission("tl1", "senior_hr", True, "x")     # TL can never hold it
    with pytest.raises(Denied):
        services.set_senior_hr(w, U("hr"), "hr", True, "self-grant")    # only Admin grants
    services.set_senior_hr(w, U("admin"), "hr", True, "cover for leave")
    assert auth.can(U("hr"), "senior_hr")
    services.set_senior_hr(w, U("admin"), "hr", False, "cover ended")
    assert not auth.can(U("hr"), "senior_hr")
    assert sum(r["action"] == "Senior HR permission" for r in comms.audit_rows()) == 2


# ------------------------------------------------------------------ HR-01 separation
def test_separation_needs_hr_confirmation_and_deactivates_once(w, monkeypatch):
    for who in ("tl1", "ops", "analyst", "admin", "aarav"):
        with pytest.raises(Denied):
            services.record_separation(w, U(who), "A02", "2026-10-07", "Resignation")
    from datetime import datetime
    from zoneinfo import ZoneInfo
    clock = {"now": datetime(2026, 10, 31, 23, 59, tzinfo=ZoneInfo("Asia/Kolkata"))}
    monkeypatch.setattr(hr, "local_now", lambda: clock["now"])
    eff = "2026-10-31"                                            # last working day
    s = services.record_separation(w, U("hr"), "A01", eff, "Resignation", "notice served")
    assert services.run_separations(w) == []                      # unconfirmed: nothing happens
    assert accounts.get("aarav")["active"]
    with pytest.raises(ValueError):
        services.confirm_separation(w, U("hr"), s["id"])          # a second HR user confirms
    accounts.set_email_code("aarav", "h", "2026-11-02T00:00:00")
    services.confirm_separation(w, U("hrlead"), s["id"])
    assert services.run_separations(w) == []                      # 23:59 IST on the last day: still active
    assert auth.current(U("aarav"))
    clock["now"] = datetime(2026, 11, 1, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))   # 00:00 IST next day
    assert auth.current({**U("hr"), "username": "aarav", "role": "agent", "agent_id": "A01", "team": None}) is None
    assert auth.authenticate("aarav", PW["aarav"]) is None        # access ends on time, before the bookkeeping run
    done = services.run_separations(w)
    assert done and done[0]["deactivated"] == ["aarav"]
    assert not accounts.get("aarav")["active"]
    assert accounts.email_code("aarav") == (None, None)           # outstanding codes revoked
    assert services.run_separations(w) == []                      # idempotent
    with pytest.raises(Denied):
        services.get_request(w, {**U("hr"), "username": "aarav", "role": "agent", "agent_id": "A01",
                                 "team": None, "name": "x", "role_label": "Agent"}, "REQ-1")
    sep = hr.separation(s["id"])
    assert sep["status"] == "executed" and sep["reason_category"] == "Resignation"
    services.rescind_separation(w, U("hrlead"), s["id"], "resignation withdrawn")
    assert accounts.get("aarav")["active"]                        # rescind reactivates, audited
    assert auth.authenticate("aarav", PW["aarav"])
    acts = [r["action"] for r in comms.audit_rows()]
    for a in ("Separation recorded", "Separation confirmed", "Separation executed", "Separation rescinded"):
        assert a in acts


def test_separation_correction_needs_reconfirming(w):
    s = services.record_separation(w, U("hr"), "A02", "2026-10-20", "End of contract")
    services.confirm_separation(w, U("hrlead"), s["id"])
    with pytest.raises(ValueError):
        services.correct_separation(w, U("hr"), s["id"], "2026-10-25", "End of contract", "")
    c = services.correct_separation(w, U("hr"), s["id"], "2026-10-25", "End of contract", "extended by a week")
    assert hr.separation(s["id"])["status"] == "corrected" and c["status"] == "recorded"
    assert c["correction_of"] == s["id"]


def test_separation_not_in_demo_scenario(w):
    sc = wf.make_scenario(w)
    with pytest.raises(Denied):
        services.record_separation(sc, U("hr"), "A02", "2026-10-07", "Resignation")


# ------------------------------------------------------------------ HR-02 balances
def test_balance_unverified_is_unresolved_and_approval_debits(w, green):
    d = _green_day(w)
    r = services.submit_leave(w, U("aarav"), d, leave_type="annual")
    assert r["state"] == "Pending"                                 # Green, but no verified balance: not eligible
    assert hr.review_tasks(w.run_id, r["id"])[0]["trigger_name"] == "unverified_balance"
    with pytest.raises(ValueError):
        services.decide_request(w, U("tl1"), r["id"], True)         # waits for HR recommendation
    b = services.enter_leave_balance(w, U("hr"), "A01", "annual", 10, "2026-10-01", "HRIS export")
    assert hr.balance("A01", "annual")["state"] == "unverified"
    with pytest.raises(ValueError):
        services.verify_leave_balance(w, U("hr"), b["id"])          # not by the person who entered it
    services.verify_leave_balance(w, U("hrlead"), b["id"])
    t = hr.review_tasks(w.run_id, r["id"])[0]
    services.recommend_on_review(w, U("hr"), t["id"], "approve", "balance now verified")
    services.decide_request(w, U("tl1"), r["id"], True)
    assert hr.balance("A01", "annual")["available"] == 9            # a transaction, not an overwrite
    services.adjust_leave_balance(w, U("hr"), "A01", "annual", 0.5, "carry-over correction")
    assert hr.balance("A01", "annual")["available"] == 9.5
    with pytest.raises(ValueError):
        services.adjust_leave_balance(w, U("hr"), "A01", "annual", 1, "")
    kinds = [t["kind"] for t in hr.transactions("A01")]
    assert kinds == ["debit", "adjustment"]


def test_verified_balance_green_auto_approves_and_revoke_credits(w, green):
    _verified(w)
    d = _green_day(w)
    r = services.submit_leave(w, U("aarav"), d, leave_type="annual")
    assert r["state"] == "Approved" and hr.balance("A01", "annual")["available"] == 9
    services.revoke(w, U("tl1"), r["id"], "needed for audit week")
    assert r["state"] == "Revoked" and hr.balance("A01", "annual")["available"] == 10


def test_unpaid_leave_needs_no_balance(w, green):
    d = _green_day(w)
    r = services.submit_leave(w, U("aarav"), d, leave_type="unpaid")
    assert r["state"] == "Approved" and hr.transactions("A01") == []


def test_debit_rolled_back_when_approval_fails(w, green):
    _verified(w)
    d = _green_day(w)
    r = services.submit_leave(w, U("aarav"), d, leave_type="annual")
    services.revoke(w, U("tl1"), r["id"], "x")
    r2 = services.submit_leave(w, U("aarav"), d, leave_type="annual")
    w.now_t = d * 48 + 1                                           # too late: approval refused
    if r2["state"] == "Pending":
        with pytest.raises(ValueError):
            services.decide_request(w, U("tl1"), r2["id"], True)
    assert hr.balance("A01", "annual")["available"] == 10 - (r2["state"] == "Approved")


def test_scenario_balance_moves_stay_in_scenario(w, green):
    _verified(w)
    sc = wf.make_scenario(w)
    d = _green_day(sc)
    services.submit_leave(sc, U("aarav"), d, leave_type="annual")
    assert hr.balance("A01", "annual", sc.run_id)["available"] == 9
    assert hr.balance("A01", "annual", w.run_id)["available"] == 10     # live untouched


# ------------------------------------------------------------------ HR-03 triggers
def test_sick_leave_trigger_and_masking(w, green):
    _verified(w, lt="sick")
    reqs = []
    for d in _run(w, 3):
        reqs.append(services.submit_leave(w, U("aarav"), d, note="fever", leave_type="sick"))
    third = reqs[-1]
    assert third["state"] == "Pending"
    trig = [t["trigger_name"] for t in hr.review_tasks(w.run_id, third["id"])]
    assert "sick_leave" in trig
    seen = services.request_view(U("tl1"), third)
    assert seen["leave_type"].startswith("leave (details") and seen["note"] == ""
    assert services.request_view(U("hr"), third)["leave_type"] == "sick"
    st = services.review_status(w, U("tl1"), third["id"])
    assert all("details held by HR" in t["reason"] for t in st)
    assert services.decide_request(w, U("tl1"), third["id"], False, "cover not possible")["state"] == "Rejected"
    assert not hr.review_tasks(w.run_id, third["id"], ("open",))       # closed with the decision


def test_triggers_configurable_and_never_decide(w, green):
    _verified(w)
    services.set_hr_policy(w, U("hrlead"), "leave.long_leave_days", 1, "approved", "HR policy 4.2")
    with pytest.raises(Denied):
        services.set_hr_policy(w, U("hr"), "leave.long_leave_days", 9, "approved", "x")   # Senior HR only
    with pytest.raises(ValueError):
        services.set_hr_policy(w, U("hrlead"), "leave.long_leave_days", 3, "approved", "")  # reference needed
    days = _run(w, 2)
    r1 = services.submit_leave(w, U("aarav"), days[0], leave_type="unpaid")
    r2 = services.submit_leave(w, U("aarav"), days[1], leave_type="unpaid")
    assert r2["state"] == "Pending" and r2["hold"]                    # 2 consecutive > 1: review, not rejection
    assert any(t["trigger_name"] == "long_leave" for t in hr.review_tasks(w.run_id, r2["id"]))
    assert r1["state"] in ("Approved", "Pending")


def test_consecutive_basis_is_explicit():
    rows = {r["key"]: r for r in hr.policy_rows()}
    assert rows["leave.consecutive_basis"]["value"] == "calendar_days"
    assert rows["leave.consecutive_basis"]["status"] == "approved"
    assert rows["separation.timezone"]["value"] == "Asia/Kolkata" and rows["separation.timezone"]["status"] == "approved"
    assert rows["separation.effective_time"]["value"] == "end_of_day"
    assert rows["absence.threshold"]["value"] == 3 and rows["absence.window_days"]["value"] == 30
    assert rows["absence.flags_enabled"]["value"] is False and rows["absence.flags_enabled"]["status"] == "pending"
    assert rows["overtime.monthly_threshold"]["value"] is None
    assert rows["overtime.hr_approval_enabled"]["value"] is False
    assert rows["retention.policy"]["value"] is None
    with pytest.raises(ValueError):
        hr.set_policy("retention.policy", "7 years", "approved", "x", "", "t")


# ------------------------------------------------------------------ HR-04 48 h lock
def _started_shift(w):
    r = w.roster[w.roster.day == w.today + 1].iloc[0]
    return r.agent_id, int(r.day)


def test_attendance_locks_after_48h_and_correction_is_hr_reviewed(w):
    a, d = _started_shift(w)
    end = hr.shift_end_t(w, a, d)
    w.now_t = end + hr.lock_intervals(w.policy)                       # exactly 48 h after the shift ended
    tl = next(u for u, r in auth.config()["users"].items() if r.get("team") == w.data["agents"].set_index("agent_id")
              .loc[a, "team"] and r["role"] == "team_lead")
    with pytest.raises(ValueError, match="locked"):
        services.correct_attendance(w, U(tl), a, d, "was here")
    c = services.request_attendance_correction(w, U(tl), a, d, "present", "badge reader down")
    assert c["original"] == "no record" and c["status"] == "pending"
    with pytest.raises(Denied):
        services.review_attendance_correction(w, U(tl), c["id"], True, "ok")    # TL cannot apply
    services.review_attendance_correction(w, U("hr"), c["id"], True, "badge log checked")
    assert w.attendance[(a, d)] == "confirmed"
    assert w.attendance_detail[(a, d)]["corrected_from"] == "no record"
    assert hr.correction(c["id"])["status"] == "applied"
    with pytest.raises(ValueError):
        services.review_attendance_correction(w, U("hr"), c["id"], True, "again")


def test_before_lock_tl_can_confirm(w):
    a, d = _started_shift(w)
    w.now_t = hr.shift_end_t(w, a, d) + 2
    assert not hr.is_locked(w, a, d)


# ------------------------------------------------------------------ HR-05 absence flags
def test_absence_flags_inactive_until_threshold_approved(w):
    for d in range(3):
        w.attendance[("A03", d)] = "no-show"
    w.now_t = 5 * 48
    assert services.check_absence_flags(w, U("hr"))["active"] is False          # off pending validation
    assert hr.absence_flags(w.run_id) == []
    val = services.absence_validation(w, U("hr"))
    assert [v["employee_id"] for v in val if v["would_flag"]] == ["A03"]
    assert hr.absence_flags(w.run_id) == []                                    # dry run creates nothing
    with pytest.raises(Denied):
        services.set_hr_policy(w, U("hr"), "absence.flags_enabled", True, "approved", "validated")
    services.set_hr_policy(w, U("hrlead"), "absence.flags_enabled", True, "approved", "validation run 20 Oct")
    res = services.check_absence_flags(w, U("hr"))
    assert res["active"] and res["created"] == 1
    assert services.check_absence_flags(w, U("hr"))["created"] == 0          # no duplicates
    for who in ("tl1", "ops", "analyst", "admin", "aarav"):
        with pytest.raises(Denied):
            services.absence_flags(w, U(who))
    f = services.absence_flags(w, U("hr"))[0]
    assert f["absences"] == 3
    w.attendance[("A03", 1)] = "confirmed"                                   # a correction re-counts
    services.check_absence_flags(w, U("hr"))
    assert services.absence_flags(w, U("hr"))[0]["absences"] == 2
    services.move_absence_flag(w, U("hr"), f["id"], "under_review", "support call booked")
    services.move_absence_flag(w, U("hr"), f["id"], "resolved", "support agreed")
    with pytest.raises(ValueError):
        services.move_absence_flag(w, U("hr"), f["id"], "open", "x")


def test_missing_checkin_is_never_an_absence(w):
    assert hr.qualifying_absences(w, "A01", 0, 13) == []


# ------------------------------------------------------------------ HR-06 overtime
def test_overtime_approval_off_by_default(w):
    v = services.overtime_view(w, U("hr"))
    assert v["active"] is False and v["tasks"] == []
    assert services.check_overtime(w, U("hr"))["created"] == 0
    with pytest.raises(Denied):
        services.overtime_view(w, U("ops"))


def test_scheduled_extension_is_not_worked_overtime(w):
    i = w.roster.index[w.roster.day == 0][0]
    w.roster.loc[i, "ext_after"] = 2
    a = w.roster.loc[i, "agent_id"]
    w.now_t = 47
    o = hr.overtime_hours(w, a)
    assert o["worked"] == 0 and o["scheduled_unconfirmed"] >= 2
    w.attendance[(a, 0)] = "checkin"
    assert hr.overtime_hours(w, a)["worked"] >= 2


# ------------------------------------------------------------------ HR-07 cases
def test_restricted_cases(w):
    c = services.open_hr_case(w, U("hrlead"), "Grievance", "restricted", "A05", "complaint about rota", "hr",
                              "2026-11-01")
    assert services.view_hr_case(w, U("hr"), c["id"])["category"] == "Grievance"        # caseworker
    assert services.view_hr_case(w, U("hrlead"), c["id"])                                 # Senior HR
    for who in ("tl1", "ops", "analyst", "admin", "aarav"):
        with pytest.raises(Denied):
            services.view_hr_case(w, U(who), c["id"])
        with pytest.raises(Denied):
            services.hr_cases(w, U(who))
    services.add_case_entry(w, U("hr"), c["id"], "evidence", "Email thread stored in HR drive /cases/12")
    with pytest.raises(Denied):
        services.reassign_hr_case(w, U("hr"), c["id"], "hrlead", "x")                     # Senior HR only
    services.reassign_hr_case(w, U("hrlead"), c["id"], "hrlead", "conflict of interest")
    with pytest.raises(Denied):
        services.view_hr_case(w, U("hr"), c["id"])                                        # no longer assigned
    with pytest.raises(ValueError):
        services.set_hr_case_status(w, U("hrlead"), c["id"], "closed", "")
    services.set_hr_case_status(w, U("hrlead"), c["id"], "closed", "resolved informally")
    log = hr.access_log(c["id"])
    assert any(not x["allowed"] and x["username"] == "tl1" for x in log)
    n = [x for x in comms.notices_for(U("hr"), w.run_id) if "case" in x["title"].lower()]
    assert n and all("Grievance" not in (x["title"] + (x["body"] or "")) for x in n)    # no details in notices


def test_hr_policy_pending_register_listed():
    keys = {r["key"] for r in hr.pending_items()}
    assert {"absence.flags_enabled", "overtime.monthly_threshold", "retention.policy"} <= keys
    assert not {"separation.timezone", "separation.effective_time", "leave.consecutive_basis"} & keys
    assert len(hr.RETENTION_REGISTER) >= 7


def test_reported_absences_count_and_rolling_window(w):
    """Validated against the attendance model: reported 'can't come in' absences count, approved leave and a
    missing check-in never do, the same day twice is one event, and any rolling 30-day window qualifies."""
    import pandas as pd
    lv = w.data["leave"]
    add = pd.DataFrame([{"agent_id": "A04", "day": 1, "reason": "absent (sick)"},
                        {"agent_id": "A04", "day": 3, "reason": "approved leave"},
                        {"agent_id": "A04", "day": 5, "reason": "absent (no-show)"}])
    w.data = {**w.data, "leave": pd.concat([lv[lv.agent_id != "A04"], add], ignore_index=True)}
    w.attendance[("A04", 5)] = "no-show"                       # same day as the report: one event
    w.attendance[("A04", 3)] = "no-show"                       # on approved leave: excluded
    w.now_t = 10 * 48
    ev = hr.absence_events(w, "A04")
    assert list(ev) == [1, 5] and len(ev[5]) == 2
    w.attendance[("A04", 8)] = "no-show"
    assert hr.first_window(list(hr.absence_events(w, "A04")), 3, 30) == (1, 30, 3)
    assert hr.first_window([1, 20, 40], 3, 30) is None
    w.attendance[("A04", 1)] = "confirmed"                     # corrected to present: no longer counts
    assert 1 not in hr.absence_events(w, "A04")


def test_long_leave_counts_calendar_days_including_days_off(w, green):
    """Calendar basis: a day off between two leave days still counts, so 6 calendar days trigger review."""
    have = {int(d) for d in w.roster[w.roster.agent_id == "A01"].day}
    import pandas as pd
    lv = w.data["leave"]
    start = w.first_day
    extra = pd.DataFrame([{"agent_id": "A01", "day": d, "reason": "approved leave"} for d in range(start, start + 6)
                          if d not in have])
    w.data = {**w.data, "leave": pd.concat([lv, extra], ignore_index=True)}
    for d in range(start, start + 6):
        if d in have:
            w.requests.append({"id": f"X{d}", "kind": "leave", "agent_id": "A01", "day": d, "state": "Approved",
                               "alternative": None, "leave_type": "unpaid"})
    assert hr.consecutive_days(w, "A01", start + 2) >= 6
