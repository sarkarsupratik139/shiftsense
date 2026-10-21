"""HR · People data: employee details, employment contracts and leave; team leads' proposals.

HR owns people data. Every applied change flows into the roster the same way as any data change, and the Analyst
is told what changed and what it means for the plan. Contract changes still need Ops (cost and coverage)."""
import pandas as pd
import streamlit as st

import ui_common as ui
import ui_data as ud
import ui_kit as kit
from shiftsense import accounts, exports, hr, refdata, services, workflow
from shiftsense.schemas import DOMAINS

ui.page("hr")
w = ui.live_world()
u = ui.user()
SENIOR = ui.can("senior_hr")
area = ui.area("hr")
# each part of this screen -> (area, tab within the area)
PLACE = {"People overview": ("People overview", None), "Directory": ("Employees", "Directory"),
         "Update people data": ("Employees", "Update data"), "Separations": ("Employees", "Separations"),
         "Leave balances": ("Leave & balances", None), "Attendance corrections": ("Attendance", None),
         "Absence and overtime": ("Attendance", None), "Reviews and corrections": ("Reviews & approvals", None),
         "Proposals and changes": ("Reviews & approvals", None), "Restricted cases": ("Restricted cases", None),
         "HR policy": ("HR policies", None), "People downloads": ("Reports & exports", None)}
ui.shell(area, f"{workflow.now_label(w)} · HR{' (Senior HR)' if SENIOR else ''} · live workspace")
emp_tab = None
if area == "Employees":
    emp_tab = st.segmented_control("View", ["Directory", "Update data", "Separations"], default="Directory",
                                   key="hr_emp_tab", label_visibility="collapsed") or "Directory"


def SHOW(part: str) -> bool:
    a, tab = PLACE[part]
    return area == a and (tab is None or tab == emp_tab)
refdata.ensure()
services.hr_reminders(w)                        # due / overdue HR work -> reminders (once per item per day)
PEOPLE = ["employees", "contracts", "leave"]
emps = refdata.current("employees")

