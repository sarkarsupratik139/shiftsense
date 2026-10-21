import numpy as np
import pandas as pd
import streamlit as st

import ui_common as ui
import ui_kit as kit
from shiftsense import planning, services, workflow
from shiftsense.config import interval_label, intervals_per_day, is_night, is_weekend, queue_names
from shiftsense.constraints import RULES, check, summary as rule_summary
from shiftsense.kpis import burden
from shiftsense.roster import coverage

ui.page("planner")
w = ui.world()
p = w.policy
b = ui.base()
queues = queue_names(p)
per_day = intervals_per_day(p)
DAYS = p["horizon"]["days"]
open_days = planning.open_days(w)
agents_df, leave = w.data["agents"], w.data["leave"]
shrink = p["staffing"]["shrinkage"]

LW = ui.live_world()                       # drafts and publication always concern the live roster
published = next((x for x in LW.plans if x["status"] == "published"), None)
draft = next((x for x in LW.plans if x["status"] in ("draft", "validated")), None)
right = kit.pill(f"Live: {published['id']}" if published else "No published plan", "Normal" if published else "Critical")
if draft:
    right += " " + kit.pill(f"{draft['status'].title()}: {draft['id']}", "Info")
AREAS = ["Forecast & staffing", "Roster optimization", "What-if", "Review & publish", "Benchmarks & history"]
area = st.session_state.get("pl_area") if st.session_state.get("pl_area") in AREAS else AREAS[0]   # set by the nav
ui.shell(f"Planning · {area}", f"{workflow.now_label(w)} · {'demo scenario (simulated) · ' if w.scenario else ''}"
         f"changeable from {ui.dlabel(open_days[0])}", right)
if ui.can("escalate_risk") and st.button("Flag a risk to Ops", icon=":material/report:", key="pl_risk"):
    ui.open_card("risk", True)

live_m = planning.metrics(p, agents_df, leave, w.req, w.roster, open_days)
kit.kpis([
    {"label": "Coverage (open days)", "value": f"{live_m['Coverage %']}%", "status": kit.coverage_status(live_m["Coverage %"])},
    {"label": "Uncovered (people-h)", "value": f"{live_m['Uncovered people-hours']:g}"},
    {"label": "SLA exposure", "value": f"{live_m['SLA exposure']:g}", "help": "uncovered people-hours × queue SLA sensitivity"},
    {"label": "Overtime", "value": f"{live_m['OT hours']:g} h"},
    {"label": "Labour cost", "value": kit.money(live_m["Labour cost ₹"]), "sub": "simulated rates"},
    {"label": "Rule violations", "value": live_m["Violations"], "status": "Normal" if not live_m["Violations"] else "Critical"},
])


def _ask_ctx():
    return {"now": workflow.now_label(w), "live_plan": live_m,
            "coverage_next_7_days": pd.concat([ui.day_status(d) for d in ui.days_ahead(7)]).to_dict("records"),
            "people_per_queue": {q: {"main": int((w.data["skills"][q] == 2).sum()),
                                     "can_cover": int((w.data["skills"][q] == 1).sum())} for q in queues}}


ui.ask_box("analyst", _ask_ctx, "pl_ask", "Which queue is most at risk next week?")


# ------------------------------------------------------------------ cards
@st.dialog("Interval detail", width="large", on_dismiss=ui.close_card)
def interval_card(ref):
    ui.interval_detail(*ref)
    if st.button("Close", key="iv_close"):
        ui.close_card()
        st.rerun()


@st.dialog("Agent detail", width="large", on_dismiss=ui.close_card)
def agent_card(aid):
    a = ui.agents().loc[aid]
    st.markdown(f"#### {ui.name(aid)}")
    st.markdown(" ".join([kit.pill(a.team, "Neutral"), kit.pill(f"Main: {a.primary}", "Info")] +
                         [kit.pill(f"Can cover: {q}", "Neutral") for q in a.secondary] +
                         [kit.pill("Nights OK" if a.night_ok else "No nights", "Neutral")]), unsafe_allow_html=True)
    bur = burden(p, w.roster, agents_df)
    rows = []
    for d in range(DAYS):
        r = ui.shift_row(aid, d)
        if r is None:
            continue
        why = ["primary queue" if r["queue"] == a.primary else "cross-skilled cover"]
        why.append("preferred shift" if r["shift"] == a.pref_shift else f"prefers {a.pref_shift}")
        if is_night(p, r["shift"]) or is_weekend(p, d):
            why.append(f"night/weekend load {bur[aid]:.0f} (team avg {bur.mean():.1f})")
        rows.append({"Date": ui.dlabel(d), "Shift": ui.shift_label(r), "Why": "; ".join(why)})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if st.button("Close", key="ag_close"):
        ui.close_card()
        st.rerun()


