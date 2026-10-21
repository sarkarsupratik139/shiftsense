import numpy as np
import pandas as pd
import streamlit as st

import ui_common as ui
import ui_kit as kit
from shiftsense import assistant, comms, finance, performance, priority, records, services, variance, workflow
from shiftsense.config import interval_label, intervals_per_day, queue_names
from shiftsense.roster import coverage

ui.page("ops")
AREA = ui.area("ops")
w = ui.world()
p = w.policy
queues = queue_names(p)
per_day = intervals_per_day(p)
rate, mult = finance.rates(p)

active = [i for i in w.incidents if i.status in ("open", "needs_new_fix", "escalated")]
ranked = priority.rank(w, active)
esc = [(i, s) for i, s in ranked if i.status == "escalated"]
with_tl = [(i, s) for i, s in ranked if i.status != "escalated"]
pending_req = [r for r in w.requests if r["state"] == "Pending"]
window = [w.today] + ui.days_ahead(2)

# ------------------------------------------------------------------ operational KPIs (today + 2 days)
cov = coverage(p, w.roster)
idx = np.concatenate([np.arange(d * per_day, (d + 1) * per_day) for d in window])
idx = idx[idx >= w.now_t]
req, sch = w.req[:, idx], cov[:, idx]
need, have = req.sum() / 2, sch.sum() / 2
short = np.clip(req - sch, 0, None).sum() / 2
surplus = np.clip(sch - req, 0, None).sum() / 2
sens = np.array([p["queues"][q]["sensitivity"] for q in queues])[:, None]
exposure = (np.clip(req - sch, 0, None) * sens).sum() / 2
past = [d for d in range(max(0, w.today - 6), w.today + 1)]
codes = [records.day_record(w, a, d)["code"] for a in w.data["agents"].agent_id for d in past]
starts = sum(c in ("worked", "overtime", "extra", "swap", "left_early", "sick", "no_show") for c in codes)
missed = sum(c in ("sick", "no_show") for c in codes)
unrec = codes.count("unrecorded")                 # started, no attendance record: excluded, never assumed
resolved = sum(i.status in ("fixed", "no_gap") or (i.status == "closed" and not i.residual_ph) for i in w.incidents)
pct_cov = round(100 * (1 - short / max(need, 1e-9)), 1)

referrals = [c for c in comms.cases(w.run_id) if c["status"] in ("Referred to Operations", "Action Required")]
ui.shell("Operations", f"{workflow.now_label(w)} · next 72 h from now",
         kit.pill(f"{len(esc) + len(referrals)} need your decision", "Critical" if esc or referrals else "Normal"),
         open_kinds=("incident",))
if AREA == "Overview":
    kit.label("Next 72 hours · plan against forecast demand", anchor="plan")
    kit.kpis([
        {"label": "Coverage", "value": f"{pct_cov}%", "status": kit.coverage_status(pct_cov),
         "sub": f"{have:,.0f} of {need:,.0f} people-h"},
        {"label": "Shortage (people-h)", "value": f"{short:g}",
         "status": "Critical" if short > 10 else "Warning" if short else "Normal"},
        {"label": "SLA exposure", "value": f"{exposure:.1f}", "help": "shortage × queue SLA sensitivity"},
        {"label": "Escalations", "value": len(esc), "status": "Critical" if esc else "Normal"},
        {"label": "Absenteeism (7 d, recorded)", "value": f"{100 * missed / starts:.1f}%" if starts else "Unavailable",
         "sub": f"{missed} of {starts} recorded shifts"},
    ])
    kit.stats([("With team leads", len(with_tl) + len(pending_req)),
               ("Alerts resolved", f"{resolved} / {len(w.incidents)}"), ("Surplus (people-h)", f"{surplus:g}"),
               ("Shifts without a record (7 d)", unrec)])


def _option_rows(inc):
    rec = inc.rec
    if not rec or inc.status == "closed":
        return []
    opts = [("Recommended", rec["eval"])] + list(rec["alternatives"].items())
    return [{"name": n, "covers_pct": round(e["restored_pct"], 1), "cost": round(e["cost"]),
             "left_ph": e["short_head_hours_after"], "ot_hours": e["ot_hours"], "people": e["agents"]} for n, e in opts]