if SHOW("Directory"):
    from datetime import date as _date
    from shiftsense import records
    from shiftsense.config import queue_names
    _ag = w.data["agents"].set_index("agent_id")
    _ids = sorted(set(_ag.index) | set(emps))
    _tl = {a["team"]: a["name"] for a in accounts.all_accounts() if a["role"] == "team_lead" and a["active"]}
    _leaving = {s["employee_id"]: s for s in hr.separations() if s["status"] in ("recorded", "confirmed")}
    _ph = ui.photos(_ids)

    def _rec(e: str) -> dict:
        """The employee record (reference data when present, else the planning record)."""
        if e in emps:
            return dict(emps[e])
        a = _ag.loc[e]
        return {"employee_id": e, "name": a["name"], "team": a["team"], "primary": a["primary"],
                "secondary": list(a["secondary"]), "night_ok": bool(a["night_ok"]), "pref_shift": a["pref_shift"],
                "pref_off": int(a["pref_off"]), "status": "Active"}

    _recs = {e: _rec(e) for e in _ids}
    c1, c2, c3, c4 = st.columns([2.6, 1.2, 1.2, 1.6])
    q = c1.text_input("Search employees", key="hr_q", placeholder="Name, employee ID or queue")
    _teams = sorted({r.get("team") for r in _recs.values() if r.get("team")})
    tf = c2.selectbox("Team", ["All teams"] + _teams, key="hr_team_f")
    sf = c3.selectbox("Status", ["All", "Active", "On notice", "Long leave", "Exited"], key="hr_stat_f")
    layout = c4.segmented_control("Layout", ["Table", "Cards"], default="Table", key="hr_layout") or "Table"
    shown = [e for e, r in _recs.items()
             if (not q or q.lower() in f"{r.get('name', '')} {e} {r.get('primary', '')}".lower())
             and (tf == "All teams" or r.get("team") == tf) and (sf == "All" or (r.get("status") or "Active") == sf)]
    st.caption(f"{len(shown)} of {len(_ids)} employees · photos are shown where one is on file, otherwise initials")
    if not shown:
        kit.empty("No employee matches", "Clear the search or choose another team or status.")
    elif layout == "Table":
        _df = pd.DataFrame([{"Photo": kit.avatar_uri(_recs[e]["name"], _ph.get(e)), "Name": _recs[e]["name"],
                             "Employee ID": e, "Team": _recs[e].get("team", "—"),
                             "Reports to": _tl.get(_recs[e].get("team"), "—"), "Primary queue": _recs[e].get("primary"),
                             "Status": (_recs[e].get("status") or "Active")
                             + (" · leaving" if e in _leaving else "")} for e in shown])

        def _pick():
            sel = st.session_state["hr_dir"].selection.rows
            if sel:
                st.session_state["hr_emp"] = _df.iloc[sel[0]]["Employee ID"]

        st.dataframe(_df, hide_index=True, width="stretch", on_select=_pick, selection_mode="single-row",
                     key="hr_dir", height=min(40 + 35 * len(_df), 420),
                     column_config={"Photo": st.column_config.ImageColumn("", width="small")})
    else:
        kit.people([(_recs[e]["name"], f"{e} · {_recs[e].get('team', '—')} · {_recs[e].get('primary', '')}",
                     _ph.get(e)) for e in shown[:60]])
        if len(shown) > 60:
            st.caption(f"Showing the first 60 of {len(shown)}; narrow the search to see the rest.")
    pick = st.selectbox("Open an employee", shown, index=shown.index(st.session_state["hr_emp"])
                        if st.session_state.get("hr_emp") in shown else None,
                        format_func=lambda e: f"{_recs[e]['name']} ({e})", key="hr_open",
                        placeholder="Choose an employee to see the profile") if shown else None
    if pick:
        st.session_state["hr_emp"] = pick
        r = _recs[pick]
        stat = r.get("status") or "Active"
        pills = kit.pill(stat, {"Active": "Normal", "On notice": "Warning", "Exited": "Neutral"}.get(stat, "Info"))
        if pick in _leaving:
            pills += " " + kit.pill(f"Leaving after {_leaving[pick]['effective_date']} ({_leaving[pick]['status']})",
                                    "Warning")
        big = services.photo_uris(w, u, [pick], "image").get(pick)
        kit.profile_header(r["name"], [f"{pick} · Team {r.get('team', '—')} · reports to {_tl.get(r.get('team'), '—')}",
                                       f"{r.get('primary', '—')} · {r.get('contract') or 'contract not recorded'}"],
                           big, pills)
        t_emp, t_sch, t_lv, t_att, t_hist, t_photo, t_edit = st.tabs(
            ["Employment", "Schedule", "Leave", "Attendance", "History", "Profile photo", "Edit details"])
        with t_emp:
            _days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            st.dataframe(pd.DataFrame([
                ("Employee ID", pick), ("Full name", r["name"]), ("Team", r.get("team")),
                ("Reporting manager", _tl.get(r.get("team"), "—")), ("Contract", r.get("contract") or "—"),
                ("Status", stat), ("Start date", r.get("start_date") or "—"),
                ("Last working day", r.get("end_date") or "—"), ("Primary queue", r.get("primary")),
                ("Other trained queues", ", ".join(r.get("secondary") or []) or "—"),
                ("Skill levels", r.get("skill_levels") or "—"),
                ("Night shifts allowed", "Yes" if r.get("night_ok", True) else "No"),
                ("Preferred shift", r.get("pref_shift") or "—"),
                ("Preferred day off", _days[int(r.get("pref_off") or 6)] if str(r.get("pref_off", "")).strip() != ""
                 else "—"),
                ("Profile photo", "On file" if pick in _ph else "Not set · initials shown")],
                columns=["Field", "Value"]), hide_index=True, width="stretch")
            if pick not in emps:
                st.caption("This person's details come from the planning data; saving an edit creates their "
                           "versioned employee record.")
        with t_sch:
            if pick in _ag.index:
                _H = w.policy["horizon"]["days"]
                for _w0 in (w.today, w.today + 7):
                    _dd = [d for d in range(_w0, min(_w0 + 7, _H))]
                    if _dd:
                        ui.roster_calendar([pick], _dd, f"Schedule from {ui.dlabel(_dd[0])}", with_record=True)
            else:
                kit.empty("No schedule", "This person is not in the planning data yet.")
        with t_lv:
            bal = [{"Leave type": lbl, "Available": (f"{b['available']:g} {b['unit']}" if b["state"] == "verified"
                                                     else "Unresolved"), "Status": b["state"]}
                   for lt, (lbl, paid, _u) in hr.LEAVE_TYPES.items() if paid
                   for b in [hr.balance(pick, lt, w.run_id)]]
            st.dataframe(pd.DataFrame(bal), hide_index=True, width="stretch")
            _req = [x for x in w.requests if x["agent_id"] == pick and x.get("kind") == "leave"]
            if _req:
                st.dataframe(pd.DataFrame([{"Request": x["id"], "Day": ui.dlabel(x["day"]),
                                            "Type": hr.LEAVE_TYPES.get(x.get("leave_type") or "", ("—",))[0],
                                            "State": x["state"]} for x in _req]), hide_index=True, width="stretch")
            else:
                st.caption("No leave requests in this workspace.")
        with t_att:
            if pick in _ag.index:
                st.dataframe(pd.DataFrame([{"Day": ui.dlabel(d), "Record": records.STATUS[
                    records.day_record(w, pick, d)["code"]][0]} for d in range(max(0, w.today - 13), w.today + 1)][::-1]),
                    hide_index=True, width="stretch")
                st.caption("“No record” is unknown: never treated as an absence.")
        with t_hist:
            hst = refdata.history("employees", pick)
            if hst:
                st.dataframe(pd.DataFrame([{"Version": h["version"], "Effective": h["effective_from"],
                                            "By": h["changed_by"], "Source": h["source"],
                                            "Created": (h.get("created") or "")[:16]} for h in hst][::-1]),
                             hide_index=True, width="stretch")
            else:
                st.caption("No versioned changes yet for this employee.")
        with t_photo:
            st.caption("Optional. JPEG, PNG or WebP up to "
                       f"{__import__('shiftsense.photos', fromlist=['x']).max_bytes() / 1048576:g} MB. The image is "
                       "checked, cropped to a square and resized on the server; location and camera data are removed. "
                       "Without a photo, initials are shown. Photos are never used for recognition or sent to AI.")
            pc1, pc2 = st.columns([1, 3])
            with pc1:
                st.markdown(kit.avatar(r["name"], big, "xl"), unsafe_allow_html=True)
            with pc2:
                up = st.file_uploader("Choose a photo", type=["jpg", "jpeg", "png", "webp"], key=f"hr_ph_{pick}")
                if up is not None:
                    st.image(up.getvalue(), caption="Preview (before cropping)", width=160)
                    if st.button("Save photo" if pick not in _ph else "Replace photo", type="primary",
                                 key=f"hr_ph_go_{pick}"):
                        try:
                            with st.spinner("Checking and resizing…"):
                                services.set_employee_photo(w, u, pick, up.getvalue())
                            ui.flash("Photo saved · shown wherever this person appears")
                        except (ValueError, services.Denied) as e:
                            st.error(str(e))
                if pick in _ph:
                    sure = st.checkbox("I want to remove this photo (initials will be shown)", key=f"hr_ph_rm_ok_{pick}")
                    if st.button("Remove photo", key=f"hr_ph_rm_{pick}", disabled=not sure):
                        try:
                            services.remove_employee_photo(w, u, pick)
                            ui.flash("Photo removed · initials shown")
                        except (ValueError, services.Denied) as e:
                            st.error(str(e))
        with t_edit:
            from shiftsense.config import load_policy as _lp
            _contracts = sorted(refdata.current("contracts")) or sorted((w.policy.get("contracts") or {}).keys())
            _queues = queue_names(w.policy)
            _shifts = list(w.policy["shifts"])
            st.caption("Changes are saved as a new version of the employee record from the date you choose, and flow "
                       "into the roster like any people-data change. Leave a field as it is to keep it.")
            with st.form(f"hr_edit_{pick}"):
                st.markdown("**1 · Basic information**")
                e1, e2 = st.columns(2)
                e1.text_input("Employee ID", value=pick, disabled=True)
                nm = e2.text_input("Full name", value=r["name"])
                st.markdown("**2 · Employment and team**")
                e1, e2, e3 = st.columns(3)
                tm = e1.selectbox("Team", _teams, index=_teams.index(r["team"]) if r.get("team") in _teams else 0)
                ct = e2.selectbox("Contract", _contracts, index=_contracts.index(r["contract"])
                                  if r.get("contract") in _contracts else 0) if _contracts else None
                stt_opts = ["Active", "On notice", "Long leave", "Exited"]
                stt = e3.selectbox("Status", stt_opts, index=stt_opts.index(stat) if stat in stt_opts else 0)
                e1, e2 = st.columns(2)
                sd = e1.date_input("Start date", value=_date.fromisoformat(r["start_date"]) if r.get("start_date")
                                   else None)
                ed = e2.date_input("Last working day", value=_date.fromisoformat(r["end_date"]) if r.get("end_date")
                                   else None, help="Leaving is handled under Separations; this only records the date")
                st.markdown("**3 · Schedule and skills**")
                e1, e2 = st.columns(2)
                pq = e1.selectbox("Primary queue", _queues, index=_queues.index(r["primary"])
                                  if r.get("primary") in _queues else 0)
                sq = e2.multiselect("Other trained queues", [x for x in _queues if x != pq],
                                    default=[x for x in (r.get("secondary") or []) if x in _queues and x != pq])
                e1, e2, e3 = st.columns(3)
                ps = e1.selectbox("Preferred shift", _shifts, index=_shifts.index(r["pref_shift"])
                                  if r.get("pref_shift") in _shifts else 0)
                po = e2.selectbox("Preferred day off", _days, index=int(r.get("pref_off") or 6))
                no = e3.checkbox("Night shifts allowed", value=bool(r.get("night_ok", True)))
                sl = st.text_input("Skill level per queue", value=r.get("skill_levels") or "",
                                   placeholder="Claims-EN:3; Banking-EN:2")
                st.markdown("**4 · Apply**")
                e1, e2 = st.columns(2)
                eff = e1.date_input("Effective from", value=_date.fromisoformat(refdata.iso_of_day(w.policy, w.today)))
                why = e2.text_input("Reason for the change (required)")
                st.caption("The profile photo is managed in its own tab. Unsaved edits are lost if you leave this page.")
                go = st.form_submit_button("Save new version", type="primary")
            if go:
                new = {**r, "name": nm, "team": tm, "status": stt, "start_date": sd.isoformat() if sd else None,
                       "end_date": ed.isoformat() if ed else None, "primary": pq, "secondary": sq,
                       "pref_shift": ps, "pref_off": _days.index(po), "night_ok": no, "skill_levels": sl or None}
                if ct:
                    new["contract"] = ct
                if not why.strip():
                    st.error("Give a reason for the change.")
                elif not new.get("contract"):
                    st.error("Choose a contract (none are loaded yet: the Analyst loads the base data first).")
                else:
                    try:
                        with ui.mutate(w):
                            out = services.submit_change(w, u, "employees", [new], effective_from=eff.isoformat(),
                                                         reason=why, source="employee profile")
                        ui.flash(f"Saved · change #{out['id']} {out['status']}")
                    except (ValueError, services.Denied) as e:
                        st.error(str(e))


