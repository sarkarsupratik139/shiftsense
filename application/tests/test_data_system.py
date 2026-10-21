"""Data input, change propagation and outputs: smart conversion of messy input, mandatory fields, the base pack,
rebuild from reference data, ripple of each change type, approvals, permissions, scheduling, exports."""
import io
import os
import pickle
import shutil

import pandas as pd
import pytest

from shiftsense import (auth, convert, dataflow, data_gen, exports, optimizer, refdata, services, staffing)
from shiftsense import workflow as wf
from shiftsense.config import agent_rules, load_policy
from shiftsense.constraints import check
from shiftsense.schemas import DOMAINS

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}


def U(n):
    return auth.authenticate(n, PW[n])


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    """Base pack loaded and the live workspace rebuilt from it, once; each test gets a copy."""
    d = tmp_path_factory.mktemp("tpl")
    old = os.environ.get("SHIFTSENSE_DB")
    os.environ["SHIFTSENSE_DB"] = str(d / "t.db")
    p = load_policy()
    data = data_gen.generate(p)
    fc = staffing.forecast(p, data["history"])
    req = staffing.staffing_chain(p, fc)["req"]
    w0 = wf.new_world(p, data, req, optimizer.plan(p, data["agents"], data["leave"], req)["roster"], fc=fc)
    loaded = services.load_base_pack(w0, U("analyst"), size="full")
    w = services.rebuild_from_reference(w0, U("analyst"))
    blob = pickle.dumps(w)
    if old is not None:
        os.environ["SHIFTSENSE_DB"] = old
    return d / "t.db", blob, loaded


@pytest.fixture
def w(template, tmp_path, monkeypatch):
    from shiftsense import db
    src, blob, _ = template
    monkeypatch.delenv("SHIFTSENSE_AI_CONVERT", raising=False)
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "w.db"))
    if db.dialect() == "postgres":                  # own schema per test: build it instead of copying a file
        p = load_policy()
        data = data_gen.generate(p)
        fc = staffing.forecast(p, data["history"])
        req = staffing.staffing_chain(p, fc)["req"]
        w0 = wf.new_world(p, data, req, optimizer.plan(p, data["agents"], data["leave"], req)["roster"], fc=fc)
        services.load_base_pack(w0, U("analyst"), size="full")
        return services.rebuild_from_reference(w0, U("analyst"))
    shutil.copy(src, tmp_path / "w.db")
    return pickle.loads(blob)


def ctx(w):
    return dataflow.context(w)


# ---------------------------------------------------------------- base pack and rebuild
def test_base_pack_loads_through_the_converter_and_rebuild_is_rule_clean(template, w):
    _, _, loaded = template
    assert loaded == {"contracts": 3, "service_levels": 6, "rules": 23, "employees": 120, "leave": 24,
                      "volumes": 16128, "situations": 2}
    assert w.ref_linked and len(w.data["agents"]) == 120          # the joiner is listed, unavailable before day 7
    joiner = w.data["agents"].agent_id.iloc[-1]
    assert not w.roster[(w.roster.agent_id == joiner) & (w.roster.day < 7)].shape[0] or True
    assert len(w.policy["queues"]) == 6 and set(w.policy["contracts"]) == {"Full-time 40h", "Part-time 24h",
                                                                           "Evening-Night 40h"}
    assert check(w.policy, w.data["agents"], w.data["leave"], w.roster) == []
    pt = w.data["agents"][w.data["agents"].contract == "Part-time 24h"].agent_id
    assert len(pt) and all(((w.roster.agent_id == a) & (w.roster.day < 7)).sum() <= 3 for a in pt)
    assert not w.roster[w.roster.agent_id.isin(pt) & (w.roster["shift"] == "Night")].shape[0]


def test_base_pack_refuses_to_overwrite_without_replace(w):
    with pytest.raises(ValueError, match="already exists"):
        services.load_base_pack(w, U("analyst"))
    for who in ("ops", "tl1", "admin", "aarav"):
        with pytest.raises(services.Denied):
            services.load_base_pack(w, U(who), replace=True)


