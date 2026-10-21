"""Shared Streamlit plumbing: the live workspace, sign-in, contextual cards and shared decision card."""
from __future__ import annotations

import threading
from datetime import datetime
from contextlib import contextmanager

import numpy as np
import pandas as pd
import streamlit as st

import ui_kit as kit
from shiftsense import assistant, auth, comms, evidence, history, priority, services, store, workflow
from shiftsense.config import day_label, interval_label, intervals_per_day, load_policy, queue_names
from shiftsense.roster import coverage

DEMO_AGENTS = {"A01", "A02", "A06"}     # agents with a demo login never no-show on their own
REQ_STATUS = {"Green": "Normal", "Amber": "Warning", "Red": "Critical"}
REQ_LABEL = {"Green": "Covered", "Amber": "Thin", "Red": "Short"}


# ------------------------------------------------------------------ workspace
@st.cache_resource(show_spinner="Loading the workspace…")
def base() -> dict:
    return evidence.build_base(load_policy())


def fresh_world() -> workflow.World:
    b = base()
    return workflow.new_world(load_policy(), b["data"], b["req"], b["plans"]["ShiftSense optimizer"]["roster"],
                              fc=b["fc"])


@st.cache_resource(show_spinner="Opening the workspace…")
def _live_slot() -> dict:
    w = store.load()
    if w is None:
        w = fresh_world()
        store.save(w)
        history.record_baseline(w, "Roster published", "Analyst")
    w = workflow.upgrade(w)
    w.lock = threading.Lock()
    return {"w": w}


def live_world() -> workflow.World:
    """The LIVE workspace shared by everyone signed in: the published roster and genuine records. If another app
    server saved a newer version, this server reloads it first (several servers can share one database)."""
    slot = _live_slot()
    w = slot["w"]
    try:
        newer = store.version() > getattr(w, "db_version", 0)
    except Exception:                                   # database briefly unreachable: keep the copy in memory
        newer = False
    if newer:
        fresh = store.load()
        if fresh is not None:
            fresh = workflow.upgrade(fresh)
            fresh.lock = w.lock
            slot["w"] = w = fresh
    return w


live_world.clear = lambda: _live_slot.clear()           # same API as before for resets


@st.cache_resource(show_spinner=False)
def _scenario_slot() -> dict:
    """The DEMO SCENARIO sandbox (or None). Stored in its own slot; never written to the live slot."""
    sc = store.load(slot="scenario")
    if sc is not None:
        sc = workflow.upgrade(sc)
        sc.lock = threading.Lock()
    return {"w": sc}


def scenario() -> workflow.World | None:
    return _scenario_slot()["w"]


def context() -> str:
    """'scenario' when this session chose to view the loaded demo scenario, else 'live'."""
    try:
        ctx = st.session_state.get("ctx", "live")
    except Exception:
        ctx = "live"
    return "scenario" if ctx == "scenario" and scenario() is not None else "live"


def world() -> workflow.World:
    """The workspace this session is looking at (live by default)."""
    return scenario() if context() == "scenario" else live_world()


def set_scenario(sc: workflow.World | None, event: str = "Demo scenario loaded"):
    slot = _scenario_slot()
    if sc is None:
        store.clear(slot="scenario")
        slot["w"] = None
        return
    sc.lock = threading.Lock()
    store.save(sc, slot="scenario")
    slot["w"] = sc
    history.record_baseline(sc, event, me())


@contextmanager
def mutate(target: workflow.World | None = None):
    """Serialise changes to the workspace being viewed (or `target`), save them to its own slot and record
    history. All-or-nothing, and a change that would introduce a hard-rule violation is refused."""
    w = target or world()
    with w.lock:
        try:
            with workflow.transaction(w):
                before, audit_from = w.roster.copy(), len(w.audit.records)
                yield w
                store.save(w, slot="scenario" if w.scenario else "live",
                           expected=getattr(w, "db_version", None))          # refused if another server saved first
        except (ValueError, services.Denied):
            raise                                   # business refusal: shown to the user, nothing changed
        except store.WorkspaceConflict:
            (_live_slot if not w.scenario else _scenario_slot).clear()   # reload the newer copy on the next run
            raise ValueError("Someone else changed this workspace a moment ago. The screen now shows the latest "
                             "version; please check and do it again. Nothing was changed.")
        except Exception:
            from shiftsense.logs import get
            u = user() or {}
            get().exception("action failed and was rolled back user=%s run=%s", u.get("username"), w.run_id)
            raise
        history.record(w, before, audit_from, me())


def reset_world(new: workflow.World | None = None, event: str = "Workspace reset"):
    """Replace the LIVE workspace (used by import and by tests). The change history is kept.
    One atomic write replaces the saved workspace; if it fails, the old workspace stays saved and in memory."""
    new = new or fresh_world()
    store.save(new)                     # INSERT OR REPLACE: never an empty gap between old and new
    live_world.clear()
    history.record_baseline(new, event, me())


def policy() -> dict:
    return world().policy


# ------------------------------------------------------------------ session / access
def user() -> dict | None:
    return st.session_state.get("user")


def me() -> str:
    u = user()
    return f"{u['name']} ({u['role_label']})" if u else "system"


def can(permission: str) -> bool:
    return auth.can(user(), permission)


# Single-screen roles get an 'On this page' list (always-present sections only).
ON_PAGE = {"me": [("shifts", "My shifts"), ("inbox", "Inbox"), ("requests", "My requests"), ("history", "My history")],
           "team": [("attendance", "Attendance today"), ("priorities", "Top priorities"), ("alerts", "All alerts"),
                    ("requests", "Time-off and swaps"), ("data", "Team data"), ("history", "Team history")],
           "ops": [("plan", "Plan vs forecast"), ("now", "Operations now"), ("decide", "Decide first"), ("intervene", "Where to intervene"),
                   ("variance", "Staffing variance"), ("performance", "Recovery performance"), ("cost", "Cost"),
                   ("data", "Data approvals"), ("history", "Operations history")]}


ON_PAGE_SCREEN = {"me": "My work", "team": "My team", "ops": "Operations"}


def photos(ids) -> dict:
    """employee_id -> photo data URI for the people this user may see (server-checked); others show initials."""
    try:
        return services.photo_uris(world(), user(), list(ids))
    except Exception:                       # photos are optional: never break a screen over one
        return {}


def own_avatar(u: dict) -> str:
    ids = [x for x in (u.get("agent_id"), u.get("employee_id")) if x]
    ph = photos(ids) if ids else {}
    return kit.avatar(u["name"], next((ph[i] for i in ids if i in ph), None), "md")


def person(agent_id: str, meta: str = "", size: str = "sm", uris: dict | None = None) -> str:
    """Avatar + name for one agent (photo when permitted, else initials)."""
    uris = uris if uris is not None else photos([agent_id])
    return kit.person(pname(agent_id) or agent_id, meta, uris.get(agent_id), size)


def area(page_key: str) -> str:
    """The area of a multi-area screen this page was opened as (first area by default)."""
    from shiftsense import navigation as nav
    key, areas = nav.AREAS[page_key]
    names = [a for a, _ in areas]
    cur = st.session_state.get(key)
    return cur if cur in names else names[0]


def crumb() -> str:
    """'My team › Attendance' for the current screen and area (empty for single screens)."""
    from shiftsense import navigation as nav
    k = st.session_state.get("_page_key")
    if k not in nav.AREAS:
        return ""
    return f"{nav.PAGES[k][1]} › {area(k)}"


