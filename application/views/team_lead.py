import pandas as pd
import streamlit as st

import ui_common as ui
import ui_kit as kit
from shiftsense import attendance, comms, hr, mailer, priority, records, services, workflow
from shiftsense import exports as _ex

ui.page("team")
AREA = ui.area("team")
w = ui.world()
team = ui.user()["team"]
members = ui.team_members(team)

mine = [i for i in w.incidents if i.owner_team == team]
active = [i for i in mine if i.status in workflow.DECIDABLE]
ranked = priority.rank(w, active)
reqs = [r for r in w.requests if r["agent_id"] in members and r["state"] == "Pending"]
decided_upcoming = [r for r in w.requests if r["agent_id"] in members and r["state"] == "Approved"
                    and r["day"] >= w.first_day and not r.get("simulated_history")]
waiting = (sum(s in ("waiting", "offered") for i in mine for s in i.action_status.values())
           + sum(o["status"] == "open" for i in mine for o in i.offers))
escalated = [i for i in mine if i.status == "escalated"]
on_now = [a for a in members if ui.on_shift_now(a)]
tomorrow = ui.day_status(w.first_day)
cov_tomorrow = round(tomorrow["Covered %"].mean(), 1) if len(tomorrow) else 100.0

notes = comms.notices_for(ui.user(), w.run_id, w.now_t)
due_items = [n for n in notes if n["status"] == "action" and n["due_t"] is not None]
overdue = [n for n in due_items if n["overdue"]]
my_cases = [c for c in comms.cases(w.run_id) if c["subject_user"] == ui.user()["username"]
            and c["status"] not in ("Closed", "Dismissed")]
ui.shell(f"Team {team}", f"{workflow.now_label(w)} · {len(members)} agents",
         kit.pill(f"{len(active)} to decide", "Critical" if active else "Normal"), open_kinds=("incident", "request"))
if AREA == "Overview":
    kit.kpis([
        {"label": "Alerts to decide", "value": len(active), "status": "Critical" if active else "Normal"},
            {"label": "Requests waiting", "value": len(reqs), "status": "Warning" if reqs else None},
            {"label": "On shift now", "value": len(on_now), "sub": "incl. overnight from yesterday"},
        {"label": "Coverage tomorrow (planned)", "value": f"{cov_tomorrow}%", "status": kit.coverage_status(cov_tomorrow)},
        {"label": "Overdue", "value": len(overdue), "status": "Critical" if overdue else "Normal"},
    ])


def _ctx():
    return {"team": team, "now": workflow.now_label(w),
            "alerts_ranked": [{"id": i.id, **{k: s[k] for k in ("severity", "short_ph", "exposure", "hours_left", "starts",
                                                                  "fix_cost", "fix_covers_pct")},
                               "title": ui.incident_title(i)} for i, s in ranked],
            "requests": [{"who": ui.name(r["agent_id"]), "day": ui.dlabel(r["day"]), "check": r["status"]} for r in reqs],
            "coverage_tomorrow": tomorrow.to_dict("records")}


if AREA == "Overview":
    c1, c2, c3, c4 = st.columns([1, 1, 1, 1])
    if ui.can("report_absence") and c1.button("Report absence", icon=":material/person_off:", width="stretch"):
        ui.open_card("absence", True)
    if on_now and c2.button("Send someone home", icon=":material/logout:", width="stretch"):
        ui.open_card("leave_now", True)
    if c3.button("Team schedule", icon=":material/calendar_month:", width="stretch"):
        ui.open_card("schedule", True)
    with c4:
        ui.ask_box("team lead", _ctx, "tl_ask", "Which alert first?")

    # ------------------------------------------------------------------ deadlines
    if due_items:
        kit.label("Deadlines")
        for n in sorted(due_items, key=lambda x: x["due_t"])[:6]:
            txt, stt = ui.due_text(n["due_t"])
            c1, c2 = st.columns([5, 1])
            c1.markdown(f'{kit.pill(txt, stt)} <b>{kit.esc(n["title"])}</b> <span class="ss-muted">· from '
                        f'{kit.esc(n["sender"])}</span>', unsafe_allow_html=True)
            kind = n["related_kind"]
            if kind in ("incident", "request", "case", "thread") and c2.button("Open", key=f"dl_{n['id']}"):
                ui.open_card(kind, int(n["related_ref"]) if kind == "case" else n["related_ref"])
                st.rerun()
            if n["title"].startswith("Follow-up from Operations") and c2.button("Done", key=f"fd_{n['id']}"):
                ui.open_card("followup", n["id"])
                st.rerun()

