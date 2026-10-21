"""Low-effort pilot gaps: late check-ins, saved what-if runs, deployment mode, restore drill."""
import os
import pytest

from shiftsense import attendance, auth, backup, comms, health, planning, report, services, staffing, store
from shiftsense import workflow as wf

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "p.db"))
    monkeypatch.delenv("SHIFTSENSE_ENV", raising=False)
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def U(name):
    return auth.authenticate(name, PW[name])


def day_for(w, aid):
    return next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == aid) & (w.roster.day == x)).any())


def test_late_checkin_is_recorded_in_minutes_and_reported(w):
    d1, d6 = day_for(w, "A01"), day_for(w, "A06")
    on_time = services.send_attendance_reminder(w, U("tl1"), "A01", d1)
    w.now_t = on_time["opens_t"]                                     # 30 min before the start
    services.check_in_self(w, U("aarav"), on_time["id"])
    late = services.send_attendance_reminder(w, U("tl1"), "A06", d6)
    w.now_t = late["expires_t"]                                      # 1 h after the start, last allowed moment
    services.check_in_self(w, U("sneha"), late["id"])
    rows = {r["agent_id"]: r for r in attendance.log(w.run_id)}
    assert attendance.lateness(rows["A01"]) == "on time" and attendance.lateness(rows["A06"]) == "60 min after start"
    assert [r["agent_id"] for r in attendance.late_checkins(w.run_id)] == ["A06"]
    assert any(r["action"] == "Check-in" and "60 min after start" in r["after"] for r in comms.audit_rows(w.run_id))
    assert "60 min after start" in services.reminder_view(w, rows["A06"])["checked_in"]
    rep = report.weekly_report(w, d6 // 7)
    assert rep["summary"]["Late check-ins (after shift start)"] == 1


def test_whatif_runs_are_saved_with_inputs_and_metrics(w):
    fp = wf.roster_fp(w.roster)
    sc = planning.Scenario(name="spike", demand_pct={"Claims-EN": 20}, headcount_change=-1)
    res = planning.run_scenario(w, sc, time_limit=20)
    services.record_whatif(w, U("analyst"), "spike", "summary", result=res)
    saved = services.whatif_runs(w, U("analyst"))
    assert len(saved) == 1 and saved[0]["inputs"]["demand_pct"] == {"Claims-EN": 20}
    assert saved[0]["as_is"]["Coverage %"] == res["as_is"]["Coverage %"]
    assert (saved[0]["after"] or {}).get("Coverage %") == (res["after"] or {}).get("Coverage %")
    assert wf.roster_fp(w.roster) == fp                              # analysis only
    for who in ("ops", "admin", "tl1", "aarav"):
        with pytest.raises(services.Denied):
            services.whatif_runs(w, U(who))


def test_pilot_mode_flags_published_demo_passwords(w, monkeypatch):
    store.save(w)
    rows = {r[0]: r for r in health.run()}
    assert rows["Deployment mode"][2].startswith("demo") and "Demo passwords retired" not in rows
    monkeypatch.setenv("SHIFTSENSE_ENV", "pilot")
    rows = {r[0]: r for r in health.run()}
    assert rows["Deployment mode"][2] == "pilot"
    assert rows["Demo passwords retired"][1] is False and "admin" in rows["Demo passwords retired"][2]
    assert PW["admin"] not in str(rows)                              # passwords never printed


@pytest.mark.skipif(bool(os.environ.get("SHIFTSENSE_DATABASE_URL")), reason="SQLite file drill; PostgreSQL drill "
                                                                             "is tested in test_postgres.py")
def test_restore_drill_checks_a_copy_and_never_touches_live(w, tmp_path):
    store.save(w)
    team = dict(zip(w.data["agents"].agent_id, w.data["agents"].team))
    services.report_absence(w, U("tl1"), [next(a for a in w.roster[w.roster.day == w.first_day].agent_id
                                                if team[a] == "TL-1" and a not in ("A01", "A06"))], w.first_day)
    store.save(w)
    live = store.db_path()
    before = live.read_bytes()
    b = backup.create(tmp_path / "bk")
    r = backup.drill(b)
    assert r["problems"] == [] and r["audit"] == "verified" and "0 rule violation" in r["workspace"]
    assert live.read_bytes() == before
    with pytest.raises(FileNotFoundError):
        backup.drill(tmp_path / "missing.db")
    (tmp_path / "junk.db").write_bytes(b"not a database")
    assert backup.drill(tmp_path / "junk.db")["problems"]                    # a corrupt backup fails the drill