if SHOW("People overview"):
    if not emps:
        st.info("No people data yet. The Analyst loads the base data once (Data hub); after that HR maintains it here.")
    else:
        df = pd.DataFrame(emps.values())
        status = df.get("status", pd.Series(["Active"] * len(df))).fillna("Active")
        pending = [r for r in services.data_changes(w, u, status="pending") if r["domain"] == "employees"]
        kit.kpis([{"label": "Employees", "value": len(df)},
                  {"label": "Active", "value": int((status == "Active").sum())},
                  {"label": "On notice", "value": int((status == "On notice").sum()),
                   "status": "Warning" if (status == "On notice").any() else None},
                  {"label": "Contracts in use", "value": df["contract"].nunique()},
                  {"label": "Proposals to decide", "value": len(pending), "status": "Warning" if pending else None}])
        kit.label("Headcount by team and contract")
        st.dataframe(pd.crosstab(df["team"], df["contract"], margins=True, margins_name="Total"), width="stretch")
        kit.label("Joining and leaving")
        moves = [{"Employee": e["name"], "ID": e["employee_id"], "Team": e["team"],
                  "Joins": e.get("start_date") or "", "Last day": e.get("end_date") or "", "Status": e.get("status")}
                 for e in emps.values() if e.get("end_date") or (e.get("start_date") or "") >= refdata.iso_of_day(
                     w.policy, 0)]
        st.dataframe(pd.DataFrame(moves), hide_index=True, width="stretch") if moves else st.caption("None in the plan period.")
        kit.label("Leave in the plan period")
        lv = list(refdata.current("leave").values())
        st.caption(f"{len(lv)} leave record(s); the Analyst and team leads see their effect on coverage.")