# ================================================================== A. Demand & staffing
if area == "Forecast & staffing":
    day = ui.coverage_matrix_section(list(range(DAYS)), "ds", default_day=min(w.first_day, DAYS - 1))
    t = np.arange(day * per_day, (day + 1) * per_day)
    cov = coverage(p, w.roster)
    x = [interval_label(p, i) for i in t]
    with st.expander("Queue details: forecast, staffing and skills"):
        q = st.segmented_control("Queue", queues, default=queues[0], key="ds_q") or queues[0]
        qi = queues.index(q)
        qp = p["queues"][q]
        req, sch = w.req[qi, t], cov[qi, t]
        short, surplus = np.clip(req - sch, 0, None), np.clip(sch - req, 0, None)
        sk = w.data["skills"][q]
        kit.kpis([
            {"label": "Calls (forecast)", "value": f"{w.fc['mid'][qi, t].sum():,.0f}" if w.fc else "—",
             "sub": f"AHT {qp['aht_sec']}s"},
            {"label": "Required (people-h)", "value": f"{req.sum() / 2:g}", "sub": "scheduled heads incl. shrinkage"},
            {"label": "Scheduled (people-h)", "value": f"{sch.sum() / 2:g}"},
            {"label": "Shortage (people-h)", "value": f"{short.sum() / 2:g}",
             "status": "Critical" if short.sum() else "Normal"},
            {"label": "Surplus (people-h)", "value": f"{surplus.sum() / 2:g}"},
        ])
        kit.stats([("Skilled agents", f"{int((sk > 0).sum())} ({int((sk == 2).sum())} main · {int((sk == 1).sum())} cover)"),
                   ("SLA", f"{qp['sla_pct']:.0%} in {qp['sla_sec']}s"),
                   ("Productive (people-h)", f"{sch.sum() / 2 * (1 - shrink):.0f} after {shrink:.0%} shrinkage")])
    kit.label("Gaps this day · click a row for detail")
    rows = []
    for i, qq in enumerate(queues):
        s_ = np.clip(w.req[i, t] - cov[i, t], 0, None)
        if not s_.any():
            continue
        idx = np.nonzero(s_)[0].tolist()
        runs, cur = [], [idx[0]]
        for k in idx[1:]:
            if k == cur[-1] + 1:
                cur.append(k)
            else:
                runs.append(cur)
                cur = [k]
        runs.append(cur)
        for r in runs:
            rows.append({"Queue": qq, "From": x[r[0]], "To": interval_label(p, t[r[-1]] + 1),
                         "Worst gap": int(s_[r].max()), "People-h": s_[r].sum() / 2,
                         "_t0": int(t[r[0]]), "_t1": int(t[r[-1]] + 1)})
    gaps = pd.DataFrame(rows)
    if gaps.empty:
        st.caption("No gaps.")
    else:
        gaps = gaps.sort_values("People-h", ascending=False).reset_index(drop=True)

        def _open_gap():
            rows_ = st.session_state["ds_gaps"].selection.rows
            if rows_:
                g = gaps.iloc[rows_[0]]
                ui.open_card("interval", (g.Queue, int(g._t0), int(g._t1)))

        st.dataframe(gaps.drop(columns=["_t0", "_t1"]), hide_index=True, width="stretch", on_select=_open_gap,
                     selection_mode="single-row", key="ds_gaps")
    with st.popover("Method"):
        st.markdown(f"Forecast: same weekday, last 4 weeks, ±{p['staffing']['forecast_band_z']}σ range. "
                    f"Required = Erlang C (SLA {qp['sla_pct']:.0%}/{qp['sla_sec']}s, occupancy ≤ "
                    f"{p['staffing']['max_occupancy']:.0%}) ÷ (1 − {shrink:.0%} shrinkage). "
                    "Productive capacity = scheduled × (1 − shrinkage).")