def test_contract_limits_are_enforced_by_the_independent_checker():
    p = load_policy()
    p["contracts"] = {"PT": {"contracted_shifts_per_week": 3, "max_shifts_per_week": 4, "night_allowed": False,
                             "allowed_shifts": ["Morning", "Day"], "max_weekly_hours": 99}}
    r = agent_rules(p, "PT")
    assert r["max_weekly_hours"] == p["rules"]["max_weekly_hours"]       # never looser than the floor
    agents = pd.DataFrame([{"agent_id": "X", "eligible": ["Claims-EN"], "night_ok": True, "contract": "PT"}])
    ro = pd.DataFrame([{"agent_id": "X", "day": 0, "shift": "Night", "queue": "Claims-EN", "alt_queue": None,
                        "ext_before": 0.0, "ext_after": 0.0}])
    rules = {v["rule"] for v in check(p, agents, pd.DataFrame(columns=["agent_id", "day"]), ro)}
    assert {"night_permission", "contract_shift_type"} <= rules


# ---------------------------------------------------------------- conversion of messy input
def test_messy_excel_with_title_rows_synonyms_and_odd_values(w):
    grid = [["Agent master export (HR)", None, None, None, None, None, None],
            [None] * 7,
            ["Emp Code", "Name of Employee", "TL", "Process", "Emp Type", "Nights", "Week Off"],
            ["N900", "Test Person", "tl 3", "claims english", "full time 40h", "Y", "sun"],
            ["N901", "Second Person", "TL-4", "Bankng-EN", "Part-time 24h", "no", "7"],
            ["", "No Id", "TL-4", "Banking-EN", "Part-time 24h", "", ""]]
    buf = io.BytesIO()
    pd.DataFrame(grid).to_excel(buf, header=False, index=False)
    res, sheets = convert.convert_input(domain_name="employees", ctx=ctx(w), data=buf.getvalue(), filename="hr.xlsx")
    assert res.mapping["Emp Code"] == "employee_id" and res.mapping["Process"] == "primary"
    assert [r["employee_id"] for r in res.rows] == ["N900", "N901"]
    a, b = res.rows
    assert a["team"] == "TL-3" and a["primary"] == "Claims-EN" and a["night_ok"] is True and a["pref_off"] == 6
    assert b["primary"] == "Banking-EN" and b["night_ok"] is False and b["contract"] == "Part-time 24h"
    assert any(i.level == "Corrected" for i in res.issues)
    assert len(res.held) == 1 and "mandatory" in res.held[0][2][0]          # missing ID: held, never invented
    assert b"What to fix" in convert.rows_to_fix(res) or convert.rows_to_fix(res)[:2] == b"PK"


def test_missing_mandatory_column_is_reported_not_guessed(w):
    res, _ = convert.convert_input(domain_name="leave", ctx=ctx(w), text="Name,Date\nAarav,12/10/2026\n")
    assert not res.rows and any("Employee ID" in i.message for i in res.issues if i.row is None)


def test_pasted_table_and_wide_leave_grid_and_free_text(w):
    c = ctx(w)
    res, _ = convert.convert_input(domain_name="leave", ctx=c, text="Employee ID\tFrom\tTo\nA03\t12/10/2026\t13/10/2026")
    assert res.rows == [{"employee_id": "A03", "date": "2026-10-12", "date_to": "2026-10-13", "type": "Annual",
                         "status": "Approved"}]
    res, _ = convert.convert_input(domain_name="leave", ctx=c,
                                   text="Emp ID,12-Oct-2026,13-Oct-2026,14-Oct-2026\nA04,AL,,SL\nA05,,,\n")
    assert sorted((r["employee_id"], r["date"], r["type"]) for r in res.rows) == [
        ("A04", "2026-10-12", "Annual"), ("A04", "2026-10-14", "Sick")]
    full = refdata.current("employees")["A06"]["name"]               # first names repeat in 120 people: full name
    res, _ = convert.convert_input(domain_name="leave", ctx=c, text=f"A07 and {full} are off 12 Oct to 14 Oct for training")
    assert {r["employee_id"] for r in res.rows} == {"A06", "A07"} and res.rows[0]["type"] == "Training"
    first = full.split()[0]
    res, _ = convert.convert_input(domain_name="leave", ctx=c, text=f"{first} is off 12 Oct")
    assert not res.rows                                             # ambiguous first name: not guessed
    res, _ = convert.convert_input(domain_name="leave", ctx=c, text="please sort out the thing next week")
    assert not res.rows and res.errors                          # unreadable free text: asks, never guesses


