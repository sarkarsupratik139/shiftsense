import io
from datetime import datetime

import pandas as pd
import streamlit as st

import ui_common as ui
import ui_kit as kit
from shiftsense import accounts, auth, comms, governance, history, records, report, services, store, workflow
from shiftsense.config import day_label, queue_names
from shiftsense.constraints import check

ui.page("admin")
w = ui.world()
p = w.policy
a = ui.agents()
DAYS = p["horizon"]["days"]
WEEKS = [(0, f"Week 1 ({ui.dlabel(0)[4:]}–{ui.dlabel(6)[4:]})"), (1, f"Week 2 ({ui.dlabel(7)[4:]}–{ui.dlabel(DAYS - 1)[4:]})")]
CELL = {"worked": ("Worked", "ok-bg"), "overtime": ("Worked +OT", "info-bg"), "extra": ("Extra shift", "info-bg"),
        "swap": ("Swap", "accent-soft"), "left_early": ("Left early", "warn-bg"), "no_show": ("No-show", "crit-bg"),
        "sick": ("Absent", "crit-bg"), "leave": ("Leave", "neutral-bg"), "scheduled": ("Scheduled", "paper"),
        "unrecorded": ("No attendance record", "code-bg"), "off": ("Off", "bg")}   # background = design token
SHORT = {"Morning": "M", "Day": "D", "Evening": "E", "Night": "N"}
APPROVAL = {"Accept": ("Approved", "Normal"), "Publish": ("Published", "Normal"), "Auto-approve": ("Auto-approved", "Normal"),
            "Reject": ("Rejected", "Critical"), "Modify": ("Modified", "Warning"), "Pending": ("Pending", "Warning")}

cases_open = [c for c in comms.cases(w.run_id) if c["status"] not in ("Closed", "Dismissed")]
area = ui.area("admin")
ui.shell(area, f"{workflow.now_label(w)} · oversight and governance · read-only operations",
         kit.pill(f"{len(cases_open)} open cases", "Warning" if cases_open else "Normal"), open_kinds=("incident",))

_gd = None
if area == "Governance & decisions":
    _gd = st.segmented_control("View", ["Governance cases", "Decision log"], default="Governance cases",
                               key="ad_gd", label_visibility="collapsed") or "Governance cases"

