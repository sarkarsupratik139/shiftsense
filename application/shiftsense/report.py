"""Weekly operations report: coverage, attendance, overtime, alerts, requests, schedule changes."""
from __future__ import annotations

import io

import numpy as np
import pandas as pd

from . import attendance as attendance_mod, history, records
from .config import day_label, intervals_per_day, queue_names, warmup_mask
from .roster import coverage

KIND = {"absence": "Absence", "no_show": "No-show", "left_early": "Left early", "spike": "Call spike",
        "data_change": "Data change", "requirement": "Staffing need changed"}
STATE = {"open": "Waiting for team lead", "needs_new_fix": "Waiting for team lead", "fixed": "Fixed",
         "escalated": "With Ops", "closed": "Closed by Ops", "no_gap": "No cover needed"}


def weekly_report(w, week: int) -> dict:
    p = w.policy
    days = records.week_days(w, week)
    per_day = intervals_per_day(p)
    queues = queue_names(p)
    cov = coverage(p, w.roster)
    mask = warmup_mask(p)

    # coverage by queue and by day
    q_rows, d_rows = [], []
    for qi, q in enumerate(queues):
        row = {"Queue": q}
        need_all = got_all = 0
        for d in days:
            sl = slice(d * per_day, (d + 1) * per_day)
            r, c, m = w.req[qi, sl], cov[qi, sl], mask[sl]
            need, got = (r * m).sum(), (np.minimum(r, c) * m).sum()
            need_all, got_all = need_all + need, got_all + got
            row[day_label(p, d)] = round(100 * got / need, 1) if need else 100.0
        d_rows.append(row)
        q_rows.append({"Queue": q, "Covered %": round(100 * got_all / max(need_all, 1), 1),
                       "Short (people-hours)": round((need_all - got_all) / 2, 1)})
    by_queue, by_day = pd.DataFrame(q_rows), pd.DataFrame(d_rows)

    # attendance per agent
    agents = w.data["agents"].set_index("agent_id")
    a_rows = []
    for a in agents.index:
        s = records.summary(w, a, days)
        a_rows.append({"Agent": f"{agents.loc[a, 'name']} ({a})", "Team": agents.loc[a, "team"],
                       "Worked": s["worked"] + s["overtime"] + s["extra"] + s["swap"],
                       "Left early": s["left_early"], "No-show": s["no_show"], "Absent": s["sick"],
                       "Leave": s["leave"], "No attendance record": s["unrecorded"], "Scheduled (future)": s["scheduled"],
                       "Approved OT hours": s["ot_hours"]})
    attendance = pd.DataFrame(a_rows)

    # alerts (incidents) in the week
    inc = [i for i in w.incidents if i.day in days]
    alerts = pd.DataFrame([{
        "Date": day_label(p, i.day), "Type": KIND.get(i.kind, i.kind), "Team": i.owner_team,
        "Who": ", ".join(i.absent) or i.title, "Status": STATE.get(i.status, i.status),
        "Fix cost ₹": round(sum(a.cost for a in i.applied)),
    } for i in inc], columns=["Date", "Type", "Team", "Who", "Status", "Fix cost ₹"])

    reqs = [r for r in w.requests if r["day"] in days]
    requests = pd.DataFrame([{
        "Date": day_label(p, r["day"]), "Agent": r["agent_id"], "Type": "Swap" if r.get("kind") == "swap" else "Time off",
        "Check": r["status"], "State": r["state"], "Reason": r.get("reason", ""),
    } for r in reqs], columns=["Date", "Agent", "Type", "Check", "State", "Reason"])

    changes = []
    for c in history.changes(getattr(w, "run_id", None)):
        hits = [d for d in c["details"] if d["day"] in days]
        if hits:
            changes.append({"When": c["sim_time"], "Event": c["event"], "By": c["actor"],
                            "Shifts changed": len(hits), "Summary": c["summary"]})
    changes = pd.DataFrame(changes, columns=["When", "Event", "By", "Shifts changed", "Summary"])

    worked = int(attendance.Worked.sum())
    # attendance rate only over shifts that have started and have a recorded outcome
    past = [d for d in days if d <= w.today]
    p_codes = [records.day_record(w, a, d)["code"] for a in agents.index for d in past]
    p_worked = sum(c in ("worked", "overtime", "extra", "swap", "left_early") for c in p_codes)
    missed = sum(c in ("no_show", "sick") for c in p_codes)
    ot = float(attendance["Approved OT hours"].sum())
    need = sum((w.req[:, d * per_day:(d + 1) * per_day] * mask[d * per_day:(d + 1) * per_day]).sum() for d in days)
    got = sum((np.minimum(w.req, cov)[:, d * per_day:(d + 1) * per_day] * mask[d * per_day:(d + 1) * per_day]).sum()
              for d in days)
    summary = {
        "Context": "Demo scenario (simulated, not actual)" if getattr(w, "scenario", False)
        else "Live (synthetic PoC dataset)",
        "Week": f"{day_label(p, days[0])} – {day_label(p, days[-1])}",
        "Coverage %": round(100 * got / max(need, 1), 1),
        "Shifts worked (recorded)": worked,
        "Attendance %": round(100 * p_worked / (p_worked + missed), 1) if p_worked else "Unavailable",
        "Started shifts without attendance record": int(attendance["No attendance record"].sum()),
        "Genuine check-ins": sum(1 for (a, d), v in w.attendance.items() if d in days and v == "checkin"),
        "Late check-ins (after shift start)": len(attendance_mod.late_checkins(getattr(w, "run_id", ""), set(days))),
        "Confirmed by team lead": sum(1 for (a, d), v in w.attendance.items() if d in days and v == "confirmed"),
        "From attendance feed": sum(1 for (a, d), v in w.attendance.items() if d in days and v == "feed"),
        "Simulated attendance records": sum(1 for (a, d), v in w.attendance.items() if d in days and v == "in"),
        "No-shows": int(attendance["No-show"].sum()),
        "Absences": int(attendance.Absent.sum()),
        "Leave days": int(attendance.Leave.sum()),
        "Approved overtime hours": ot,
        "Approved overtime cost ₹ (configured rates)": round(ot * p["costs"]["hourly_rate"] * p["costs"]["ot_multiplier"]),
        "Alerts": len(alerts), "Alerts fixed": int((alerts.Status == "Fixed").sum()),
        "Sent to Ops": int(alerts.Status.isin(["With Ops", "Closed by Ops"]).sum()),
        "Requests": len(requests), "Requests approved": int((requests.State == "Approved").sum()),
        "Schedule changes": int(changes["Shifts changed"].sum()) if len(changes) else 0,
        "Status": "complete" if days[-1] < w.today else ("in progress" if days[0] <= w.today else "upcoming"),
    }
    return {"summary": summary, "by_queue": by_queue, "by_day": by_day, "attendance": attendance,
            "alerts": alerts, "requests": requests, "changes": changes}


def to_excel(rep: dict) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        pd.DataFrame(list(rep["summary"].items()), columns=["Measure", "Value"]).astype(str).to_excel(
            xw, sheet_name="Summary", index=False)
        rep["by_queue"].to_excel(xw, sheet_name="Coverage by queue", index=False)
        rep["by_day"].to_excel(xw, sheet_name="Coverage by day", index=False)
        rep["attendance"].to_excel(xw, sheet_name="Attendance", index=False)
        rep["alerts"].to_excel(xw, sheet_name="Alerts", index=False)
        rep["requests"].to_excel(xw, sheet_name="Requests", index=False)
        rep["changes"].to_excel(xw, sheet_name="Schedule changes", index=False)
    return buf.getvalue()
