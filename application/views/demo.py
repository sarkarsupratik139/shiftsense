"""Analyst · Demo & data: the demo scenario sandbox and the clocks (data input now lives in the Data hub).

The demo scenario is a SEPARATE copy of the live workspace (own storage slot, own 'sim-' run id). Loading,
exploring, advancing its clock, reloading or unloading it never changes the published roster or live records.
"""
import io

import pandas as pd
import streamlit as st

import ui_common as ui
import ui_kit as kit
from shiftsense import services, workflow

ui.page("demo")
LW = ui.live_world()
SC = ui.scenario()
p = LW.policy
AREAS = ["Demo scenario", "Clocks"]
area = st.session_state.get("sys_area") if st.session_state.get("sys_area") in AREAS else AREAS[0]   # set by the nav
ui.shell(f"Demo & data · {area}", f"{workflow.now_label(ui.world())} · Analyst", kit.pill(
    f"Scenario loaded · {SC.run_id}" if SC else "No demo scenario loaded", "Warning" if SC else "Neutral"))
kit.kpis([
    {"label": "Live workspace", "value": f"run {LW.run_id}", "sub": f"{len(LW.roster)} published shifts · not touched here",
     "status": "Info"},
    {"label": "Demo scenario", "value": SC.run_id if SC else "Not loaded",
     "sub": f"simulated clock {workflow.now_label(SC)}" if SC else "load it below", "status": "Warning" if SC else None},
    {"label": "Messaging", "value": "In-app", "sub": "email is never sent from the demo scenario"},
])


# ================================================================== Demo scenario
if area == "Demo scenario":
    st.markdown("**Load demo story** creates a **separate sandbox**: a copy of the live workspace, then a prepared, "
                "reproducible story run through the normal workflow rules. Everyone can switch to it from the sidebar "
                "(*Workspace → Demo scenario*) to explore with their own role's permissions.")
    st.dataframe(pd.DataFrame([
        ("Added (sandbox only)", "TL-1: a sick call for tomorrow, waiting for the team lead's decision"),
        ("Added (sandbox only)", "TL-2: an absence already fixed, with agents still to confirm"),
        ("Added (sandbox only)", "TL-3: a gap the team lead could not close, escalated to Ops"),
        ("Added (sandbox only)", "Two time-off requests waiting for team leads (Sneha, Priya)"),
        ("Added (sandbox only)", "7 prior-period decisions by Fatima (TL-2), labelled 'simulated prior period'"),
        ("Replaced", "Only a previously loaded demo scenario (if any)"),
        ("Preserved", "The published roster and every live record: requests, alerts, attendance, notifications, "
                      "plans, accounts, audit and change history"),
        ("Never", "Real email: reminders inside the scenario stay in-app"),
    ], columns=["Effect", "What"]), hide_index=True, width="stretch")
    c1, c2, _ = st.columns([1, 1, 2])
    if c1.button("Reload demo story" if SC else "Load demo story", type="primary", width="stretch"):
        ui.open_card("confirm", "story")
    if SC and c2.button("Unload scenario", width="stretch"):
        ui.open_card("confirm", "unload")
    st.caption("Workflow: load demo → explore forecast → simulate disruption (clock) → evaluate recovery → "
               "Planning · What-if → Use as draft → Review & publish (validate, review, confirm). Nothing is ever "
               "published by loading or exploring a scenario.")