def _ranked_ctx():
    return [{"id": i.id, "title": ui.incident_title(i), "team": i.owner_team, "date": ui.dlabel(i.day),
             "state": ui.incident_state(i)[0], "escalated": i.status == "escalated", **{k: s[k] for k in ("severity", "short_ph", "exposure", "hours_left", "starts")},
             "options": _option_rows(i)} for i, s in ranked]


if AREA == "Overview":
    # ------------------------------------------------------------------ decide first (deterministic)
    c1, c2 = st.columns([1, 1])
    if ui.can("report_spike") and c1.button("Report call spike", icon=":material/trending_up:", width="stretch"):
        ui.open_card("spike", True)
    if c2.button("Activity log", icon=":material/history:", width="stretch"):
        ui.open_card("log", True)
    kit.label("Decide first", anchor="decide")
    items, _src = assistant.ops_priorities({"now": workflow.now_label(w), "ranked": _ranked_ctx()})
    if not items:
        st.caption("Nothing waiting for a decision.")
    for it in items:
        with st.container(border=True):
            a, b = st.columns([6, 1])
            with a:
                kit.flow(f"<b>{kit.esc(it['issue'])}</b><br>{kit.esc(it['impact'])}", kit.esc(it["best_action"]),
                         kit.esc(it["expected_result"]))
            if b.button("Open", key=f"prio_{it['id']}", type="primary", width="stretch",
                        help="Open this alert to see all options and decide"):
                ui.open_card("incident", it["id"])
                st.rerun()
    if items:
        st.caption("Ranked by severity, SLA exposure and time to the gap; actions and figures come from the recovery "
                   "engine (no AI). Simulated data.")

if AREA == "Coverage & risk":
    ui.coverage_matrix_section(window, "ops_cm", default_day=w.today)

