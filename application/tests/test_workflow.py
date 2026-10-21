import pytest

from shiftsense import workflow as wf
from shiftsense.constraints import check


@pytest.fixture
def world(policy, data, req, optimized_plan):
    from shiftsense import staffing
    fc = staffing.forecast(policy, data["history"])
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=fc)


def working(w, day, n=1, queue=None):
    r = w.roster[w.roster.day == day]
    if queue:
        r = r[r.queue == queue]
    return r.agent_id.tolist()[:n]


def ok(w):
    assert check(w.policy, w.data["agents"], w.data["leave"], w.roster) == []


def test_clock_and_first_day(world):
    assert world.first_day == world.today + 1
    with pytest.raises(ValueError):
        wf.report_absence(world, working(world, world.today - 1), world.today - 1, "tl1")


def test_absence_creates_incident_with_fix(world):
    day = world.first_day
    who = working(world, day, 3, "Claims-EN")
    inc = wf.report_absence(world, who, day, "tl1")
    assert inc.status == "open"
    assert inc.rec["actions"]
    assert not world.roster[(world.roster.day == day) & world.roster.agent_id.isin(who)].shape[0]
    assert wf.gap_summary(world, inc)


def test_approve_applies_fix_notifies_and_stays_compliant(world):
    day = world.first_day
    inc = wf.report_absence(world, working(world, day, 3, "Claims-EN"), day, "tl1")
    wf.approve_fix(world, inc, "tl1")
    assert inc.status in ("fixed", "escalated")
    ok(world)
    targets = {a.agent_id for a in inc.applied}
    assert targets <= {n["agent_id"] for n in world.notifications}
    # the absent agent cannot be scheduled back on that day
    assert not world.roster[(world.roster.day == day) & world.roster.agent_id.isin(inc.absent)].shape[0]


def test_decline_undoes_step_and_proposes_new_fix(world):
    day = world.first_day + 1
    inc = wf.report_absence(world, working(world, day, 3, "Claims-EN"), day, "tl1")
    wf.approve_fix(world, inc, "tl1")
    n = next((x for x in world.notifications if x["kind"] == "confirm"), None)
    if n is None:
        pytest.skip("fix needed no confirmations")
    before = len(world.roster)
    wf.respond(world, n["id"], accept=False, by=n["agent_id"])
    assert inc.status in ("needs_new_fix", "no_gap")
    assert n["agent_id"] in inc.declined_agents
    assert n["agent_id"] not in {a.agent_id for a in inc.rec["actions"]}
    ok(world)
    assert len(world.roster) <= before


def test_change_and_reject_need_reason(world):
    day = world.first_day
    inc = wf.report_absence(world, working(world, day, 2, "Claims-EN"), day, "tl1")
    first = inc.rec["actions"][0].id
    with pytest.raises(ValueError):
        wf.change_fix(world, inc, [first], "tl1", "")
    wf.change_fix(world, inc, [first], "tl1", "agent on training")
    assert first not in {a.id for a in inc.rec["actions"]}
    with pytest.raises(ValueError):
        wf.reject_fix(world, inc, "tl1", " ")
    wf.reject_fix(world, inc, "tl1", "prefer agency staff")
    assert inc.status in ("open", "no_gap")                 # reject = ask for another fix
    if inc.status == "open":
        with pytest.raises(ValueError):
            wf.escalate_fix(world, inc, "tl1", "")
        wf.escalate_fix(world, inc, "tl1", "agency needed")
        assert inc.status == "escalated"
    wf.ops_close(world, inc, "Approve agency cover", "2 temps booked", "ops")
    assert inc.status == "closed"


def test_leave_green_auto_and_red_goes_to_tl(world):
    results = []
    for r in world.roster[world.roster.day >= world.first_day].head(60).itertuples():
        chk = wf.leave_check(world, r.agent_id, r.day)
        results.append((chk["status"], r.agent_id, r.day))
    green = next((x for x in results if x[0] == "Green"), None)
    red = next((x for x in results if x[0] == "Red"), None)
    if green:
        req = wf.submit_leave(world, green[1], green[2], green[1])
        assert req["state"] == "Approved"
        assert ((world.data["leave"].agent_id == green[1]) & (world.data["leave"].day == green[2])).any()
    assert red is not None
    req = wf.submit_leave(world, red[1], red[2], red[1])
    assert req["state"] == "Pending"
    with pytest.raises(ValueError):
        wf.decide_leave(world, req["id"], False, "tl1", "")
    wf.decide_leave(world, req["id"], False, "tl1", "peak week")
    assert req["state"] == "Rejected"
    assert any("peak week" in n["text"] for n in world.notifications if n["agent_id"] == red[1])


def test_approved_swap_alternative_is_applied(world):
    for r in world.roster[world.roster.day >= world.first_day].itertuples():
        chk = wf.leave_check(world, r.agent_id, r.day)
        swap = next((a for a in chk["alternatives"] if a["type"] == "Swap"), None)
        if swap:
            req = wf.submit_leave(world, r.agent_id, r.day, r.agent_id, alternative=swap)
            wf.decide_leave(world, req["id"], True, "tl1")
            ro = world.roster
            assert not ((ro.agent_id == r.agent_id) & (ro.day == r.day)).any()
            assert ((ro.agent_id == swap["partner"]) & (ro.day == r.day)).any()
            ok(world)
            return
    pytest.skip("no swap alternative in this dataset")


