"""Analyst · Data hub: put reference data in (any format), see what a change would do, apply it, follow every
change, report situations and take data out. Always works on the live workspace."""
import pandas as pd
import streamlit as st

import ui_common as ui
import ui_data as ud
import ui_kit as kit
from shiftsense import backup, basepack, convert, dataflow, exports, refdata, services, workflow
from shiftsense.schemas import DOMAINS, SITUATION_TYPES

ui.page("data")
w = ui.live_world()
u = ui.user()
AREAS = ["Data overview", "Add or update data", "Data changes", "Situations", "Downloads"]
area = st.session_state.get("data_area") if st.session_state.get("data_area") in AREAS else AREAS[0]
linked = bool(getattr(w, "ref_linked", False))
ui.shell(f"Data hub · {area}", f"{workflow.now_label(w)} · live workspace · reference data",
         kit.pill("Built from reference data" if linked else "Not yet built from reference data",
                  "Normal" if linked else "Warning"))
if ui.context() == "scenario":
    st.info("You are viewing the demo scenario elsewhere; the Data hub always works on the live workspace.")
refdata.ensure()
counts = refdata.counts()

# ================================================================== overview
if area == "Data overview":
    if not linked:
        st.warning("The live workspace still runs on the original generated dataset (50 agents, 4 queues). "
                   "Reference data you add is stored and versioned, and takes effect when you rebuild the live "
                   "workspace from it below.")
    sits = [s for s in refdata.current("situations").values() if (s.get("status") or "Active") == "Active"]
    kit.kpis([{"label": "Employees", "value": counts.get("employees", 0)},
              {"label": "Contracts", "value": counts.get("contracts", 0)},
              {"label": "Queues (service levels)", "value": counts.get("service_levels", 0)},
              {"label": "Rules set", "value": counts.get("rules", 0)},
              {"label": "Leave records", "value": counts.get("leave", 0)},
              {"label": "Call-volume days", "value": counts.get("volumes", 0)},
              {"label": "Active situations", "value": len(sits), "status": "Warning" if sits else None}])
    pending = services.data_changes(w, u, status="pending")
    scheduled = dataflow.sets(status="scheduled")
    c1, c2 = st.columns(2)
    c1.markdown(f"**Waiting for approval:** {len(pending)}  \n**Scheduled for a later date:** {len(scheduled)}")
    c2.markdown(f"**Live workspace:** run {w.run_id} · {len(w.data['agents'])} agents · {len(w.policy['queues'])} "
                f"queues · {len(w.roster)} shifts")

    kit.label("Base input data")
    st.caption("The base pack is the minimum data ShiftSense needs to run: employees, 3 contract types, queues with "
               "their clients' service levels, company rules, leave, 8 weeks of call volumes and 2 example situations. "
               "All fictional. Small (default): 50 employees, 5 teams, 4 queues. Full: 120 employees, 8 teams, "
               "6 queues. It is loaded through the same converter as any upload, and every record can be replaced or "
               "removed later.")
    b1, b2, b3 = st.columns(3)
    empty = refdata.is_empty()
    if b1.button("Load base data" if empty else "Reload base data (replace)", type="primary" if empty else "secondary",
                 width="stretch", disabled=not ui.can("manage_data")):
        ui.open_card("dh_confirm", "load" if empty else "reload")
    if b2.button("Rebuild live workspace from reference data", width="stretch",
                 disabled=empty or not ui.can("manage_data")):
        ui.open_card("dh_confirm", "rebuild")
    with b3.popover("Base pack files", width="stretch"):
        for dom in basepack.ORDER:
            f = basepack.folder() / basepack.FILES[dom]
            if f.exists():
                st.download_button(f"{DOMAINS[dom].label} · {f.name}", f.read_bytes(), f.name, "text/csv",
                                   key=f"bp_{dom}")

    ud.drop_folder(w, u, "dh")

    kit.label("Data quality")
    ud.quality_list(w)
    kit.label("Calendar · situations and scheduled changes")
    ud.calendar(w, "dh_ov")