if AREA == "Coverage & risk":
    # ------------------------------------------------------------------ where to intervene
    kit.label("Where to intervene first", anchor="intervene")
    if not ranked:
        st.success("No open issues.")
    else:
        left, right = st.columns([3, 2])
        with left:
            rows = []
            for i, s in ranked:
                opts = _option_rows(i)
                rows.append({"Severity": s["severity"], "Issue": ui.incident_title(i), "Team": i.owner_team,
                             "Owner": "Ops" if i.status == "escalated" else "Team lead",
                             "Starts": f"{ui.dlabel(i.day)} {s['starts']}",
                             "In (h)": s["hours_left"], "Gap (people-h)": s["short_ph"], "Exposure": s["exposure"],
                             "Cheapest full fix ₹": min((o["cost"] for o in opts if o["covers_pct"] >= 99.5), default=None),
                             "_id": i.id})
            rdf = pd.DataFrame(rows)

            def _open():
                sel = st.session_state["ops_rank"].selection.rows
                if sel:
                    ui.open_card("incident", rdf.iloc[sel[0]]["_id"])

            st.dataframe(rdf.drop(columns="_id"), hide_index=True, width="stretch", on_select=_open,
                         selection_mode="single-row", key="ops_rank")
            st.caption("Ranked by severity, SLA exposure and time to the gap. Click a row for options.")
        with right:
            ev = kit.chart(kit.risk_matrix([{"name": i.id, "x": s["hours_left"] or 0, "y": s["exposure"],
                                             "size": s["short_ph"], "status": s["severity"]} for i, s in ranked]),
                           "Risk matrix: issues further up are more exposed, further right start sooner. Same data as "
                           "the table. Select a point to open the alert.", key="ops_risk", provenance="simulated",
                           title="Staffing risk prioritisation",
                           subtitle="Right = starts sooner · up = more SLA exposure · size = gap · shape = severity",
                           on_select=True)
            _pts = (ev.selection.points if ev and getattr(ev, "selection", None) else []) or []
            _sel = _pts[0]["customdata"][0] if _pts and _pts[0].get("customdata") else None
            if _sel and st.session_state.get("ops_risk_last") != _sel:     # open once per new selection
                st.session_state["ops_risk_last"] = _sel
                ui.open_card("incident", _sel)
                st.rerun()
            if not _sel:
                st.session_state.pop("ops_risk_last", None)

    # ------------------------------------------------------------------ staffing variance (by 30 minutes)
    kit.label("Staffing variance · required vs scheduled vs recorded", anchor="variance")
    c1, c2, _ = st.columns([1, 1, 2])
    vday = c1.selectbox("Day", [w.today] + ui.days_ahead(1), format_func=ui.dlabel, key="ops_vday")
    vq = c2.selectbox("Queue", ["All queues"] + queues, key="ops_vq")
    vt = variance.interval_table(w, vday)
    vsel = vt if vq == "All queues" else vt[vt.Queue == vq]
    agg = vsel.groupby(["t", "Time"], as_index=False).agg(
        req=("Required (forecast)", "sum"), sch=("Scheduled", "sum"),
        conf=("Checked in (genuine)", lambda s: None if s.isna().all() else s.sum()),
        sim=("Simulated attendance", lambda s: None if s.isna().all() else s.sum()),
        unk=("No record yet", lambda s: None if s.isna().all() else s.sum()))
    conf = agg.conf.fillna(0) + agg.sim.fillna(0) if w.scenario else agg.conf
    kit.chart(kit.variance_chart(agg.Time, agg.req, agg.sch, conf, agg.unk, height=280,
                                confirmed_label="Present (simulated + checked in)" if w.scenario else "Checked in (recorded)"),
              f"{vq} on {ui.dlabel(vday)}: " + " ".join(variance.explain(vsel)),
              key="ops_var", title="Staffing variance: required vs scheduled vs recorded", subtitle=f"{vq} · {ui.dlabel(vday)} · people per 30 minutes", stats_=kit.gap_stats(agg.Time, agg.req, agg.sch), zoom=True, data=vsel.drop(columns=["t"]))
    fr = variance.freshness(w)
    st.caption(f"Recorded figures as of {workflow.now_label(w)}: {fr['genuine_checkins']} genuine check-in(s)"
               + (f", {fr['simulated_records']} simulated attendance record(s) (demo scenario)" if fr["scenario"] else "")
               + ". Upcoming half-hours have no actual value yet. Started shifts without a record are unknown — not "
                 "counted as present or absent. Queue gaps are cross-team (Ops); team attendance is with each Team Lead.")
    st.dataframe(variance.summary(vt), hide_index=True, width="stretch")

if AREA == "Alerts & escalations":
    if referrals:
        kit.label("Governance referrals")
        gdf = pd.DataFrame([{"Case": c["id"], "Status": c["status"], "Issue": c["title"], "Team": c["team"] or "—",
                             "Raised": c["sim_time"]} for c in referrals])

        def _open_case():
            sel = st.session_state["ops_cases"].selection.rows
            if sel:
                ui.open_card("case", int(gdf.iloc[sel[0]]["Case"]))

        st.dataframe(gdf, hide_index=True, width="stretch", on_select=_open_case, selection_mode="single-row",
                     key="ops_cases")

    risks = [n for n in comms.notices_for(ui.user(), w.run_id, w.now_t)
             if n["title"].startswith("Risk from Analyst") and n["status"] == "action"]
    if risks:
        kit.label("Risks raised by the Analyst · need an Ops decision")
        for n in risks[:5]:
            with st.container(border=True):
                st.markdown(f"**{n['title'].replace('Risk from Analyst: ', '')}** · "
                            f"{kit.pill(ui.due_text(n['due_t'])[0], ui.due_text(n['due_t'])[1])}", unsafe_allow_html=True)
                st.caption(n["body"])
                c1, c2, c3 = st.columns([2, 3, 1])
                dec = c1.selectbox("Decision", ["Decision recorded", "Returned to Analyst"], key=f"rd_{n['id']}")
                note = c2.text_input("Reason / instruction (required)", key=f"rn_{n['id']}")
                if c3.button("Record", key=f"rb_{n['id']}"):
                    try:
                        services.answer_risk(w, ui.user(), n["id"], dec, note)
                        ui.flash(f"{dec} · Analyst informed")
                    except (ValueError, services.Denied) as e:
                        st.error(str(e))