def test_replan_keeps_frozen_days_and_rules(world):
    day = world.first_day + 2
    inc = wf.report_absence(world, working(world, day, 2), day, "tl1")
    wf.approve_fix(world, inc, "tl1")
    frozen = world.roster[world.roster.day <= world.first_day].reset_index(drop=True)
    res = wf.replan(world, "analyst")
    assert res["ok"], res
    ok(world)
    assert world.roster[world.roster.day <= world.first_day].reset_index(drop=True).equals(frozen)
    assert not world.roster[(world.roster.day == day) & world.roster.agent_id.isin(inc.absent)].shape[0]


def test_clock_advances_and_detects_no_shows(world, policy):
    with pytest.raises(ValueError, match="only in the demo scenario"):
        wf.advance(world, 1, "admin")                    # never on the live workspace
    world = wf.make_scenario(world)
    policy_hi = {**world.policy, "recovery": {**world.policy["recovery"], "no_show_rate": 0.3}}
    world.policy = policy_hi
    start = world.now_t
    res = wf.advance(world, 14, "admin")                 # Tue 18:00 -> Wed 08:00
    assert world.now_t == start + 28
    assert world.today == 2
    assert res["no_shows"], "expected some no-shows at a 30% rate"
    inc = res["no_shows"][0]
    assert inc.kind == "no_show" and inc.owner_team
    assert all(v in ("in", "no-show") for v in world.attendance.values())
    ok(world)


def test_no_shows_are_deterministic(policy, data, req, optimized_plan):
    runs = []
    for _ in range(2):
        w = wf.make_scenario(wf.new_world(policy, data, req, optimized_plan["roster"]))
        w.policy = {**policy, "recovery": {**policy["recovery"], "no_show_rate": 0.2}}
        wf.advance(w, 14, "admin")
        runs.append(sorted(k for k, v in w.attendance.items() if v == "no-show"))
    assert runs[0] == runs[1]


def test_protected_agents_never_no_show(world):
    world = wf.make_scenario(world)
    world.policy = {**world.policy, "recovery": {**world.policy["recovery"], "no_show_rate": 1.0}}
    wf.advance(world, 14, "admin", protected={"A01"})
    assert all(v == "in" for (a, _), v in world.attendance.items() if a == "A01")


def test_left_early_truncates_shift_and_finds_cover(world):
    on_now = [r for r in world.roster.to_dict("records")
              if r["day"] == world.today and r["shift"] == "Evening"]
    aid = on_now[0]["agent_id"]
    inc = wf.report_left_early(world, aid, "tl1")
    row = world.roster[(world.roster.agent_id == aid) & (world.roster.day == world.today)].iloc[0]
    assert row.ext_after < 0
    assert inc.kind == "left_early"
    assert aid not in {a.agent_id for a in inc.rec["actions"]}
    ok(world)


def test_demand_spike_raises_need_and_proposes_cover(world):
    before = world.req.copy()
    inc = wf.demand_spike(world, "Claims-EN", 40, 3, "ops")
    assert (world.req >= before).all() and (world.req > before).any()
    assert inc.kind == "spike" and inc.owner_team
    if inc.status == "open":
        wf.approve_fix(world, inc, "tl")
        ok(world)


def _incident_with_offer(world):
    for day in range(world.first_day, world.first_day + 4):
        who = working(world, day, 3, "Claims-EN")
        inc = wf.report_absence(world, who, day, "tl1")
        if any(a.kind == "open_shift" for a in inc.rec["actions"]):
            wf.approve_fix(world, inc, "tl1")
            return inc
    pytest.skip("no open-shift step in this dataset")


def test_open_shift_offered_to_several_first_accept_wins(world):
    inc = _incident_with_offer(world)
    offer = inc.offers[0]
    notes = [n for n in world.notifications if n.get("offer") == offer["id"]]
    assert len(notes) == len(offer["actions"]) >= 1
    rows_before = len(world.roster)
    winner = notes[-1]
    wf.respond(world, winner["id"], True, winner["agent_id"])
    assert offer["status"] == "filled" and offer["filled_by"] == winner["agent_id"]
    assert len(world.roster) == rows_before + 1
    assert all(n["status"] == "withdrawn" for n in notes if n is not winner)
    ok(world)


def test_open_shift_declined_by_all_triggers_new_fix(world):
    inc = _incident_with_offer(world)
    offer = inc.offers[0]
    for n in [n for n in world.notifications if n.get("offer") == offer["id"]]:
        wf.respond(world, n["id"], False, n["agent_id"])
    assert offer["status"] == "unfilled"
    assert inc.status in ("needs_new_fix", "no_gap")
    assert not set(offer["declined"]) & {a.agent_id for a in inc.rec["actions"]}


def test_swap_request_needs_colleague_then_team_lead(world):
    from shiftsense import requests as rq
    for r in world.roster[world.roster.day >= world.first_day].itertuples():
        opts = rq.swap_options(world.policy, world.data, world.roster, r.agent_id, r.day, min_day=world.first_day)
        if opts:
            break
    else:
        pytest.skip("no swap options")
    req = wf.request_swap(world, r.agent_id, r.day, opts[0], r.agent_id)
    ask = world.notifications[-1]
    assert ask["kind"] == "swap_ask" and ask["agent_id"] == opts[0]["partner"]
    wf.respond(world, ask["id"], True, ask["agent_id"])
    assert req["state"] == "Pending"
    wf.decide_leave(world, req["id"], True, "tl1")
    ro = world.roster
    assert ((ro.agent_id == opts[0]["partner"]) & (ro.day == r.day)).any()
    assert ((ro.agent_id == r.agent_id) & (ro.day == opts[0]["partner_day"])).any()
    ok(world)