if SHOW("Update people data"):
    ud.drop_folder(w, u, "hr")
    st.caption("Upload an HR export, paste a table or text, or type a single change. Changes apply from the date you "
               "choose; shifts they make impossible become alerts (today and tomorrow) or a re-plan recommendation "
               "for the Analyst (later days). Contract changes go to Ops for approval.")
    ud.add_or_update(w, u, PEOPLE)

if SHOW("Proposals and changes"):
    kit.label("Team leads' proposals")
    ud.approvals(w, can_decide=ui.can("manage_people"), key="hr_appr", domains=("employees",))
    kit.label("People data changes")
    rows = [r for r in services.data_changes(w, u) if r["domain"] in PEOPLE]
    if rows:
        st.dataframe(pd.DataFrame([{"Change": r["id"], "Data": DOMAINS[r["domain"]].label, "Status": r["status"].title(),
                                    "By": r["submitted_by"], "Effective": r["effective_from"],
                                    "Reason": (r["reason"] or "—")[:60],
                                    "Effect": services._outcome_text(r["outcome"] or {}) if r["outcome"] else ""}
                                   for r in rows]), hide_index=True, width="stretch")
        st.download_button("Change log (Excel)", exports.change_log(rows), "people_data_changes.xlsx")
    else:
        st.caption("No people data changes yet.")

if SHOW("Reviews and corrections"):
    kit.label("Leave review tasks")
    st.caption("A trigger puts a leave request here. You recommend; you never approve or reject. The team lead "
               "decides, and cannot approve while the review is open.")
    tasks = services.review_tasks(w, u)
    if not tasks:
        st.caption("No open review tasks.")
    for t in tasks:
        r = next((x for x in w.requests if x["id"] == t["request_id"]), None)
        with st.container(border=True):
            st.markdown(f"**{t['request_id']}** · {ui.name(t['employee_id'])} · "
                        + (f"{ui.dlabel(r['day'])} · {hr.LEAVE_TYPES.get(r.get('leave_type') or '', ('—',))[0]} · "
                           f"{r['state']}" if r else "request not found"))
            st.caption(f"Trigger: {t['trigger_name'].replace('_', ' ')} · {t['reason']} · assigned to "
                       f"{t['assigned_to'] or 'HR queue'}" + (f" · note from agent: {r['note']}" if r and r['note']
                                                              else ""))
            if t["recommendation"]:
                st.caption(f"Recommended: {t['recommendation']} by {t['recommended_by']} · {t['note']}")
            with st.popover("Reassign", icon=":material/person_search:"):
                pool = [a["username"] for a in accounts.all_accounts() if a["role"] == "hr" and a["active"]]
                to = st.selectbox("Reviewer", pool, key=f"rv_to_{t['id']}")
                rr = st.text_input("Reason (required)", key=f"rv_rr_{t['id']}")
                if st.button("Reassign", key=f"rv_ra_{t['id']}"):
                    try:
                        services.reassign_review(w, u, t["id"], to, rr)
                        ui.flash(f"Reassigned to {to}")
                    except (ValueError, services.Denied) as e:
                        st.error(str(e))
            c1, c2, c3 = st.columns([2, 3, 1])
            rec = c1.selectbox("Recommendation", ["approve", "reject", "no objection", "needs more information"],
                               key=f"rv_rec_{t['id']}")
            note = c2.text_input("Note (required)", key=f"rv_note_{t['id']}")
            if c3.button("Send", key=f"rv_go_{t['id']}", width="stretch"):
                try:
                    services.recommend_on_review(w, u, t["id"], rec, note)
                    ui.flash("Recommendation sent · the team lead decides")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))
if SHOW("Attendance corrections"):
    kit.label("Attendance correction requests")
    st.caption(f"Attendance locks {hr.value('attendance.lock_hours'):g} h after the shift ends. Applying writes an "
               "audited adjustment; the original value stays on record. Absence counts are re-checked.")
    cs = services.attendance_corrections(w, u, "pending")
    if not cs:
        st.caption("No correction requests waiting.")
    for c in cs:
        with st.container(border=True):
            st.markdown(f"**#{c['id']}** · {ui.name(c['employee_id'])} · {ui.dlabel(int(c['day']))} · "
                        f"{c['original']} → **{c['requested']}**")
            st.caption(f"Asked by {c['requested_by']} · {c['reason']}")
            c1, c2, c3 = st.columns([3, 1, 1])
            why = c1.text_input("Reason (required)", key=f"ac_why_{c['id']}")
            for col, ok, lbl in ((c2, True, "Apply"), (c3, False, "Reject")):
                if col.button(lbl, key=f"ac_{lbl}_{c['id']}", width="stretch"):
                    try:
                        with ui.mutate(w):
                            services.review_attendance_correction(w, u, c["id"], ok, why)
                        ui.flash(f"Correction {'applied' if ok else 'rejected'} · audited")
                    except (ValueError, services.Denied) as e:
                        st.error(str(e))
    done = [c for c in services.attendance_corrections(w, u) if c["status"] != "pending"]
    if done:
        with st.expander(f"Reviewed corrections ({len(done)})"):
            st.dataframe(pd.DataFrame([{"#": c["id"], "Agent": c["employee_id"], "Day": ui.dlabel(int(c["day"])),
                                        "Original": c["original"], "Requested": c["requested"],
                                        "Result": c["status"], "Reviewer": c["reviewed_by"],
                                        "Reason": c["review_reason"]} for c in done]),
                         hide_index=True, width="stretch")