# ================================================================== add or update
elif area == "Add or update data":
    own = [d for d in DOMAINS if d not in ("situations",) and
           (ui.can("manage_data") and d in ("service_levels", "rules", "volumes", "attendance"))]
    st.caption("Employee details, contracts and leave are maintained by HR (People data); you are notified of every "
               "change and its effect on the plan.")
    ud.add_or_update(w, u, own)
    with st.expander("Import a roster grid as a draft plan (one column per date)"):
        st.caption("Cells like M, D, E, N (or 'M · Claims-EN'), WO for a day off, L for leave. The result is a DRAFT: "
                   "validate, review and publish it in Review & publish. Days already started keep the live roster.")
        gf = st.file_uploader("Roster grid (Excel or CSV)", type=["xlsx", "xls", "csv"], key="rg_file")
        gname = st.text_input("Draft name", value="Imported roster", key="rg_name")
        if gf is not None and st.button("Create draft from grid", key="rg_go"):
            try:
                with ui.mutate(w):
                    out = services.import_roster_grid(w, u, data=gf.getvalue(), filename=gf.name, name=gname)
                msg = (f"Draft {out['plan']['id']} created with {out['shifts']} shifts"
                       + (f" · {len(out['violations'])} rule problem(s) to fix before publishing" if out["violations"]
                          else " · no rule problems"))
                st.session_state["rg_issues"] = [i.message for i in out["issues"]][:30]
                ui.flash(msg)
            except (ValueError, services.Denied) as e:
                st.error(str(e))
        for m in st.session_state.get("rg_issues", []):
            st.caption(m)

# ================================================================== changes
elif area == "Data changes":
    st.caption("Team leads' proposals are decided by HR (People data). Contract, rule and service-level changes are "
               "decided by Ops on the Operations screen. Every change, whoever made it, is listed here.")
    kit.label("All changes")
    rows = services.data_changes(w, u)
    if not rows:
        st.caption("No changes yet.")
    else:
        df = pd.DataFrame([{"Change": r["id"], "Data": DOMAINS[r["domain"]].label, "Status": r["status"].title(),
                            "Submitted by": r["submitted_by"], "Submitted": r["submitted_at"][:16].replace("T", " "),
                            "Effective": r["effective_from"], "Converted by": r["converted_by"],
                            "New": (r["summary"] or {}).get("new", 0), "Changed": (r["summary"] or {}).get("changed", 0),
                            "Removed": (r["summary"] or {}).get("delete", 0),
                            "Outcome": services._outcome_text(r["outcome"] or {}) if r["outcome"] else ""} for r in rows])

        def _open_change():
            sel = st.session_state["dh_tbl"].selection.rows
            if sel:
                ui.open_card("dh_change", int(df.iloc[sel[0]]["Change"]))

        st.dataframe(df, hide_index=True, width="stretch", on_select=_open_change, selection_mode="single-row",
                     key="dh_tbl")
        st.download_button("Change log (Excel)", exports.change_log(rows), "shiftsense_data_changes.xlsx")

# ================================================================== situations
elif area == "Situations":
    kit.label("Calendar")
    ud.calendar(w, "dh_sitcal")
    kit.label("Active situations")
    ud.active_situations(w, "dh_sit", can_close=ui.can("report_situation"))
    with st.expander("Update an active situation"):
        ud.situation_update(w, "dh_situp")
    kit.label("Report a situation")
    types = [t for t, v in SITUATION_TYPES.items() if u["role"] in v[3]]
    ud.quick_report(w, "dh_qr", types)

# ================================================================== downloads
else:
    kit.label("Plans and forecasts")
    c1, c2, c3 = st.columns(3)
    c1.download_button("Published roster (Excel)", exports.roster_workbook(w), "shiftsense_roster.xlsx", width="stretch")
    c2.download_button("Forecast and requirement (Excel)", exports.forecast_workbook(w), "shiftsense_forecast.xlsx",
                       width="stretch")
    c3.download_button("Operations brief, next 72 h (Excel)", exports.ops_brief(w), "shiftsense_ops_brief.xlsx",
                       width="stretch")
    c4, c5, c6 = st.columns(3)
    c4.download_button("Service level per client (Excel)", exports.client_service_report(w)[1],
                       "shiftsense_client_service.xlsx", width="stretch")
    c5.download_button("Printable roster (HTML)", exports.print_html("Published roster", {
        "By agent and day": exports.roster_grid(w)}, w), "shiftsense_roster_print.html", "text/html", width="stretch",
        help="Open in a browser and print")
    c6.download_button("Printable ops brief (HTML)", exports.print_html("Operations brief · next 3 days", {
        "Coverage": exports.coverage_rows(w, list(range(w.today, min(w.policy["horizon"]["days"], w.today + 3))))}, w),
        "shiftsense_brief_print.html", "text/html", width="stretch")
    kit.label("Current reference data as templates")
    st.caption("Each file holds today's data with friendly headers. Edit it in Excel and upload it back in "
               "Add or update data: only changed rows are applied.")
    cols = st.columns(3)
    for i, dom in enumerate([d for d in DOMAINS if d != "volumes"]):
        recs = list(refdata.current(dom).values())
        cols[i % 3].download_button(f"{DOMAINS[dom].label} ({len(recs)})", exports.reference_template(dom, recs),
                                    f"shiftsense_{dom}.xlsx", width="stretch", key=f"tpl_{dom}")
    st.caption("All files start with a Read me sheet: what it is, when it was produced, whether it is live or demo, "
               "the source and every column's meaning. All figures are simulated.")