if AREA == "Follow-ups, data & history":
    fups = comms.all_notices(w.run_id, "Follow-up from Operations")
    if fups:
        kit.label("Follow-ups assigned")
        st.dataframe(pd.DataFrame([{
            "Follow-up": f["title"].replace("Follow-up from Operations: ", ""), "Owner": f["recipient"].get("team", "—"),
            "Related": f"{f['related_kind']} {f['related_ref']}" if f["related_kind"] else "—",
            "Deadline": ui.due_text(f["due_t"])[0] if f["status"] == "action" else ui.tlabel(f["due_t"]),
            "Status": {"action": "Open", "resolved": "Done", "acknowledged": "Seen"}.get(f["status"], f["status"])}
            for f in fups]), hide_index=True, width="stretch")
if AREA == "Recovery":
    carried = [i for i in w.incidents if i.status == "closed" and i.residual_ph]
    if carried:
        kit.label("Risk carried after Ops decisions")
        st.dataframe(pd.DataFrame([{"Alert": i.id, "Issue": ui.incident_title(i), "Team": i.owner_team,
                                    "Decision": i.note.split(" · ")[0], "Decided by": i.decided_by.split(" (")[0],
                                    "Still uncovered (people-h)": i.residual_ph} for i in carried]),
                     hide_index=True, width="stretch")
        st.caption("These gaps were not filled in the roster. They stay visible here until cover is added.")

    perf = performance.recovery(w)
    kit.label("Recovery performance · this run (simulated time)", anchor="performance")
    kit.kpis([
        {"label": "Alerts raised", "value": perf["alerts"]},
        {"label": "Covered", "value": perf["resolved"], "help": "gap closed in the roster"},
        {"label": "Closed with risk carried", "value": perf["closed_with_risk"],
         "status": "Warning" if perf["closed_with_risk"] else None, "help": "Ops decided; people-hours still uncovered"},
        {"label": "Median time to cover", "value": "—" if perf["median_ttc_min"] is None else f"{perf['median_ttc_min']:g} min",
         "help": "alert raised → fixed or closed, simulated minutes"},
        {"label": "Accepted as recommended", "value": "—" if perf["acceptance_pct"] is None else f"{perf['acceptance_pct']}%",
         "sub": f"{perf['modified']} modified · {perf['rejected']} rejected"},
        {"label": "Escalations", "value": perf["escalated"]},
        {"label": "Uncovered now (people-h)", "value": f"{perf['residual_ph']:g}", "sub": "open alerts + carried risk"},
        {"label": "Forecast accuracy", "value": "Unavailable" if perf["forecast_accuracy"] is None
         else f"{100 - perf['forecast_accuracy']:.0f}%", "sub": "no actual call data" if perf["forecast_accuracy"] is None
         else f"{perf['forecast_accuracy']:g}% average error vs imported actual calls"},
    ])