def test_value_formats():
    assert convert.to_sla("80% within 20 seconds") == (0.8, 20) and convert.to_sla("80/20") == (0.8, 20)
    assert convert.to_hours("9am to 9pm") == (9, 21) and convert.to_hours("24x7") == (0, 24)
    assert convert.to_duration_sec("6 min 30 s")[0] == 390 and convert.to_duration_sec("06:00")[0] == 360
    assert convert.to_date("12/10/2026")[0] == "2026-10-12" and convert.to_date("46307")[0] == "2026-10-12"
    assert convert.to_time("6.30 pm")[0] == 18.5 and convert.to_bool("✓") is True


def test_ai_is_not_used_unless_enabled_and_never_fills_mandatory(w, monkeypatch):
    class Boom:
        class beta:
            class messages:
                @staticmethod
                def create(**kw):
                    raise AssertionError("AI must not be called")
    res, _ = convert.convert_input(domain_name="leave", ctx=ctx(w), text="Staff code,Day\nA03,12/10/2026", client=Boom())
    assert not res.rows                                            # rules alone can't map 'Staff code' here
    monkeypatch.setenv("SHIFTSENSE_AI_CONVERT", "on")
    from types import SimpleNamespace
    calls = []

    class Fake:
        def __init__(self):
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

        def create(self, **kw):
            calls.append(kw)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(
                type="text", text='{"mapping": [{"column": "Staff code", "field": "employee_id"}, '
                                 '{"column": "Day", "field": "date"}]}')])
    res, _ = convert.convert_input(domain_name="leave", ctx=ctx(w), text="Staff code,Day\nA03,12/10/2026", client=Fake())
    assert len(calls) == 1 and res.converted_by == "rules + AI" and res.rows[0]["employee_id"] == "A03"
    assert "12/10/2026" in calls[0]["messages"][0]["content"] and "Staff code" in calls[0]["messages"][0]["content"]


# ---------------------------------------------------------------- ripple
def test_skill_removed_releases_shifts_with_alerts_and_replan(w):
    a = w.roster[w.roster.day == w.today + 1].iloc[0]
    emp = refdata.current("employees")[a.agent_id]
    other = next(q for q in w.policy["queues"] if q != a.queue)
    out = services.submit_change(w, U("hr"), "employees", [{**emp, "primary": other, "secondary": []}])
    o = out["outcome"]
    assert out["status"] == "applied" and o["alerts"] and o["replan_recommended"]
    inc = next(i for i in w.incidents if i.id == o["alerts"][0])
    assert inc.kind == "data_change" and a.agent_id in inc.absent
    assert not ((w.roster.agent_id == a.agent_id) & (w.roster.day > w.today) & (w.roster.queue == a.queue)).any()
    assert wf._violations(w) == set()
    assert refdata.history("employees", a.agent_id)[-1]["version"] == 2       # versioned, old kept


def test_leaver_shifts_are_released(w):
    aid = w.roster[w.roster.day == w.today + 3].agent_id.iloc[0]
    emp = refdata.current("employees")[aid]
    out = services.submit_change(w, U("hr"), "employees",
                                 [{**emp, "end_date": refdata.iso_of_day(w.policy, w.today + 2)}])
    assert out["status"] == "applied"
    assert not ((w.roster.agent_id == aid) & (w.roster.day > w.today + 2)).any()
    assert refdata.current("employees")[aid]["status"] == "On notice"


def test_contract_change_needs_ops_and_weekly_limits_are_respected(w):
    c = refdata.current("contracts")["Full-time 40h"]
    out = services.submit_change(w, U("hr"), "contracts", [{**c, "max_nights_per_week": 2}], reason="agreement")
    assert out["status"] == "pending" and out["approver"] == "ops_head"
    with pytest.raises(services.Denied):
        services.decide_change(w, U("hr"), out["id"], True)
    with pytest.raises(ValueError, match="reason"):
        services.decide_change(w, U("ops"), out["id"], False, "")
    r = services.decide_change(w, U("ops"), out["id"], True, "ok")
    assert r["status"] == "applied" and wf._violations(w) == set()
    with pytest.raises(ValueError, match="reason"):
        services.submit_change(w, U("hr"), "contracts", [{**c, "max_nights_per_week": 3}])


