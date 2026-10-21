import pandas as pd
import streamlit as st

import ui_common as ui
import ui_kit as kit
from shiftsense import attendance, hr, records, services, workflow
from shiftsense import requests as rq
from shiftsense.config import week_of

ui.page("me")
AREA = ui.area("me")
w = ui.world()
p = w.policy
aid = ui.user()["agent_id"]
if aid not in ui.agents().index:
    st.warning("Your agent record isn't in the loaded data. Ask the admin to include it.")
    st.stop()
ag = ui.agents().loc[aid]
rate, mult = p["costs"]["hourly_rate"], p["costs"]["ot_multiplier"]
ASK = {"confirm", "offer", "swap_ask"}

now_row = ui.on_shift_now(aid)
upcoming = [d for d in [w.today] + ui.days_ahead() if ui.shift_row(aid, d)
            and workflow._shift_end_t(w, ui.shift_row(aid, d)) > w.now_t]
nxt = upcoming[0] if upcoming else None
this_week = list(range((w.today // 7) * 7, min((w.today // 7) * 7 + 7, p["horizon"]["days"])))


def week_hours(days) -> float:
    ro = w.roster[(w.roster.agent_id == aid) & w.roster.day.isin(days)]
    return float(sum(p["shifts"][s]["paid"] for s in ro["shift"]) + ro.ext_before.sum() + ro.ext_after.sum())


inbox = [n for n in w.notifications if n["agent_id"] == aid]
todo = [n for n in inbox if n["kind"] in ASK and n["status"] == "new"]
mine = [r for r in w.requests if r["agent_id"] == aid]
checkins = attendance.open_for(w.run_id, aid, w.now_t)

ui.shell(f"Hi {ag['name'].split()[0]}", f"{ag.team} · trained for {', '.join(ag.eligible)}",
         kit.pill(f"{len(todo)} to answer", "Warning") if todo else "", open_kinds=("thread", "checkin"))

def _hm(h: float) -> str:
    h = h % 24
    return f"{int(h):02d}:{int(round(h % 1 * 60)):02d}"


if now_row:
    s, e = ui.shift_hours(now_row)
    hero = ("On shift now", f"{_hm(s)}–{_hm(e)}", f"{now_row['shift']} · {now_row['alt_queue'] or now_row['queue']}")
elif nxt is not None:
    r = ui.shift_row(aid, nxt)
    s, e = ui.shift_hours(r)
    hero = (f"Next shift · {ui.dlabel(nxt)}", f"{_hm(s)}–{_hm(e)}",
            f"{r['shift']} · {r['queue']} · report by {_hm(s - 0.25)}")
else:
    hero = ("Next shift", "None", "")
if AREA == "Home":
    kit.kpis([
        {"label": hero[0], "value": hero[1], "sub": hero[2], "status": "Info"},
        {"label": "Hours this week", "value": f"{week_hours(this_week):g} h", "sub": f"limit {p['rules']['max_weekly_hours']} h"},
        {"label": "Shifts this week", "value": int(((w.roster.agent_id == aid) & w.roster.day.isin(this_week)).sum())},
        {"label": "To answer", "value": len(todo) + len(checkins), "status": "Warning" if todo or checkins else None},
        {"label": "Open requests", "value": sum(r["state"] in ("Pending", "Waiting for colleague") for r in mine)},
    ])

    # ------------------------------------------------------------------ actions
    b = st.columns(3) + st.columns(3)          # two rows of three: labels stay readable at any width
    if b[0].button("Request time off", icon=":material/event_busy:", width="stretch"):
        ui.open_card("timeoff", True)
    if b[1].button("Swap a shift", icon=":material/swap_horiz:", width="stretch"):
        ui.open_card("swap", True)
    if b[2].button("Can't come in", icon=":material/sick:", width="stretch"):
        ui.open_card("absent", True)
    if b[3].button("Leave now", icon=":material/logout:", width="stretch", disabled=not now_row,
                   help=None if now_row else "Only while you are on shift"):
        ui.open_card("leavenow", True)
    if b[4].button("Message team lead", icon=":material/chat:", width="stretch"):
        ui.open_card("thread", f"agent:{aid}")
    with b[5]:
        with st.popover("Type a request", icon=":material/edit_note:", width="stretch"):
            msg = st.text_input("In your own words", key="nl_msg", placeholder="I need Friday off for a wedding")
            labels = {ui.dlabel(d): d for d in upcoming if d >= w.first_day}
            if st.button("Continue", key="nl_go") and msg.strip():
                nl = ui.assistant.parse_request(msg, list(labels), ui.dlabel(w.today), ui.ai_client())
                target = {"time_off": "timeoff", "swap": "swap", "absence": "absent", "leave_now": "leavenow"}.get(nl["intent"])
                if target:
                    st.session_state["nl_day"] = labels.get(nl["day"])
                    st.session_state["nl_reason"] = nl["reason"]
                    if nl.get("leave_type"):
                        st.session_state["to_type"] = nl["leave_type"]
                    ui.open_card(target, True)
                    st.rerun()
                st.markdown(nl["reply"])
                if nl["intent"] == "question":
                    st.markdown(ui.assistant.ask("call-centre agent", msg, {"next_shift": hero, "requests": [
                        {"what": (r["alternative"]["option"] if r["alternative"] else f"day off {ui.dlabel(r['day'])}"),
                         "state": r["state"]} for r in mine]}, ui.ai_client()).text)

    c_pref, c_cal, c_xl = st.columns(3)
    with c_pref.popover("My preferences", icon=":material/tune:", width="stretch"):
        st.caption("Wishes for the next plan. They never change a published shift.")
        from shiftsense import refdata as _rd
        me_rec = _rd.current("employees").get(aid) if not w.scenario else None
        if me_rec is None:
            st.caption("Not available here yet (your record isn't in the reference data, or you are viewing the demo).")
        else:
            sh = list(p["shifts"])
            ps = st.selectbox("Preferred shift", sh, index=sh.index(me_rec.get("pref_shift")) if me_rec.get("pref_shift") in sh
                              else 0, key="pf_shift")
            days7 = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            po = st.selectbox("Preferred day off", days7, index=int(me_rec.get("pref_off") or 6), key="pf_off")
            if st.button("Save preferences", key="pf_go"):
                try:
                    services.update_own_preferences(w, ui.user(), ps, days7.index(po))
                    ui.flash("Preferences saved · used at the next re-plan")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))
    from shiftsense import exports as _ex
    c_cal.download_button("Add my shifts to my calendar", _ex.ics(w, aid), f"shifts_{aid}.ics", "text/calendar",
                          icon=":material/event:", width="stretch", help="Calendar file for phone or Outlook")
    c_xl.download_button("My schedule (Excel)", _ex.roster_workbook(w, {aid}, title=f"Schedule of {ag['name']}"),
                         f"schedule_{aid}.xlsx", icon=":material/download:", width="stretch")

# ------------------------------------------------------------------ attendance check-in
def _checkin_row(r, key):
    c1, c2 = st.columns([4, 1])
    opens = w.now_t >= r["opens_t"]
    c1.markdown(f'{kit.pill("Open now" if opens else "Opens " + ui.tlabel(r["opens_t"]), "Warning" if opens else "Info")} '
                f'<b>{kit.esc(r["shift"])}</b> <span class="ss-muted">· closes {kit.esc(ui.tlabel(r["expires_t"]))} · '
                f'reminder from {kit.esc(r["sent_by"].split(" (")[0])}</span>', unsafe_allow_html=True)
    if c2.button("Check in now", key=f"{key}_{r['id']}", disabled=not opens, width="stretch", type="primary",
                 help=None if opens else "Check-in opens 30 minutes before the shift starts"):
        try:
            with ui.mutate():
                services.check_in_self(w, ui.user(), r["id"])
            ui.close_card()
            ui.flash("Checked in · attendance recorded")
        except (ValueError, services.Denied) as e:
            st.error(str(e))


if AREA == "Home":
    if todo:
        kit.label("Needs your answer")
        for n in todo[:3]:
            c1, c2 = st.columns([5, 1])
            c1.markdown(f'{kit.pill("Action needed", "Warning")} {kit.esc(n["text"])}', unsafe_allow_html=True)
            if c2.button("Review", key=f"hrv_{n['id']}", width="stretch"):
                ui.open_card("notif", n["id"])
        if len(todo) > 3:
            st.caption(f"{len(todo) - 3} more in Inbox & requests.")
    if checkins:
        kit.label("Check in", anchor="checkin")
        for r in checkins[:2]:
            _checkin_row(r, "ci")

# ------------------------------------------------------------------ week timeline
if AREA == "My shifts":
    kit.label("My shifts", anchor="shifts")
    wk = st.segmented_control("Week", ["This week", "Next week"], default="This week", key="ag_week",
                              label_visibility="collapsed") or "This week"
    week = w.today // 7 if wk == "This week" else min(w.today // 7 + 1, (p["horizon"]["days"] - 1) // 7)
    rows = []
    for c in records.agent_week(w, aid, week):
        r = ui.shift_row(aid, c["day"])
        label = records.STATUS[c["code"]][1]
        if r is not None:
            s, e = ui.shift_hours(r)
            status = {"scheduled": "Info", "left_early": "Warning", "no_show": "Critical",
                      "unrecorded": "Neutral"}.get(c["code"], "Normal")       # green = worked (recorded) only
            rows.append({"day_label": c["label"], "start": s, "end": e, "status": status,
                         "text": f"{r['shift']} · {r['alt_queue'] or r['queue']} · {label}"})
        else:
            rows.append({"day_label": c["label"], "start": None, "end": None, "text": label})
    _view = st.segmented_control("View", ["Calendar", "Timeline"], default="Calendar", key="ag_view",
                                 label_visibility="collapsed") or "Calendar"
    _days = [c["day"] for c in records.agent_week(w, aid, week)]
    if _view == "Calendar":
        ui.roster_calendar([aid], _days, "My week", with_record=True)
    if _view == "Timeline":
        kit.chart(kit.week_timeline(rows),
                  "Your week: " + "; ".join(f"{r['day_label']} {r['text']}" for r in rows) + ".", key="ag_tl", title="My week", subtitle="Shift times, queue and record status")

# ------------------------------------------------------------------ inbox
if AREA == "Inbox & requests":
    kit.label("Inbox", anchor="inbox")
    if not inbox:
        kit.empty("Nothing new", "Offers, swap asks from colleagues and decisions on your requests appear here.")
    for n in sorted(inbox, key=lambda x: (x["kind"] not in ASK or x["status"] != "new", x["id"]), reverse=False)[:8]:
        needs = n["kind"] in ASK and n["status"] == "new"
        state = {"accepted": ("Accepted", "Normal"), "declined": ("Declined", "Neutral"),
                 "withdrawn": ("Taken by a colleague", "Neutral"), "expired": ("No longer possible", "Neutral")}.get(
            n["status"], ("Action needed", "Warning") if needs else ("Info", "Neutral"))
        c1, c2 = st.columns([5, 1])
        by = ""
        if needs and n.get("incident"):
            inc_day = next((i.day for i in w.incidents if i.id == n["incident"]), None)
            by = f" · respond before {ui.dlabel(inc_day)}" if inc_day is not None else ""
        elif needs and n.get("request"):
            rq_day = next((x["day"] for x in w.requests if x["id"] == n["request"]), None)
            by = f" · respond before {ui.dlabel(rq_day)}" if rq_day is not None else ""
        c1.markdown(f'{kit.pill(state[0], state[1])} {kit.esc(n["text"])} <span class="ss-muted">· {kit.esc(n["time"])}'
                    f'{kit.esc(by)}</span>', unsafe_allow_html=True)
        if needs and c2.button("Review", key=f"rv_{n['id']}", width="stretch"):
            ui.open_card("notif", n["id"])

    # ------------------------------------------------------------------ requests
    kit.label("My requests", anchor="requests")
    if mine:
        nxt_actor = {"Pending": "Team lead", "Waiting for colleague": "Colleague", "Approved": "Done",
                     "Rejected": "Done", "Declined by colleague": "Done", "Withdrawn": "Done", "Revoked": "Done"}
        def _next(r):
            if r["state"] == "Pending" and r.get("kind") == "leave":
                return services.request_hr_status(w, ui.user(), r["id"])
            return nxt_actor.get(r["state"], "—")

        mdf = pd.DataFrame([{
            "Request": ("Swap · " if r.get("kind") == "swap" else "") + (r["alternative"]["option"] if r["alternative"]
                                                                         else f"{hr.LEAVE_TYPES.get(r.get('leave_type') or '', ('Day off',))[0]} · {ui.dlabel(r['day'])}")
            + (" · simulated prior-period record" if r.get("simulated_history") else ""),
            "Status": r["state"], "Next": _next(r),
            "Decision due": f"before {ui.dlabel(r['day'])}" if r["state"] in ("Pending", "Waiting for colleague") else "—",
            "Reason": r.get("reason", ""), "_id": r["id"]}
            for r in reversed(mine)])

        def _open_req():
            sel = st.session_state["ag_reqs"].selection.rows
            if sel:
                ui.open_card("myreq", mdf.iloc[sel[0]]["_id"])

        st.dataframe(mdf.drop(columns="_id"), hide_index=True, width="stretch", on_select=_open_req,
                     selection_mode="single-row", key="ag_reqs")
        st.caption("Select a request to see details, withdraw it or message your team lead about it.")
    else:
        kit.empty("No requests yet", "Use Request time off or Swap a shift on Home.")


# ------------------------------------------------------------------ history
if AREA == "My history":
    kit.label("My history", anchor="history")
    c1, c2 = st.columns([4, 1])
    with c2:
        if st.button("Report a record problem", key="disc_btn", width="stretch",
                     help="Tell your team lead an attendance or schedule record looks wrong"):
            ui.open_card("disc", True)
    with c1:
        st.caption("Your own shifts and changes, attendance and reminders, requests and decisions, notifications "
                   "and messages. Corrections are made by your team lead and keep the original record.")
    ui.history_view("ag_hist", "Only your own records.")


# ------------------------------------------------------------------ cards
def _preview(n) -> tuple[list[dict], float]:
    """Before/after rows and the extra paid hours for a proposed change."""
    rows, extra = [], 0.0
    if n["kind"] == "swap_ask":
        r = next(x for x in w.requests if x["id"] == n.get("request"))
        alt = r["alternative"]
        mine_d, theirs = ui.shift_row(r["agent_id"], r["day"]), ui.shift_row(aid, alt["partner_day"])
        rows.append({"Date": ui.dlabel(r["day"]), "Before": ui.shift_label(ui.shift_row(aid, r["day"])),
                     "After": ui.shift_label(mine_d)})
        rows.append({"Date": ui.dlabel(alt["partner_day"]), "Before": ui.shift_label(theirs), "After": "Off"})
        return rows, 0.0
    inc = next(i for i in w.incidents if i.id == n["incident"])
    act = next((a for a in inc.applied if a.id == n["action_id"]), None) or next(
        a for o in inc.offers for a in o["actions"] if a.id == n["action_id"])
    cur = ui.shift_row(aid, act.day)
    if act.kind == "open_shift":
        rows.append({"Date": ui.dlabel(act.day), "Before": "Off", "After": f"{act.shift} · {act.queue}"})
        extra = p["shifts"][act.shift]["paid"]
    elif act.kind == "extension":
        before = dict(cur, ext_before=cur["ext_before"] - act.ext_before, ext_after=cur["ext_after"] - act.ext_after) \
            if n["status"] == "new" and inc.action_status.get(act.id) == "waiting" else cur
        rows.append({"Date": ui.dlabel(act.day), "Before": ui.shift_label(before), "After": ui.shift_label(cur)})
        extra = act.ext_before + act.ext_after
    elif act.kind == "swap":
        rows.append({"Date": ui.dlabel(act.day), "Before": "Off", "After": f"{act.shift} · {act.queue}"})
        rows.append({"Date": ui.dlabel(act.swap_day), "Before": "Working", "After": "Off"})
    return rows, extra


@st.dialog("Proposed change", on_dismiss=ui.close_card)
def notif_card(nid):
    n = next(x for x in w.notifications if x["id"] == nid)
    st.markdown(f"**{n['text']}**")
    rows, extra = _preview(n)
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    day = None
    if n["kind"] != "swap_ask":
        inc = next(i for i in w.incidents if i.id == n["incident"])
        day = inc.day
    wk_days = [d for d in range(p["horizon"]["days"]) if day is not None and week_of(d) == week_of(day)]
    hrs = week_hours(wk_days) if wk_days else None
    pend = n["kind"] == "offer"
    kit.kpis([
        {"label": "Week hours after", "value": f"{(hrs or 0) + (extra if pend else 0):g} h" if hrs is not None else "unchanged",
         "sub": f"limit {p['rules']['max_weekly_hours']} h"},
        {"label": "Overtime pay", "value": kit.money(extra * rate * mult) if extra else "—",
         "sub": f"{mult:g}× rate" if extra else ""},
        {"label": "Rules", "value": "Checked", "sub": f"rest ≥ {p['rules']['min_rest_hours']} h · skills", "status": "Normal"},
    ])
    st.caption("Declining is fine: your team lead gets another option." if n["kind"] != "swap_ask"
               else "If you agree, your team lead approves the swap.")
    if n["status"] != "new":
        return
    c1, c2 = st.columns(2)
    for col, ok, lbl in ((c1, True, "Accept"), (c2, False, "Decline")):
        if col.button(lbl, type="primary" if ok else "secondary", width="stretch", key=f"n_{lbl}_{nid}"):
            with ui.mutate():
                services.respond(w, ui.user(), nid, ok)
            ui.close_card()
            ui.flash({"accepted": "Accepted · thank you", "declined": "Declined · team lead informed",
                      "withdrawn": "Already taken by a colleague", "expired": "No longer possible",
                      "read": "Updated"}.get(n["status"], "Updated"))


@st.dialog("Request time off", on_dismiss=ui.close_card)
def timeoff_card(_):
    days = [d for d in upcoming if d >= w.first_day and d not in
            {r["day"] for r in mine if r["state"] in ("Pending", "Waiting for colleague")}]
    if not days:
        st.caption("No working days to request.")
        return
    pre = st.session_state.pop("nl_day", None)
    day = st.selectbox("Day", days, index=days.index(pre) if pre in days else 0,
                       format_func=lambda d: f"{ui.dlabel(d)} · {ui.shift_label(ui.shift_row(aid, d))}", key="to_day")
    chk = workflow.leave_check(w, aid, day)
    st.markdown(kit.pill(ui.REQ_LABEL[chk["status"]], ui.REQ_STATUS[chk["status"]]) + " "
                + kit.esc({"Green": "Team covered · approved instantly", "Amber": "Team thin · team lead approves",
                           "Red": "Team short · team lead decides; options below keep it covered"}[chk["status"]]),
                unsafe_allow_html=True)
    choice = None
    if chk["alternatives"]:
        opts = ["The day I asked for"] + [a["option"] for a in chk["alternatives"]]
        pick = st.radio("Option", opts, key="to_opt")
        choice = None if pick == opts[0] else chk["alternatives"][opts.index(pick) - 1]
    lt = st.selectbox("Type of leave", list(hr.LEAVE_TYPES), format_func=lambda k: hr.LEAVE_TYPES[k][0], key="to_type")
    if hr.LEAVE_TYPES[lt][1]:
        b = services.leave_balance(w, ui.user(), aid, lt)
        st.caption(f"Your balance: {b['available']:g} {b['unit']}" if b["state"] == "verified" else
                   "Your balance is not verified by HR yet, so this goes to a person (HR review, then your team lead).")
    if lt == "sick":
        st.caption("Sick-leave details stay with HR; your team lead sees only that you are on leave.")
    note = st.text_input("Note", value=st.session_state.pop("nl_reason", "") or "", key="to_note")
    if st.button("Send", type="primary"):
        try:
            with ui.mutate():
                r = services.submit_leave(w, ui.user(), day, choice, note, leave_type=lt)
        except (ValueError, services.Denied) as e:
            st.error(str(e))
            return
        ui.close_card()
        ui.flash("Approved" if r["state"] == "Approved" else
                 "Sent · next: HR review, then team lead" if r.get("hold", "").startswith("HR review") else
                 "Sent · next: team lead")


@st.dialog("Swap a shift", on_dismiss=ui.close_card)
def swap_card(_):
    days = [d for d in upcoming if d >= w.first_day]
    if not days:
        st.caption("No shifts to swap.")
        return
    day = st.selectbox("My shift", days, format_func=lambda d: f"{ui.dlabel(d)} · {ui.shift_label(ui.shift_row(aid, d))}",
                       key="sw_day")
    opts = rq.swap_options(p, w.data, w.roster, aid, day, limit=5, min_day=w.first_day)
    if not opts:
        st.caption("No trained colleague free that day.")
        return
    labels = [o["option"] for o in opts]             # text options: screen readers announce the colleague
    pick = labels.index(st.radio("Colleague", labels, key="sw_pick"))
    st.caption("Only colleagues trained for your queue · rules checked for both")
    if st.button("Ask colleague", type="primary"):
        with ui.mutate():
            services.request_swap(w, ui.user(), day, opts[pick])
        ui.close_card()
        ui.flash(f"Asked {opts[pick]['partner']} · next: colleague, then team lead")


@st.dialog("Can't come in", on_dismiss=ui.close_card)
def absent_card(_):
    days = [d for d in upcoming[:4] if not (now_row and d == now_row["day"])]
    if not days:
        st.caption("No shifts in the next days.")
        return
    pre = st.session_state.pop("nl_day", None)
    day = st.selectbox("Shift", days, index=days.index(pre) if pre in days else 0,
                       format_func=lambda d: f"{ui.dlabel(d)} · {ui.shift_label(ui.shift_row(aid, d))}", key="ab_day")
    why = st.selectbox("Reason", ["Unwell", "Family emergency", "Other"], key="ab_why")
    if st.button("Report", type="primary"):
        try:
            with ui.mutate():
                services.report_absence(w, ui.user(), [aid], day, why.lower())
            ui.close_card()
            ui.flash("Reported · team lead is arranging cover")
        except (ValueError, services.Denied) as e:
            st.error(str(e))


@st.dialog("Leave now", on_dismiss=ui.close_card)
def leavenow_card(_):
    st.markdown(f"On shift: **{ui.shift_label(now_row)}**")
    if st.button("Confirm I need to leave", type="primary"):
        with ui.mutate():
            services.report_left_early(w, ui.user(), aid)
        ui.close_card()
        ui.flash("Recorded · team lead is arranging cover")


@st.dialog("My request", on_dismiss=ui.close_card)
def myreq_card(req_id):
    try:
        r = services.get_request(w, ui.user(), req_id)
    except services.Denied as e:
        st.error(str(e))
        return
    what = r["alternative"]["option"] if r["alternative"] else f"Day off · {ui.dlabel(r['day'])}"
    st.markdown(f"**{what}**")
    st.markdown(f'{kit.pill(r["state"], "Info")} {kit.pill(ui.REQ_LABEL[r["status"]], ui.REQ_STATUS[r["status"]])}',
                unsafe_allow_html=True)
    if r.get("reason"):
        st.caption(f"Reason: {r['reason']}")
    if r["state"] in ("Pending", "Waiting for colleague") and st.button("Withdraw request", key=f"wd_{req_id}"):
        try:
            with ui.mutate():
                services.withdraw(w, ui.user(), req_id)
            ui.close_card()
            ui.flash("Request withdrawn")
        except (ValueError, services.Denied) as e:
            st.error(str(e))
    if not r.get("simulated_history"):
        with st.expander("Message team lead about this request"):
            ui.thread_view(f"request:{req_id}", key=f"mr_thr_{req_id}")


@st.dialog("Report a record problem", on_dismiss=ui.close_card)
def disc_card(_):
    past = [d for d in range(max(0, w.today - 13), w.today + 1)]
    day = st.selectbox("Which day?", past[::-1], format_func=ui.dlabel, key="disc_day")
    st.caption(f"Current record: {records.STATUS[records.day_record(w, aid, day)['code']][0]} · "
               f"{records.day_record(w, aid, day)['detail'] or '—'}")
    note = st.text_area("What looks wrong? (required)", key="disc_note",
                        placeholder="I worked this shift but could not check in")
    if st.button("Send to team lead", type="primary", key="disc_go"):
        try:
            services.report_discrepancy(w, ui.user(), day, note)
            ui.close_card()
            ui.flash("Sent to your team lead · nothing changes until they record a correction")
        except (ValueError, services.Denied) as e:
            st.error(str(e))
    if ui.shift_row(aid, day):
        st.divider()
        st.caption(f"Formal correction: attendance locks {hr.value('attendance.lock_hours'):g} h after a shift ends. "
                   "Ask for a correction and an HR reviewer applies or rejects it; the original stays on record.")
        want = st.selectbox("The record should say", ["present", "absent", "no record"], key="disc_want")
        if st.button("Ask HR for a correction", key="disc_hr"):
            try:
                services.request_attendance_correction(w, ui.user(), aid, day, want, note)
                ui.close_card()
                ui.flash("Correction request sent · HR reviews it")
            except (ValueError, services.Denied) as e:
                st.error(str(e))


@st.dialog("Check in", on_dismiss=ui.close_card)
def checkin_card(rid):
    r = next((x for x in attendance.log(w.run_id, {aid}) if str(x["id"]) == str(rid)), None)
    if r is None:
        st.caption("This reminder is not yours or no longer exists.")
        return
    services.open_reminder(w, ui.user(), r["id"])
    if w.attendance.get((aid, r["day"])):
        st.success(f"Attendance already recorded for {r['shift']}.")
    elif not any(x["id"] == r["id"] for x in checkins):
        st.info(f"Check-in for {r['shift']} has closed or was replaced by a newer reminder. If you are working, "
                "ask your team lead to confirm your attendance.")
    else:
        st.caption("A reminder never records attendance. Press Check in when you have arrived.")
        _checkin_row(r, "cic")


if ui.card("disc"):
    disc_card(True)
elif (rid := ui.card("checkin")) is not None:
    checkin_card(rid)
elif (ref := ui.card("notif")) is not None:
    notif_card(ref)
elif (ref := ui.card("myreq")) is not None:
    myreq_card(ref)
elif ui.card("timeoff"):
    timeoff_card(True)
elif ui.card("swap"):
    swap_card(True)
elif ui.card("absent"):
    absent_card(True)
elif ui.card("leavenow") and now_row:
    leavenow_card(True)
else:
    ui.shared_cards()