if AREA == "Coverage & risk":
    # ------------------------------------------------------------------ coverage gaps over time (timeline)
    kit.label("When the gaps happen · next 72 hours")
    _base = window[0] * per_day
    _runs = []
    for qi, q in enumerate(queues):
        sh = np.clip(req[qi] - sch[qi], 0, None)          # the existing shortage measure, people per 30 min
        i = 0
        while i < len(sh):
            if sh[i] > 0:
                j = i
                while j < len(sh) and sh[j] > 0:
                    j += 1
                t0, t1 = int(idx[i]), int(idx[j - 1]) + 1
                _runs.append({"queue": q, "t0": t0, "t1": t1, "peak": float(sh[i:j].max()),
                              "ph": float(sh[i:j].sum()) / 2})
                i = j
            else:
                i += 1
    if _runs:
        def _lab(t):
            return f"{ui.dlabel(t // per_day)[:3]} {interval_label(p, t)}"
        _items = [{"row": r["queue"], "start": (r["t0"] - _base) * 24 / per_day, "end": (r["t1"] - _base) * 24 / per_day,
                   "kind": "gap", "label": f"−{r['peak']:g}",
                   "detail": f"{_lab(r['t0'])} – {_lab(r['t1'] - 1)} · up to {r['peak']:g} people short · "
                             f"{r['ph']:g} people-h"} for r in _runs]
        _span = len(window) * 24
        _ticks = [(h, f"{ui.dlabel(window[0] + h // 24)[:3]} {h % 24:02d}") for h in range(0, _span + 1, 6)]
        g1, g2 = st.columns([3, 2])
        with g1:
            kit.chart(kit.timeline(_items, queues, (0, _span), _ticks,
                                   now=(w.now_t - _base) * 24 / per_day),
                      f"{len(_runs)} shortage period(s) across queues in the next 72 hours.", key="ops_gap_tl",
                      title="Coverage gaps over time", subtitle="Each bar = a continuous shortage; label = peak "
                      "people short · dotted line = now", provenance="modelled")
        with g2:
            kit.label("Most urgent gaps")
            _rk = sorted(_runs, key=lambda r: (r["t0"], -r["peak"]))[:8]
            st.dataframe(pd.DataFrame([{"Queue": r["queue"], "From": _lab(r["t0"]), "To": _lab(r["t1"] - 1),
                                        "Peak short": r["peak"], "People-h": r["ph"]} for r in _rk]),
                         hide_index=True, width="stretch")
            st.caption("Earliest first. Same shortage measure as the charts below.")
    else:
        kit.empty("No coverage gaps in the next 72 hours", "Scheduled staffing meets the forecast requirement.")
    # ------------------------------------------------------------------ 3-day watch list (queue-days < 95%)
    heat = np.array([[round(100 * np.minimum(w.req[qi, d * per_day:(d + 1) * per_day], cov[qi, d * per_day:(d + 1) * per_day]).sum()
                             / max(w.req[qi, d * per_day:(d + 1) * per_day].sum(), 1), 1) for d in window]
                     for qi in range(len(queues))])
    worst = [(queues[qi], ui.dlabel(window[di]), heat[qi, di]) for qi in range(len(queues)) for di in range(len(window))
             if heat[qi, di] < 95]
    kit.label("Watch list · queue-days below 95% covered")
    if worst:
        st.dataframe(pd.DataFrame(sorted(worst, key=lambda r: r[2]), columns=["Queue", "Day", "Covered %"]),
                     hide_index=True, width="stretch")
    else:
        kit.empty("Every queue is at least 95% covered", "For each of the next three days.")

# ------------------------------------------------------------------ financial intelligence
if AREA == "Cost & staffing":
    kit.label("Cost · simulated, configured rates", anchor="cost")