# ================================================================== Clocks
elif area == "Clocks":
    kit.label("Live clock · genuine time")
    st.markdown(kit.pill(f"Live · {workflow.now_label(LW)}", "Info"), unsafe_allow_html=True)
    st.caption("Moves represented time in the LIVE workspace so real check-in windows open. It creates no attendance, "
               "no no-shows and no other events: live attendance comes only from genuine check-ins. It never "
               "touches the demo scenario.")
    lend = LW.now_t >= LW.req.shape[1] - 1
    for col, (lbl, hrs) in zip(st.columns(4), [("+30 min", 0.5), ("+1 h", 1), ("+4 h", 4), ("+1 day", 24)]):
        if col.button(lbl, width="stretch", disabled=lend, key=f"live_{lbl}"):
            try:
                with ui.mutate(LW):
                    services.advance_live_clock(LW, ui.user(), hrs)
                ui.flash(f"Live time is now {workflow.now_label(LW)} · nothing simulated")
            except (ValueError, services.Denied) as e:
                st.error(str(e))
    kit.label("Simulation clock · demo scenario only")
    if SC is None:
        st.info("The simulation clock runs only inside the demo scenario, so simulated attendance and no-shows never "
                "mix with live records. Load the demo story first.")
    else:
        st.markdown(kit.pill(f"Demo scenario · {workflow.now_label(SC)}", "Warning"), unsafe_allow_html=True)
        st.caption("Moving the simulated clock starts shifts in the scenario. At each start the simulated attendance "
                   "system records 'in' or a no-show; each no-show raises one alert for the agent's team lead (in the "
                   "scenario). Agents who already checked in are not re-checked. Demo-login agents never no-show.")
        rate = st.slider("No-show rate", 0, 20, 5, format="%d%%", key="sim_rate") / 100
        to_morning = (((SC.today + 1) * 48 + 16 - SC.now_t) if SC.now_t % 48 >= 16 else (SC.today * 48 + 16 - SC.now_t)) / 2
        end = SC.now_t >= SC.req.shape[1] - 1
        for col, (lbl, hrs) in zip(st.columns(4), [("+1 h", 1), ("+4 h", 4), ("Next 08:00", to_morning), ("+1 day", 24)]):
            if col.button(lbl, width="stretch", disabled=end, key=f"sim_{lbl}"):
                try:
                    with st.spinner("Advancing…"), ui.mutate(SC):
                        res = services.advance_clock(SC, ui.user(), hrs, protected=ui.DEMO_AGENTS, rate=rate)
                    st.session_state["sim_result"] = {"time": workflow.now_label(SC), "hours": res["hours"],
                                                      "started": res["started"], "attended": res["attended"],
                                                      "no_shows": [f"{ui.name(i.absent[0])} · {i.owner_team}"
                                                                   for i in res["no_shows"]]}
                    st.rerun()
                except (ValueError, services.Denied) as e:
                    st.error(str(e))
        r = st.session_state.get("sim_result")
        if r:
            kit.kpis([{"label": "Simulated now", "value": r["time"], "sub": f"+{r['hours']:g} h"},
                      {"label": "Shifts started (simulated)", "value": r["started"]},
                      {"label": "Attended (simulated)", "value": r["attended"]},
                      {"label": "No-shows (simulated)", "value": len(r["no_shows"]),
                       "status": "Warning" if r["no_shows"] else "Normal"}])
            st.caption("Alert sent in the scenario: " + "; ".join(r["no_shows"]) if r["no_shows"]
                       else "No no-shows in this step.")
        if end:
            st.caption("End of the planning fortnight · reload the demo story to start again")

# ================================================================== Import (live)


@st.dialog("Confirm", on_dismiss=ui.close_card)
def confirm_card(what):
    if what == "story":
        st.markdown("**Load the demo story into the sandbox?**")
        st.caption("Creates a new demo scenario from a copy of the live workspace. The published roster and live "
                   "records are not changed." + (" The current scenario is replaced." if SC else ""))
    else:
        st.markdown("**Unload the demo scenario?**")
        st.caption("Removes the sandbox. Live records are not affected. The scenario's audit, notifications and "
                   "messages stay in history under its 'sim-' run id.")
    ok = st.checkbox("I understand", key="cf_ok")
    if st.button("Confirm", type="primary", disabled=not ok):
        try:
            with st.spinner("Working…"):
                if what == "story":
                    sc = services.load_demo_story(LW, ui.user())
                    ui.set_scenario(sc)
                    st.session_state["ctx"] = "scenario"
                else:
                    services.unload_demo(LW, ui.user(), SC)
                    ui.set_scenario(None)
                    st.session_state["ctx"] = "live"
            st.session_state.pop("sim_result", None)
            ui.close_card()
            ui.flash("Demo scenario loaded · you are now viewing it (switch in the sidebar)" if what == "story"
                     else "Demo scenario unloaded")
        except (ValueError, services.Denied) as e:
            st.error(str(e))


if (ref := ui.card("confirm")) is not None:
    confirm_card(ref)
else:
    ui.shared_cards()