def page(key: str):
    """Every screen starts here: theme, access check, sidebar, pending confirmation."""
    kit.theme()
    u = user()
    if not u or not auth.can_view(u, key):
        st.error("You don't have access to this screen.")
        st.stop()
    st.session_state["_page_key"] = key
    sidebar()
    msg = st.session_state.pop("flash", None)
    if msg:
        st.toast(msg)
        st.success(msg)


def flash(msg: str):
    """Confirm after the rerun that refreshes the screen (only called once an action succeeded)."""
    st.session_state["flash"] = msg
    st.rerun()


CTX_LABEL = {"live": "Live workspace", "scenario": "Demo scenario"}


def switch_context(target: str):
    """Deliberate switch between the live workspace and the demo scenario (view only; nothing is changed).
    The current page is kept; cards that belong to the other workspace are closed."""
    if target == "scenario" and scenario() is None:
        return
    st.session_state["ctx"] = target
    close_card()
    st.session_state["flash"] = ("Now viewing the DEMO SCENARIO · simulated records only" if target == "scenario"
                                 else "Now viewing the LIVE workspace")


def sidebar():
    """Compact, role-specific sidebar: navigation (drawn by st.navigation above this), workspace context,
    the relevant clock, unpublished drafts, and a small account block. Collapsing is done by Streamlit's single
    built-in control at the top of the sidebar (named 'Collapse navigation' / 'Open navigation'); on phones the
    sidebar is a drawer."""
    from datetime import datetime
    u = user()
    w = world()
    with st.sidebar:
        ctx = "Demo scenario" if w.scenario else "Live workspace"
        clock = "Simulation clock" if w.scenario else "Live clock"
        drafts = [x for x in live_world().plans if x["status"] in ("draft", "validated")] if can("replan") else []
        st.markdown(
            f'<div class="ss-side ss-side-ctx" role="group" aria-label="Workspace">'
            f'<div class="ss-side-k">Workspace</div>'
            f'<div>{kit.pill(ctx, "Warning" if w.scenario else "Info")} '
            f'<span class="ss-side-tag">{"simulated" if w.scenario else "synthetic data"}</span></div>'
            f'<div class="ss-side-k">{clock}</div><div class="ss-side-v">{kit.esc(workflow.now_label(w))}</div>'
            + "".join(f'<div class="ss-side-k">Unpublished draft</div><div class="ss-side-v">{kit.esc(d["id"])} · '
                      f'{kit.esc(d["status"])} · not live</div>' for d in drafts[:1])
            + '</div>', unsafe_allow_html=True)
        if scenario() is not None:
            target = "live" if w.scenario else "scenario"
            st.button("Switch to live workspace" if w.scenario else "Switch to demo scenario",
                      key="ctx_switch", width="stretch", on_click=switch_context,
                      args=(target,), help="Changes what you are looking at. Nothing is copied or changed.")
        st.markdown(
            f'<div class="ss-side ss-side-acct" role="group" aria-label="Account">'
            + own_avatar(u) +
            f'<div><div class="ss-side-name">{kit.esc(u["name"])}</div>'
            f'<div class="ss-side-role">{kit.esc(u["role_label"])}{" · " + kit.esc(u["team"]) if u.get("team") else ""}'
            f'</div></div></div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        if c1.button("Refresh", key="refresh", width="stretch",
                     help="Load changes other people made since this screen was drawn"):
            st.rerun()
        if c2.button("Sign out", key="signout", width="stretch"):
            sign_out()
        email_settings(w, u)
        password_settings(w, u)
        theme_settings(u)
        st.markdown(f'<div class="ss-side-upd">Updated {datetime.now():%H:%M:%S} · screens update when you act or '
                    'press Refresh</div>', unsafe_allow_html=True)


def sign_out():
    sso = bool(st.session_state.get("sso"))
    for k in list(st.session_state.keys()):
        del st.session_state[k]
    if sso:
        st.logout()                     # also ends the single sign-on session in this browser
    st.rerun()


def theme_settings(u):
    """Appearance: System, Light or Dark, kept on the account so it follows the person to any device."""
    from shiftsense import accounts as acc_mod
    saved = (acc_mod.get(u["username"]) or {}).get("theme_pref") or "System"
    if "theme_synced" not in st.session_state:          # first screen after sign-in: apply the saved choice once
        st.session_state["theme_synced"] = saved
        if saved != "System" or kit.mode() == "dark":
            kit.apply_theme(saved, f"sync-{u['username']}")
    pick = st.segmented_control("Theme", list(acc_mod.THEMES), default=saved, key="theme_pick",
                                help="Display only. Saved to your account.")
    if pick and pick != saved:
        services.set_own_theme(u, pick)
        st.session_state["theme_synced"] = pick
        kit.apply_theme(pick, f"{pick}-{datetime.now():%H%M%S%f}")


def password_settings(w, u):
    """Change own password (not shown for single sign-on sessions: the company account is managed there)."""
    if st.session_state.get("sso"):
        return
    from shiftsense import security
    with st.popover("Password", icon=":material/key:", width="stretch"):
        st.caption("At least 12 characters with 3 of: lower-case, upper-case, digit, symbol. Not your name or "
                   "username.")
        cur = st.text_input("Current password", type="password", key="pw_cur")
        new = st.text_input("New password", type="password", key="pw_new")
        new2 = st.text_input("New password again", type="password", key="pw_new2")
        if st.button("Change password", key="pw_go"):
            try:
                if new != new2:
                    raise ValueError("The two new passwords differ.")
                services.change_own_password(w, u, cur, new)
                flash("Password changed")
            except (ValueError, services.Denied) as e:
                st.error(str(e))


def email_settings(w, u):
    """Own email address: status and verification. In-app messages always reach the person regardless."""
    from shiftsense import accounts as acc_mod, mailer
    acct = acc_mod.get(u["username"]) or {}
    addr, ok = acct.get("email", ""), acct.get("email_verified", False)
    label = "Email: verified" if addr and ok else ("Email: unverified" if addr else "Email: none")
    with st.popover(label, icon=":material/mail:", width="stretch"):
        st.caption("ShiftSense messages always arrive in-app. Email is an optional copy.")
        st.markdown(f"**Address:** {kit.esc(acc_mod.mask_email(addr))} · "
                    f"{'verified' if ok else 'not verified' if addr else 'none on your account'}  \n"
                    f"**Preference:** {acc_mod.CONTACT.get(acct.get('contact_pref'), '—')}  \n"
                    f"**Server:** {mailer.status_text()}")
        if not addr:
            st.caption("Ask an administrator to add your work email.")
        elif not ok and not mailer.configured():
            st.caption("The address can be verified once email is configured on the server.")
        elif not ok:
            if st.button("Send code", key="em_send"):
                try:
                    services.request_email_code(w, u)
                    st.success("Code sent. It is valid for 30 minutes.")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))
            code = st.text_input("6-digit code", key="em_code", max_chars=6)
            if st.button("Confirm", key="em_ok") and code:
                try:
                    services.confirm_email_code(w, u, code)
                    st.success("Email verified.")
                except (ValueError, services.Denied) as e:
                    st.error(str(e))


# ------------------------------------------------------------------ contextual cards
def open_card(kind: str, ref):
    st.session_state["card"] = (kind, ref)


def close_card():
    st.session_state.pop("card", None)