week = list(range((w.today // 7) * 7, min((w.today // 7) * 7 + 7, p["horizon"]["days"])))
dc = finance.day_costs(w, week)
rc = finance.recovery_costs(w)
proj = finance.day_costs(w, ui.days_ahead(3))
bud = finance.budget(w)
unresolved = sum(s["short_ph"] for _, s in ranked)
if AREA == "Cost & staffing":
    kit.kpis([
        {"label": "Labour this week (planned)", "value": kit.money(dc["Labour ₹"].sum())},
        {"label": "Overtime this week", "value": kit.money(dc["Overtime ₹"].sum()), "sub": f"{dc['OT hours'].sum():g} h"},
        {"label": "Recovery spend", "value": kit.money(rc["Cost ₹"].sum()), "sub": f"{len(rc)} actions"},
        {"label": "Next 3 days (projected)", "value": kit.money(proj["Total ₹"].sum()), "sub": "scheduled + approved OT"},
        {"label": "Budget", "value": kit.money(bud) if bud else "Not configured"},
        {"label": "Unresolved gaps (people-h)", "value": f"{unresolved:g}", "sub": "no ₹ value: no penalty rate configured",
         "status": "Warning" if unresolved else None},
    ])
    c1, c2 = st.columns(2)
    with c1:
        kit.chart(kit.bars(dc.Date, {"Labour ₹": dc["Labour ₹"], "Overtime ₹": dc["Overtime ₹"]},
                           money_axis=True, stacked=True),
                  f"Planned labour {kit.money(dc['Labour ₹'].sum())} and overtime {kit.money(dc['Overtime ₹'].sum())} this week.",
                  key="ops_cost", title="Daily labour and overtime cost", subtitle="This week · planned roster at contract rates · bars add up to the daily total", provenance="simulated", data=dc.drop(columns="day"))
    if len(rc):
        by = rc.groupby("Type")["Cost ₹"].sum().sort_values()
        with c2:
            kit.chart(kit.bars(list(by.index), {"Cost ₹": list(by.values)}, title="Recovery spend by action type",
                               horizontal=True, money_axis=True),
                      "Recovery spend: " + ", ".join(f"{k} {kit.money(v)}" for k, v in by.items()) + ".", key="ops_rc", title="Recovery spend by action type", subtitle="₹ at configured rates · this run", provenance="simulated")
    else:
        c2.caption("No recovery spend yet.")
if AREA == "Recovery":
    if ranked:
        kit.label("Options per open issue · cost vs coverage")
        rows = []
        for i, s in ranked:
            for o in _option_rows(i):
                rows.append({"Issue": f"{i.id} · {ui.incident_title(i)}", "Option": o["name"], "Covers %": o["covers_pct"],
                             "Cost ₹": o["cost"], "OT hours": o["ot_hours"], "People": o["people"],
                             "Left uncovered (people-h)": o["left_ph"]})
            rows.append({"Issue": f"{i.id} · {ui.incident_title(i)}", "Option": "No action", "Covers %": 0, "Cost ₹": 0,
                         "OT hours": 0, "People": 0, "Left uncovered (people-h)": s["short_ph"]})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=min(40 + 35 * len(rows), 360))


def _ask_ctx():
    return {"now": workflow.now_label(w), "ranked": _ranked_ctx(),
            "costs_this_week": dc.drop(columns="day").to_dict("records"),
            "recovery_spend": rc.groupby("Type")["Cost ₹"].sum().to_dict() if len(rc) else {},
            "coverage_next_72h_pct": pct_cov, "shortage_people_hours": short, "budget": bud or "not configured"}


if AREA == "Cost & staffing":
    ui.ask_box("operations head", _ask_ctx, "ops_ask", "Which issue is most expensive to fix?")


if AREA == "Follow-ups, data & history":
    # ------------------------------------------------------------------ data approvals and situations
    kit.label("Data approvals and situations", anchor="data")
    import ui_data as ud
    from shiftsense import exports as _ex
    from shiftsense.schemas import SITUATION_TYPES as _ST
    st.caption("Contract, rule and service-level changes submitted by the Analyst need your approval; each shows its "
               "impact before you decide. Situations (outages, protests, disasters…) change demand or availability for "
               "a period and are handled like any other change.")
    if not w.scenario:
        ud.approvals(w, can_decide=ui.can("approve_data"), key="op_appr",
                     domains=("contracts", "service_levels", "rules", "situations"))
        d1, d2 = st.columns([1, 1])
        with d1.popover("Report a situation", icon=":material/emergency:", width="stretch"):
            ud.quick_report(w, "op_qr", [t for t, v in _ST.items() if "ops_head" in v[3]])
        d2.download_button("Daily operations brief (Excel)", _ex.ops_brief(w), "shiftsense_ops_brief.xlsx",
                           icon=":material/download:", width="stretch")
        ud.active_situations(w, "op_sits", can_close=True)
        with st.expander("Update an active situation"):
            ud.situation_update(w, "op_situp")
        with st.expander("Calendar · situations and scheduled changes"):
            ud.calendar(w, "op_cal")
        st.download_button("Service level per client (Excel)", _ex.client_service_report(w)[1],
                           "shiftsense_client_service.xlsx", icon=":material/download:")
    else:
        st.caption("Data changes and situations are handled in the live workspace.")


    # ------------------------------------------------------------------ operations history
    kit.label("Operations history", anchor="history")
    ui.history_view("ops_hist", "All teams: alerts and outcomes, escalations and Ops decisions, follow-ups, recovery "
                                "actions and schedule changes, attendance records, governance referrals.")


# ------------------------------------------------------------------ cards
@st.dialog("Issue", width="large", on_dismiss=ui.close_card)
def issue_card(inc_id):
    inc = next(i for i in w.incidents if i.id == inc_id)
    ui.decision_card(inc, False, key=f"op_{inc_id}")
    if inc.status == "escalated" and ui.can("ops_decide"):
        kit.label("Operations decision")
        decision = st.selectbox("Decision", ["Approve extra overtime budget", "Bring in cover from another site / agency",
                                             "Accept the risk for this window", "Return to team lead"],
                                key=f"od_{inc_id}")
        if decision != "Return to team lead":
            left = priority.assess(w, inc)["short_ph"]
            st.caption(f"This records your decision; it does not change the roster. {left:g} people-hours stay "
                       "uncovered in the roster and remain visible as carried risk"
                       + (" until cover is added." if left else "."))
        note = st.text_input("Reason / instruction (required)", key=f"on_{inc_id}")
        if st.button("Record decision", type="primary", key=f"ob_{inc_id}"):
            try:
                if not note.strip():
                    raise ValueError("A documented reason is required.")
                with ui.mutate():
                    services.ops_decide(w, ui.user(), inc_id, decision, note)
                ui.close_card()
                ui.flash(f"Recorded · {inc.id} {'back with team lead' if inc.status == 'open' else 'closed'}")
            except (ValueError, services.Denied) as e:
                st.error(str(e))
    if ui.can("ops_decide"):
        with st.expander("Assign a follow-up to the team lead"):
            fu = st.text_input("Follow-up", key=f"fu_{inc_id}")
            hrs = st.selectbox("Due in", [2, 4, 8, 24], index=1, format_func=lambda h: f"{h} h", key=f"fuh_{inc_id}")
            if st.button("Assign", key=f"fub_{inc_id}"):
                try:
                    services.assign_followup(w, ui.user(), inc.owner_team, fu, hrs, related=("incident", inc.id))
                    ui.flash(f"Follow-up sent to {inc.owner_team}")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))
    if st.button("Close", key="op_close"):
        ui.close_card()
        st.rerun()