# ================================================================== B. Roster & optimization
elif area == "Roster optimization":
    heur = b["plans"]["Manual heuristic"]["roster"]
    opt = b["plans"]["ShiftSense optimizer"]["roster"]
    comp = {"Live roster": w.roster, "Manual baseline": heur, "Optimized (original)": opt}
    rows = []
    for name, ro in comp.items():
        m = planning.metrics(p, agents_df, leave, w.req, ro, open_days)
        rows.append({"Roster": name, **{k: m[k] for k in ("Coverage %", "Uncovered people-hours", "SLA exposure",
                                                           "OT hours", "Labour cost ₹", "Fairness (std)",
                                                           "Primary-queue %", "Violations")}})
    cmpdf = pd.DataFrame(rows)
    kit.label(f"Comparison · {ui.dlabel(open_days[0])} – {ui.dlabel(open_days[-1])} · live demand")
    st.dataframe(cmpdf, hide_index=True, width="stretch")
    st.caption("Manual baseline and optimized are the original plans; the live roster includes approved changes. "
               "Violations are checked against today's leave and absences.")
    c1, c2 = st.columns(2)
    with c1:
        kit.chart(kit.bars(cmpdf.Roster, {"Uncovered people-hours": cmpdf["Uncovered people-hours"],
                                          "SLA exposure": cmpdf["SLA exposure"]}, title="Gaps (lower is better)"),
                  "Uncovered people-hours: " + ", ".join(f"{r.Roster} {r._3:g}" for r in cmpdf.itertuples()) + ".",
                  key="ro_bars", title="Uncovered workload by roster", subtitle="People-hours and SLA exposure · open days · lower is better", provenance="simulated", data=cmpdf)
    viol = rule_summary(check(p, agents_df, leave, w.roster))
    with c2:
        kit.label("Hard rules · live roster")
        st.dataframe(pd.DataFrame([{"Rule": r.replace("_", " ").capitalize(),
                                    "Status": "Pass" if viol[r] == 0 else f"{viol[r]} violation(s)"} for r in RULES]),
                     hide_index=True, width="stretch", height=300)

    kit.label("Where the live roster is short · days × queues (people-h)")
    cov = coverage(p, w.roster)
    grid = []
    for d in open_days:
        sl = slice(d * per_day, (d + 1) * per_day)
        grid.append([np.clip(w.req[i, sl] - cov[i, sl], 0, None).sum() / 2 for i in range(len(queues))])
    z = -np.array(grid).T
    kit.chart(kit.gap_heatmap(z, [ui.dlabel(d) for d in open_days], queues, unit="people-h short",
                              mode_="shortfall", fmt=".1f"),
              "Short people-hours per queue and day for the live roster (darker = larger gap).", key="ro_heat", title="Coverage gaps by queue and day", subtitle="People-hours short · live roster · open days", provenance="modelled",
              data=pd.DataFrame(-z, index=queues, columns=[ui.dlabel(d) for d in open_days]).reset_index(names="Queue"))
    kit.label("Roster calendar")
    _rc1, _rc2, _rc3 = st.columns(3)
    _teams = sorted(set(agents_df.team))
    _rt = _rc1.selectbox("Team", _teams, key="ro_cal_team")
    _wk = _rc2.selectbox("Week", sorted({d // 7 for d in range(DAYS)}), index=min(w.today // 7, (DAYS - 1) // 7),
                         format_func=lambda k: f"{ui.dlabel(k * 7)} – {ui.dlabel(min(k * 7 + 6, DAYS - 1))}",
                         key="ro_cal_wk")
    _rq = _rc3.selectbox("Queue", ["All queues"] + queues, key="ro_cal_q")
    ui.roster_calendar(list(agents_df[agents_df.team == _rt].agent_id),
                       [d for d in range(_wk * 7, min(_wk * 7 + 7, DAYS))], f"Live roster · team {_rt}",
                       queue=None if _rq == "All queues" else _rq)
    kit.label("Assignments · pick an agent to see why")
    st.selectbox("Agent", [None] + list(agents_df.agent_id), format_func=lambda i: "—" if i is None else ui.name(i),
                 key="ro_agent", label_visibility="collapsed",
                 on_change=lambda: st.session_state.ro_agent and ui.open_card("agent", st.session_state.ro_agent))

# ================================================================== C. What-if
elif area == "What-if":
    with st.form("whatif"):
        c1, c2, c3, c4 = st.columns(4)
        name = c1.text_input("Name", value=f"Scenario {len(st.session_state.get('scenarios', [])) + 1}")
        dq = c2.multiselect("Demand change for", queues, default=queues[:1])
        dpct = c2.slider("Demand change %", -30, 50, 15, step=5)
        absn = c3.slider("Extra absences (agent-days)", 0, 20, 0)
        hc = c3.slider("Headcount change", -10, 0, 0)
        nights = c4.slider("Max nights / week", 1, 6, p["rules"]["max_nights_per_week"])
        run = st.form_submit_button("Run scenario", type="primary")
    if run:
        sc = planning.Scenario(name=name, demand_pct={q: dpct for q in dq}, extra_absences=absn,
                               headcount_change=hc, max_nights_per_week=nights)
        with st.spinner("Re-optimising on a copy (live roster untouched)…"):
            res = planning.run_scenario(w, sc, time_limit=30)
        st.session_state.setdefault("scenarios", []).append(res)
        after = res["after"] or {}
        services.record_whatif(w, ui.user(), sc.name,
                               f"status {res['status']}; coverage {res['as_is']['Coverage %']}% as-is"
                               + (f" -> {after['Coverage %']}% re-planned, uncovered {after['Uncovered people-hours']} "
                                  f"people-h, violations {after['Violations']}" if after else " · no feasible plan"),
                               result=res)
        st.session_state["scenarios"] = st.session_state["scenarios"][-4:]
    scs = st.session_state.get("scenarios", [])
    if not scs:
        st.caption("Scenarios run on copies. Nothing changes until a plan is published.")
    else:
        keys = ("Coverage %", "Uncovered people-hours", "SLA exposure", "OT hours", "Labour cost ₹", "Fairness (std)",
                "Violations")
        rows = [{"Scenario": "Live plan · current inputs", **{k: live_m[k] for k in keys}, "Changes vs live": 0,
                 "Feasible": "Yes"}]
        for r in scs:
            rows.append({"Scenario": f"{r['name']} · keep plan", **{k: r["as_is"][k] for k in keys},
                         "Changes vs live": 0, "Feasible": "—"})
            if r["after"]:
                rows.append({"Scenario": f"{r['name']} · re-planned", **{k: r["after"][k] for k in keys},
                             "Changes vs live": r["after"]["Changes vs live"], "Feasible": "Yes"})
            else:
                rows.append({"Scenario": f"{r['name']} · re-planned", "Feasible": f"No ({r['status']})"})
        df = pd.DataFrame(rows)
        st.dataframe(df, hide_index=True, width="stretch")
        kit.chart(kit.bars(df.Scenario, {"Uncovered people-hours": df["Uncovered people-hours"].fillna(0)},
                           title="Uncovered people-hours · baseline vs scenarios", height=240),
                  "Uncovered people-hours per scenario, compared with the live plan.", key="wi_bars", title="Uncovered workload by scenario", subtitle="People-hours · keep plan vs re-planned", provenance="simulated")
        for i, r in enumerate(scs):
            sc = r["scenario"]
            with st.container(border=True):
                bits = [f"demand {', '.join(sc.demand_pct)} {list(sc.demand_pct.values())[0]:+d}%" if sc.demand_pct else None,
                        f"{sc.extra_absences} absences" if sc.extra_absences else None,
                        f"headcount {sc.headcount_change}" if sc.headcount_change else None,
                        f"max {sc.max_nights_per_week} nights/wk"]
                st.markdown(f"**{r['name']}** · " + " · ".join(x for x in bits if x))
                if r["after"] is None:
                    st.caption(planning.explain_status(r["status"]))
                    for d in r["diagnosis"]:
                        st.markdown(kit.pill("Proven" if d["type"] == "proven" else "Likely",
                                             "Critical" if d["type"] == "proven" else "Warning") + " " + kit.esc(d["text"]),
                                    unsafe_allow_html=True)
                else:
                    c1, c2 = st.columns([1, 4])
                    if c1.button("Use as draft", key=f"wi_draft_{i}", disabled=not ui.can("replan"),
                                 help="Creates a separate draft for the live roster; nothing is published"):
                        with ui.mutate(LW):
                            plan = services.draft_plan(LW, ui.user(), r["roster"], r["name"],
                                                       f"demo scenario {w.run_id}" if w.scenario else "what-if")
                        ui.flash(f"Draft {plan['id']} created · open Review & publish")
                    c2.caption(f"Removed agents: {', '.join(r['removed'])}" if r["removed"] else
                               "Scenario demand and absences are for testing; publishing applies only the roster.")
        if st.button("Clear scenarios", key="wi_clear"):
            st.session_state["scenarios"] = []
            st.rerun()

# ================================================================== E. History
elif area == "Benchmarks & history":
    kit.label("Benchmark · reproducible, simulated")
    st.caption("Same seeded data and constraints for every approach. Recovery is compared on the SAME roster and the "
               "SAME injected absences; time figures are modelled from configured assumptions.")
    if st.button("Run benchmark (about 20 s)", key="bench_btn"):
        with st.spinner("Running scenarios…"):
            st.session_state["bench"] = ui.evidence_benchmark()
    bench = st.session_state.get("bench")
    if isinstance(bench, dict):
        c1, c2 = st.columns([3, 2])
        with c1:
            st.markdown("**Recovery on the same roster** · medians over scenarios")
            st.dataframe(bench["same_roster"], hide_index=True, width="stretch")
        with c2:
            st.markdown("**Inputs and assumptions**")
            st.dataframe(bench["provenance"], hide_index=True, width="stretch")
        st.markdown("**Plan quality** · manual heuristic vs optimiser variants (fortnight plan)")
        st.dataframe(bench["plans"], hide_index=True, width="stretch")
        st.caption("Synthetic data. Not evidence of real-world performance until validated with actual operations.")

    kit.label("Roster versions")
    st.dataframe(pd.DataFrame([{"Plan": x["id"], "Name": x["name"], "State": x["status"].title(), "Source": x["source"],
                                "Created by": x["created_by"], "Created": x["created"], "Notes": x.get("notes", "")}
                               for x in reversed(LW.plans)]), hide_index=True, width="stretch")
    st.caption("Every published, superseded, discarded and draft version is kept; publishing never rewrites an "
               "earlier version. Restore one from Review & publish (it becomes a draft to validate and review).")
    kit.label("Forecast and demand revisions")
    st.markdown("**Forecast version for this run:** seasonal baseline (same weekday, previous 4 weeks, about 80% "
                "band) built when the run started; required heads from Erlang C with "
                f"{p['staffing']['shrinkage']:.0%} shrinkage.")
    if LW.spikes:
        st.dataframe(pd.DataFrame(LW.spikes).rename(columns={"queue": "Queue", "pct": "Above forecast %",
                                                            "window": "Window"}), hide_index=True, width="stretch")
    else:
        st.caption("No demand revisions (reported call spikes) recorded in this run.")
    st.caption("Forecast accuracy is not shown: actual call volumes are not available, so it cannot be measured.")
    kit.label("Saved what-if runs")
    saved = services.whatif_runs(w, ui.user())
    if not saved:
        st.caption("No what-if runs saved in this workspace yet. Every run in What-if is saved here automatically.")
    else:
        keys = ("Coverage %", "Uncovered people-hours", "SLA exposure", "OT hours", "Labour cost ₹", "Violations")
        rows = []
        for r in saved:
            inp = r["inputs"] or {}
            what = "; ".join(x for x in [
                ", ".join(f"{q} {v:+g}%" for q, v in (inp.get("demand_pct") or {}).items()),
                f"{inp['extra_absences']} extra absences" if inp.get("extra_absences") else "",
                f"headcount {inp['headcount_change']:+g}" if inp.get("headcount_change") else "",
                f"max {inp['max_nights_per_week']} nights/week" if inp.get("max_nights_per_week") else ""] if x)
            for label, m in (("keep plan", r["as_is"]), ("re-planned", r["after"])):
                rows.append({"Run": f"#{r['id']} {r['name']}", "When": r["sim_time"], "By": r["actor"].split(" (")[0],
                             "Inputs": what or "no changes", "Version": label,
                             **({k: m.get(k) for k in keys} if m else {"Coverage %": None}),
                             "Result": "feasible" if m else f"no plan ({r['status']})"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("Inputs and full metric comparison of each run, kept per workspace. Runs used copies; the live "
                   "roster was never changed by them.")
    kit.label("Planning history")
    ui.history_view("an_hist", "Drafts, validations, publications and restores; imports; demo scenario and clock "
                               "actions; what-if runs; schedule changes across all teams.")

# ================================================================== D. Review & publish
elif area == "Review & publish":
    st.markdown(kit.pill("PUBLISHED ROSTER (LIVE)", "Info") + " Drafts are separate proposals. Only <b>Publish</b> here "
                "changes the live roster: it re-validates the draft, needs your review confirmation, and refuses if the "
                "live roster changed since the draft was made.", unsafe_allow_html=True)
    kit.steps(["Configure", "Generate draft", "Validate", "Review impact", "Publish"],
              1 if draft is None else 2 if draft["status"] == "draft" else 3)
    st.caption("Configure in What-if or Roster optimization → generate a draft below → validate it → review the "
               "impact and confirm → publish. Each step re-checks the hard rules; nothing reaches the live roster "
               "before Publish.")
    kit.label("Plans")
    st.dataframe(pd.DataFrame([{"Plan": x["id"], "Name": x["name"], "State": x["status"].title(), "Source": x["source"],
                                "By": x["created_by"], "Created": x["created"]} for x in reversed(LW.plans)]),
                 hide_index=True, width="stretch", height=min(40 + 35 * len(LW.plans), 220))
    older = [x for x in LW.plans if x["status"] in ("published", "superseded")]
    if ui.can("replan") and len(older) > 1:
        with st.expander("Roll back to an earlier published version", expanded=bool(st.session_state.get("rb_why"))):
            st.caption("Creates a draft from that version. It must be validated, reviewed and published like any "
                       "plan; days that have started are kept as they are. Audited.")
            pick = st.selectbox("Version", [x["id"] for x in older if x["status"] == "superseded"], key="rb_pick")
            why = st.text_input("Reason (required)", key="rb_why")
            if not why.strip():
                st.caption("Enter a reason to enable the button.")
            if st.button("Restore as draft", key="rb_go", disabled=not why.strip()):
                try:
                    with ui.mutate(LW):
                        d = services.restore_plan_as_draft(LW, ui.user(), pick, why)
                    ui.flash(f"Draft {d['id']} created from {pick} · validate and review it before publishing")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))
    if ui.can("replan") and st.button("New draft · re-optimize open days", icon=":material/refresh:"):
        with st.spinner("Optimising (live roster untouched)…"):
            res = planning.run_scenario(LW, planning.Scenario(name="Re-optimized"), time_limit=30)
        if res["roster"] is None:
            st.error(planning.explain_status(res["status"]))
            for d in res["diagnosis"]:
                st.markdown(f"- {'Blocking rule' if d['type'] == 'proven' else 'Likely cause'}: {d['text']}")
            st.caption("Safe alternatives: run a What-if with fewer simultaneous changes, or escalate the uncovered "
                       "demand to Operations. Hard rules are never relaxed to force a plan.")
        else:
            with ui.mutate(LW):
                plan = services.draft_plan(LW, ui.user(), res["roster"], "Re-optimized", "re-optimize")
            ui.flash(f"Draft {plan['id']} ready for review")
    if draft:
        rv = planning.review(LW, draft["id"])
        kit.label(f"Checklist · {draft['id']} {draft['name']} · {draft['status']}")
        st.markdown(" ".join(kit.pill(f"{'Pass' if ok else 'Check'} · {n}: {txt}", "Normal" if ok else "Warning")
                             for n, ok, txt in rv["checks"]), unsafe_allow_html=True)
        new, live = rv["new"], rv["live"]
        kit.kpis([
            {"label": "Coverage", "value": f"{new['Coverage %']}%", "sub": f"live {live['Coverage %']}%"},
            {"label": "Uncovered (people-h)", "value": f"{new['Uncovered people-hours']:g}",
             "sub": f"live {live['Uncovered people-hours']:g}"},
            {"label": "SLA exposure", "value": f"{new['SLA exposure']:g}", "sub": f"live {live['SLA exposure']:g}"},
            {"label": "Overtime", "value": f"{new['OT hours']:g} h", "sub": f"live {live['OT hours']:g} h"},
            {"label": "Labour cost", "value": kit.money(new["Labour cost ₹"]), "sub": f"live {kit.money(live['Labour cost ₹'])}"},
            {"label": "Shifts changed", "value": len(rv["changes"]), "sub": f"{len(rv['affected_agents'])} agents"},
            {"label": "Violations", "value": len(rv["violations"]), "status": "Normal" if not rv["violations"] else "Critical"},
        ])
        if rv["changes"]:
            kit.label("Change preview")
            ch = pd.DataFrame(rv["changes"])
            ch["Agent"] = ch.agent_id.map(ui.name)
            st.dataframe(ch[["date", "Agent", "before", "after"]].rename(columns={"date": "Date", "before": "Before",
                                                                                  "after": "After"}),
                         hide_index=True, width="stretch", height=min(40 + 35 * len(ch), 300))
        c1, c2, c3 = st.columns([1, 2, 1])
        if draft["status"] == "draft" and c1.button("Validate", type="primary", disabled=not ui.can("replan")):
            with ui.mutate(LW):
                rv2 = services.validate_plan(LW, ui.user(), draft["id"])
            if rv2["violations"]:
                st.error(f"{len(rv2['violations'])} rule violation(s): cannot validate.")
            else:
                ui.flash(f"{draft['id']} validated")
        if draft["status"] == "validated":
            ok = c2.checkbox("I reviewed the changes and affected agents", key="pub_ok")
            if c1.button("Publish", type="primary", disabled=not (ok and ui.can("replan"))):
                try:
                    with ui.mutate(LW):
                        out = services.publish_plan(LW, ui.user(), draft["id"], reviewed=ok)
                    ui.flash(f"Published {draft['id']} · {out['changes']} shift changes · {out['agents']} agents notified")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))
        if c3.button("Discard draft"):
            with ui.mutate(LW):
                draft["status"] = "discarded"
            ui.flash("Draft discarded")
    else:
        st.caption("No draft. Create one here or from a What-if scenario.")

    kit.label("Original plan vs live roster · past days")
    pva = planning.planned_vs_actual(LW)
    if pva.empty:
        st.caption("No past days yet.")
    else:
        kit.chart(kit.bars(pva.Date, {"Original plan coverage %": pva["Planned coverage %"],
                                      "Live roster coverage %": pva["Live-roster coverage %"]}, height=220),
                  "Coverage of the original plan and of the live roster (after approved changes) against forecast "
                  "demand, for each past day. Both are scheduled coverage, not measured service.", key="pva_bars", title="Planned vs live-roster coverage · past days", subtitle="% of forecast demand that was scheduled (not measured service)", provenance="simulated")
        st.dataframe(pva, hide_index=True, width="stretch")
        st.caption("Coverage figures compare rosters with forecast demand; they are not measured results. Worked and "
                   "absence counts are recorded events only. Actual call volumes are not available (no call-platform "
                   "feed), so forecast accuracy cannot be measured.")

# ------------------------------------------------------------------ open card (if any)
@st.dialog("Flag a risk to Ops", on_dismiss=ui.close_card)
def risk_card(_):
    st.caption("Asks Ops for an operational decision. You keep doing the analysis; Ops decides the intervention.")
    summ = st.text_input("Risk (one line)", key="rk_sum", placeholder="Claims-EN short 6 people-h Thursday evening")
    det = st.text_area("Evidence and what you checked", key="rk_det")
    if st.button("Send to Ops", type="primary", key="rk_go"):
        try:
            services.escalate_risk(w, ui.user(), summ, det + (" [demo scenario, simulated]" if w.scenario else ""))
            ui.close_card()
            ui.flash("Sent to Ops")
        except (ValueError, services.Denied) as e:
            st.error(str(e))


if ui.card("risk"):
    risk_card(True)
elif (ref := ui.card("interval")) is not None:
    interval_card(ref)
elif (ref := ui.card("agent")) is not None:
    agent_card(ref)
else:
    ui.shared_cards()