# ------------------------------------------------------------------ attendance today (recorded only)
today_codes = {a: records.day_record(w, a, w.today) for a in members}
started = [a for a, r in today_codes.items() if r["code"] not in ("scheduled", "off", "leave")]
checked = [a for a in started if w.attendance.get((a, w.today)) in ("checkin", "confirmed", "feed")]
sim_in = [a for a in started if w.attendance.get((a, w.today)) == "in"]
no_rec = [a for a in started if today_codes[a]["code"] == "unrecorded"]
missing = [a for a in started if today_codes[a]["code"] in ("no_show", "sick")]
coming = [a for a, r in today_codes.items() if r["code"] == "scheduled"]
if AREA == "Attendance":
    kit.label(f"Attendance today · {ui.dlabel(w.today)} · recorded events only", anchor="attendance")
    kit.stats([("Today's shifts started", len(started)), ("Checked in", len(checked))]
              + ([("Present (simulated)", len(sim_in))] if w.scenario else [])
              + [("No record yet", len(no_rec)), ("Absent / no-show", len(missing)), ("Still to start", len(coming))])
    late_today = [r for r in attendance.late_checkins(w.run_id, {w.today}) if r["agent_id"] in members]
    feed_late = [(a, v["late_min"]) for (a, d), v in getattr(w, "attendance_detail", {}).items()
                 if d == w.today and a in members and v.get("late_min")]
    if feed_late:
        st.caption("Late per the attendance feed: " + ", ".join(f"{ui.name(a)} ({m} min)" for a, m in feed_late))
    if late_today:
        st.caption("Checked in after the start: " + ", ".join(f"{ui.name(r['agent_id'])} ({attendance.lateness(r)})"
                                                              for r in late_today)
                   + ". Simulated time moves in 30-minute steps, so lateness is known to the half-hour.")
    if no_rec:
        st.caption("No record yet (unknown, not assumed absent): " + ", ".join(ui.name(a) for a in no_rec[:8])
                   + (" …" if len(no_rec) > 8 else "") + ". Send a reminder from Team schedule, or confirm presence if "
                   "you know the agent is working.")
        if st.button("Confirm an agent present…", key="corr_btn"):
            ui.open_card("correct", True)
    _ph = ui.photos(members)
    _amode = st.segmented_control("Show", ["Agenda", "Table"], default="Agenda", key="tl_att_mode",
                                  label_visibility="collapsed") or "Agenda"
    if _amode == "Agenda":                     # today's shifts in time order, with what is actually recorded
        _code_pill = {"unrecorded": ("No record (unknown)", "Neutral"), "no_show": ("No-show", "Critical"),
                      "sick": ("Absent", "Critical"), "left_early": ("Left early", "Warning"),
                      "scheduled": ("Still to start", "Info")}
        _ag = []
        for a in members:
            r = ui.shift_row(a, w.today)
            if r is None:
                continue
            s_, _e = ui.shift_hours(r)
            code = today_codes[a]["code"]
            lab, stt = _code_pill.get(code, (records.STATUS[code][0], "Normal"))
            _ag.append((s_, {"time": f"{int(s_ % 24):02d}:{int(round(s_ % 1 * 60)):02d}",
                             "who": kit.avatar(ui.pname(a), _ph.get(a), "sm"), "title": ui.pname(a),
                             "meta": f"{r['shift']} · {r['alt_queue'] or r['queue']}", "pill": lab, "status": stt}))
        kit.agenda([x for _, x in sorted(_ag, key=lambda y: y[0])])
    _att = pd.DataFrame([{"Photo": kit.avatar_uri(ui.pname(a), _ph.get(a)), "Agent": ui.name(a),
                          "Today": records.STATUS[today_codes[a]["code"]][0],
                          "Shift": ui.shift_label(ui.shift_row(a, w.today)) if ui.shift_row(a, w.today) else "—"}
                         for a in sorted(members, key=lambda x: (today_codes[x]["code"] != "unrecorded", ui.name(x)))])
    if _amode == "Table":
        st.dataframe(_att, hide_index=True, width="stretch", height=min(40 + 35 * len(_att), 380),
                     column_config={"Photo": st.column_config.ImageColumn("", width="small")})
    st.caption("“No record” means unknown: it is never treated as an absence.")
    if st.button("Ask HR to correct attendance…", key="attfix_btn",
                 help="For a recorded value or a shift older than the attendance lock"):
        ui.open_card("attfix", True)

if AREA == "Overview":
    # ------------------------------------------------------------------ top priorities
    kit.label("Top priorities", anchor="priorities")
    if not ranked:
        st.success("No alerts need a decision.")
    else:
        cols = st.columns(3)
        for col, (inc, s) in zip(cols, ranked[:3]):
            label, status = ui.incident_state(inc)
            rec = inc.rec
            action = rec["actions"][0].description if rec["actions"] else "No feasible action · escalate"
            more = f" +{len(rec['actions']) - 1} more" if len(rec["actions"]) > 1 else ""
            with col.container(border=True):
                st.markdown(f'{kit.pill(s["severity"], s["severity"])} {kit.pill(label, status)}', unsafe_allow_html=True)
                st.markdown(f"**{ui.incident_title(inc)}**")
                st.caption(f"{', '.join(s['queues']) or '—'} · {ui.dlabel(inc.day)} {s['starts']}"
                           + ("" if s["hours_left"] is None else f" · in {s['hours_left']:g} h"))
                kit.stats([("Gap", f"{s['short_ph']:g} people-h"),
                           ("Fix", kit.money(s["fix_cost"]) if rec["actions"] else "—"),
                           ("Covers", f"{s['fix_covers_pct']:.0f}%" if rec["actions"] else "—")])
                st.caption(f"{action}{more}")
                if st.button("Review & decide", key=f"top_{inc.id}", type="primary", width="stretch"):
                    ui.open_card("incident", inc.id)

