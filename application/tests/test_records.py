import pytest

from shiftsense import records, staffing
from shiftsense import workflow as wf


@pytest.fixture
def world(policy, data, req, optimized_plan):
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def working(w, day):
    return w.roster[w.roster.day == day].agent_id.tolist()


def test_past_shift_is_worked_only_with_an_attendance_record(world):
    a = working(world, 0)[0]
    assert records.day_record(world, a, 0)["code"] == "unrecorded"      # never assumed
    world.attendance[(a, 0)] = "in"
    assert records.day_record(world, a, 0)["code"] == "worked"
    b = working(world, world.first_day + 2)[0]
    assert records.day_record(world, b, world.first_day + 2)["code"] == "scheduled"


def test_leave_absence_and_no_show_are_recorded(world):
    lv = world.data["leave"].iloc[0]
    assert records.day_record(world, lv.agent_id, int(lv.day))["code"] == "leave"
    a = working(world, world.first_day)[0]
    wf.report_absence(world, [a], world.first_day, "tl", "unwell")
    assert records.day_record(world, a, world.first_day)["code"] == "sick"
    b = working(world, world.first_day)[0]
    wf.report_absence(world, [b], world.first_day, "system", "no-show, not clocked in", kind="no_show")
    assert records.day_record(world, b, world.first_day)["code"] == "no_show"


def test_left_early_and_off(world):
    on_now = [r["agent_id"] for r in world.roster.to_dict("records")
              if r["day"] == world.today and r["shift"] == "Evening"]
    wf.report_left_early(world, on_now[0], "tl")
    assert records.day_record(world, on_now[0], world.today)["code"] == "left_early"
    a = world.data["agents"].agent_id.iloc[0]
    off = next(d for d in range(14) if not ((world.roster.agent_id == a) & (world.roster.day == d)).any()
               and not ((world.data["leave"].agent_id == a) & (world.data["leave"].day == d)).any())
    assert records.day_record(world, a, off)["code"] == "off"


def test_week_views_and_export(world):
    ids = world.data["agents"].agent_id.tolist()[:5]
    wk = records.agent_week(world, ids[0], 0)
    assert len(wk) == 7 and {"label", "code", "detail"} <= set(wk[0])
    grid = records.team_week(world, ids, 1)
    assert grid.shape == (5, 8)
    s = records.summary(world, ids[0], range(14))
    assert sum(s[k] for k in records.COUNTED) == 14
    ex = records.export(world, ids)
    assert len(ex) == 5 * 14 and set(ex.status) <= {v[0] for v in records.STATUS.values()}