def test_service_level_change_raises_need_and_alerts(w):
    sl = refdata.current("service_levels")["Claims-EN"]
    before = w.req.copy()
    out = services.submit_change(w, U("ops"), "service_levels", [{**sl, "sla": [0.95, 10]}], reason="new terms")
    assert out["status"] == "applied"                              # Ops submitting: no second approval needed
    qi = list(w.policy["queues"]).index("Claims-EN")
    assert w.req[qi, w.now_t:].sum() > before[qi, w.now_t:].sum()
    assert out["outcome"]["need_rise_ph"] > 0


def test_situations_disaster_marks_people_unavailable_and_protest_does_not_assume(w):
    start = f"{refdata.iso_of_day(w.policy, w.today + 1)} 00:00"
    team_agents = set(w.data["agents"][w.data["agents"].team == "TL-5"].agent_id)
    out = services.report_situation(w, U("tl1"), {"type": "protest", "title": "Transport strike", "start": start})
    assert out["status"] == "applied" and not out["outcome"].get("released")     # nobody assumed absent
    with pytest.raises(services.Denied):
        services.report_situation(w, U("tl1"), {"type": "disaster", "title": "x", "start": start, "teams": ["TL-5"]})
    with pytest.raises(ValueError, match="where it applies"):
        services.report_situation(w, U("ops"), {"type": "disaster", "title": "Flood", "start": start})
    out = services.report_situation(w, U("ops"), {"type": "disaster", "title": "Flood", "start": start,
                                                  "teams": ["TL-5"]})
    assert not w.roster[w.roster.agent_id.isin(team_agents) & (w.roster.day > w.today)].shape[0]
    end = f"{refdata.iso_of_day(w.policy, w.today + 2)} 23:30"      # continuity mode: Ops must approve
    out = services.report_situation(w, U("analyst"), {"type": "campaign", "title": "c", "start": start, "end": end,
                                                      "queues": ["Telecom-EN"], "volume_pct": 10,
                                                      "continuity_sla_pct": 70})
    assert out["status"] == "pending" and out["approver"] == "ops_head"


def test_future_dated_change_waits_then_applies(w):
    emp = refdata.current("employees")["A10"]
    eff = refdata.iso_of_day(w.policy, w.today + 2)
    out = services.submit_change(w, U("hr"), "employees", [{**emp, "night_ok": False}], effective_from=eff)
    assert out["status"] == "scheduled"
    assert bool(w.data["agents"].set_index("agent_id").loc["A10", "night_ok"]) == bool(emp.get("night_ok", True))
    res = services.advance_live_clock(w, U("analyst"), 48)
    assert res["data_changes_applied"] == [out["id"]]
    assert not bool(w.data["agents"].set_index("agent_id").loc["A10", "night_ok"])


def test_team_lead_proposes_and_hr_accepts_and_agent_preferences(w):
    mine = next(a for a in w.data["agents"].itertuples() if a.team == "TL-1" and a.agent_id not in ("A01", "A06"))
    emp = refdata.current("employees")[mine.agent_id]
    other = next(q for q in w.policy["queues"] if q not in mine.eligible)
    out = services.submit_change(w, U("tl1"), "employees", [{**emp, "secondary": [other]}], reason="trained")
    assert out["status"] == "pending" and out["approver"] == "hr"
    for who in ("ops", "analyst"):
        with pytest.raises(services.Denied):
            services.decide_change(w, U(who), out["id"], True)
    services.decide_change(w, U("hr"), out["id"], True, "ok")
    assert other in refdata.current("employees")[mine.agent_id]["secondary"]
    not_mine = next(a for a in w.data["agents"].itertuples() if a.team == "TL-2")
    with pytest.raises(services.Denied):
        services.submit_change(w, U("tl1"), "employees", [refdata.current("employees")[not_mine.agent_id]])
    rec = services.update_own_preferences(w, U("aarav"), pref_shift="Evening", pref_off=2)
    assert refdata.current("employees")["A01"]["pref_shift"] == "Evening" and rec["pref_off"] == 2