def card(kind: str):
    c = st.session_state.get("card")
    return c[1] if c and c[0] == kind else None


# ------------------------------------------------------------------ lookups
def agents() -> pd.DataFrame:
    return world().data["agents"].set_index("agent_id")


def name(agent_id: str) -> str:
    a = agents()
    return f"{a.loc[agent_id, 'name']} ({agent_id})" if agent_id in a.index else agent_id


def pname(agent_id: str) -> str:
    """Plain name without the ID (for avatars and initials)."""
    a = agents()
    return str(a.loc[agent_id, "name"]) if agent_id in a.index else ""


def team_members(team: str) -> list[str]:
    a = agents()
    return a[a.team == team].index.tolist()


def team_of(agent_id: str) -> str:
    return agents().loc[agent_id, "team"]


def days_ahead(n: int | None = None) -> list[int]:
    w = world()
    last = w.policy["horizon"]["days"]
    return list(range(w.first_day, last if n is None else min(last, w.first_day + n)))


def dlabel(d: int) -> str:
    return day_label(policy(), d)


def tlabel(t: int | None) -> str:
    """Simulated time label for an interval index (deadlines)."""
    if t is None:
        return "—"
    per_day = intervals_per_day(policy())
    return f"{dlabel(t // per_day)} {interval_label(policy(), t % per_day)}"


def due_text(t: int | None) -> tuple[str, str]:
    """('due Tue 06 Oct 20:00 · in 2 h', status) relative to the simulated clock."""
    if t is None:
        return "no deadline", "Neutral"
    left = (t - world().now_t) / 2
    if left < 0:
        return f"overdue since {tlabel(t)}", "Critical"
    return f"due {tlabel(t)} · in {left:g} h", "Warning" if left <= 2 else "Info"


def shift_row(agent_id: str, day: int):
    ro = world().roster
    r = ro[(ro.agent_id == agent_id) & (ro.day == day)]
    return None if r.empty else r.iloc[0].to_dict()


def shift_label(r: dict | None) -> str:
    if r is None:
        return "Off"
    txt = f"{workflow.shift_text(policy(), r)} · {r['queue']}"
    if r.get("alt_queue"):
        txt += f" (helps {r['alt_queue']} {interval_label(policy(), r['alt_start'])}–{interval_label(policy(), r['alt_end'])})"
    return txt


def shift_hours(r: dict) -> tuple[float, float]:
    s = policy()["shifts"][r["shift"]]
    return s["start"] - r.get("ext_before", 0), s["start"] + s["span"] + r.get("ext_after", 0)


def unavailable(agent_id: str, day: int) -> str | None:
    lv = world().data["leave"]
    hit = lv[(lv.agent_id == agent_id) & (lv.day == day)]
    return None if hit.empty else str(hit.iloc[0].get("reason", "leave"))


def on_shift_now(agent_id: str) -> dict | None:
    w = world()
    for r in w.roster[w.roster.agent_id == agent_id].to_dict("records"):
        rng = workflow.shift_intervals(w.policy, r["day"], r["shift"], r["ext_before"], r["ext_after"])
        if rng.start <= w.now_t < rng.stop:
            return r
    return None


# ------------------------------------------------------------------ coverage
def day_status(day: int, queues: list[str] | None = None) -> pd.DataFrame:
    """Per queue: share of needed time covered, short people-hours, worst gap."""
    w, p = world(), policy()
    per_day = intervals_per_day(p)
    cov = coverage(p, w.roster)
    rows = []
    for qi, q in enumerate(queue_names(p)):
        if queues and q not in queues:
            continue
        sl = slice(day * per_day, (day + 1) * per_day)
        r, c = w.req[qi, sl], cov[qi, sl]
        short = np.clip(r - c, 0, None)
        worst = int(short.max())
        pct = 100 * np.minimum(r, c).sum() / max(r.sum(), 1)
        first = int(np.argmax(short > 0)) if worst else None
        rows.append({"Status": kit.coverage_status(pct), "Day": dlabel(day), "Queue": q, "Covered %": round(pct, 1),
                     "Short (people-h)": short.sum() / 2,
                     "Worst gap": f"−{worst} at {interval_label(p, sl.start + first)}" if worst else "—"})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ incident states and decision card
def incident_state(inc) -> tuple[str, str]:
    """(label, status) using one vocabulary across roles."""
    waiting = any(s in ("waiting", "offered") for s in inc.action_status.values()) or \
        any(o["status"] == "open" for o in inc.offers)
    return {
        "open": ("Recommended · awaiting approval", "Warning"),
        "needs_new_fix": ("Declined · new fix ready", "Critical"),
        "fixed": ("Awaiting agent confirmation", "Info") if waiting else ("Resolved", "Normal"),
        "escalated": ("Escalated to Ops", "Critical"),
        "closed": (f"Closed by Ops · {inc.residual_ph:g} people-h still uncovered", "Warning")
        if inc.residual_ph else ("Closed by Ops", "Neutral"),
        "no_gap": ("No action needed", "Normal"),
    }.get(inc.status, (inc.status, "Neutral"))


def incident_title(inc) -> str:
    if inc.kind in ("spike", "requirement"):
        return inc.title
    who = ", ".join(agents().loc[a, "name"] if a in agents().index else a for a in inc.absent)
    what = {"no_show": "no-show", "left_early": "left early", "data_change": "shift released by a data change"
            }.get(inc.kind, "absent")
    return f"{who} · {what}"


def _step_state(s):
    if s and s.startswith("taken by"):
        return ("Confirmed · " + s, "Normal")
    return {"waiting": ("Awaiting agent", "Info"), "confirmed": ("Confirmed", "Normal"), "declined": ("Declined", "Critical"),
            "done": ("Done", "Normal"), "offered": ("Offered", "Info")}.get(s, ("—", "Neutral"))


def _before_after(inc, queue: str):
    w = world()
    ctx = workflow._ctx(w, inc)
    qi = ctx.queues.index(queue)
    per_day = intervals_per_day(w.policy)
    t = np.arange(inc.day * per_day + 12, min(w.req.shape[1], (inc.day + 1) * per_day + 14))
    after = None
    if inc.status in ("open", "needs_new_fix") and inc.rec["actions"]:
        after = coverage(w.policy, inc.rec["eval"]["roster"])[qi, t]
    x = [interval_label(w.policy, i) for i in t]
    return kit.supply_demand(x, w.req[qi, t], ctx.cur[qi, t], after=after,
                             title=f"{queue} · {dlabel(inc.day)} · people per 30 min", height=260)