if AREA == "Requests":
    kit.label("Team leave calendar · next 14 days")
    ui.leave_calendar(members, [d for d in range(w.today, min(w.today + 14, w.policy["horizon"]["days"]))],
                      show_type=False, key="tl_leavecal")

if AREA == "Alerts & decisions":
    kit.stats([("To decide", len(active)), ("Awaiting agents' answers", waiting), ("Escalated to Ops", len(escalated))])
    # ------------------------------------------------------------------ all alerts
    kit.label("All alerts", anchor="alerts")
    show_all = st.segmented_control("Show", ["Active", "All"], default="Active", key="tl_show",
                                    label_visibility="collapsed") == "All"
    lst = mine if show_all else active
    if lst:
        rows = []
        for inc in sorted(lst, key=lambda i: (i.status not in workflow.DECIDABLE, i.day)):
            s = priority.assess(w, inc)
            rows.append({"Severity": s["severity"], "State": ui.incident_state(inc)[0], "Alert": ui.incident_title(inc),
                         "Date": ui.dlabel(inc.day), "Starts": s["starts"], "Gap (people-h)": s["short_ph"],
                         "Fix ₹": round(s["fix_cost"]) if inc.status in workflow.DECIDABLE else None, "_id": inc.id})
        adf = pd.DataFrame(rows)

        def _open_alert():
            sel = st.session_state["tl_alerts"].selection.rows
            if sel:
                ui.open_card("incident", adf.iloc[sel[0]]["_id"])

        st.dataframe(adf.drop(columns="_id"), hide_index=True, width="stretch", on_select=_open_alert,
                     selection_mode="single-row", key="tl_alerts")
    else:
        kit.empty("No alerts", "Staffing gaps for your team appear here with a proposed fix.")

if AREA == "Requests":
    # ------------------------------------------------------------------ requests
    kit.label("Time-off and swap requests", anchor="requests")
    lst_req = reqs + (decided_upcoming if st.toggle("Show approved upcoming (revocable)", key="tl_rev") else [])
    if lst_req:
        _rph = ui.photos([r["agent_id"] for r in lst_req])
        rdf = pd.DataFrame([{"Photo": kit.avatar_uri(ui.pname(r["agent_id"]), _rph.get(r["agent_id"])),
                             "Agent": ui.name(r["agent_id"]), "Type": "Swap" if r.get("kind") == "swap" else "Time off",
                             "Date": ui.dlabel(r["day"]), "Coverage": ui.REQ_LABEL[r["status"]], "State": r["state"],
                             "_id": r["id"]} for r in lst_req])

        def _open_req():
            sel = st.session_state["tl_reqs"].selection.rows
            if sel:
                ui.open_card("request", rdf.iloc[sel[0]]["_id"])

        st.dataframe(rdf.drop(columns="_id"), hide_index=True, width="stretch", on_select=_open_req,
                     selection_mode="single-row", key="tl_reqs",
                     column_config={"Photo": st.column_config.ImageColumn("", width="small")})
    else:
        kit.empty("No requests waiting", "Time-off and swap requests from your team appear here for your decision.")


if AREA == "Alerts & decisions":
    # ------------------------------------------------------------------ governance questions about my decisions
    if my_cases:
        kit.label("Governance questions about my decisions")
        st.caption("A review case is a signal for review, not a finding. You can read the evidence and give your "
                   "explanation; you cannot edit the evidence or the audit trail.")
        gq = pd.DataFrame([{"Case": c["id"], "Status": c["status"], "Issue": c["title"],
                            "Reply by": ui.due_text(c["due_t"])[0] if c["status"] == "Explanation Requested" else "—"}
                           for c in my_cases])

        def _open_case():
            sel = st.session_state["tl_cases"].selection.rows
            if sel:
                ui.open_card("case", int(gq.iloc[sel[0]]["Case"]))

        st.dataframe(gq, hide_index=True, width="stretch", on_select=_open_case, selection_mode="single-row",
                     key="tl_cases")