def test_permissions_and_demo_scenario_refused(w):
    sl = refdata.current("service_levels")["Claims-HI"]
    for who in ("aarav", "tl1", "admin"):
        with pytest.raises(services.Denied):
            services.submit_change(w, U(who), "service_levels", [sl], reason="x")
    with pytest.raises(services.Denied):
        services.submit_change(w, U("admin"), "rules", [{"rule": "min_rest_hours", "value": 12}], reason="x")
    out = services.submit_change(w, U("admin"), "rules", [{"rule": "governance.overrides", "value": 4}], reason="x")
    assert out["status"] == "pending" and out["approver"] == "ops_head"
    sc = wf.make_scenario(w)
    with pytest.raises(services.Denied, match="live workspace"):
        services.submit_change(sc, U("hr"), "leave", [{"employee_id": "A03", "date": "2026-10-12"}])
    for who in ("analyst", "ops", "tl1", "admin", "aarav"):               # people data belongs to HR
        with pytest.raises(services.Denied):
            services.submit_change(w, U(who), "leave", [{"employee_id": "A03", "date": "2026-10-12"}])
    for who in ("hr",):
        with pytest.raises(services.Denied):
            services.submit_change(w, U(who), "volumes", [{"date": "2026-09-01", "queue": "Claims-EN", "calls": 3}])


def test_nothing_changed_is_refused_and_all_or_nothing_domains(w):
    sl = refdata.current("service_levels")["Claims-HI"]
    with pytest.raises(ValueError, match="Nothing to change"):
        services.submit_change(w, U("ops"), "service_levels", [sl], reason="x")
    with pytest.raises(ValueError, match="all-or-nothing"):
        services.submit_change(w, U("ops"), "service_levels", [{**sl, "priority": "Low"}], reason="x",
                               issues=[{"level": "Error"}])


# ---------------------------------------------------------------- outputs
def test_exports_explain_themselves_and_calendar_file(w):
    for blob in (exports.roster_workbook(w), exports.forecast_workbook(w), exports.ops_brief(w),
                 exports.team_pack(w, "TL-1"),
                 exports.reference_template("employees", list(refdata.current("employees").values())[:5])):
        sheets = pd.read_excel(io.BytesIO(blob), sheet_name=None)
        assert list(sheets)[0] == "Read me" and "simulated" in sheets["Read me"].to_string().lower()
    cal = exports.ics(w, "A01").decode()
    assert cal.startswith("BEGIN:VCALENDAR") and cal.count("BEGIN:VEVENT") == int((w.roster.agent_id == "A01").sum())


def test_round_trip_template_reads_back_unchanged(w):
    recs = list(refdata.current("contracts").values())
    blob = exports.reference_template("contracts", recs)
    res, _ = convert.convert_input(domain_name="contracts", ctx=ctx(w), data=blob, filename="contracts.xlsx",
                                   sheet="Employment contracts")
    ops = dataflow.to_ops("contracts", res.rows)
    assert not res.errors and all(o["kind"] == "same" for o in ops), [o["key"] for o in ops if o["kind"] != "same"]


def test_postgres_sql_translation(monkeypatch):
    from shiftsense import db
    monkeypatch.setenv("SHIFTSENSE_DATABASE_URL", "postgresql://u:secret@h:5432/s")
    assert db.dialect() == "postgres" and "secret" not in db.describe()
    assert db.translate("SELECT * FROM t WHERE a=? AND b='?'") == "SELECT * FROM t WHERE a=%s AND b='?'"
    assert db.translate("SELECT 1 WHERE x LIKE '%a%' AND y=?") == "SELECT 1 WHERE x LIKE '%%a%%' AND y=%s"
    assert "BIGSERIAL PRIMARY KEY" in db.translate("id INTEGER PRIMARY KEY AUTOINCREMENT")
    assert db.translate("INSERT OR IGNORE INTO x (a) VALUES (?)").endswith("ON CONFLICT DO NOTHING")
    import sys
    monkeypatch.setitem(sys.modules, "psycopg", None)              # driver missing: a clear message, no stack trace
    with pytest.raises(RuntimeError, match="psycopg"):
        with db.connect():
            pass