# ================================================================== cards
@st.dialog("Data change", width="large", on_dismiss=ui.close_card)
def change_card(cid):
    cs = dataflow.get_set(int(cid))
    if cs is None:
        st.caption("Not found.")
        return
    st.markdown(f"**#{cs['id']} · {DOMAINS[cs['domain']].label}** · {cs['status']}")
    st.caption(f"Submitted by {cs['submitted_by']} on {cs['submitted_at'][:16].replace('T', ' ')} · effective "
               f"{cs['effective_from']} · source {cs['source']} · converted by {cs['converted_by']}"
               + (f" · decided by {cs['decided_by']}" if cs["decided_by"] else ""))
    if cs["reason"]:
        st.markdown(f"**Reason:** {kit.esc(cs['reason'])}", unsafe_allow_html=True)
    if cs["outcome"]:
        st.markdown(f"**Outcome:** {kit.esc(services._outcome_text(cs['outcome']))}", unsafe_allow_html=True)
    kit.label("What changed")
    ud.show_ops(cs)
    kit.label("Impact estimated when submitted")
    ud.show_impact(cs["impact"] or {})


@st.dialog("Confirm", on_dismiss=ui.close_card)
def confirm_card(what):
    text = {"load": ("Load the base data into reference data?",
                     "Converts and stores the 7 base files as version 1 of each record. The live workspace is not "
                     "changed until you rebuild it."),
            "reload": ("Reload the base data over the current reference data?",
                       "Every record is replaced by the base pack; records not in it are marked removed. Old versions "
                       "stay in history. The live workspace is not changed until you rebuild it."),
            "rebuild": ("Rebuild the live workspace from reference data?",
                        "Builds a new optimised roster for the whole fortnight from the reference data and makes it the "
                        "live workspace. The current live workspace is replaced (a verified backup is taken first). "
                        "Alerts, requests and attendance of the old workspace stay in history under its run id.")}[what]
    st.markdown(f"**{text[0]}**")
    st.caption(text[1])
    size = "small"
    if what in ("load", "reload"):
        size = st.radio("Size", ["small", "full"], horizontal=True, key="dh_size",
                        format_func=lambda x: {"small": "Small · 50 employees, 4 queues",
                                               "full": "Full · 120 employees, 6 queues"}[x])
    ok = st.checkbox("I understand", key="dh_cf_ok")
    if st.button("Confirm", type="primary", disabled=not ok):
        try:
            with st.spinner("Working…"):
                if what in ("load", "reload"):
                    out = services.load_base_pack(w, u, replace=what == "reload", size=size)
                    msg = "Base data loaded: " + ", ".join(f"{DOMAINS[k].label.lower()} {v}" for k, v in out.items())
                else:
                    bk = backup.create() if __import__("shiftsense.store", fromlist=["x"]).db_path().exists() else None
                    new = services.rebuild_from_reference(w, u, time_limit=45)
                    ui.reset_world(new, "Rebuilt from reference data")
                    msg = f"Live workspace rebuilt from reference data (run {new.run_id})" + (
                        f" · backup {bk.name}" if bk else "")
            ui.close_card()
            ui.flash(msg)
        except (ValueError, services.Denied, RuntimeError) as e:
            st.error(str(e))


if (ref := ui.card("dh_confirm")) is not None:
    confirm_card(ref)
elif (ref := ui.card("dh_change")) is not None:
    change_card(ref)
else:
    ui.shared_cards()