if AREA == "Team information":
    # ------------------------------------------------------------------ team data
    kit.label("Team data", anchor="data")
    import ui_data as ud
    from shiftsense import exports as _ex
    c1, c2, c3 = st.columns(3)
    if c1.button("Propose an agent change…", icon=":material/edit:", width="stretch", disabled=w.scenario,
                 help="Skills, night shifts or leaving date for an agent in your team; HR decides"):
        ui.open_card("propose", True)
    if c2.button("Report training or disruption…", icon=":material/emergency:", width="stretch", disabled=w.scenario):
        ui.open_card("tl_sit", True)
    c3.download_button("Team pack (Excel)", _ex.team_pack(w, team), f"team_{team}.xlsx", icon=":material/download:",
                       width="stretch")
    _ids = set(w.data["agents"][w.data["agents"].team == team].agent_id)
    st.download_button("Printable team schedule (HTML)", _ex.print_html(f"Team {team} schedule",
                       {"Schedule": _ex.roster_grid(w, _ids)}, w), f"team_{team}_print.html", "text/html",
                       help="Open in a browser and print")
    if not w.scenario:
        mine_sets = [r for r in services.data_changes(w, ui.user()) if r["status"] in ("pending", "rejected")][:5]
        for r in mine_sets:
            st.caption(f"Your proposal #{r['id']}: {r['status']}"
                       + (f" · {r['decision_reason']}" if r.get("decision_reason") else ""))


if AREA == "Team information":
    # ------------------------------------------------------------------ team history
    kit.label("Team history", anchor="history")
    ui.history_view("tl_hist", f"Team {team}: schedules and changes, attendance and reminders, absences, requests "
                               "and decisions, alerts, recovery and escalations, your governance questions.")


