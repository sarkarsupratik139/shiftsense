"""Per-agent attendance and leave records, derived from the saved workspace.

Truthfulness rule: a shift that has started only counts as worked when an attendance record exists:
a genuine check-in ("checkin") or, inside the demo scenario only, the simulated attendance system ("in"). Without one it is shown as "No attendance record" — the
system never assumes attendance. Absences, no-shows and early departures are recorded events.
"""
from __future__ import annotations

import pandas as pd

from .config import day_label, intervals_per_day

# code -> (label, short calendar text)
STATUS = {
    "worked": ("Worked", "Worked"),
    "overtime": ("Worked + overtime", "Worked +OT"),
    "extra": ("Extra shift", "Extra shift"),
    "swap": ("Swapped shift", "Swap"),
    "left_early": ("Left early", "Left early"),
    "no_show": ("No-show", "No-show"),
    "sick": ("Absent", "Absent"),
    "leave": ("Leave", "Leave"),
    "scheduled": ("Scheduled", "Scheduled"),
    "unrecorded": ("No attendance record", "No record"),
    "off": ("Day off", "Off"),
}
COUNTED = ["worked", "overtime", "extra", "swap", "left_early", "no_show", "sick", "leave", "scheduled", "unrecorded",
           "off"]
ACTUAL = ("worked", "overtime", "extra", "swap", "left_early", "no_show", "sick")   # recorded events only


def day_record(w, agent_id: str, day: int) -> dict:
    """What happened (or is planned) for one agent on one day."""
    per_day = intervals_per_day(w.policy)
    lv = w.data["leave"]
    hit = lv[(lv.agent_id == agent_id) & (lv.day == day)]
    if not hit.empty:
        reason = str(hit.iloc[0].get("reason", "approved leave"))
        if "no-show" in reason:
            code = "no_show"
        elif reason.startswith("absent"):
            code = "sick"
        else:
            code = "leave"
        detail = reason.replace("absent (", "").rstrip(")")
        return {"code": code, "detail": detail, "shift": "", "queue": ""}
    ro = w.roster
    row = ro[(ro.agent_id == agent_id) & (ro.day == day)]
    if row.empty:
        return {"code": "off", "detail": "", "shift": "", "queue": ""}
    r = row.iloc[0]
    started = day * per_day + w.policy["shifts"][r["shift"]]["start"] * (per_day // 24) <= w.now_t
    seen = w.attendance.get((agent_id, int(day)))
    change = {"open_shift": "approved extra shift", "swap": "approved swap"}.get(r.kind, "")
    if r.ext_before + r.ext_after > 0:
        change = f"approved +{r.ext_before + r.ext_after:g} h"
    if not started:
        code = "scheduled"
    elif r.kind == "left_early" or r.ext_after < 0:
        code = "left_early"
    elif seen not in ("in", "checkin", "confirmed", "feed"):
        code = "unrecorded"
    elif r.kind == "open_shift":
        code = "extra"
    elif r.kind == "swap":
        code = "swap"
    elif r.ext_before + r.ext_after > 0:
        code = "overtime"
    else:
        code = "worked"
    detail = f"{r['shift']} · {r.queue}" + (f" ({change})" if change else "")
    if started and seen == "checkin":
        detail += " · checked in"
    elif started and seen == "confirmed":
        detail += " · confirmed by team lead"
    elif started and seen == "feed":
        detail += " · attendance feed"
    elif started and seen == "in":
        detail += " · simulated attendance"
    return {"code": code, "detail": detail, "shift": r["shift"], "queue": r.queue}


def week_days(w, week: int) -> list[int]:
    return [d for d in range(week * 7, week * 7 + 7) if d < w.policy["horizon"]["days"]]


def agent_week(w, agent_id: str, week: int) -> list[dict]:
    return [{"day": d, "label": day_label(w.policy, d), **day_record(w, agent_id, d)} for d in week_days(w, week)]


def team_week(w, agent_ids: list[str], week: int) -> pd.DataFrame:
    """Rows = agents, columns = days, cell = short status code."""
    rows = []
    for a in agent_ids:
        row = {"agent_id": a}
        for d in week_days(w, week):
            row[day_label(w.policy, d)] = day_record(w, a, d)["code"]
        rows.append(row)
    return pd.DataFrame(rows)


def summary(w, agent_id: str, days: list[int]) -> dict:
    counts = {k: 0 for k in COUNTED}
    for d in days:
        c = day_record(w, agent_id, d)["code"]
        if c in counts:
            counts[c] += 1
    ro = w.roster[(w.roster.agent_id == agent_id) & w.roster.day.isin(days)]
    counts["ot_hours"] = float(ro.ext_before.clip(lower=0).sum() + ro.ext_after.clip(lower=0).sum()
                               + 8 * (ro.kind == "open_shift").sum())
    return counts


def export(w, agent_ids: list[str]) -> pd.DataFrame:
    """Full fortnight, one row per agent-day (for download)."""
    names = w.data["agents"].set_index("agent_id")
    rows = []
    for a in agent_ids:
        for d in range(w.policy["horizon"]["days"]):
            rec = day_record(w, a, d)
            rows.append({"agent_id": a, "name": names.loc[a, "name"], "team": names.loc[a, "team"],
                         "date": day_label(w.policy, d), "status": STATUS[rec["code"]][0], "detail": rec["detail"]})
    return pd.DataFrame(rows)