def decision_card(inc, can_decide: bool, key: str):
    """Problem -> Action -> Outcome, comparison, staffing before/after and the decision, in one card."""
    w = world()
    a = agents()
    s = priority.assess(w, inc)
    label, status = incident_state(inc)
    st.markdown(f'<div class="ss-row">{kit.pill(s["severity"], s["severity"])} {kit.pill(label, status)} '
                f'<span class="ss-muted">{kit.esc(dlabel(inc.day))} · {kit.esc(inc.id)} · team {kit.esc(inc.owner_team)}'
                f'</span></div>', unsafe_allow_html=True)
    st.markdown(f"#### {incident_title(inc)}")
    _out = list(dict.fromkeys(inc.absent))
    _in = list(dict.fromkeys(x.agent_id for x in (inc.rec or {}).get("actions", []) if x.agent_id not in _out))
    if _out or _in:                      # who is missing and who the fix would involve (photo or initials)
        _ph = photos(_out + _in)
        st.markdown('<div class="ss-row" style="gap:18px">'
                    + "".join(f'<div>{kit.person(pname(x) or x, "Missing", _ph.get(x), "sm")}</div>' for x in _out)
                    + "".join(f'<div>{kit.person(pname(x) or x, "In the proposed fix", _ph.get(x), "sm")}</div>'
                              for x in _in[:6]) + "</div>", unsafe_allow_html=True)
    gaps = workflow.gap_summary(w, inc)
    rec, ev = inc.rec, inc.rec["eval"]
    kit.kpis([
        {"label": "Gap (people-h)", "value": f"{s['short_ph']:g}", "status": s["severity"] if s["short_ph"] else "Normal"},
        {"label": "SLA exposure", "value": f"{s['exposure']:g}", "help": "people-hours × queue SLA sensitivity"},
        {"label": "Starts in", "value": "—" if s["hours_left"] is None else f"{s['hours_left']:g} h", "sub": s["starts"]},
        {"label": "Fix cost", "value": kit.money(ev["cost"]) if rec["actions"] else "—"},
        {"label": "Gap covered", "value": f"{ev['restored_pct']:.0f}%" if rec["actions"] else "—"},
    ])
    decidable = inc.status in workflow.DECIDABLE
    if decidable:
        problem = "<br>".join(kit.esc(g) for g in gaps) or "No gap left"
        steps = "<br>".join(kit.esc(x.description) for x in rec["actions"]) or "No feasible action — escalate"
        resid = ev["short_head_hours_after"]
        outcome = (f"{ev['restored_pct']:.0f}% of gap covered · {kit.money(ev['cost'])} · {ev['agents']} people"
                   + (f"<br>{resid:g} people-h left → Ops" if resid else "<br>No gap left"))
        kit.flow(problem, steps, outcome)
    elif gaps and inc.status != "closed":
        st.markdown(kit.pill("Still uncovered", "Warning") + " " + kit.esc("; ".join(gaps)), unsafe_allow_html=True)

    queues = s["queues"] or sorted({x.queue for x in inc.applied})
    if queues:
        q = queues[0] if len(queues) == 1 else st.segmented_control("Queue", queues, default=queues[0], key=f"{key}_q")
        q = q or queues[0]
        w_ = world()
        ctx_ = workflow._ctx(w_, inc)
        qi_ = ctx_.queues.index(q)
        per_ = intervals_per_day(w_.policy)
        tt = np.arange(inc.day * per_ + 12, min(w_.req.shape[1], (inc.day + 1) * per_ + 14))
        rows_, sch_ = ["Now (scheduled)"], [ctx_.cur[qi_, tt]]
        if inc.status in ("open", "needs_new_fix") and inc.rec["actions"]:
            rows_.append("After proposed fix")
            sch_.append(coverage(w_.policy, inc.rec["eval"]["roster"])[qi_, tt])
        agg_ = kit.matrix_bins(np.vstack([w_.req[qi_, tt]] * len(rows_)), np.vstack(sch_), 2)
        cols_ = [interval_label(w_.policy, int(tt[b * 2])) for b in range(agg_["req"].shape[1])]
        kit.chart(kit.coverage_matrix(rows_, cols_, agg_),
                  f"Staffing for {q} on {dlabel(inc.day)} by hour: scheduled / required people now"
                  + (" and after the proposed fix (not yet approved)." if len(rows_) > 1 else "."),
                  key=f"{key}_mx", provenance="simulated", title="Coverage now and after the proposed fix",
                  subtitle=f"{q} · {dlabel(inc.day)} · each cell: scheduled / required people per hour")
        st.markdown(kit.matrix_legend(), unsafe_allow_html=True)
        with st.expander("Trend chart"):
            kit.chart(_before_after(inc, q),
                      f"Staffing for {q} on {dlabel(inc.day)}: dashed line is required people, blue is scheduled now"
                      + (", green is after the recommended fix." if decidable and rec["actions"] else "."),
                      key=f"{key}_chart", provenance="simulated", title="Required vs scheduled over the day",
                      subtitle=f"{q} · {dlabel(inc.day)} · people per 30 minutes")

    if decidable and rec["actions"]:
        kit.label("Options compared")
        opts = [("Recommended", ev)] + list(rec["alternatives"].items())
        cmp = pd.DataFrame([{
            "Option": n, "Gap covered %": round(e["restored_pct"]), "Cost ₹": round(e["cost"]),
            "OT hours": e["ot_hours"], "People": e["agents"], "Queues touched": e["queues"],
            "Left uncovered (people-h)": e["short_head_hours_after"]} for n, e in opts])
        st.dataframe(cmp, hide_index=True, width="stretch")
        st.caption(rec["why_not"])
        kit.label("Steps")
        donors = sorted({x.from_queue for x in rec["actions"] if x.kind == "redeploy" and x.from_queue})
        st.dataframe(pd.DataFrame([{
            "Action": x.description, "Person": name(x.agent_id), "Trained for": ", ".join(a.loc[x.agent_id, "eligible"]),
            "Needs agent OK": "Yes" if x.kind in workflow.CONFIRM_KINDS else "No",
            "Cost ₹": round(x.cost), "Status": "Proposed · not approved"} for x in rec["actions"]]),
            hide_index=True, width="stretch")
        if donors:
            st.caption(f"{', '.join(donors)} stay at or above their own requirement (protected).")
        st.caption(f"Confidence: {rec['confidence']} · {rec['n_feasible']} rule-checked candidates")
    elif inc.applied or inc.offers:
        kit.label("Changes and confirmations")
        rows = [{"Action": x.description, "Person": name(x.agent_id),
                 "Status": _step_state(inc.action_status.get(x.id))[0]} for x in inc.applied]
        for o in inc.offers:
            x = o["actions"][0]
            rows.append({"Action": f"Open {x.shift} shift on {x.queue}",
                         "Person": ", ".join(y.agent_id for y in o["actions"]),
                         "Status": {"open": "Offered · first to accept", "filled": f"Taken by {o['filled_by']}",
                                    "unfilled": "Nobody accepted"}[o["status"]]})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if inc.note:
        st.caption(inc.note)

    if decidable and rec["actions"]:
        with st.expander("Why this fix · trade-offs", expanded=False):
            st.markdown(assistant.explain_fix(fix_context(inc)).text)
            st.caption("From the recovery engine's own figures (no AI) · you decide")

    if decidable and can_decide:
        kit.label("Decision")
        opts = ["Approve", "Modify", "Reject", "Escalate"] if rec["actions"] else ["Escalate"]
        choice = st.segmented_control("Decision", opts, default=opts[0], key=f"{key}_c",
                                      label_visibility="collapsed") or opts[0]
        st.caption({"Approve": "Applies the fix; agents asked to confirm.",
                    "Modify": "Remove steps; a new rule-checked fix is computed.",
                    "Reject": "Rejects these steps; another fix is proposed.",
                    "Escalate": "Sends the alert to Operations."}[choice])
        remove = []
        if choice == "Modify":
            remove = st.multiselect("Steps to remove", [x.id for x in rec["actions"]],
                                    format_func=lambda i: next(x.description for x in rec["actions"] if x.id == i),
                                    key=f"{key}_rm")
        reason = st.text_input("Reason" + ("" if choice == "Approve" else " (required)"), key=f"{key}_r")
        if st.button(choice, type="primary", key=f"{key}_go"):
            try:
                with mutate():
                    services.decide_incident(w, user(), inc.id, choice, reason, remove)
                nxt = {"fixed": "agents notified", "escalated": "sent to Operations",
                       "open": "new fix ready for review", "needs_new_fix": "new fix ready for review",
                       "no_gap": "no gap left"}
                if choice not in ("Modify", "Reject") or inc.status not in workflow.DECIDABLE:
                    close_card()
                flash(f"{choice} recorded · {nxt.get(inc.status, inc.status)}")
            except (ValueError, services.Denied) as e:
                st.error(str(e))
    if can_decide or can("ops_decide"):
        with st.expander("Conversation"):
            thread_view(f"incident:{inc.id}", key=f"{key}_thr")
    with st.expander("History"):
        for h in inc.history:
            st.caption(h)