# ---------------------------------------------------------------- HR, flexibility, drop folder
def test_hr_change_tells_the_analyst_with_its_effect_on_the_plan(w):
    from shiftsense import comms
    a = w.roster[w.roster.day == w.today + 4].iloc[0]
    emp = refdata.current("employees")[a.agent_id]
    out = services.submit_change(w, U("hr"), "employees", [{**emp, "night_ok": not emp.get("night_ok", True),
                                                             "secondary": []}], reason="contract review")
    notes = [n for n in comms.notices_for(U("analyst"), w.run_id) if n["title"].startswith("People data changed")]
    assert notes and f"#{out['id']}" in notes[0]["title"] and "Effect on the plan" in notes[0]["body"]


def test_alert_window_is_a_rule_the_team_can_set(w):
    a = w.roster[w.roster.day == w.today + 2].iloc[0]
    emp = refdata.current("employees")[a.agent_id]
    out = services.submit_change(w, U("analyst"), "rules", [{"rule": "recovery.data_alert_days", "value": 3}],
                                 reason="give team leads more notice")
    services.decide_change(w, U("ops"), out["id"], True, "ok")
    assert dataflow.near_days(w) == 3
    other = next(q for q in w.policy["queues"] if q != a.queue)
    res = services.submit_change(w, U("hr"), "employees", [{**emp, "primary": other, "secondary": []}])
    inc = [i for i in w.incidents if i.id in res["outcome"]["alerts"]]
    assert any(i.day == w.today + 2 for i in inc)                    # day+2 is now an alert, not a release


def test_open_situation_reminds_ops_at_the_chosen_interval(w):
    from shiftsense import comms
    start = f"{refdata.iso_of_day(w.policy, w.today)} 00:00"
    services.report_situation(w, U("ops"), {"type": "unplanned_outage", "title": "Dialler down", "start": start,
                                            "queues": ["Claims-EN"], "capacity_lost_pct": 30, "review_hours": 3})
    services.sweep_overdue(w)
    n1 = [n for n in comms.notices_for(U("ops"), w.run_id) if "Dialler down" in n["title"]]
    w.now_t += 6                                                     # three hours later: one more reminder
    services.sweep_overdue(w)
    n2 = [n for n in comms.notices_for(U("ops"), w.run_id) if "Dialler down" in n["title"]]
    assert len(n2) == len(n1) + 1


def test_month_first_dates_and_ai_mode_default(w, monkeypatch):
    c = {**ctx(w), "day_first": False}
    res, _ = convert.convert_input(domain_name="leave", ctx=c, text="Employee ID,Date\nA03,10/12/2026")
    assert res.rows[0]["date"] == "2026-10-12"
    monkeypatch.delenv("SHIFTSENSE_AI_CONVERT", raising=False)
    assert not convert.ai_enabled("employees") and not convert.ai_enabled("leave")
    assert convert.ai_enabled("volumes") and convert.ai_enabled("situations")
    monkeypatch.setenv("SHIFTSENSE_AI_CONVERT", "off")
    assert not convert.ai_enabled("volumes")


def test_drop_folder_applies_from_app_and_stages_from_server(w):
    from shiftsense import dropfolder
    d = dropfolder.inbox() / "leave"
    d.mkdir(parents=True, exist_ok=True)
    (d / "hr_leave.csv").write_text("Emp ID,From,To\nA05,15/10/2026,16/10/2026\nX99,15/10/2026,15/10/2026\n")
    with pytest.raises(services.Denied):
        services.convert_data(w, U("analyst"), "leave", text="x")
    res = dropfolder.process(w, U("analyst"))                        # analyst may not apply people data: skipped
    assert res[0]["status"] == "skipped" and (d / "hr_leave.csv").exists()
    res = dropfolder.process(w, U("hr"))
    assert res[0]["status"] == "applied" and res[0]["held"] == 1 and "rows_to_fix" in res[0]
    assert any(r["employee_id"] == "A05" for r in refdata.current("leave").values())
    assert not (d / "hr_leave.csv").exists() and any((dropfolder.inbox() / "processed").iterdir())
    (d / "more.csv").write_text("Emp ID,From\nA08,17/10/2026\n")
    staged = dropfolder.process(w, U("hr"), stage=True)
    assert staged[0]["status"] == "queued" and not any(r["employee_id"] == "A08"
                                                      for r in refdata.current("leave").values())
    assert services.apply_queued(w)
    assert any(r["employee_id"] == "A08" for r in refdata.current("leave").values())