@st.dialog("Staffing detail", width="large", on_dismiss=ui.close_card)
def interval_card(ref):
    ui.interval_detail(*ref)
    if st.button("Close", key="ops_iv_close"):
        ui.close_card()
        st.rerun()


@st.dialog("Report call spike", on_dismiss=ui.close_card)
def spike_card(_):
    q = st.selectbox("Queue", queues, key="sp_q")
    pct = st.slider("Above forecast", 10, 80, 30, step=5, format="%d%%", key="sp_pct")
    hrs = st.selectbox("For the next", [2, 3, 4, 6], index=1, format_func=lambda h: f"{h} hours", key="sp_h")
    if st.button("Raise alert", type="primary"):
        with ui.mutate():
            inc = services.report_spike(w, ui.user(), q, pct, hrs)
        ui.close_card()
        ui.flash(f"Alert sent to {inc.owner_team}" if inc.status == "open" else "Absorbed by current staffing")


@st.dialog("Activity log", width="large", on_dismiss=ui.close_card)
def log_card(_):
    rows = comms.audit_rows(w.run_id)
    ok, bad = comms.verify_audit()
    st.markdown(kit.pill("Audit chain verified" if ok else f"Audit chain broken at row {bad}", "Normal" if ok else "Critical"),
                unsafe_allow_html=True)
    if rows:
        af = pd.DataFrame(rows)[["sim_time", "actor", "action", "record", "before", "after", "reason"]]
        st.dataframe(af, hide_index=True, width="stretch", height=420)
        st.download_button("Download CSV", af.to_csv(index=False), "shiftsense_audit.csv", "text/csv")
    else:
        st.caption("No activity yet.")


if (ref := ui.card("incident")) is not None:
    issue_card(ref)
elif (ref := ui.card("interval")) is not None:
    interval_card(ref)
elif ui.card("spike"):
    spike_card(True)
elif ui.card("log"):
    log_card(True)
else:
    ui.shared_cards()