if AREA == "Team schedule":
    import numpy as _np
    from shiftsense.config import intervals_per_day as _ipd, queue_names as _qn
    from shiftsense.roster import coverage as _coverage
    _H = w.policy["horizon"]["days"]
    _ph = ui.photos(members)
    v1, v2 = st.columns([2, 3], vertical_alignment="bottom")
    view = v1.segmented_control("View", ["Week", "Day"], default="Week", key="tl_cal_view") or "Week"
    _qf = v2.selectbox("Queue", ["All queues"] + _qn(w.policy), key="tl_cal_q")
    anchor = int(st.session_state.get("tl_cal_day", w.today))
    step = 7 if view == "Week" else 1
    n1, n2, n3, n4 = st.columns([1.2, 1, 1.2, 4], vertical_alignment="center")
    if n1.button("Previous", icon=":material/chevron_left:", key="tl_cal_prev", width="stretch",
                 help=f"Previous {view.lower()}"):
        anchor = max(0, anchor - step)
    if n2.button("Today", key="tl_cal_today", width="stretch"):
        anchor = w.today
    if n3.button("Next", icon=":material/chevron_right:", key="tl_cal_next", width="stretch",
                 help=f"Next {view.lower()}"):
        anchor = min(_H - 1, anchor + step)
    st.session_state["tl_cal_day"] = anchor
    _pending = {(r["agent_id"], r["day"]) for r in w.requests if r["agent_id"] in members and r["state"] == "Pending"
                and r.get("kind") == "leave"}

    def _fmt(h):
        h = h % 24
        return f"{int(h):02d}:{int(round(h % 1 * 60)):02d}"

    def _cell(a, d):
        r = ui.shift_row(a, d)
        if r is not None:
            if _qf != "All queues" and (r["alt_queue"] or r["queue"]) != _qf:
                return None
            s_, e_ = ui.shift_hours(r)
            return {"text": f"{_fmt(s_)}–{_fmt(e_)}", "sub": f"{r['shift']} · {r['alt_queue'] or r['queue']}"
                    + (" · leave requested" if (a, d) in _pending else ""), "kind": "pending" if (a, d) in _pending
                    else "shift"}
        why = ui.unavailable(a, d)
        if why:                                   # team leads see "leave" or "absent", never the leave type
            return {"text": "Absent" if str(why).startswith("absent") else "Leave", "kind":
                    "absent" if str(why).startswith("absent") else "leave"}
        return {"text": "Off", "kind": "off"} if _qf == "All queues" else None

    if view == "Week":
        w0 = (anchor // 7) * 7
        days = [d for d in range(w0, w0 + 7) if d < _H]
        n4.markdown(f"**{ui.dlabel(days[0])} – {ui.dlabel(days[-1])}**" if days else "")
        rows = []
        for a in sorted(members, key=ui.pname):
            cells = {d: c for d in days if (c := _cell(a, d))}
            if _qf != "All queues" and not any(c["kind"] in ("shift", "pending") for c in cells.values()):
                continue
            rows.append({"name": ui.pname(a), "meta": a, "photo": _ph.get(a), "cells": cells})
        if rows:
            kit.week_grid(rows, [(d, ui.dlabel(d)) for d in days], today=w.today, caption=f"Team {team} week roster")
            st.caption("Blocks show shift times; dashed = leave requested (pending); “Leave”/“Absent” come from "
                       "approved leave or reported absences. Today's column is highlighted.")
        else:
            kit.empty("No shifts in this view", "Choose another week or queue.")
    else:
        d = anchor
        n4.markdown(f"**{ui.dlabel(d)}**" + (" · today" if d == w.today else ""))
        per = _ipd(w.policy)
        items, rows = [], []
        for a in sorted(members, key=ui.pname):
            c = _cell(a, d)
            r = ui.shift_row(a, d)
            if r is None or c is None or c["kind"] not in ("shift", "pending"):
                continue
            s_, e_ = ui.shift_hours(r)
            rows.append(ui.pname(a))
            rec = w.attendance.get((a, d))
            items.append({"row": ui.pname(a), "start": s_, "end": e_, "kind": "shift", "ref": a,
                          "label": f"{_fmt(s_)}–{_fmt(e_)} {r['alt_queue'] or r['queue']}",
                          "detail": f"{r['shift']} · {_fmt(s_)}–{_fmt(e_)} · {r['alt_queue'] or r['queue']} · "
                                    f"record: {rec or 'none yet'}"})
        tq = sorted({ui.agents().loc[a, "primary"] for a in members} if _qf == "All queues" else {_qf})
        cov = _coverage(w.policy, w.roster)
        qn = _qn(w.policy)
        for q in tq:                               # this team's queues: where scheduled < required this day
            qi = qn.index(q)
            sl = range(d * per, (d + 1) * per)
            short = [w.req[qi, t] - cov[qi, t] for t in sl]
            i = 0
            while i < len(short):
                if short[i] > 0:
                    j, peak = i, 0
                    while j < len(short) and short[j] > 0:
                        peak = max(peak, short[j])
                        j += 1
                    rows.append(f"Gap · {q}") if f"Gap · {q}" not in rows else None
                    items.append({"row": f"Gap · {q}", "start": i * 24 / per, "end": j * 24 / per, "kind": "gap",
                                  "label": f"−{peak:g}", "detail": f"{q}: {_fmt(i * 24 / per)}–{_fmt(j * 24 / per)}, "
                                                                     f"up to {peak:g} people short"})
                    i = j
                else:
                    i += 1
        if items:
            nowh = (w.now_t - d * per) * 24 / per if d == w.today else None
            ev = kit.chart(kit.timeline(items, rows, (0, 31), [(h, f"{h % 24:02d}") for h in range(0, 31, 3)],
                                        now=nowh),
                           f"{ui.dlabel(d)}: {sum(1 for i in items if i['kind'] == 'shift')} shifts; "
                           f"{sum(1 for i in items if i['kind'] == 'gap')} coverage gap period(s) on your queues.",
                           key="tl_day_tl", title="Day schedule and coverage gaps",
                           subtitle="Shifts by person · red rows = periods where your queues are short",
                           provenance="synthetic", on_select=True)
            _pts = (ev.selection.points if ev and getattr(ev, "selection", None) else []) or []
            if _pts and _pts[0].get("customdata") and _pts[0]["customdata"][0]:
                st.session_state["tl_cal_pick"] = _pts[0]["customdata"][0]
        else:
            kit.empty("No shifts this day", "Choose another day or queue.")
        _on = [i["ref"] for i in items if i["kind"] == "shift"]
        if _on:                                     # list alternative (keyboard and screen readers) + detail panel
            pick = st.selectbox("Shift details", _on, index=_on.index(st.session_state["tl_cal_pick"])
                                if st.session_state.get("tl_cal_pick") in _on else 0,
                                format_func=lambda a: f"{ui.pname(a)} · {_cell(a, d)['text']}", key="tl_cal_sel")
            r = ui.shift_row(pick, d)
            with st.container(border=True):
                st.markdown(kit.person(ui.pname(pick), f"{pick} · {r['shift']} · {r['alt_queue'] or r['queue']}",
                                       _ph.get(pick), "md"), unsafe_allow_html=True)
                kit.stats([("Time", _cell(pick, d)["text"]),
                           ("Record", records.STATUS[records.day_record(w, pick, d)["code"]][0]),
                           ("Leave request", "Pending" if (pick, d) in _pending else "None")])
                b1, b2 = st.columns(2)
                if b1.button("Reminders and day view…", key="tl_cal_rem", width="stretch"):
                    ui.open_card("schedule", True)
                if ui.can("report_absence") and b2.button("Report absence…", key="tl_cal_abs", width="stretch"):
                    ui.open_card("absence", True)
    c2, c3 = st.columns(2)
    c2.download_button("Team pack (Excel)", _ex.team_pack(w, team), f"team_{team}_pack.xlsx",
                       icon=":material/download:", width="stretch", key="tl_pack2")
    c3.download_button("Printable schedule (HTML)", _ex.print_html(
        f"Team {team} schedule", {"Schedule": _ex.roster_grid(w, set(members))}, w), f"team_{team}_print.html",
        "text/html", width="stretch", key="tl_print2", help="Open in a browser and print")

if AREA == "Team information":
    kit.label("Team directory")
    _ph = ui.photos(members)
    _ag = ui.agents()
    kit.people([(ui.pname(a), f"{a} · {_ag.loc[a, 'primary']}" + (f" + {len(_ag.loc[a, 'eligible']) - 1} more queue(s)"
                 if len(_ag.loc[a, "eligible"]) > 1 else ""), _ph.get(a)) for a in sorted(members, key=ui.name)])


# ------------------------------------------------------------------ cards
@st.dialog("Alert", width="large", on_dismiss=ui.close_card)
def alert_card(inc_id):
    inc = next(i for i in w.incidents if i.id == inc_id)
    ui.decision_card(inc, ui.can("decide_fix") and inc.owner_team == team, key=f"dc_{inc_id}")
    if st.button("Close", key="alert_close"):
        ui.close_card()
        st.rerun()


@st.dialog("Request", on_dismiss=ui.close_card)
def request_card(req_id):
    try:
        r = services.request_view(ui.user(), services.get_request(w, ui.user(), req_id))
    except services.Denied as e:
        st.error(str(e))
        return
    what = r["alternative"]["option"] if r["alternative"] else f"Day off · {ui.dlabel(r['day'])}"
    st.markdown(f"#### {ui.name(r['agent_id'])}")
    st.markdown(f'{kit.pill(ui.REQ_LABEL[r["status"]], ui.REQ_STATUS[r["status"]])} '
                f'{kit.pill(r["state"], "Info")}', unsafe_allow_html=True)
    st.markdown(("Swap agreed between colleagues · " if r.get("kind") == "swap" else "") + what)
    st.caption(f"Impact: {r['detail']}" + (f" · Note: {r['note']}" if r["note"] else ""))
    if r.get("leave_type"):
        st.caption(f"Type: {hr.LEAVE_TYPES.get(r['leave_type'], (r['leave_type'],))[0]}")
    reviews = services.review_status(w, ui.user(), req_id)
    for t in reviews:
        st.caption(f"HR review · {t['reason']} · " + (f"HR recommends: **{t['recommendation']}** "
                   f"({t['recommended_by']})" + (f" · {t['note']}" if t["note"] else "")
                   if t["recommendation"] else "waiting for HR" if t["status"] == "open" else t["status"]))
    if r.get("hold") and not reviews:
        st.caption(r["hold"])
    cur = ui.shift_row(r["agent_id"], r["day"])
    st.caption(f"Current shift that day: {ui.shift_label(cur)}")
    if r["state"] == "Approved" and ui.can("approve_requests"):
        reason = st.text_input("Reason to revoke (required)", key=f"rv_r_{req_id}")
        if st.button("Revoke approval", key=f"rv_{req_id}"):
            try:
                with ui.mutate():
                    services.revoke(w, ui.user(), req_id, reason)
                ui.close_card()
                ui.flash("Approval revoked · shift restored · agent notified")
            except (ValueError, services.Denied) as e:
                st.error(str(e))
    elif r["state"] == "Pending" and ui.can("approve_requests"):
        reason = st.text_input("Reason (required to decline)", key=f"rq_r_{req_id}")
        b1, b2 = st.columns(2)
        for col, ok, lbl in ((b1, True, "Approve"), (b2, False, "Decline")):
            if col.button(lbl, key=f"rq_{lbl}_{req_id}", type="primary" if ok else "secondary", width="stretch"):
                try:
                    with ui.mutate():
                        services.decide_request(w, ui.user(), req_id, ok, reason)
                    ui.close_card()
                    ui.flash(f"{lbl}d · agent notified")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))
    with st.expander("Conversation with the agent"):
        ui.thread_view(f"request:{req_id}", key=f"rq_thr_{req_id}")