if SHOW("Leave balances"):
    kit.label("Leave calendar")
    _lt = sorted(set(w.data["agents"].team))
    _lc1, _lc2 = st.columns([1, 3])
    _tsel = _lc1.selectbox("Team", ["All teams"] + _lt, key="hr_lc_team")
    _ppl = [a for a, t in zip(w.data["agents"].agent_id, w.data["agents"].team) if _tsel == "All teams" or t == _tsel]
    ui.leave_calendar(_ppl, [d for d in range(w.today, min(w.today + 14, w.policy["horizon"]["days"]))],
                      show_type=True, key="hr_leavecal")
    kit.label("Balances")
    st.caption("Opening balances are entered by HR and stay unverified until a second HR user verifies them. Only "
               "verified balances make leave eligible; a missing or unverified balance is shown as unresolved, never "
               "assumed. Approved leave and cancellations are transactions; corrections are adjustments with a "
               "reason. Live only: demo-scenario moves never touch these figures.")
    agents = w.data["agents"].set_index("agent_id")["name"].to_dict()
    rows = []
    for a in sorted(agents):
        for lt, (lbl, paid, unit) in hr.LEAVE_TYPES.items():
            if not paid:
                continue
            b = hr.balance(a, lt, w.run_id)
            rows.append({"Agent": f"{agents[a]} ({a})", "Leave type": lbl,
                         "Available": f"{b['available']:g} {b['unit']}" if b["state"] == "verified" else "Unresolved",
                         "Status": b["state"]})
    df = pd.DataFrame(rows)
    kit.kpis([{"label": "Verified", "value": int((df.Status == "verified").sum())},
              {"label": "Waiting for verification", "value": int((df.Status == "unverified").sum()),
               "status": "Warning" if (df.Status == "unverified").any() else None},
              {"label": "Missing", "value": int((df.Status == "missing").sum())}])
    st.dataframe(df, hide_index=True, width="stretch", height=280)
    with st.expander("Import balances from the HR system (Excel or CSV)"):
        st.caption("Columns are matched by name (e.g. Employee ID, Leave type, Balance, As of, Source). Every usable "
                   "row is saved as unverified; rows with a missing or unreadable value are listed and not saved. "
                   "Nightly feeds can be dropped in the drop folder under leave_balances/.")
        up = st.file_uploader("HR system export", type=["xlsx", "xls", "csv", "tsv", "txt"], key="bal_up")
        if up is not None and st.button("Import as unverified", key="bal_imp"):
            try:
                res = services.import_leave_balances(w, u, up.getvalue(), up.name)
                st.session_state["bal_impres"] = res
                ui.flash(f"{len(res['entered'])} balance(s) saved as unverified · {len(res['problems'])} row(s) "
                         "held back")
            except (ValueError, services.Denied) as e:
                st.error(str(e))
        res = st.session_state.get("bal_impres")
        if res and res["problems"]:
            st.dataframe(pd.DataFrame(res["problems"], columns=["Row", "Problem"]), hide_index=True, width="stretch")
    with st.expander("Enter an opening balance"):
        c1, c2, c3 = st.columns(3)
        ea = c1.selectbox("Employee", sorted(agents), format_func=lambda a: f"{agents[a]} ({a})", key="bal_emp")
        elt = c2.selectbox("Leave type", [k for k, v in hr.LEAVE_TYPES.items() if v[1]],
                           format_func=lambda k: hr.LEAVE_TYPES[k][0], key="bal_lt")
        amt = c3.number_input("Opening balance (days)", min_value=0.0, step=0.5, key="bal_amt")
        c4, c5 = st.columns(2)
        eff = c4.date_input("Effective date", key="bal_eff")
        src = c5.text_input("Source reference (required)", key="bal_src", placeholder="HRIS export 2026-10, row 14")
        if st.button("Save as unverified", key="bal_go"):
            try:
                services.enter_leave_balance(w, u, ea, elt, amt, eff.isoformat(), src)
                ui.flash("Saved · waiting for a second HR user to verify")
            except (ValueError, services.Denied) as e:
                st.error(str(e))
    pend = [b for b in hr.balances() if b["status"] == "unverified"]
    if pend:
        kit.label("Waiting for verification")
        others = [b["id"] for b in pend if b["entered_by_user"] != u["username"]]
        if others and st.button(f"Verify all {len(others)} entered by someone else", key="bal_vall",
                                help="Check them against the source first. Balances you entered need another HR user."):
            r = services.verify_leave_balances(w, u, others)
            ui.flash(f"{len(r['verified'])} verified" + (f" · {len(r['refused'])} refused" if r["refused"] else ""))
        for b in pend:
            c1, c2 = st.columns([5, 1])
            c1.caption(f"{agents.get(b['employee_id'], b['employee_id'])} · {hr.LEAVE_TYPES[b['leave_type_id']][0]} · "
                       f"{b['opening_balance']:g} {b['unit']} from {b['effective_date']} · source: "
                       f"{b['source_reference']} · entered by {b['entered_by']}")
            if c2.button("Verify", key=f"bal_v_{b['id']}", width="stretch",
                         disabled=b["entered_by_user"] == u["username"],
                         help="A different HR user verifies" if b["entered_by_user"] == u["username"] else None):
                try:
                    services.verify_leave_balance(w, u, b["id"])
                    ui.flash("Verified")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))
    with st.expander("Adjust a balance (correction with a reason)"):
        c1, c2, c3 = st.columns(3)
        aa = c1.selectbox("Employee", sorted(agents), format_func=lambda a: f"{agents[a]} ({a})", key="adj_emp")
        alt = c2.selectbox("Leave type", [k for k, v in hr.LEAVE_TYPES.items() if v[1]],
                           format_func=lambda k: hr.LEAVE_TYPES[k][0], key="adj_lt")
        am = c3.number_input("Change (+/- days)", step=0.5, key="adj_amt")
        why = st.text_input("Reason (required)", key="adj_why")
        if st.button("Record adjustment", key="adj_go"):
            try:
                services.adjust_leave_balance(w, u, aa, alt, am, why)
                ui.flash("Adjustment recorded")
            except (ValueError, services.Denied) as e:
                st.error(str(e))
    tx = hr.transactions(run_id=w.run_id)
    if tx:
        with st.expander(f"Transactions ({len(tx)})"):
            st.dataframe(pd.DataFrame([{"When": t["at"].replace("T", " "), "Employee": t["employee_id"],
                                        "Type": t["leave_type_id"], "Kind": t["kind"], "Days": t["amount"],
                                        "Request": t["request_id"] or "", "Reason": t["reason"], "By": t["by_name"]}
                                       for t in tx]), hide_index=True, width="stretch")