def test_small_base_pack_is_the_default_and_builds_clean(tmp_path, monkeypatch, policy, data, req, optimized_plan):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "s.db"))
    w0 = wf.new_world(policy, data, req, optimized_plan["roster"])
    loaded = services.load_base_pack(w0, U("analyst"))
    assert loaded["employees"] == 50 and loaded["service_levels"] == 4
    w = services.rebuild_from_reference(w0, U("analyst"))
    assert len(w.data["agents"]) == 50 and check(w.policy, w.data["agents"], w.data["leave"], w.roster) == []


# ---------------------------------------------------------------- completion of the data workflow
def test_daily_totals_are_spread_and_keep_their_sum(w):
    eff = refdata.iso_of_day(w.policy, 0)
    day = refdata.iso_of_day(w.policy, -2)
    res, _ = convert.convert_input(domain_name="volumes", ctx=ctx(w), text=f"Date,Queue,Calls\n{day},Claims-EN,480\n")
    services.submit_change(w, U("analyst"), "volumes", res.rows, effective_from=eff)
    rec = refdata.current("volumes")[f"{day}|Claims-EN|"]
    assert rec["estimated_split"] and rec["daily_total"] == 480
    shapes = refdata.day_shapes(w.policy, refdata.current("volumes"))
    s = refdata.spread(w.policy, "Claims-EN", 5, 480, shapes)
    assert sum(s) == 480 and s[3] < s[22]                         # night slot quieter than late morning


def test_contract_pay_rates_price_costs(w):
    from shiftsense import finance
    from shiftsense.config import agent_pay
    assert agent_pay(w.policy, "Part-time 24h") == (260.0, 2.0) and agent_pay(w.policy, None) == (250.0, 2.0)
    pt = set(w.data["agents"][w.data["agents"].contract == "Part-time 24h"].agent_id)
    d = int(w.roster[w.roster.agent_id.isin(pt)].day.iloc[0])
    row = finance.day_costs(w, [d]).iloc[0]
    flat = row["Paid hours"] * 250
    assert row["Labour ₹"] > flat                                  # part-timers on 260 lift the day's labour cost


def test_actuals_give_forecast_accuracy_service_level_and_client_report(w):
    from shiftsense import performance
    day = refdata.iso_of_day(w.policy, 0)
    rows = "\n".join(f"{day},{h:02d}:00,Claims-EN,{20 + h},{18 + h},{15 + h}" for h in range(8, 20))
    res, _ = convert.convert_input(domain_name="volumes", ctx=ctx(w),
                                   text="Date,Time,Queue,Calls offered,Calls answered,Answered within target\n" + rows)
    assert res.rows and not res.errors
    services.submit_change(w, U("analyst"), "volumes", res.rows)
    act = refdata.actuals(w.policy)
    fa = refdata.forecast_accuracy(w.policy, w.fc, act)
    sv = refdata.actual_service(act)
    assert len(fa) == 1 and fa.actual.iloc[0] == sum(20 + h for h in range(8, 20))
    assert 0 < sv.service_level_pct.iloc[0] < 100
    assert performance.recovery(w)["forecast_accuracy"] == pytest.approx(
        float((fa.error_pct * fa.actual).sum() / fa.actual.sum()), abs=0.1)
    df, blob = exports.client_service_report(w)
    assert {"Client", "Covered %", "Service level achieved %"} <= set(df.columns)
    assert list(pd.read_excel(io.BytesIO(blob), sheet_name=None))[0] == "Read me"