@st.dialog("Report absence", on_dismiss=ui.close_card)
def absence_card(_):
    day = st.selectbox("Day", [w.today] + ui.days_ahead(5), format_func=ui.dlabel, key="abs_day_tl")
    on = [a for a in members if ui.shift_row(a, day) and not ui.unavailable(a, day)]
    who = st.multiselect("Who", on, format_func=lambda a: f"{ui.name(a)} · {ui.shift_label(ui.shift_row(a, day))}",
                         key="abs_who_tl")
    if st.button("Report and find cover", type="primary", disabled=not who):
        try:
            with ui.mutate():
                inc = services.report_absence(w, ui.user(), who, day)
            ui.open_card("incident", inc.id) if inc.status != "no_gap" else ui.close_card()
            ui.flash("Reported · fix ready for review" if inc.status != "no_gap" else "Reported · no cover needed")
        except (ValueError, services.Denied) as e:
            st.error(str(e))


@st.dialog("Send someone home", on_dismiss=ui.close_card)
def leave_now_card(_):
    leaver = st.selectbox("Who", on_now, format_func=lambda a: f"{ui.name(a)} · {ui.shift_label(ui.on_shift_now(a))}",
                          key="leaver_tl")
    if st.button("Send home and find cover", type="primary"):
        with ui.mutate():
            inc = services.report_left_early(w, ui.user(), leaver)
        ui.open_card("incident", inc.id)
        ui.flash("Recorded · fix ready for review")