if SHOW("Separations"):
    services.run_separations(w, None)          # records any separation that reached its time (idempotent)
    tz, eff_t = hr.value("separation.timezone"), hr.value("separation.effective_time")
    st.caption(f"Record → confirm (by a different HR user) → on the effective date the account is deactivated "
               f"automatically and every open session ends on its next action. History is kept. "
               + ("Access ends at 00:00 " + tz + " on the day after the last working day"
                  if eff_t == "end_of_day" else "Access ends at 00:00 " + tz + " on the effective date")
               + (" (approved)." if hr.pol("separation.effective_time")[1] == "approved" else
                  " (pending HR confirmation).")
               + " Unconfirmed separations never deactivate anything. Rescind and corrections are audited.")
    agents = w.data["agents"].set_index("agent_id")["name"].to_dict()
    with st.expander("Record a separation"):
        c1, c2, c3 = st.columns(3)
        se = c1.selectbox("Employee", sorted(agents), format_func=lambda a: f"{agents[a]} ({a})", key="sep_emp")
        sd = c2.date_input("Effective date (last working day)", key="sep_date")
        sr = c3.selectbox("Reason category", hr.SEPARATION_REASONS, key="sep_reason")
        sn = st.text_input("Note (optional, HR only)", key="sep_note")
        if st.button("Record (not yet confirmed)", key="sep_go"):
            try:
                services.record_separation(w, u, se, sd.isoformat(), sr, sn)
                ui.flash("Recorded · a different HR user confirms it")
            except (ValueError, services.Denied) as e:
                st.error(str(e))
    seps = services.separations_view(u)
    if not seps:
        st.caption("No separations recorded.")
    for sp in reversed(seps):
        with st.container(border=True):
            st.markdown(f"**#{sp['id']}** · {agents.get(sp['employee_id'], sp['employee_id'])} ({sp['employee_id']}) · "
                        f"effective {sp['effective_date']} · {kit.pill(sp['status'], {'executed': 'Info', 'confirmed': 'Warning', 'recorded': 'Warning'}.get(sp['status'], 'Normal'))}",
                        unsafe_allow_html=True)
            st.caption(f"{sp['reason_category']} · recorded by {sp['recorded_by']}"
                       + (f" · confirmed by {sp['confirmed_by']}" if sp["confirmed_by"] else "")
                       + (f" · executed {sp['executed_at'].replace('T', ' ')}" if sp["executed_at"] else "")
                       + (f" · rescinded: {sp['rescind_reason']}" if sp["rescind_reason"] else "")
                       + (f" · corrected by #{sp['corrected_by_id']}" if sp["corrected_by_id"] else ""))
            if sp["status"] in ("recorded", "confirmed", "executed"):
                c1, c2, c3 = st.columns(3)
                if sp["status"] == "recorded" and c1.button("Confirm", key=f"sep_c_{sp['id']}", width="stretch"):
                    try:
                        with ui.mutate(w):
                            services.confirm_separation(w, u, sp["id"])
                        ui.flash("Confirmed · takes effect on the date")
                    except (ValueError, services.Denied) as e:
                        st.error(str(e))
                why = c2.text_input("Reason (rescind / correct)", key=f"sep_why_{sp['id']}",
                                    label_visibility="collapsed", placeholder="Reason (rescind / correct)")
                if c3.button("Rescind", key=f"sep_r_{sp['id']}", width="stretch"):
                    try:
                        with ui.mutate(w):
                            services.rescind_separation(w, u, sp["id"], why)
                        ui.flash("Rescinded" + (" · account reactivated" if sp["status"] == "executed" else ""))
                    except (ValueError, services.Denied) as e:
                        st.error(str(e))
                if sp["status"] != "executed":
                    nd = st.date_input("Corrected effective date", key=f"sep_nd_{sp['id']}")
                    if st.button("Save correction (needs confirming again)", key=f"sep_x_{sp['id']}"):
                        try:
                            services.correct_separation(w, u, sp["id"], nd.isoformat(), sp["reason_category"], why)
                            ui.flash("Corrected · confirm the new record")
                        except (ValueError, services.Denied) as e:
                            st.error(str(e))
    if st.button("Run due separations now", key="sep_run",
                 help="Also runs automatically when live time moves. Running it twice changes nothing."):
        done = services.run_separations(w, u)
        ui.flash(f"{len(done)} separation(s) executed" if done else "Nothing due")

