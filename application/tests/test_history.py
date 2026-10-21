"""History for all five roles: server-side scope, identifier substitution, filters/pagination, version
preservation, corrections, live/demo context, honest empty state, export scope, persistence, no tokens."""
import pytest

from shiftsense import attendance, auth, comms, planning, services, staffing, store, timeline
from shiftsense import workflow as wf

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "h.db"))
    monkeypatch.delenv("SHIFTSENSE_EMAIL", raising=False)
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def U(name):
    return auth.authenticate(name, PW[name])


def team(w):
    return dict(zip(w.data["agents"].agent_id, w.data["agents"].team))


def busy(w):
    """Put real events into the run: TL-1 and TL-2 absences, an Aarav request + decision, a reminder + check-in,
    an account change, a plan draft and a governance scan."""
    t = team(w)
    d = w.first_day
    a1 = next(a for a in w.roster[w.roster.day == d].agent_id if t[a] == "TL-1" and a not in ("A01", "A06"))
    a2 = next(a for a in w.roster[w.roster.day == d].agent_id if t[a] == "TL-2" and a != "A02")
    i1 = services.report_absence(w, U("tl1"), [a1], d)
    i2 = services.report_absence(w, U("tl2"), [a2], d)
    rd = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A01") & (w.roster.day == x)).any()
              and wf.leave_check(w, "A01", x)["status"] != "Green")
    r = services.submit_leave(w, U("aarav"), rd)
    services.decide_request(w, U("tl1"), r["id"], False, "team short that day")
    dd = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A01") & (w.roster.day == x)).any())
    out = services.send_attendance_reminder(w, U("tl1"), "A01", dd)
    acc = __import__("shiftsense.accounts", fromlist=["x"]).get("sneha")
    services.update_account(w, U("admin"), "sneha", {"email": "sneha.g@example.com"}, acc["version"])
    services.draft_plan(w, U("analyst"), w.roster, "h", "test")
    services.post_message(w, U("aarav"), "agent:A01", "private note to my team lead")
    return {"i1": i1, "i2": i2, "a1": a1, "a2": a2, "req": r, "rem": out, "dd": dd}


def test_every_role_sees_its_permitted_history(w):
    ctx = busy(w)
    seen = {u: services.history(w, U(u)) for u in ("aarav", "tl1", "ops", "analyst", "admin")}
    assert all(seen.values()), {k: len(v) for k, v in seen.items()}
    ag = seen["aarav"]
    assert any(e["Record"] == ctx["req"]["id"] for e in ag)                   # own request decision
    assert any(e["Type"] == "Attendance" for e in ag)                           # own reminder
    assert any(e["Type"] == "Message" and "private note" in e["Event"] for e in ag)
    assert any(e["Record"] == ctx["i1"].id for e in seen["tl1"])
    assert any(e["Event"] == "Create draft" for e in seen["analyst"])
    assert any(e["Type"] == "Account" for e in seen["admin"])


def test_restricted_records_are_not_in_other_scopes(w):
    ctx = busy(w)
    ag = services.history(w, U("aarav"))
    assert all("A01" in e["Agents"].split(", ") or e["Type"] in ("Notification", "Message") for e in ag)
    assert not any(e["Record"] == ctx["i1"].id for e in ag)                    # someone else's absence
    tl1 = services.history(w, U("tl1"))
    assert not any(e["Record"] == ctx["i2"].id for e in tl1)                   # other team's alert
    assert not any(ctx["a2"] in e["Agents"] for e in tl1)
    assert not any(e["Type"] == "Account" for e in tl1 + services.history(w, U("ops")) + services.history(w, U("analyst")))
    for who in ("ops", "analyst", "admin", "tl2", "sneha"):                    # private thread stays private
        assert not any("private note" in e["Event"] for e in services.history(w, U(who)))
    assert not any(e["Type"] == "Decision" and ctx["i1"].id == e["Record"] for e in services.history(w, U("analyst")))


def test_identifier_and_team_substitution_is_refused(w):
    busy(w)
    with pytest.raises(services.Denied):
        services.history(w, U("aarav"), agent_id="A06")
    with pytest.raises(services.Denied):
        services.history(w, U("tl1"), team="TL-2")
    tl2_agent = next(a for a, t in team(w).items() if t == "TL-2")
    with pytest.raises(services.Denied):
        services.history(w, U("tl1"), agent_id=tl2_agent)
    for forged in (dict(U("aarav"), agent_id="A06"), dict(U("aarav"), role="admin"), dict(U("tl1"), team="TL-2"),
                   dict(U("ops"), role="admin")):
        with pytest.raises(services.Denied):          # identity is re-read from the stored account every time
            services.history(w, forged)
        with pytest.raises(services.Denied):
            services.history_export(w, forged)