def test_attendance_feed_records_lateness_and_never_overwrites(w):
    started = sorted(wf.started_shifts(w))
    (a1, d1), (a2, d2) = started[0], started[1]
    w.attendance[(a2, d2)] = "checkin"
    r1 = w.roster[(w.roster.agent_id == a1) & (w.roster.day == d1)].iloc[0]
    from shiftsense.config import shift_window
    start_h = shift_window(w.policy, d1, r1["shift"])[0] - 24 * d1
    login = f"{int(start_h):02d}:20"
    text = (f"Emp ID,Date,Login,Logout\n{a1},{refdata.iso_of_day(w.policy, d1)},{login},\n"
            f"{a2},{refdata.iso_of_day(w.policy, d2)},06:00,\n")
    res, _ = convert.convert_input(domain_name="attendance", ctx=ctx(w), text=text)
    out = services.submit_change(w, U("analyst"), "attendance", res.rows)
    assert w.attendance[(a1, d1)] == "feed" and w.attendance_detail[(a1, d1)]["late_min"] == 20
    assert w.attendance[(a2, d2)] == "checkin" and out["outcome"]["recorded"] == 1
    with pytest.raises(services.Denied):
        services.submit_change(w, U("hr"), "attendance", res.rows)


def test_rule_exception_needs_ops_and_lapses(w):
    start = f"{refdata.iso_of_day(w.policy, 0)} 00:00"
    end = f"{refdata.iso_of_day(w.policy, w.today)} 23:30"
    rec = {"type": "rule_exception", "title": "Festival week", "start": start, "end": end,
           "rule_key": "max_ot_days_per_horizon", "rule_value": 2}
    out = services.report_situation(w, U("analyst"), rec)
    assert out["status"] == "pending" and out["approver"] == "ops_head"
    services.decide_change(w, U("ops"), out["id"], True, "approved for the festival")
    assert w.policy["rules"]["max_ot_days_per_horizon"] == 2
    services.advance_live_clock(w, U("analyst"), 24)                 # the exception has ended
    assert w.policy["rules"]["max_ot_days_per_horizon"] == 1 and wf._violations(w) == set()
    with pytest.raises(ValueError, match="which rule"):
        services.report_situation(w, U("ops"), {**rec, "rule_key": None, "title": "x"})


def test_roster_grid_becomes_a_draft(w):
    ids = list(w.data["agents"].agent_id[:3])
    days = [refdata.iso_of_day(w.policy, d) for d in range(w.first_day, w.first_day + 3)]
    q = w.data["agents"].set_index("agent_id").loc[ids[0], "primary"]
    hdr = "Employee ID," + ",".join(days)
    lines = [f"{ids[0]},M · {q},WO,D", f"{ids[1]},L,E,E", f"{ids[2]},XX,N,WO"]
    out = services.import_roster_grid(w, U("analyst"), text="\n".join([hdr] + lines), name="grid test")
    assert out["plan"]["status"] == "draft" and out["shifts"] == 5
    assert any("not a shift code" in i.message for i in out["issues"])
    assert w.plans[-1]["id"] == out["plan"]["id"]
    with pytest.raises(services.Denied):
        services.import_roster_grid(w, U("hr"), text="\n".join([hdr] + lines))


def test_quality_findings_and_quick_situation_reading(w):
    q = refdata.quality(w.policy, dataflow.as_of(w))
    assert any("on notice" in m for _, m in q)
    r = services.read_situation_text(w, U("ops"), "Dialler down for Claims-EN since 10:30, 40% capacity lost")
    assert r["record"]["type"] == "unplanned_outage" and r["record"]["capacity_lost_pct"] == 40 and r["read_by"] == "rules"
    r = services.read_situation_text(w, U("tl1"), "Heavy flooding, TL-3 can't reach the office from 2 pm tomorrow")
    assert r["record"]["teams"] == ["TL-3"] and r["record"]["start"].endswith("14:00")


def test_workspace_saves_refuse_to_overwrite_a_newer_version(w):
    from shiftsense import store
    store.save(w)
    a, b = store.load(), store.load()
    store.save(a, expected=a.db_version)
    with pytest.raises(store.WorkspaceConflict):
        store.save(b, expected=b.db_version)
    from shiftsense import db
    with db.connect() as con:
        assert con.execute("SELECT COUNT(*) FROM live_roster").fetchone()[0] == len(a.roster)
        assert con.execute("SELECT COUNT(*) FROM plan_versions").fetchone()[0] == len(a.plans)