if SHOW("Absence and overtime"):
    kit.label("Absence-pattern flags (confidential, non-disciplinary)")
    on, why = hr.absence_active()
    st.caption(f"{why} Only an explicit no-show counts; a missing check-in is never an absence; approved leave and "
               "duplicate days are excluded; corrections re-count. Team leads never see these flags.")
    with st.expander("Validation preview (dry run: creates nothing)"):
        st.caption("Counts every qualifying unplanned absence in this workspace with its source, so the rules can be "
                   "checked against the attendance model before Senior HR switches flags on.")
        val = services.absence_validation(w, u)
        if val:
            st.dataframe(pd.DataFrame([{"Agent": ui.name(v["employee_id"]),
                                        "Events": "; ".join(f"{ui.dlabel(d)}: {', '.join(src)}"
                                                            for d, src in v["events"].items()),
                                        "Count": len(v["events"]), "Would flag": "Yes" if v["would_flag"] else "No"}
                                       for v in val]), hide_index=True, width="stretch")
        else:
            st.caption("No qualifying unplanned absences recorded in this workspace.")
    if st.button("Check now", key="abs_chk", disabled=not on):
        r = services.check_absence_flags(w, u)
        ui.flash(f"{r['created']} new flag(s), {r['updated']} re-counted")
    for f in services.absence_flags(w, u):
        with st.container(border=True):
            st.markdown(f"**Flag #{f['id']}** · {ui.name(f['employee_id'])} · {f['absences']} absence(s) "
                        f"(threshold {f['threshold']}) · {ui.dlabel(int(f['window_start']))} – "
                        f"{ui.dlabel(int(f['window_end']))} · **{f['status'].replace('_', ' ')}**")
            st.caption(f["note"] or "")
            moves = sorted(hr.FLAG_MOVES.get(f["status"], ()))
            if moves:
                c1, c2, c3 = st.columns([2, 3, 1])
                to = c1.selectbox("Move to", moves, key=f"fl_to_{f['id']}", format_func=lambda x: x.replace("_", " "))
                nt = c2.text_input("Note (required)", key=f"fl_nt_{f['id']}")
                if c3.button("Save", key=f"fl_go_{f['id']}", width="stretch"):
                    try:
                        services.move_absence_flag(w, u, f["id"], to, nt)
                        ui.flash("Updated")
                    except (ValueError, services.Denied) as e:
                        st.error(str(e))
    kit.label("Overtime approval")
    ov = services.overtime_view(w, u)
    st.caption(f"{ov['note']} Worked = extension hours on shifts with a presence record; a scheduled extension "
               "without a record is not proof that overtime was worked. No pay is calculated here.")
    hrs = [{"Agent": ui.name(a), "Month": o["period"], "Worked overtime (h)": o["worked"],
            "Scheduled, not confirmed (h)": o["scheduled_unconfirmed"]} for a, o in ov["hours"].items()
           if o["worked"] or o["scheduled_unconfirmed"]]
    if hrs:
        st.dataframe(pd.DataFrame(hrs), hide_index=True, width="stretch")
    else:
        st.caption("No extension hours this month so far.")
    if ov["active"] and st.button("Check overtime now", key="ot_chk"):
        r = services.check_overtime(w, u)
        ui.flash(f"{r['created']} new task(s)")
    for t in [t for t in ov["tasks"] if t["status"] == "pending"]:
        c1, c2, c3, c4 = st.columns([3, 3, 1, 1])
        c1.caption(f"{ui.name(t['employee_id'])} · {t['period']} · worked {t['worked_hours']:g} h > {t['threshold']:g} h")
        rs = c2.text_input("Reason", key=f"ot_r_{t['id']}", label_visibility="collapsed", placeholder="Reason")
        for col, ok, lbl in ((c3, True, "Approve"), (c4, False, "Reject")):
            if col.button(lbl, key=f"ot_{lbl}_{t['id']}"):
                try:
                    services.decide_overtime(w, u, t["id"], ok, rs)
                    ui.flash("Recorded")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))

if SHOW("Restricted cases"):
    st.caption("Visible only to the assigned caseworker and Senior HR — checked on the server for every request, "
               "and every access (allowed or refused) is logged. Notifications say only that a case was assigned. "
               "Evidence is recorded as a reference to where it is kept, not uploaded here.")
    hr_users = [a["username"] for a in accounts.all_accounts() if a["role"] == "hr" and a["active"]]
    agents = w.data["agents"].set_index("agent_id")["name"].to_dict()
    with st.expander("Open a case"):
        c1, c2, c3 = st.columns(3)
        cat = c1.selectbox("Category", hr.CASE_CATEGORIES, key="cs_cat")
        conf = c2.selectbox("Confidentiality", ["restricted", "highly restricted"], key="cs_conf")
        cw = c3.selectbox("Caseworker", hr_users, index=hr_users.index(u["username"]) if u["username"] in hr_users
                          else 0, key="cs_cw")
        c4, c5 = st.columns(2)
        subj = c4.selectbox("Employee concerned", sorted(agents), format_func=lambda a: f"{agents[a]} ({a})",
                            key="cs_subj")
        dl = c5.date_input("Deadline", value=None, key="cs_dl")
        summ = st.text_area("Summary", key="cs_sum")
        if st.button("Open case", key="cs_go"):
            try:
                c = services.open_hr_case(w, u, cat, conf, subj, summ, cw, dl.isoformat() if dl else "")
                ui.flash(f"Case #{c['id']} opened")
            except (ValueError, services.Denied) as e:
                st.error(str(e))
    mine = services.hr_cases(w, u)
    if not mine:
        st.caption("No cases you can see.")
    pick = st.selectbox("Case", [c["id"] for c in mine], format_func=lambda i: next(
        f"#{c['id']} · {c['category']} · {c['status']} · {c['caseworker']}" for c in mine if c["id"] == i),
        key="cs_pick") if mine else None
    if pick:
        try:
            c = services.view_hr_case(w, u, pick)
        except services.Denied as e:
            st.error(str(e))
            c = None
        if c:
            st.markdown(f"**Case #{c['id']}** · {c['category']} · {c['confidentiality']} · **{c['status']}** · "
                        f"caseworker {c['caseworker']} · employee {c['subject_employee']}"
                        + (f" · deadline {c['deadline']}" if c["deadline"] else ""))
            st.caption(c["summary"] or "")
            if c["events"]:
                st.dataframe(pd.DataFrame([{"When": e["at"].replace("T", " "), "Type": e["kind"], "Entry": e["body"],
                                            "Due": e["due"] or "", "By": e["by_name"]} for e in c["events"]]),
                             hide_index=True, width="stretch")
            if c["status"] != "closed":
                c1, c2, c3 = st.columns([1, 3, 1])
                kind = c1.selectbox("Add", ["comment", "evidence", "followup"], key=f"ce_k_{pick}",
                                    format_func={"comment": "Comment", "evidence": "Evidence reference",
                                                 "followup": "Follow-up"}.get)
                body = c2.text_input("Text", key=f"ce_b_{pick}")
                due = c3.date_input("Due", value=None, key=f"ce_d_{pick}") if kind == "followup" else None
                if st.button("Add entry", key=f"ce_go_{pick}"):
                    try:
                        services.add_case_entry(w, u, pick, kind, body, due.isoformat() if due else "")
                        ui.flash("Added")
                    except (ValueError, services.Denied) as e:
                        st.error(str(e))
                c1, c2, c3 = st.columns([1, 3, 1])
                ns = c1.selectbox("Status", hr.CASE_STATUSES, index=hr.CASE_STATUSES.index(c["status"]),
                                  key=f"cst_{pick}")
                out = c2.text_input("Outcome (required to close)", key=f"cso_{pick}")
                if c3.button("Update status", key=f"cs_up_{pick}"):
                    try:
                        services.set_hr_case_status(w, u, pick, ns, out)
                        ui.flash("Updated")
                    except (ValueError, services.Denied) as e:
                        st.error(str(e))
                if SENIOR:
                    c1, c2, c3 = st.columns([2, 3, 1])
                    nw = c1.selectbox("Reassign to", hr_users, key=f"cs_rw_{pick}")
                    rr = c2.text_input("Reason", key=f"cs_rr_{pick}")
                    if c3.button("Reassign", key=f"cs_ra_{pick}"):
                        try:
                            services.reassign_hr_case(w, u, pick, nw, rr)
                            ui.flash("Reassigned")
                        except (ValueError, services.Denied) as e:
                            st.error(str(e))
            if c["access_log"]:
                with st.expander("Access log (Senior HR)"):
                    st.dataframe(pd.DataFrame([{"When": x["at"].replace("T", " "), "User": x["username"],
                                                "Action": x["action"], "Allowed": bool(x["allowed"])}
                                               for x in c["access_log"]]), hide_index=True, width="stretch")