# ------------------------------------------------------------------ AI assistant
@st.cache_resource(show_spinner=False)
def ai_client():
    key = None
    try:
        key = st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        pass
    return assistant.get_client(key)


def ai_note(source: str):
    st.caption("AI-generated from data on this screen · you decide" if source == "ai"
               else "Answered from this screen's data (no AI)")


def ask_box(persona: str, context_fn, key: str, placeholder: str):
    with st.popover("Ask ShiftSense", icon=":material/auto_awesome:"):
        q = st.text_input("Question", key=f"{key}_q", placeholder=placeholder)
        if st.button("Ask", key=f"{key}_go") and q.strip():
            with st.spinner("Thinking…"):
                st.session_state[f"{key}_a"] = assistant.ask(persona, q, context_fn(), ai_client())
        ans = st.session_state.get(f"{key}_a")
        if ans:
            st.markdown(ans.text)
            ai_note(ans.source)


def fix_context(inc) -> dict:
    w = world()
    rec = inc.rec
    ev = rec["eval"]
    a = agents()
    return {
        "situation": incident_title(inc) + f" on {dlabel(inc.day)}",
        "gaps": workflow.gap_summary(w, inc),
        "steps": [{"what": x.description, "person": name(x.agent_id), "trained_for": a.loc[x.agent_id, "eligible"],
                   "cost": f"₹{round(x.cost):,}" if x.cost else "no extra cost"} for x in rec["actions"]],
        "covered_pct": ev["restored_pct"], "cost": ev["cost"], "ot_hours": ev["ot_hours"],
        "people_asked": ev["agents"], "why": rec["why_not"],
        "uncovered": rec["escalation"]["residual"] if rec["escalation"] else [],
        "other_options": {k: f"covers {v['restored_pct']:.0f}% for ₹{v['cost']:,.0f}" for k, v in rec["alternatives"].items()},
    }


# ------------------------------------------------------------------ application shell
def shell(title: str, subtitle: str = "", right: str = "", open_kinds: tuple = ()):
    """Top bar on every screen: page title, context, status chips and the notification centre."""
    w, u = world(), user()
    try:
        services.sweep_overdue(w)
    except Exception:
        pass
    if not w.scenario and getattr(w, "ref_linked", False):     # active situations touching today
        try:
            from shiftsense import refdata
            today = refdata.iso_of_day(w.policy, w.today)
            act = [s for s in refdata.current("situations").values() if (s.get("status") or "Active") == "Active"
                   and str(s["start"])[:10] <= today and (not s.get("end") or str(s["end"])[:10] >= today)]
            if act:
                st.markdown('<div role="status" class="ss-banner">' + " · ".join(
                    f"<b>{kit.esc(s['title'])}</b> ({kit.esc(s.get('end') and 'until ' + s['end'] or 'open, no end yet')})"
                    for s in act[:3]) + "</div>", unsafe_allow_html=True)
        except Exception:
            pass
    if not w.scenario and getattr(w, "ref_linked", False):     # changes staged by a server drop-folder run
        try:
            from shiftsense import dataflow
            if dataflow.sets(status="queued"):
                with mutate(w):
                    services.apply_queued(w)
        except Exception:
            from shiftsense.logs import get
            get().exception("applying queued data changes failed")
    notes = comms.notices_for(u, w.run_id, w.now_t)
    unread = [n for n in notes if not n["read"] and n["status"] != "resolved"]
    if w.scenario:
        st.markdown('<div role="status" class="ss-banner"><b>DEMO SCENARIO · SIMULATED</b> — you are viewing the '
                    f'demo sandbox (run {kit.esc(w.run_id)}). Everything here is simulated; nothing you do here changes '
                    'the published roster or live records. Switch to <b>Live</b> in the sidebar.</div>',
                    unsafe_allow_html=True)
    c1, c2 = st.columns([5, 2], vertical_alignment="bottom")
    with c1:
        kit.header(title, subtitle, (right + " " if right else "")
                   + ('<span class="ss-sim">Demo scenario</span>' if w.scenario else
                      '<span class="ss-sim" title="All figures are synthetic">Synthetic data</span>'), crumb=crumb())
    with c2:
        label = f"Notifications · {len(unread)}" if unread else "Notifications"
        with st.popover(label, icon=":material/notifications:", width="stretch",
                        help=f"{len(unread)} unread notifications",
                        key=f"notif_pop_{st.session_state.get('notif_gen', 0)}"):   # new key = closed after Open
            if not notes:
                st.caption("No notifications.")
            for n in notes[:12]:
                state = ("Overdue", "Critical") if n["overdue"] else \
                    {"action": ("Action required", "Warning"), "resolved": ("Resolved", "Normal"),
                     "acknowledged": ("Acknowledged", "Neutral")}.get(n["status"], ("Unread" if not n["read"] else "Read",
                                                                                     "Info" if not n["read"] else "Neutral"))
                st.markdown((kit.pill("Simulated", "Neutral") + " " if w.scenario else "")
                            + f'{kit.pill(n["priority"], n["priority"] if n["priority"] in kit.STATUS else "Info")} '
                            f'{kit.pill(state[0], state[1])} <b>{kit.esc(n["title"])}</b><br>'
                            f'<span class="ss-muted">{kit.esc(n["sim_time"])} · {kit.esc(n["sender"])}'
                            + (f" · {kit.esc(n['body'][:120])}" if n["body"] else "") + "</span>",
                            unsafe_allow_html=True)
                b1, b2 = st.columns(2)
                kind = n["related_kind"]
                if kind and (kind in open_kinds or kind in ("thread", "case")) and b1.button(
                        "Open", key=f"nt_open_{n['id']}", help=f"Open {kind} {n['related_ref']}"):
                    comms.mark_read(n["id"], u["username"])
                    open_card(kind, int(n["related_ref"]) if kind == "case" else n["related_ref"])
                    st.session_state["notif_gen"] = st.session_state.get("notif_gen", 0) + 1
                    st.rerun()
                if not n["read"] and b2.button("Mark read", key=f"nt_read_{n['id']}"):
                    comms.acknowledge(n["id"], u["username"])
                    st.rerun()