@st.dialog("Team schedule", width="large", on_dismiss=ui.close_card)
def schedule_card(_):
    days = [w.today] + ui.days_ahead(6)
    rows = []
    for a in members:
        rec = records.day_record(w, a, w.today)          # recorded events only; a started shift is never "upcoming"
        seen = w.attendance.get((a, w.today))
        today_txt = {"scheduled": "Upcoming", "unrecorded": "Started · no record", "left_early": "Left early",
                     "no_show": "No-show", "sick": "Absent", "leave": "Leave", "off": "Off"}.get(rec["code"])
        if today_txt is None:                                # worked / overtime / extra / swap
            today_txt = {"checkin": "Checked in", "confirmed": "Present (confirmed by TL)"}.get(seen, "In (simulated)")
        row = {"Agent": ui.name(a), "Today": today_txt}
        for d in days[1:]:
            off, r = ui.unavailable(a, d), ui.shift_row(a, d)
            row[ui.dlabel(d)] = ("Leave" if off and "approved" in off else "Absent") if off else \
                (f"{r['shift'][0]} · {r['queue']}" if r else "—")
        rows.append(row)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption("M 06–15 · D 09–18 · E 14–23 · N 22–07 · — day off")
    kit.label("Attendance reminder")
    st.caption("Sent to the agent's ShiftSense inbox. " + mailer.status_text() + ". A reminder never records "
               "attendance: only the agent's own check-in (30 min before to 1 h after the start) does.")
    def _open(a, d):                                     # check-in still possible for this shift?
        r = ui.shift_row(a, d)
        start = workflow.shift_intervals(w.policy, d, r["shift"], r["ext_before"], r["ext_after"]).start
        return w.now_t <= start + attendance.CLOSES_AFTER
    upcoming = [(a, d) for d in days[:2] for a in members if ui.shift_row(a, d) is not None
                and not ui.unavailable(a, d) and not w.attendance.get((a, d)) and _open(a, d)]
    pick = st.selectbox("Shift", [None] + upcoming, key="rem_pick",
                        format_func=lambda x: "Choose…" if x is None else
                        f"{ui.name(x[0])} · {ui.dlabel(x[1])} · {ui.shift_label(ui.shift_row(*x))}")
    sent = {(r["agent_id"], r["day"]) for r in attendance.log(w.run_id, set(members))}
    again = pick in sent
    if pick and st.button("Send again" if again else "Send reminder", key="rem_go"):
        try:
            out = services.send_attendance_reminder(w, ui.user(), pick[0], pick[1], resend=again)
            st.session_state["rem_out"] = out
        except (ValueError, services.Denied) as e:
            st.error(str(e))
    out = st.session_state.get("rem_out")
    if out:
        ok = out["status"] == "in_inbox"
        em = out.get("email") or {}
        st.markdown(f'{kit.pill(attendance.state_text(out), "Normal" if ok else "Warning")} '
                    f'{kit.pill(mailer.LABEL.get(em.get("status"), "Email —"), "Info" if em.get("status") == "sent" else "Neutral")} '
                    f'<span class="ss-muted">attempt {out["attempt"]}'
                    + (f' · {kit.esc(out["error"])}' if out["error"] else "")
                    + (f' · {kit.esc(em["detail"])}' if em.get("detail") else "") + "</span>", unsafe_allow_html=True)
    log = attendance.log(w.run_id, set(members))
    if log:
        rows = []
        for r in log[:10]:
            v = services.reminder_view(w, r)
            rows.append({"Agent": ui.name(r["agent_id"]), "Shift": r["shift"], "Sent": r["sim_time"],
                         "In-app": v["in_app"], "Opened": v["opened"], "Checked in": v["checked_in"],
                         "Email": v["email"]})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Each column is a separate fact: in inbox ≠ opened ≠ checked in. 'Opened' means the agent opened "
                   "the notification; they can also check in straight from My work.")
    elif not upcoming:
        st.caption("No upcoming shifts in the next two days still open for check-in.")
    who = st.selectbox("Message an agent", [None] + members, format_func=lambda a: "Choose…" if a is None else ui.name(a),
                       key="tl_msg_to")
    if who and st.button("Open conversation", key="tl_msg_go"):
        ui.open_card("thread", f"agent:{who}")
        st.rerun()


@st.dialog("Follow-up", on_dismiss=ui.close_card)
def followup_card(nid):
    n = next((x for x in notes if x["id"] == nid), None)
    if n is None:
        st.caption("Not found.")
        return
    st.markdown(f"**{n['title']}**")
    st.caption(f"From {n['sender']} · {ui.due_text(n['due_t'])[0]}")
    note = st.text_area("What was done (required)", key=f"fdn_{nid}")
    if st.button("Mark done", type="primary", key=f"fdb_{nid}"):
        try:
            services.complete_followup(w, ui.user(), nid, note)
            ui.close_card()
            ui.flash("Follow-up marked done · Operations informed")
        except (ValueError, services.Denied) as e:
            st.error(str(e))