# ================================================================== Week overview
if area == "Overview":
    c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
    week = c1.selectbox("Week", WEEKS, index=min(1, w.today // 7), format_func=lambda x: x[1], key="wk_week")[0]
    team = c2.selectbox("Team", ["All"] + sorted(a.team.unique()), key="wk_team")
    queue = c3.selectbox("Queue", ["All"] + queue_names(p), key="wk_queue")
    agent = c4.selectbox("Agent", ["All"] + list(a.index), format_func=lambda i: i if i == "All" else ui.name(i),
                         key="wk_agent")
    days = records.week_days(w, week)
    ids = [i for i in a.index if (team == "All" or a.loc[i, "team"] == team)
           and (queue == "All" or queue in a.loc[i, "eligible"]) and (agent == "All" or i == agent)]
    rep = report.weekly_report(w, week)
    s = rep["summary"]
    exc = [i for i in w.incidents if i.day in days and i.status in ("open", "needs_new_fix", "escalated")]
    past = [d for d in days if d <= w.today]
    kit.label(f"Simulated so far · demo scenario · up to {workflow.now_label(w)} (not actual results)" if w.scenario
              else f"Recorded so far · up to {workflow.now_label(w)}")
    if not past:
        st.caption("This week has not started yet: there are no recorded events.")
    kit.kpis([
        {"label": "Shifts with attendance recorded", "value": s["Shifts worked (recorded)"]},
        {"label": "Started, no attendance record", "value": s["Started shifts without attendance record"],
         "status": "Warning" if s["Started shifts without attendance record"] else None,
         "help": "shift started but no clock-in or check-in exists; not counted as worked"},
        {"label": "Attendance (recorded shifts only)", "value": s["Attendance %"] if isinstance(s["Attendance %"], str)
         else f"{s['Attendance %']}%", "sub": "no attendance records yet" if isinstance(s["Attendance %"], str)
         else f"from {s['Shifts worked (recorded)']} recorded · {s['Started shifts without attendance record']} without a "
              "record (excluded)"},
        {"label": "No-shows", "value": s["No-shows"], "status": "Warning" if s["No-shows"] else None},
        {"label": "Absences reported", "value": s["Absences"]},
        {"label": "Open exceptions", "value": len(exc), "status": "Critical" if exc else "Normal"},
        {"label": "Schedule changes", "value": s["Schedule changes"]},
    ])
    future = [d for d in days if d > w.today] + ([w.today] if w.today in days else [])
    with st.container(border=True):
        st.markdown(kit.pill("FORECAST / PREDICTION — NOT ACTUAL RESULTS", "Warning"), unsafe_allow_html=True)
        st.markdown(f"**Expectation this week** · {ui.dlabel(days[0])} – {ui.dlabel(days[-1])}"
                    + (f" · remaining from {workflow.now_label(w)}" if past else ""))
        kit.kpis([
            {"label": "Planned coverage (forecast demand)", "value": f"{s['Coverage %']}%"},
            {"label": "Leave days approved", "value": s["Leave days"]},
            {"label": "Scheduled shifts still to come", "value": int(rep["attendance"]["Scheduled (future)"].sum())},
            {"label": "Approved overtime", "value": f"{s['Approved overtime hours']:g} h",
             "sub": kit.money(s["Approved overtime cost ₹ (configured rates)"]) + " at configured rates"},
        ])
        st.caption("Source: the live roster against the seasonal forecast (same weekday, last 4 weeks, ~80% band) "
                   "converted to required heads with Erlang C and 30% shrinkage. Simulated data. These figures are "
                   "never added to the recorded totals above.")

    kit.label(f"Schedule · {len(ids)} agents · click a row for the agent card")
    grid, codes = [], []
    for i in ids:
        row, crow = {"Agent": ui.name(i), "Team": a.loc[i, "team"]}, {}
        for d in days:
            rec = records.day_record(w, i, d)
            r = ui.shift_row(i, d)
            txt = CELL[rec["code"]][0]
            if r is not None and rec["code"] in ("worked", "overtime", "extra", "swap", "left_early", "scheduled",
                                                 "unrecorded"):
                lead = {"scheduled": "Scheduled ", "unrecorded": "No record · "}.get(rec["code"], "")
                txt = lead + f"{SHORT[r['shift']]} · {r['alt_queue'] or r['queue']}" + (
                    "" if rec["code"] in ("worked", "scheduled", "unrecorded") else f" · {CELL[rec['code']][0]}")
            row[day_label(p, d)] = txt
            crow[day_label(p, d)] = rec["code"]
        grid.append(row)
        codes.append(crow)
    gdf = pd.DataFrame(grid)
    cdf = pd.DataFrame(codes)

    def paint(col):
        if col.name not in cdf.columns:
            return [""] * len(col)
        t = kit.T()
        return [f"background-color:{t[CELL[c][1]]};color:{t['ink']}" for c in cdf[col.name]]

    def _open_agent():
        sel = st.session_state["wk_grid"].selection.rows
        if sel:
            ui.open_card("agent", ids[sel[0]])

    _wv = st.segmented_control("View", ["Calendar", "Table"], default="Calendar", key="wk_view",
                               label_visibility="collapsed") or "Calendar"
    if _wv == "Calendar":
        ui.roster_calendar(list(ids), list(days), "Weekly schedule", with_record=True)
        st.selectbox("Agent card", [None] + list(ids), format_func=lambda i: "Open an agent…" if i is None else ui.name(i),
                     key="wk_agent_pick", on_change=lambda: st.session_state.wk_agent_pick is not None
                     and ui.open_card("agent", st.session_state.wk_agent_pick))
    elif len(gdf):
        st.dataframe(gdf.style.apply(paint, axis=0), hide_index=True, width="stretch", on_select=_open_agent,
                     selection_mode="single-row", key="wk_grid", height=min(38 * (len(ids) + 1) + 4, 520))
    st.caption("M 06–15 · D 09–18 · E 14–23 · N 22–07 · 'Scheduled' = planned, not yet happened · 'No record' = the "
               "shift started but no attendance was recorded · Worked / No-show / Absent / Left early = recorded events. "
               "Simulated data.")

    c1, c2 = st.columns([1, 2])
    c1.selectbox("Day detail", [None] + days, format_func=lambda d: "Open a day…" if d is None else ui.dlabel(d),
                 key="wk_day", on_change=lambda: st.session_state.wk_day is not None
                 and ui.open_card("day", st.session_state.wk_day))
    with c2:
        kit.label("Exceptions this week")
        if exc:
            edf = pd.DataFrame([{"Alert": i.id, "Date": ui.dlabel(i.day), "Issue": ui.incident_title(i),
                                 "Team": i.owner_team, "State": ui.incident_state(i)[0]} for i in exc])

            def _open_exc():
                sel = st.session_state["wk_exc"].selection.rows
                if sel:
                    ui.open_card("incident", edf.iloc[sel[0]]["Alert"])

            st.dataframe(edf, hide_index=True, width="stretch", on_select=_open_exc, selection_mode="single-row",
                         key="wk_exc")
        else:
            st.caption("None open.")
    c1, c2 = st.columns([1, 1])
    c1.download_button("Weekly report (Excel)", report.to_excel(rep), f"weekly_report_week{week + 1}.xlsx",
                       icon=":material/download:", width="stretch")
    with st.container(border=True):
        kit.label("Week in brief")
        st.markdown(ui.assistant.week_summary(rep))
        st.caption("From this week's recorded figures (no AI) · simulated data")


# ================================================================== Decisions (oversight log)
if area == "Governance & decisions" and _gd == "Decision log":
    rows = comms.audit_rows(w.run_id)
    inc_team = {i.id: i.owner_team for i in w.incidents}
    req_team = {r["id"]: a.loc[r["agent_id"], "team"] for r in w.requests if r["agent_id"] in a.index}
    recs = []
    for r in rows:
        team = inc_team.get(r["record"]) or req_team.get(r["record"]) or ("—" if r["role"] != "team_lead" else "")
        recs.append({"#": r["id"], "When": r["sim_time"], "Role": r["role"], "By": r["actor"].split(" (")[0],
                     "Action": r["action"], "Record": r["record"], "Team": team,
                     "Change": f"{r['before'] or '—'} → {r['after'] or '—'}", "Reason": r["reason"]})
    ddf = pd.DataFrame(recs)
    if ddf.empty:
        st.caption("No decisions recorded yet.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        f_team = c1.selectbox("Team", ["All"] + sorted(t for t in ddf.Team.unique() if t), key="dc_team")
        f_role = c2.selectbox("Role", ["All"] + sorted(ddf.Role.unique()), key="dc_role")
        f_by = c3.selectbox("Decided by", ["All"] + sorted(ddf.By.unique()), key="dc_by")
        f_act = c4.selectbox("Decision type", ["All"] + sorted(ddf.Action.unique()), key="dc_act")
        m = pd.Series(True, index=ddf.index)
        for col, val in (("Team", f_team), ("Role", f_role), ("By", f_by), ("Action", f_act)):
            if val != "All":
                m &= ddf[col] == val
        view = ddf[m]
        kit.kpis([{"label": "Decisions", "value": len(view)},
                  {"label": "Team-lead", "value": int((view.Role == "team_lead").sum())},
                  {"label": "Operations", "value": int((view.Role == "ops_head").sum())},
                  {"label": "Planner", "value": int((view.Role == "analyst").sum())},
                  {"label": "Rejections / escalations", "value": int(view.Action.str.contains("Reject|Escalate").sum())}])
        st.dataframe(view, hide_index=True, width="stretch", height=min(40 + 35 * len(view), 520))
        st.caption("Read-only. Every row is in the tamper-evident audit log.")

# ================================================================== Governance (cases)
if area == "Governance & decisions" and _gd == "Governance cases":
    flags = governance.detect(w)
    c1, c2 = st.columns([1, 3])
    if c1.button("Scan for discrepancies", type="primary", icon=":material/policy:"):
        try:
            new = services.scan(w, ui.user())
            ui.flash(f"{len(new)} new case(s) flagged" if new else "No new discrepancies")
        except services.Denied as e:
            st.error(str(e))
    c2.caption(f"{len(flags)} pattern(s) currently above threshold · a flag is a signal for review, not proof. "
               "Thresholds: config/policy.yaml → governance.")
    cs = comms.cases(w.run_id)
    if cs:
        f_status = st.multiselect("Status", list(ui.CASE_STATUS), default=[k for k in ui.CASE_STATUS
                                                                           if k not in ("Closed", "Dismissed")],
                                  key="gv_status")
        cdf = pd.DataFrame([{"Case": c["id"], "Status": c["status"], "Type": c["kind"], "Issue": c["title"],
                             "Team": c["team"] or "—", "Reviewer": (c["reviewer"] or "—").split(" (")[0],
                             "Raised": c["sim_time"]} for c in cs if c["status"] in f_status])

        def _open_case():
            sel = st.session_state["gv_tbl"].selection.rows
            if sel:
                ui.open_card("case", int(cdf.iloc[sel[0]]["Case"]))

        if len(cdf):
            st.dataframe(cdf, hide_index=True, width="stretch", on_select=_open_case, selection_mode="single-row",
                         key="gv_tbl")
        st.caption("Lifecycle: Flagged → Under Review → Explanation Requested → Referred to Operations → "
                   "Action Required → Resolved → Closed. Formal corrective action needs Operations authorisation.")
    else:
        last = next((r for r in comms.audit_rows(w.run_id) if r["action"] == "Governance scan"), None)
        st.caption(f"No cases. Last scan: {last['sim_time'] or last['created'][:16]} by {last['actor']} · nothing above "
                   "threshold." if last else "No cases yet. Run a scan.")

# ================================================================== People & accounts
if area == "Accounts & access":
    cfg = auth.config()
    st.caption("Accounts and contact details only. Rosters, skills and attendance are operational data and change "
               "through operational roles; role permissions are set by the platform owner (read-only below).")
    accts = accounts.all_accounts()
    ROLE_LABEL = {k: v["label"] for k, v in cfg["roles"].items()}
    q = st.text_input("Search accounts", key="acct_q", placeholder="Name, username, employee ID, team or agent ID")
    shown = [x for x in accts if not q or q.lower() in " ".join(str(x[k]) for k in ("name", "username", "employee_id",
                                                                                     "team", "agent_id")).lower()]
    _aph = ui.photos([x["agent_id"] for x in shown if x["agent_id"]])
    adf = pd.DataFrame([{"Photo": kit.avatar_uri(x["name"], _aph.get(x["agent_id"])),
                         "Username": x["username"], "Name": x["name"], "Employee ID": x["employee_id"],
                         "Role": ROLE_LABEL.get(x["role"], x["role"]) + (" · Senior HR" if "senior_hr" in x.get("extra_permissions", [])
                                                                      else ""), "Team": x["team"] or "—",
                         "Agent": x["agent_id"] or "—", "Email": accounts.mask_email(x["email"]) + (" · verified" if x["email_verified"] else (" · unverified" if x["email"] else "")),
                         "Contact": accounts.CONTACT.get(x["contact_pref"], "—"),
                         "Status": "Active" if x["active"] else "Inactive"} for x in shown])

    def _open_acct():
        sel = st.session_state["acct_tbl"].selection.rows
        if sel:
            st.session_state.pop("acct_errs", None)
            ui.open_card("account", adf.iloc[sel[0]]["Username"])

    c1, c2 = st.columns([3, 1])
    c1.caption(f"{len(shown)} of {len(accts)} accounts · select a row to edit")
    if ui.can("manage_accounts") and c2.button("Add account", icon=":material/person_add:", width="stretch"):
        st.session_state.pop("acct_errs", None)
        ui.open_card("account", "")
    if len(adf):
        st.dataframe(adf, hide_index=True, width="stretch", on_select=_open_acct, selection_mode="single-row",
                     key="acct_tbl", height=min(40 + 35 * len(adf), 420),
                     column_config={"Photo": st.column_config.ImageColumn("", width="small")})
    else:
        st.caption("No account matches the search.")
    kit.label("Role permissions · read-only (platform owner)")
    perms = sorted({x for r in cfg["roles"].values() for x in r["permissions"]})
    st.dataframe(pd.DataFrame([{"Permission": x.replace("_", " "),
                                **{r["label"]: "Yes" if x in r["permissions"] else "" for r in cfg["roles"].values()}}
                               for x in perms]), hide_index=True, width="stretch")

# ================================================================== Audit & integrity
if area == "Audit & integrity":
    ok_chain, bad = comms.verify_audit()
    kit.label("Audit trail")
    st.markdown(kit.pill("Audit chain verified · append-only" if ok_chain else f"Audit chain broken at row {bad}",
                         "Normal" if ok_chain else "Critical"), unsafe_allow_html=True)
    ui.history_view("ad_hist", "Everything recorded in this workspace: decisions, account and permission changes, "
                               "governance cases, schedule changes, attendance, planning and demo actions. Other "
                               "people's private messages are not shown.")
    kit.label("Reference data changes")
    from shiftsense import exports as _ex
    from shiftsense.schemas import DOMAINS as _D
    dch = services.data_changes(w, ui.user())
    if dch:
        st.dataframe(pd.DataFrame([{"Change": r["id"], "Data": _D[r["domain"]].label, "Status": r["status"].title(),
                                    "By": r["submitted_by"], "Decided by": r["decided_by"] or "—",
                                    "Effective": r["effective_from"], "Converted by": r["converted_by"],
                                    "Reason": (r["reason"] or "—")[:60]} for r in dch]),
                     hide_index=True, width="stretch", height=min(40 + 35 * len(dch), 300))
        st.download_button("Data change log (Excel)", _ex.change_log(dch), "shiftsense_data_changes.xlsx")
    else:
        st.caption("No reference data changes yet.")
    with st.expander("Propose new governance thresholds (Ops approves)"):
        from shiftsense import refdata as _rd
        from shiftsense.schemas import RULE_LABELS
        cur = _rd.current("rules")
        g = [k for k in RULE_LABELS if k.startswith("governance.")]
        rk = st.selectbox("Threshold", g, format_func=lambda k: RULE_LABELS[k][0], key="gv_rule")
        val = st.number_input(f"New value ({RULE_LABELS[rk][1]})", value=float(cur.get(rk, {}).get("value", 0) or 0),
                              key="gv_val")
        why = st.text_input("Reason (required)", key="gv_why")
        if st.button("Send to Ops", key="gv_go"):
            try:
                out = services.submit_change(w, ui.user(), "rules", [{"rule": rk, "value": val}], reason=why,
                                             source="admin form", converted_by="form")
                ui.flash(f"Proposal #{out['id']} sent to Ops")
            except (ValueError, services.Denied) as e:
                st.error(str(e))
    kit.label("Schedule change history")
    st.caption("A logbook of every change to the roster: who changed it, when (simulated time), which agents and "
               "shifts, before → after, and why. A reversal (e.g. an approval revoked, an agent declining) is a new "
               "entry; the original stays. Select a row for the full before/after. This is separate from the decision "
               "log (Decisions) and the audit trail above.")
    c1, c2, c3 = st.columns([1, 2, 1])
    scope = c1.segmented_control("Scope", ["This run", "All runs"], default="This run", key="ch_scope") or "This run"
    rows = history.changes(None if scope == "All runs" else w.run_id)
    pick = c2.multiselect("Event", sorted({r["event"] for r in rows}), key="ch_ev")
    rows = [r for r in rows if not pick or r["event"] in pick]
    c3.metric("Changes", len(rows))
    tdf = pd.DataFrame([{"#": r["id"], "Context": history.context_of(r["run_id"]), "When": r["sim_time"],
                         "Event": r["event"], "By": r["actor"].split(" (")[0],
                         "Role": r.get("role") or "—", "Status": r.get("status") or "Applied",
                         "Related": r.get("related") or "—", "Shifts": r["n_changes"],
                         "Agents": len({d["agent_id"] for d in r["details"]}), "Run": r["run_id"],
                         "Reason": (r["reason"] or "—")[:60]} for r in rows])

    def _open_change():
        sel = st.session_state["ch_tbl"].selection.rows
        if sel:
            ui.open_card("change", int(tdf.iloc[sel[0]]["#"]))

    if len(tdf):
        st.dataframe(tdf, hide_index=True, width="stretch", on_select=_open_change, selection_mode="single-row",
                     key="ch_tbl", height=min(40 + 35 * len(tdf), 520))
        st.download_button("Download history (CSV)", history.export_rows(rows).to_csv(index=False),
                           "schedule_history.csv", "text/csv", icon=":material/download:")
        st.caption("The CSV is built from exactly the rows listed above (same scope and filter).")
    else:
        st.caption("No changes recorded.")

    cfg = auth.config()
    kit.label("Integrity")
    saved = store.list_changes()[0]["saved_at"] if store.list_changes() else None
    age_h = (datetime.now() - datetime.fromisoformat(saved)).total_seconds() / 3600 if saved else None
    viol = check(p, w.data["agents"], w.data["leave"], w.roster)
    accts = accounts.all_accounts()
    missing_logins = [x["username"] for x in accts if x["agent_id"] and x["agent_id"] not in a.index]
    missing_teams = [x["username"] for x in accts if x["role"] == "team_lead" and x["team"] not in set(a.team)]
    drafts = [x for x in w.plans if x["status"] in ("draft", "validated")]
    checks = [
        ("Live roster rules", not viol, "No violations" if not viol else f"{len(viol)} violation(s)"),
        ("Last change saved", age_h is not None and age_h < 24,
         "never" if age_h is None else f"{age_h:.1f} h ago" + (" · stale" if age_h >= 24 else "")),
        ("Agent logins match data", not missing_logins, "OK" if not missing_logins else ", ".join(missing_logins)),
        ("Team-lead teams exist", not missing_teams, "OK" if not missing_teams else ", ".join(missing_teams)),
        ("Open plan drafts", not drafts, f"{len(drafts)}"),
        ("AI assistant", ui.ai_client() is not None, "On" if ui.ai_client() else "Off (rule-based)"),
    ]
    st.markdown(" ".join(kit.pill(f"{n}: {t}", "Normal" if ok else "Warning") for n, ok, t in checks),
                unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    with c1:
        rules = p["rules"]
        kit.label("Rules (hard) · read-only")
        st.dataframe(pd.DataFrame([
            ("Minimum rest", f"{rules['min_rest_hours']} h"), ("Max hours / week", f"{rules['max_weekly_hours']} h"),
            ("Contracted shifts / week", str(rules["contracted_shifts_per_week"])),
            ("Max shifts / week", str(rules["max_shifts_per_week"])), ("Max nights / week", str(rules["max_nights_per_week"])),
            ("Max OT days / 2 weeks", str(rules["max_ot_days_per_horizon"])),
            ("Max extension", f"{rules['max_extension_hours']} h"), ("Max hours on duty / day", f"{rules['max_daily_hours']} h"),
        ], columns=["Rule", "Value"]), hide_index=True, width="stretch")
    with c2:
        kit.label("Costs and assumptions (simulated) · read-only")
        c = p["costs"]
        st.dataframe(pd.DataFrame([
            ("Hourly rate", kit.money(c["hourly_rate"])), ("Overtime multiplier", f"{c['ot_multiplier']}×"),
            ("Redeployment cost", kit.money(c["redeploy_cost"])), ("Swap allowance", kit.money(c["swap_cost"])),
            ("Shortfall penalty (ranking only, not a cost)", kit.money(c["shortfall_penalty"])),
            ("Weekly budget", kit.money(c["weekly_budget"]) if c.get("weekly_budget") else "Not configured"),
            ("Shrinkage", f"{p['staffing']['shrinkage']:.0%}"), ("No-show rate (simulation)", f"{p['recovery']['no_show_rate']:.0%}"),
        ], columns=["Item", "Value"]), hide_index=True, width="stretch")
        st.caption("Configuration changes go through the platform owner, not Admin.")


# ================================================================== System status (read-only)
if area == "System status":
    from shiftsense import health as _health
    _rows = _health.run()
    _failed = [r for r in _rows if not r[1] and r[3]]
    kit.kpis([{"label": "Health", "value": "Healthy" if not _failed else f"{len(_failed)} failing",
               "status": "Normal" if not _failed else "Critical"},
              {"label": "Checks", "value": len(_rows)},
              {"label": "Warnings", "value": sum(1 for r in _rows if not r[1] and not r[3]),
               "status": "Warning" if any(not r[1] and not r[3] for r in _rows) else None}])
    st.dataframe(pd.DataFrame([{"Check": n, "Result": "OK" if ok else ("Failing" if ess else "Warning"),
                                "Detail": d, "Essential": "Yes" if ess else "No"} for n, ok, d, ess in _rows]),
                 hide_index=True, width="stretch")
    st.caption("The same checks as `python -m shiftsense.health`. Configuration presence only; secrets are never "
               "shown.")
    kit.label("Email relay check")
    from shiftsense import mailer as _mailer
    st.caption(f"{_mailer.status_text()}. Sends one test message to the address you enter and records the relay's "
               "answer. 'Accepted by relay' is not proof of delivery; check the inbox.")
    c1, c2 = st.columns([3, 1])
    test_to = c1.text_input("Send a test email to", key="mail_test_to", label_visibility="collapsed",
                            placeholder="you@company.com")
    if c2.button("Send test email", key="mail_test_go", width="stretch", disabled=not _mailer.configured()):
        try:
            r = services.send_test_email(w, ui.user(), test_to)
            (st.success if r["status"] == "sent" else st.error)(f"{_mailer.LABEL.get(r['status'], r['status'])}: "
                                                                f"{r['detail']}")
        except (ValueError, services.Denied) as e:
            st.error(str(e))


@st.dialog("Account", width="large", on_dismiss=ui.close_card)
def account_card(username):
    cfg = auth.config()
    me = ui.user()["username"]
    rec = accounts.get(username) if username else None
    new = rec is None
    st.markdown(f"#### {'New account' if new else rec['name']}")
    if not new:
        st.caption(f"Version {rec['version']} · last changed {rec['updated_at'].replace('T', ' ')} by {rec['updated_by']}")
    protected = (rec or {}).get("role") in accounts.PROTECTED
    if protected:
        st.info("Admin accounts: only name and contact details can be edited here.")
    errs = st.session_state.get("acct_errs", {})
    roles = [rec["role"]] if protected else list(accounts.ASSIGNABLE)
    agents_df = w.data["agents"]
    with st.form(f"acct_form_{username or 'new'}"):
        c1, c2 = st.columns(2)
        uname = c1.text_input("Username", value=username, disabled=not new, help="Lowercase; used to sign in")
        name = c2.text_input("Full name", value=(rec or {}).get("name", ""))
        emp = c1.text_input("Employee ID", value=(rec or {}).get("employee_id", ""), placeholder="E-104")
        role = c2.selectbox("Role", roles, index=roles.index(rec["role"]) if rec and rec["role"] in roles else 0,
                            format_func=lambda r: cfg["roles"][r]["label"], disabled=protected or username == me)
        agent_opts = [""] + list(agents_df.agent_id)
        aid = c1.selectbox("Agent record (agents only)", agent_opts,
                           index=agent_opts.index(rec["agent_id"]) if rec and rec["agent_id"] in agent_opts else 0,
                           format_func=lambda x: "—" if not x else ui.name(x), disabled=protected,
                           help="Links the account to the agent in the workforce data; the team follows from it")
        teams = [""] + sorted(set(agents_df.team))
        team = c2.selectbox("Team (team leads)", teams, index=teams.index(rec["team"]) if rec and rec["team"] in teams else 0,
                            disabled=protected, format_func=lambda t: t or "—")
        if aid:
            st.caption(f"Skills (operational data, read-only): {', '.join(agents_df.set_index('agent_id').loc[aid, 'eligible'])}")
        email = c1.text_input("Work email (optional)", value=(rec or {}).get("email", ""), placeholder="name@company.com",
                              help="The person confirms it with a code before any email is sent to it")
        pref = c2.selectbox("Contact preference", list(accounts.CONTACT), format_func=accounts.CONTACT.get,
                            index=list(accounts.CONTACT).index((rec or {}).get("contact_pref", "in_app")))
        if rec and rec.get("email"):
            c1.caption("Verified" if rec.get("email_verified") else "Not verified yet: no email is sent until the "
                       "person confirms the code")
        active = c2.checkbox("Active", value=(rec or {}).get("active", True), disabled=username == me)
        pw = st.text_input("Temporary password (12+ characters, 3 of: lower, upper, digit, symbol)", type="password", help="The person must choose their own at first sign-in") if new else ""
        reason = "" if new else st.text_input("Reason (required to change role or deactivate)")
        go = st.form_submit_button("Create account" if new else "Save changes", type="primary")
    FIELD = {"username": "Username", "name": "Full name", "employee_id": "Employee ID", "role": "Role",
             "agent_id": "Agent record", "team": "Team", "email": "Email", "contact_pref": "Contact preference",
             "active": "Active", "password": "Initial password",
             "version": "Saving", "account": "Account"}
    for f, msg in errs.items():
        st.error(f"{FIELD.get(f, f)} — {msg}")
    if go:
        fields = {"name": name, "employee_id": emp, "role": role, "team": team, "agent_id": aid, "email": email,
                  "contact_pref": pref, "active": active}
        try:
            with ui.mutate():
                if new:
                    out = services.create_account(w, ui.user(), uname, fields, pw)
                else:
                    out = services.update_account(w, ui.user(), username, fields, rec["version"], reason)
            st.session_state.pop("acct_errs", None)
            ui.close_card()
            ui.flash(f"{'Created' if new else 'Saved'} account {out['username']}")
        except accounts.AccountError as e:
            st.session_state["acct_errs"] = dict(e.problems)
            st.rerun()
        except (ValueError, services.Denied) as e:
            st.session_state["acct_errs"] = {"account": str(e)}
            st.rerun()
    if rec and ui.can("manage_accounts") and username != me:
        full = accounts.get(username)
        locked = bool(full.get("locked_until")) and full["locked_until"] > datetime.now().isoformat()
        st.markdown("**Sign-in** · " + ("locked after wrong passwords" if locked else "not locked")
                    + (" · must choose a new password" if full.get("must_change") else ""))
        with st.expander("Reset password or unlock"):
            tmp = st.text_input("Temporary password", type="password", key=f"rp_tmp_{username}",
                                help="The person must choose their own at the next sign-in")
            rr = st.text_input("Reason (required)", key=f"rp_why_{username}")
            b1, b2 = st.columns(2)
            if b1.button("Set temporary password", key=f"rp_go_{username}"):
                try:
                    services.reset_password(w, ui.user(), username, tmp, rr)
                    ui.flash(f"Temporary password set for {username}")
                except (ValueError, services.Denied, accounts.AccountError) as e:
                    st.error(str(e))
            if locked and b2.button("Unlock", key=f"ul_go_{username}"):
                try:
                    services.unlock_account(w, ui.user(), username, rr)
                    ui.flash(f"Unlocked {username}")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))
    if rec and rec["role"] == "hr" and ui.can("manage_accounts"):
        senior = "senior_hr" in rec.get("extra_permissions", [])
        st.markdown("**Senior HR permission** · " + ("granted" if senior else "not granted"))
        st.caption("A permission within the HR role (not a new role): sees every restricted HR case and changes HR "
                   "policy settings. Granting or removing it is audited.")
        why = st.text_input("Reason (required)", key=f"shr_why_{username}")
        if st.button("Remove Senior HR" if senior else "Grant Senior HR", key=f"shr_{username}"):
            try:
                services.set_senior_hr(w, ui.user(), username, not senior, why)
                ui.close_card()
                ui.flash(("Removed" if senior else "Granted") + f" Senior HR for {username}")
            except (ValueError, services.Denied, accounts.AccountError) as e:
                st.error(str(e))


@st.dialog("Agent", width="large", on_dismiss=ui.close_card)
def agent_card(aid):
    ag = a.loc[aid]
    st.markdown(f"#### {ui.name(aid)}")
    st.markdown(" ".join([kit.pill(ag.team, "Neutral"), kit.pill(f"Main: {ag.primary}", "Info")] +
                         [kit.pill(f"Covers {q}", "Neutral") for q in ag.secondary]), unsafe_allow_html=True)
    s = records.summary(w, aid, range(DAYS))
    kit.kpis([{"label": "Worked (recorded)", "value": s["worked"] + s["overtime"] + s["extra"] + s["swap"]},
              {"label": "No attendance record", "value": s["unrecorded"]},
              {"label": "Leave", "value": s["leave"]}, {"label": "Absent", "value": s["sick"]},
              {"label": "No-show", "value": s["no_show"]}, {"label": "Approved OT hours", "value": f"{s['ot_hours']:g}"}])
    rows = []
    for d in range(DAYS):
        rec = records.day_record(w, aid, d)
        rows.append({"Date": ui.dlabel(d), "Status": CELL[rec["code"]][0], "Shift": rec["detail"]})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=300)
    changes = [(c, d) for c in history.changes(w.run_id) for d in c["details"] if d["agent_id"] == aid]
    if changes:
        kit.label("Changes")
        st.dataframe(pd.DataFrame([{"When": c["sim_time"], "Event": c["event"], "Date": d["date"], "Before": d["before"],
                                    "After": d["after"], "By": c["actor"]} for c, d in changes]),
                     hide_index=True, width="stretch")
    if st.button("Close", key="agc_close"):
        ui.close_card()
        st.rerun()


@st.dialog("Day", width="large", on_dismiss=ui.close_card)
def day_card(d):
    st.markdown(f"#### {ui.dlabel(d)}")
    stt = ui.day_status(d)
    codes = [records.day_record(w, i, d)["code"] for i in a.index]
    kit.kpis([{"label": "Worked (recorded)", "value": sum(c in ("worked", "overtime", "extra", "swap", "left_early")
                                                          for c in codes)},
              {"label": "Scheduled / no record", "value": sum(c in ("scheduled", "unrecorded") for c in codes)},
              {"label": "Absent", "value": codes.count("sick")}, {"label": "No-show", "value": codes.count("no_show")},
              {"label": "Leave", "value": codes.count("leave")},
              {"label": "Planned coverage (forecast)", "value": f"{stt['Covered %'].mean():.1f}%",
               "status": kit.coverage_status(stt["Covered %"].mean())}])
    st.dataframe(stt, hide_index=True, width="stretch")
    incs = [i for i in w.incidents if i.day == d]
    if incs:
        st.dataframe(pd.DataFrame([{"Alert": i.id, "Issue": ui.incident_title(i), "Team": i.owner_team,
                                    "State": ui.incident_state(i)[0]} for i in incs]), hide_index=True, width="stretch")
    if st.button("Close", key="dayc_close"):
        st.session_state.wk_day = None
        ui.close_card()
        st.rerun()


@st.dialog("Change", width="large", on_dismiss=ui.close_card)
def change_card(cid):
    c = next(x for x in history.changes() if x["id"] == cid)
    status = c.get("status") or "Applied"
    st.markdown(f'{kit.pill(c["event"], "Info")} '
                f'{kit.pill(status, "Warning" if status.startswith("Reversed") else "Normal")} '
                f'<span class="ss-muted">#{c["id"]} · {kit.esc(c["sim_time"])} · run {kit.esc(c["run_id"])} · saved '
                f'{kit.esc(c["saved_at"].replace("T", " "))}</span>', unsafe_allow_html=True)
    st.markdown(f"**{c['summary'] or c['event']}**")
    det = c["details"]
    queues_touched = sorted({part.split("·")[-1].split("→")[0].strip() for d in det for part in (d["before"], d["after"])
                             if "·" in part})
    kit.kpis([{"label": "By", "value": c["actor"].split(" (")[0], "sub": c["actor"].split(" (")[-1].rstrip(")") if "(" in c["actor"] else ""},
              {"label": "Shifts changed", "value": len(det)},
              {"label": "Agents", "value": len({d["agent_id"] for d in det})},
              {"label": "Queues", "value": len(queues_touched), "sub": ", ".join(queues_touched)[:40]}])
    st.markdown(f"Related record: **{c.get('related') or '—'}** · Reason / outcome: {c['reason'] or '—'}")
    if det:
        dd = pd.DataFrame(det)
        dd["Agent"] = dd.agent_id.map(ui.name)
        st.dataframe(dd[["date", "Agent", "before", "after"]].rename(columns={"date": "Date", "before": "Before",
                                                                              "after": "After"}),
                     hide_index=True, width="stretch")
    else:
        st.caption("Starting point: full roster saved.")
    if st.button("Close", key="chc_close"):
        ui.close_card()
        st.rerun()



if (ref := ui.card("account")) is not None:
    account_card(ref)
elif (ref := ui.card("agent")) is not None:
    agent_card(ref)
elif (ref := ui.card("day")) is not None:
    day_card(ref)
elif (ref := ui.card("change")) is not None:
    change_card(ref)
elif (ref := ui.card("incident")) is not None:
    @st.dialog("Alert", width="large", on_dismiss=ui.close_card)
    def _inc(i):
        ui.decision_card(next(x for x in w.incidents if x.id == i), False, key=f"adm_{i}")
    _inc(ref)
else:
    ui.shared_cards()