def test_filters_pagination_and_sorting_are_consistent(w):
    busy(w)
    rows = services.history(w, U("admin"))
    df_all, total = timeline.query(rows, size=10 ** 6)
    pages = [timeline.query(rows, page=p, size=5)[0] for p in range(1, -(-total // 5) + 1)]
    assert sum(len(p) for p in pages) == total == len(df_all)
    assert [r for p in pages for r in p.Source] == list(df_all.Source)        # pages tile the full list in order
    old, _ = timeline.query(rows, newest_first=False, size=10 ** 6)
    assert list(old.Source) == list(reversed(list(df_all.Source)))
    only, n = timeline.query(rows, types=["Account"], size=10 ** 6)
    assert n == len(only) and set(only.Type) == {"Account"}
    hit, n = timeline.query(rows, text="team short that day", size=10 ** 6)
    assert n >= 1 and all("team short that day" in " ".join(map(str, r)).lower() for r in hit.values)


def test_publishing_never_rewrites_earlier_versions(w):
    busy(w)
    v0 = w.plans[0]["roster"].copy()
    res = planning.run_scenario(w, planning.Scenario(name="re", headcount_change=-2), time_limit=20)
    p = services.draft_plan(w, U("analyst"), res["roster"], "re", "test")
    services.validate_plan(w, U("analyst"), p["id"])
    services.publish_plan(w, U("analyst"), p["id"], reviewed=True)
    assert w.plans[0]["status"] == "superseded" and w.plans[0]["roster"].equals(v0)
    assert any(e["Event"] == "Publish plan" for e in services.history(w, U("analyst")))


def test_attendance_correction_rules_and_history(w):
    ctx = busy(w)
    d = ctx["dd"]
    r = w.roster[(w.roster.agent_id == "A06") & (w.roster.day == d)]
    if r.empty:
        d = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A06") & (w.roster.day == x)).any())
    with pytest.raises(ValueError, match="not started"):
        services.correct_attendance(w, U("tl1"), "A06", d, "seen")
    w.now_t = d * 48 + 40
    with pytest.raises(ValueError, match="reason"):
        services.correct_attendance(w, U("tl1"), "A06", d, " ")
    with pytest.raises(services.Denied):
        services.correct_attendance(w, U("tl2"), "A06", d, "x")              # other team
    for who in ("aarav", "ops", "admin", "analyst"):
        with pytest.raises(services.Denied):
            services.correct_attendance(w, U(who), "A06", d, "x")
    services.correct_attendance(w, U("tl1"), "A06", d, "seen on the floor")
    assert w.attendance[("A06", d)] == "confirmed"
    with pytest.raises(ValueError, match="never overwrite"):
        services.correct_attendance(w, U("tl1"), "A06", d, "again")
    ev = [e for e in services.history(w, U("sneha")) if e["Event"] == "Attendance correction"]
    assert ev and ev[0]["Before"] == "no record" and ev[0]["Reason"] == "seen on the floor"
    from shiftsense import records
    assert "confirmed by team lead" in records.day_record(w, "A06", d)["detail"]


def test_discrepancy_report_goes_to_team_lead_and_changes_nothing(w):
    busy(w)
    before = dict(w.attendance)
    services.report_discrepancy(w, U("aarav"), w.today, "I worked but could not check in")
    with pytest.raises(ValueError, match="already"):
        services.report_discrepancy(w, U("aarav"), w.today, "I worked but could not check in")
    assert w.attendance == before
    assert any("Record problem reported" in n["title"] for n in comms.notices_for(U("tl1"), w.run_id))
    assert not any("Record problem reported" in n["title"] for n in comms.notices_for(U("tl2"), w.run_id))
    with pytest.raises(services.Denied):
        services.report_discrepancy(w, U("tl1"), w.today, "x")


def test_live_and_demo_history_are_distinguishable(w):
    busy(w)
    sc = services.load_demo_story(w, U("analyst"))
    services.advance_clock(sc, U("analyst"), 6, rate=0.3)
    live_rows = services.history(w, U("admin"))
    sim_rows = services.history(sc, U("admin"))
    assert {e["Context"] for e in live_rows} == {"Live"}
    assert {e["Context"] for e in sim_rows} == {"Demo scenario (simulated)"}
    assert "Demo scenario (simulated)" in services.history_export(sc, U("admin"))


def test_empty_history_is_empty_not_invented(w):
    assert services.history(w, U("priya")) == []
    assert timeline.query([], size=25)[1] == 0


def test_export_is_scoped_and_audited(w):
    busy(w)
    csv = services.history_export(w, U("aarav"))
    assert "private note" in csv and "team short that day" in csv
    rows = csv.strip().splitlines()[1:]
    assert all("A01" in r or ",Notification," in r or ",Message," in r for r in rows)
    assert any(r["action"] == "History export" for r in comms.audit_rows(w.run_id))
    other = services.history_export(w, U("tl2"))
    assert "private note" not in other


def test_history_survives_restart_and_holds_no_tokens(w):
    ctx = busy(w)
    store.save(w)
    n = len(services.history(w, U("admin")))
    w2 = wf.upgrade(store.load())
    assert len(services.history(w2, U("admin"))) == n
    everything = str(services.history(w2, U("admin"))) + services.history_export(w2, U("admin"))
    everything += str(attendance.log(w2.run_id))
    assert "checkin=" not in everything and "sneha.g@example.com" not in everything   # no links, no raw addresses


def test_whatif_runs_are_recorded_without_touching_live(w):
    fp = wf.roster_fp(w.roster)
    services.record_whatif(w, U("analyst"), "spike test", "coverage 90% -> 93%")
    assert wf.roster_fp(w.roster) == fp
    assert any(e["Event"] == "What-if run" for e in services.history(w, U("analyst")))
    with pytest.raises(services.Denied):
        services.record_whatif(w, U("ops"), "x", "y")