def thread_view(thread_key: str, key: str):
    """Conversation attached to a record. Only participants can read or post (checked on the server)."""
    w, u = world(), user()
    if not services.can_access_thread(w, u, thread_key):
        st.caption("You are not part of this conversation.")
        return
    msgs = comms.thread(w.run_id, thread_key)
    if not msgs:
        st.caption("No messages yet.")
    for m in msgs[-20:]:
        st.markdown(f'<b>{kit.esc(m["sender"])}</b> <span class="ss-muted">{kit.esc(m["sim_time"])}</span><br>'
                    f'{kit.esc(m["body"])}', unsafe_allow_html=True)
    body = st.text_area("Message", key=f"{key}_body", height=80, max_chars=1000)
    if st.button("Send message", key=f"{key}_send"):
        try:
            services.post_message(w, u, thread_key, body)
            st.session_state.pop(f"{key}_body", None)
            flash("Message sent")
        except (ValueError, services.Denied) as e:
            st.error(str(e))


@st.dialog("Conversation", width="large", on_dismiss=close_card)
def thread_card(thread_key):
    st.markdown(f"**{thread_title(thread_key)}**")
    thread_view(thread_key, key=f"tc_{thread_key}")


def thread_title(thread_key: str) -> str:
    kind, ref = thread_key.split(":", 1)
    return {"agent": f"Agent {name(ref)} ↔ team lead", "incident": f"Alert {ref}", "request": f"Request {ref}",
            "case": f"Governance case {ref}"}.get(kind, thread_key)


CASE_STATUS = {"Flagged": "Warning", "Under Review": "Info", "Explanation Requested": "Warning",
               "Referred to Operations": "Warning", "Action Required": "Critical", "Resolved": "Normal",
               "Closed": "Neutral", "Dismissed": "Neutral"}


CASE_NEXT = {"Flagged": ("Admin", "Acknowledge or dismiss"),
             "Under Review": ("Admin", "Request explanation, refer to Ops, or resolve"),
             "Explanation Requested": ("Team lead", "Explain the decisions"),
             "Referred to Operations": ("Operations", "Resolve the referral"),
             "Action Required": ("Operations", "Authorise or decline corrective action"),
             "Resolved": ("Admin", "Close the case"), "Closed": ("—", "None (closed)"),
             "Dismissed": ("—", "None (dismissed)")}


@st.dialog("Governance case", width="large", on_dismiss=close_card)
def case_card(case_id):
    w, u = world(), user()
    c = next((x for x in comms.cases(w.run_id) if x["id"] == case_id), None)
    if c is None:
        st.caption("Case not found.")
        return
    ev = c["evidence"]
    st.markdown(f'{kit.pill(c["status"], CASE_STATUS.get(c["status"], "Info"))} {kit.pill(c["kind"], "Neutral")} '
                f'<span class="ss-muted">case {c["id"]} · {kit.esc(c["sim_time"])} · team {kit.esc(c["team"] or "—")}'
                f'</span>', unsafe_allow_html=True)
    st.markdown(f"#### {c['title']}")
    st.caption(ev.get("note", ""))
    owner, nxt = CASE_NEXT.get(c["status"], ("—", "No further action"))
    if owner == "Team lead" and c["subject_user"]:
        owner = f"Team lead ({c['subject_user']})"
    events = comms.case_events(c["id"])
    ui_due = due_text(c["due_t"])[0] if c["status"] == "Explanation Requested" and c["due_t"] else "—"
    kit.stats([("Owner now", owner), ("Next action", nxt), ("Deadline", ui_due),
               ("Sample", "; ".join(f"{k}: {v}" for k, v in list(ev.get("metrics", {}).items())[:1]) or "—")])
    expl = [e for e in events if e["action"] == "Explanation"]
    if expl:
        with st.container(border=True):
            st.markdown(f"**Explanation from {expl[-1]['actor'].split(' (')[0]}** · {expl[-1]['sim_time']}")
            st.markdown(kit.esc(expl[-1]["note"]))
    kit.kpis([{"label": k, "value": v} for k, v in list(ev.get("metrics", {}).items())[:6]])
    st.dataframe(pd.DataFrame([("Comparison", ev.get("comparison", "")), ("Period", ev.get("period", "")),
                               ("Request types", ", ".join(f"{k}: {v}" for k, v in ev.get("request_types", {}).items()) or "—"),
                               ("Coverage check at request", ", ".join(f"{k}: {v}" for k, v in ev.get("eligibility", {}).items()) or "—"),
                               ("Operational consequence", ev.get("consequence", "")),
                               ("Triggered by", ev.get("threshold", ""))], columns=["Evidence", "Value"]),
                 hide_index=True, width="stretch")
    kit.label("Timeline")
    st.dataframe(pd.DataFrame([{"When": e["sim_time"], "Who": e["actor"], "Action": e["action"],
                                "Status": f"{e['from_status'] or '—'} → {e['to_status']}", "Note": e["note"]}
                               for e in events]), hide_index=True, width="stretch")
    actions = []
    if u["role"] == "admin":
        actions = {"Flagged": ["Acknowledge", "Dismiss", "Add note"],
                   "Under Review": ["Request explanation", "Refer to Operations", "Request corrective action",
                                    "Mark resolved", "Dismiss", "Add note"],
                   "Explanation Requested": ["Add note"], "Referred to Operations": ["Add note"],
                   "Action Required": ["Add note"], "Resolved": ["Close", "Add note"]}.get(c["status"], [])
    elif u["role"] == "team_lead" and c["subject_user"] == u["username"] and c["status"] == "Explanation Requested":
        actions = ["Explain"]
    elif u["role"] == "ops_head":
        actions = {"Referred to Operations": ["Resolve referral"],
                   "Action Required": ["Authorise corrective action", "Decline corrective action"]}.get(c["status"], [])
    if actions:
        kit.label("Action")
        act = st.selectbox("Action", actions, key=f"case_act_{case_id}", label_visibility="collapsed")
        note = st.text_area("Reason / evidence (required)" if act not in ("Acknowledge", "Add note") else "Note",
                            key=f"case_note_{case_id}", height=80)
        if act == "Request corrective action":
            st.caption("Formal corrective action needs Operations authorisation and documented evidence.")
        if st.button("Submit", type="primary", key=f"case_go_{case_id}"):
            try:
                services.case_action(w, u, case_id, act, note)
                flash(f"{act} recorded · case {case_id}")
            except (ValueError, services.Denied) as e:
                st.error(str(e))
    with st.expander("Conversation"):
        thread_view(f"case:{case_id}", key=f"case_thr_{case_id}")


def shared_cards():
    """Cards any screen can open from the notification centre."""
    if (ref := card("thread")) is not None:
        thread_card(ref)
    elif (ref := card("case")) is not None:
        case_card(ref)


@st.cache_data(show_spinner=False)
def evidence_benchmark() -> dict:
    """Reproducible benchmark on the seeded dataset: plan comparison (ablation), the fair same-roster recovery
    comparison, and where every input comes from."""
    res = evidence.run(load_policy(), base())
    ab = res["ablation"]
    keep = ["Approach", "Coverage %", "Under-covered workload %", "Fairness (burden std)", "Hard violations"]
    return {"plans": ab[keep].round(1), "same_roster": res["same_roster"].round(2), "provenance": res["provenance"]}