@st.dialog("Confirm presence", on_dismiss=ui.close_card)
def correct_card(_):
    st.caption("Records that an own-team agent is present for a started shift that has no attendance record (for "
               "example they could not check in). It never overwrites a record; the reason and before/after are "
               "audited, and it shows as 'confirmed by team lead', not as a self check-in.")
    who = st.selectbox("Agent", no_rec, format_func=ui.name, key="corr_who")
    why = st.text_input("Reason (required)", key="corr_why", placeholder="Seen on the floor at 09:05")
    if st.button("Record correction", type="primary", key="corr_go"):
        try:
            with ui.mutate():
                services.correct_attendance(w, ui.user(), who, w.today, why)
            ui.close_card()
            ui.flash("Correction recorded · audited")
        except (ValueError, services.Denied) as e:
            st.error(str(e))



@st.dialog("Propose an agent change", on_dismiss=ui.close_card)
def propose_card(_):
    from shiftsense import refdata
    emps = {k: v for k, v in refdata.current("employees").items() if v.get("team") == team}
    if not emps:
        st.caption("Your team's agents are not in the people data yet.")
        return
    aid = st.selectbox("Agent", sorted(emps), format_func=ui.name, key="pp_who")
    e = emps[aid]
    ctx = services.data_context(w, ui.user())
    sec = st.multiselect("Other trained queues", [q for q in ctx["queues"] if q != e["primary"]],
                         default=[q for q in e.get("secondary") or [] if q in ctx["queues"]], key=f"pp_sec_{aid}")
    night = st.checkbox("Night shifts allowed", value=bool(e.get("night_ok", True)), key=f"pp_n_{aid}")
    leave_day = st.date_input("Last working day (if leaving)", value=None, key=f"pp_end_{aid}", format="DD/MM/YYYY")
    why = st.text_input("Reason (required)", key=f"pp_why_{aid}", placeholder="Completed Banking-EN training")
    if st.button("Send to HR", type="primary", key="pp_go"):
        if not why.strip():
            st.error("Give a reason.")
            return
        rec = {**e, "secondary": sec, "night_ok": night}
        if leave_day:
            rec["end_date"] = leave_day.isoformat()
            rec["status"] = "On notice"
        try:
            out = services.submit_change(w, ui.user(), "employees", [rec], reason=why, source="team lead form",
                                         converted_by="form")
            ui.close_card()
            ui.flash(f"Proposal #{out['id']} sent to HR")
        except (ValueError, services.Denied) as ex:
            st.error(str(ex))


@st.dialog("Report training or disruption", on_dismiss=ui.close_card)
def tl_sit_card(_):
    from shiftsense.schemas import SITUATION_TYPES
    ud.situation_form(w, "tl_sitf", [t for t, v in SITUATION_TYPES.items() if "team_lead" in v[3]])


@st.dialog("Ask HR to correct attendance", on_dismiss=ui.close_card)
def attfix_card(_):
    st.caption(f"Attendance locks {hr.value('attendance.lock_hours'):g} h after a shift ends, and a recorded value "
               "is never overwritten directly. Ask for a correction; an HR reviewer applies or rejects it, and "
               "the original stays on record.")
    past = [d for d in range(max(0, w.today - 7), w.today + 1)]
    d2 = st.selectbox("Day", past[::-1], format_func=ui.dlabel, key="corr2_day")
    on2 = [a for a in members if ui.shift_row(a, d2)]
    if not on2:
        st.caption("Nobody in your team worked that day.")
        return
    who2 = st.selectbox("Agent", on2, format_func=lambda a: f"{ui.name(a)} · now: "
                        f"{w.attendance.get((a, d2)) or 'no record'}", key="corr2_who")
    want = st.selectbox("Should be", ["present", "absent", "no record"], key="corr2_want")
    why2 = st.text_input("Reason (required)", key="corr2_why")
    if st.button("Send to HR", key="corr2_go"):
        try:
            services.request_attendance_correction(w, ui.user(), who2, d2, want, why2)
            ui.close_card()
            ui.flash("Correction request sent · HR reviews it")
        except (ValueError, services.Denied) as e:
            st.error(str(e))


if ui.card("propose"):
    propose_card(True)
elif ui.card("tl_sit"):
    tl_sit_card(True)
elif ui.card("correct") and no_rec:
    correct_card(True)
elif ui.card("attfix"):
    attfix_card(True)
elif (ref := ui.card("followup")) is not None:
    followup_card(ref)
elif (ref := ui.card("incident")) is not None:
    alert_card(ref)
elif (ref := ui.card("request")) is not None:
    request_card(ref)
elif ui.card("absence"):
    absence_card(True)
elif ui.card("leave_now"):
    leave_now_card(True)
elif ui.card("schedule"):
    schedule_card(True)
else:
    ui.shared_cards()