if SHOW("HR policy"):
    st.caption("Every change is a new version (who, when, reference). Items marked pending are not confirmed HR "
               "policy: features that depend on them stay off. Only Senior HR changes these.")
    rows = services.hr_policy(u)
    st.dataframe(pd.DataFrame([{"Setting": r["key"], "Value": "Not set" if r["value"] is None else str(r["value"]),
                                "Status": "Pending confirmation" if r["status"] == "pending" else "Approved",
                                "Meaning": r["meaning"], "Reference": r["reference"], "Changed by": r["changed_by"],
                                "Versions": r["versions"]} for r in rows]), hide_index=True, width="stretch")
    kit.label("Pending decisions register")
    for r in hr.pending_items():
        st.caption(f"• **{r['key']}** — {r['meaning']} (current: {'not set' if r['value'] is None else r['value']})")
    kit.label("Retention (deferred, HR-08)")
    st.caption("No retention periods are set and nothing is deleted automatically. The retention configuration is "
               "kept separate and will be filled in after the retention policy is approved.")
    st.dataframe(pd.DataFrame(hr.RETENTION_REGISTER, columns=["Record type", "Retention"]), hide_index=True,
                 width="stretch")
    if SENIOR:
        with st.expander("Change a setting (Senior HR)"):
            keys = [k for k in hr.POLICY if k != "retention.policy"]
            k = st.selectbox("Setting", keys, key="pol_k")
            cur = next(r for r in rows if r["key"] == k)
            raw = st.text_input("New value (true/false, number, or text; blank = not set)",
                                value="" if cur["value"] is None else str(cur["value"]).lower()
                                if isinstance(cur["value"], bool) else str(cur["value"]), key=f"pol_v_{k}")
            stt = st.selectbox("Status", ["approved", "pending"], key="pol_s")
            ref = st.text_input("Policy reference (required to approve)", key="pol_r")
            if st.button("Save new version", key="pol_go"):
                val = None if raw.strip() == "" else True if raw.lower() == "true" else False \
                    if raw.lower() == "false" else int(raw) if raw.strip().lstrip("-").isdigit() else raw.strip()
                try:
                    services.set_hr_policy(w, u, k, val, stt, ref)
                    ui.flash("Saved")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))

if SHOW("People downloads"):
    st.caption("Today's data as Excel files with friendly headers. Edit and upload back in Update people data: only "
               "changed rows are applied. Every file starts with a Read me sheet.")
    c1, c2 = st.columns(2)
    c1.download_button("HR records (Excel)", services.hr_records_workbook(w, u), "shiftsense_hr_records.xlsx",
                       width="stretch", key="hr_rec_dl", on_click=services.audit_hr_download, args=(w, u),
                       help="Balances, transactions, separations, review tasks and attendance corrections. "
                            "Restricted cases and absence flags are not included.")
    c2.download_button("Leave-balance import template (CSV)", services.BALANCE_TEMPLATE, "leave_balances_template.csv",
                       "text/csv", width="stretch", key="hr_bal_tpl")
    c = st.columns(3)
    for i, dom in enumerate(PEOPLE):
        recs = list(refdata.current(dom).values())
        c[i].download_button(f"{DOMAINS[dom].label} ({len(recs)})", exports.reference_template(dom, recs),
                             f"shiftsense_{dom}.xlsx", width="stretch", key=f"hr_tpl_{dom}")

ui.shared_cards()