# ------------------------------------------------------------------ history (shared by all five roles)
def history_view(key: str, intro: str):
    """Scoped, filterable, paginated history. Scope comes from the signed-in account (server side)."""
    from shiftsense import timeline
    w, u = world(), user()
    try:
        rows = services.history(w, u)
    except services.Denied as e:
        st.error(str(e))
        return
    st.caption(intro + " Times are simulated business time; only stored records are shown, nothing is estimated.")
    if not rows:
        st.info("No recorded events in your scope yet.")
        return
    per_day = intervals_per_day(policy())
    days = sorted({e["t"] // per_day for e in rows if e["t"] is not None})
    ranges = {"Last 7 days": (max(0, w.today - 6), w.today), "Today": (w.today, w.today),
              "Whole fortnight": (None, None)}
    c1, c2, c3, c4 = st.columns([2, 2, 2, 1])
    rng = c1.selectbox("Period", list(ranges), key=f"{key}_rng")
    types = c2.multiselect("Type", sorted({e["Type"] for e in rows}), key=f"{key}_types",
                           placeholder="All types")
    text = c3.text_input("Search", key=f"{key}_q", placeholder="agent, record, actor, reason…")
    newest = c4.selectbox("Order", ["Newest", "Oldest"], key=f"{key}_ord") == "Newest"
    d0, d1 = ranges[rng]
    size = 25
    _, total = timeline.query(rows, types=types, text=text, day_from=d0, day_to=d1, per_day=per_day, size=10 ** 6)
    pages = max(1, -(-total // size))
    p = st.number_input("Page", 1, pages, 1, key=f"{key}_page") if pages > 1 else 1
    df, total = timeline.query(rows, types=types, text=text, day_from=d0, day_to=d1, per_day=per_day,
                               newest_first=newest, page=p, size=size)
    st.caption(f"{total} event(s) · page {p} of {pages} · {history.context_of(w.run_id)}"
               + (f" · recorded days in scope: {ui_days(days)}" if days else ""))
    if df.empty:
        st.info("No events match these filters. Try 'Whole fortnight' or clear the search.")
        return
    shown = df[["When", "Type", "Event", "Record", "Actor", "Agents", "Outcome", "Context"]]
    st.dataframe(shown, hide_index=True, width="stretch", key=f"{key}_tbl", on_select="rerun",
                 selection_mode="single-row", height=min(40 + 35 * len(shown), 420))
    sel = st.session_state.get(f"{key}_tbl")
    rows_sel = getattr(getattr(sel, "selection", None), "rows", []) if sel else []
    if rows_sel:
        rec = df.iloc[rows_sel[0]]
        with st.container(border=True):
            st.markdown(f"**{kit.esc(rec.Event)}**")
            st.dataframe(pd.DataFrame([(c, rec[c]) for c in timeline.COLUMNS if str(rec[c])],
                                      columns=["Field", "Value"]), hide_index=True, width="stretch")
    else:
        st.caption("Select a row for full details (before → after, reason, source record).")
    if st.button("Prepare export (CSV)", key=f"{key}_exp", help="Exports exactly the rows you can see here"):
        st.session_state[f"{key}_csv"] = services.history_export(w, u, types=types, text=text, day_from=d0,
                                                                 day_to=d1, per_day=per_day)
    if st.session_state.get(f"{key}_csv"):
        st.download_button("Download history.csv", st.session_state[f"{key}_csv"], "shiftsense_history.csv",
                           "text/csv", key=f"{key}_dl")


def ui_days(days: list[int]) -> str:
    return f"{dlabel(days[0])} – {dlabel(days[-1])}" if len(days) > 1 else dlabel(days[0])


def leave_calendar(people: list[str], days: list[int], show_type: bool, key: str):
    """Team leave over a date range (approved and requested), one row per person with leave, plus how many people
    are off each day so overlaps stand out. show_type=False (team leads) shows only 'Leave'/'Requested'."""
    from shiftsense import hr as _hr
    w = world()
    lv = w.data["leave"]
    req = {(r["agent_id"], r["day"]): r for r in w.requests if r.get("kind") == "leave" and r["agent_id"] in people
           and r["state"] in ("Pending", "Approved")}
    cells, count = {}, {d: 0 for d in days}
    for a in people:
        for d in days:
            r = req.get((a, d))
            approved = ((lv.agent_id == a) & (lv.day == d) & (lv.reason == "approved leave")).any()
            if not (r or approved):
                continue
            kind = "leave" if approved or (r and r["state"] == "Approved") else "pending"
            typ = _hr.LEAVE_TYPES.get((r or {}).get("leave_type") or "", ("Leave",))[0] if show_type else "Leave"
            cells.setdefault(a, {})[d] = {"text": typ if kind == "leave" else "Requested",
                                          "sub": "" if kind == "leave" else (typ if show_type else ""), "kind": kind}
            count[d] += 1
    if not cells:
        kit.empty("No leave in this period", "Approved and requested leave for these people appears here.")
        return
    ph = photos(list(cells))
    rows = [{"name": pname(a) or a, "meta": f"{a} · {team_of(a)}",
             "photo": ph.get(a), "cells": c} for a, c in sorted(cells.items(), key=lambda x: pname(x[0]))]
    rows.append({"name": "People off", "meta": "approved + requested", "photo": None,
                 "cells": {d: {"text": str(n), "kind": "absent" if n >= 3 else "leave" if n == 2 else "off"}
                           for d, n in count.items() if n}})
    kit.week_grid(rows, [(d, dlabel(d)) for d in days], today=w.today, caption="Team leave calendar")
    peak = max(count.values())
    st.caption(f"Most people off on one day: {peak}" + (f" ({', '.join(dlabel(d) for d, n in count.items() if n == peak)})"
                                                        if peak else "") + ". Dashed = requested, not yet decided.")


# ------------------------------------------------------------------ staffing coverage matrix (shared)
def coverage_matrix_section(days: list[int], key: str, default_day: int | None = None, provenance: str = "modelled"):
    """Primary staffing view: queues × time bins, scheduled / required people per cell, state in colour and words.
    Summary above uses the app's existing definitions (scheduled basis). Clicking a cell opens its detail card;
    a trend chart per queue is an optional drill-down."""
    from shiftsense.config import interval_label as _il, queue_names as _qn
    from shiftsense.roster import coverage as _cov
    w = world()
    p = w.policy
    per = intervals_per_day(p)
    queues = _qn(p)
    f1, f2, f3 = st.columns([1.3, 2.4, 1.3])
    day = f1.selectbox("Day", days, index=days.index(default_day) if default_day in days else 0, format_func=dlabel,
                       key=f"{key}_day")
    qsel = f2.multiselect("Queues", queues, default=queues, key=f"{key}_queues") or queues
    step = {"30 min": 1, "1 hour": 2, "2 hours": 4}[f3.selectbox("Interval", ["30 min", "1 hour", "2 hours"], index=1,
                                                                  key=f"{key}_bin")]
    t = np.arange(day * per, (day + 1) * per)
    cov = _cov(p, w.roster)
    qi = [queues.index(q) for q in qsel]
    req, sch = w.req[np.ix_(qi, t)], cov[np.ix_(qi, t)]
    need, short = req.sum(), np.clip(req - sch, 0, None)
    pct = round(100 * (1 - short.sum() / need), 1) if need else None
    peak = float(short.max()) if short.size else 0.0
    agg = kit.matrix_bins(req, sch, step)
    cols = [_il(p, int(t[b * step])) for b in range(agg["req"].shape[1])]
    ev = kit.chart(kit.coverage_matrix(qsel, cols, agg),
                   f"{dlabel(day)}: coverage {pct if pct is not None else '—'}% of required staffing across "
                   f"{len(qsel)} queue(s); peak shortfall {peak:g} people; {int((short > 0).sum())} queue half-hours "
                   "short. Each cell shows scheduled / required people (average over the interval).",
                   key=f"{key}_mx", provenance=provenance, title="Staffing coverage",
                   subtitle=f"{dlabel(day)} · each cell: scheduled / required people · click a cell for detail",
                   stats_=[("Coverage", f"{pct}%" if pct is not None else "No demand"),
                           ("Peak shortfall", f"{peak:g} people" if peak else "None"),
                           ("Short half-hours", str(int((short > 0).sum())))],
                   data=pd.DataFrame([{"Queue": qsel[i], "From": cols[j], "Required (avg)": round(agg["req"][i, j], 2),
                                       "Scheduled (avg)": round(agg["sch"][i, j], 2),
                                       "Coverage %": None if np.isnan(agg["cov"][i, j]) else round(agg["cov"][i, j], 1),
                                       "Peak shortfall": agg["peak"][i, j]}
                                      for i in range(len(qsel)) for j in range(len(cols))]),
                   on_select=True)
    st.markdown(kit.matrix_legend(), unsafe_allow_html=True)
    pts = (ev.selection.points if ev and getattr(ev, "selection", None) else []) or []
    if pts and pts[0].get("customdata"):
        i, j = int(pts[0]["customdata"][3]), int(pts[0]["customdata"][4])
        ref = (qsel[i], int(t[j * step]), int(t[min(len(t) - 1, (j + 1) * step - 1)]) + 1)
        if st.session_state.get(f"{key}_last") != ref:
            st.session_state[f"{key}_last"] = ref
            open_card("interval", ref)
            st.rerun()
    elif not pts:
        st.session_state.pop(f"{key}_last", None)
    with st.expander("Trend for one queue (drill-down)"):
        tq = st.selectbox("Queue", qsel, key=f"{key}_tq")
        k = queues.index(tq)
        x = [_il(p, int(i)) for i in t]
        kit.chart(kit.supply_demand(x, w.req[k, t], cov[k, t]),
                  f"{tq} on {dlabel(day)}: required vs scheduled people per 30 minutes.", key=f"{key}_trend",
                  provenance=provenance, title="Required vs scheduled over the day",
                  subtitle=f"{tq} · {dlabel(day)} · people per 30 minutes", stats_=kit.gap_stats(x, w.req[k, t], cov[k, t]),
                  zoom=True)
    return day


def interval_detail(q: str, t0: int, t1: int):
    """Detail for one queue and time window: required, scheduled, productive vs the productive requirement,
    forecast range, who is on, who could cover. Same values as the matrix."""
    from shiftsense.config import interval_label as _il, queue_names as _qn
    from shiftsense.roster import coverage as _cov
    w = world()
    p = w.policy
    per = intervals_per_day(p)
    queues = _qn(p)
    qi = queues.index(q)
    shrink = p["staffing"]["shrinkage"]
    cov = _cov(p, w.roster)
    d = t0 // per
    t = np.arange(t0, t1)
    st.markdown(f"#### {q} · {dlabel(d)} {_il(p, t0)}–{_il(p, t1)}")
    b = base()["st"]
    same_basis = bool((b["req"][qi, t] == w.req[qi, t]).all())       # no demand revision in this window
    kit.kpis([
        {"label": "Required (peak)", "value": int(w.req[qi, t].max()), "sub": "scheduled heads, incl. shrinkage"},
        {"label": "Scheduled (min)", "value": int(cov[qi, t].min()),
         "status": "Critical" if cov[qi, t].min() < w.req[qi, t].max() else "Normal"},
        {"label": "Productive (min)", "value": f"{cov[qi, t].min() * (1 - shrink):.1f}",
         "sub": f"scheduled × (1 − {shrink:.0%})"},
        {"label": "Productive required (peak)", "value": int(b["prod_mid"][qi, t].max()) if same_basis else "Unavailable",
         "sub": "Erlang C, before shrinkage" if same_basis else "demand was revised in this window"},
        {"label": "Forecast range", "value": f"{int(b['req_low'][qi, t].max())}–{int(b['req_high'][qi, t].max())}"
         if same_basis else "Unavailable", "sub": "required heads, low–high"},
    ])
    on = [r for r in w.roster[w.roster.day.isin([d - 1, d])].to_dict("records")
          if (r["alt_queue"] or r["queue"]) == q and set(workflow.shift_intervals(p, r["day"], r["shift"], r["ext_before"],
                                                                                    r["ext_after"])) & set(t)]
    a = agents()
    kit.label(f"On {q} in this window ({len(on)})")
    ph = photos([r["agent_id"] for r in on])
    if on:
        kit.people([(pname(r["agent_id"]), shift_label(r), ph.get(r["agent_id"])) for r in on])
    working = set(w.roster[w.roster.day == d].agent_id)
    spare = [i for i in a.index if q in a.loc[i, "eligible"] and i not in working and not unavailable(i, d)]
    kit.label(f"Trained for {q}, off that day ({len(spare)})")
    st.caption(", ".join(name(i) for i in spare[:20]) or "None")
    alerts = [i for i in w.incidents if i.day == d and i.status in workflow.DECIDABLE]
    if alerts:
        st.caption("Recovery options are on the open alert(s) for this day: " + ", ".join(i.id for i in alerts) + ".")
    else:
        st.caption("Recovery options are generated when an absence or demand spike raises an alert.")


# ------------------------------------------------------------------ roster calendar (shared week grid)
def _hm(h: float) -> str:
    h = h % 24
    return f"{int(h):02d}:{int(round(h % 1 * 60)):02d}"


def schedule_cell(agent_id: str, day: int, queue: str | None = None, with_record: bool = False) -> dict | None:
    """One calendar block: shift time and queue, or Leave / Absent / Off. Never shows the leave type (that stays
    with HR); a requested-but-undecided day off is shown dashed."""
    from shiftsense import records
    w = world()
    r = shift_row(agent_id, day)
    pend = any(x["agent_id"] == agent_id and x["day"] == day and x["state"] == "Pending" and x.get("kind") == "leave"
               for x in w.requests)
    if r is not None:
        q = r["alt_queue"] or r["queue"]
        if queue and q != queue:
            return None
        s_, e_ = shift_hours(r)
        sub = f"{r['shift']} · {q}"
        if with_record and day <= w.today:
            code = records.day_record(w, agent_id, day)["code"]
            sub += f" · {records.STATUS[code][0]}"
        if pend:
            sub += " · leave requested"
        return {"text": f"{_hm(s_)}–{_hm(e_)}", "sub": sub, "kind": "pending" if pend else "shift"}
    why = unavailable(agent_id, day)
    if why:
        absent = str(why).startswith("absent")
        return {"text": "Absent" if absent else "Leave", "kind": "absent" if absent else "leave"}
    return None if queue else {"text": "Off", "kind": "off"}


def roster_calendar(ids: list[str], days: list[int], caption: str, queue: str | None = None, with_record: bool = False):
    ph = photos(ids)
    rows = []
    for a in sorted(ids, key=lambda x: pname(x) or x):
        cells = {d: c for d in days if (c := schedule_cell(a, d, queue, with_record))}
        if queue and not any(c["kind"] in ("shift", "pending") for c in cells.values()):
            continue
        rows.append({"name": pname(a) or a, "meta": f"{a} · {team_of(a)}", "photo": ph.get(a), "cells": cells})
    if rows:
        kit.week_grid(rows, [(d, dlabel(d)) for d in days], today=world().today, caption=caption)
    else:
        kit.empty("No shifts in this view", "Choose another week, team or queue.")
