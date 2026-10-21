"""Operational workflow that ties the personas together (pure, UI-free, testable).

  agent reports "can't come in"  or  TL reports an absence
      -> incident with a recommended fix (recovery engine)
      -> TL approves / changes / rejects
      -> roster updated, affected agents notified (confirm or decline)
      -> a decline undoes that step and a new fix is proposed to the TL
      -> anything that cannot be fixed is escalated to the ops head
  agent requests leave -> Green auto-approved / Amber+Red go to their TL (with alternatives)
  planner re-optimises the rest of the horizon (stable, frozen near term) -> agents notified
"""
from __future__ import annotations

import pickle
from contextlib import contextmanager
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import optimizer, recovery, staffing
from . import requests as rq
from .audit import AuditLog, AuditRecord
from .config import day_label, interval_label, intervals_per_day, is_night, queue_names, shift_intervals
from .constraints import check
from .roster import diff_count

CONFIRM_KINDS = {"open_shift", "swap", "extension"}


@dataclass
class Incident:
    id: str
    day: int
    absent: list
    reported_by: str
    plan_roster: pd.DataFrame          # roster just before the absence (what we restore)
    status: str = "open"               # open | fixed | needs_new_fix | escalated | closed | no_gap
    kind: str = "absence"              # absence | no_show | left_early | spike
    owner_team: str = ""               # team lead who handles it
    title: str = ""
    target_override: np.ndarray | None = None
    offers: list = field(default_factory=list)       # open-shift offers sent to several agents
    rec: dict | None = None
    applied: list = field(default_factory=list)
    before: dict = field(default_factory=dict)       # action id -> agent rows before the change
    action_status: dict = field(default_factory=dict)
    declined_agents: list = field(default_factory=list)
    excluded: set = field(default_factory=set)
    note: str = ""
    history: list = field(default_factory=list)
    created_t: int | None = None       # simulated time the alert was raised
    resolved_t: int | None = None      # simulated time it was fixed / closed
    decided_by: str = ""
    residual_ph: float | None = None   # people-hours still uncovered when Ops closed it (risk carried, not fixed)


@dataclass
class World:
    policy: dict
    data: dict                          # agents, leave (grows with approved leave/absence), skills
    req: np.ndarray
    roster: pd.DataFrame
    now_t: int                          # simulated "now" (global interval index)
    incidents: list = field(default_factory=list)
    requests: list = field(default_factory=list)
    notifications: list = field(default_factory=list)
    audit: AuditLog = field(default_factory=AuditLog)
    fc: dict | None = None              # demand forecast band (for same-day spikes)
    attendance: dict = field(default_factory=dict)   # (agent, day) -> "checkin" (genuine) | "in" (simulated) | "no-show"
    spikes: list = field(default_factory=list)
    _next: int = 1
    plans: list = field(default_factory=list)        # draft / validated / published / superseded
    req0: np.ndarray | None = None                   # forecast requirement before same-day revisions
    run_id: str = field(default_factory=lambda: __import__("uuid").uuid4().hex[:8])
    lock: object = field(default=None, repr=False, compare=False)
    scenario: bool = False              # True = demo sandbox copy; never the published roster
    ref_linked: bool = False            # True = built from versioned reference data (data changes flow in)
    attendance_detail: dict = field(default_factory=dict)   # (agent, day) -> login/logout/late minutes from a feed

    def __getstate__(self):
        state = dict(self.__dict__)
        state["lock"] = None                     # locks are not picklable
        return state

    @property
    def today(self) -> int:
        return self.now_t // intervals_per_day(self.policy)

    @property
    def first_day(self) -> int:
        """First day that can still be changed (tomorrow)."""
        return self.today + 1

    def nid(self, prefix: str) -> str:
        n, self._next = self._next, self._next + 1
        return f"{prefix}-{n}"


def roster_fp(roster: pd.DataFrame) -> str:
    """Fingerprint of a roster: a draft records it so publishing over a changed roster is refused."""
    import hashlib
    cols = ["agent_id", "day", "shift", "queue", "ext_before", "ext_after"]
    df = roster[[c for c in cols if c in roster.columns]].sort_values(["day", "agent_id"]).reset_index(drop=True)
    return hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()[:16]


def make_scenario(live: "World") -> "World":
    """A demo sandbox: an independent deep copy of the live workspace with its own 'sim-' run id.
    Every later change happens on the copy; the live roster and records are untouched."""
    import uuid
    sc = pickle.loads(pickle.dumps(live, protocol=pickle.HIGHEST_PROTOCOL))
    sc.run_id = f"sim-{uuid.uuid4().hex[:8]}"
    sc.scenario = True
    sc.lock = None
    return sc


WEEK_RULES = {"max_weekly_hours", "weekly_off", "max_nights_per_week"}
ROW_RULES = {"skill_eligibility", "approved_leave", "night_permission", "contract_shift_type", "extension_limit",
             "max_daily_hours", "one_shift_per_day", "min_rest"}


def started_shifts(w: "World") -> set:
    """(agent, day) of shifts that have already started: history, which no change can alter."""
    out = set()
    for r in w.roster[w.roster.day <= w.today].itertuples():
        if shift_intervals(w.policy, r.day, r.shift, r.ext_before, r.ext_after).start <= w.now_t:
            out.add((r.agent_id, int(r.day)))
    return out


def _rows(roster: pd.DataFrame) -> dict:
    return {(r["agent_id"], int(r["day"])): tuple(sorted(r.items())) for r in roster.to_dict("records")}


def _violations(w: "World", before_roster: pd.DataFrame | None = None) -> set[str]:
    """Hard-rule breaks the guard compares. A per-shift break on a shift that has already started is left out only
    when that shift itself was not edited (the break comes from reference data changing underneath it, e.g. a
    skill removed after the shift began): such a shift is history. Editing a started shift is always checked, and
    weekly limits are always counted."""
    started = started_shifts(w)
    same = None
    if before_roster is not None:
        old, new = _rows(before_roster), _rows(w.roster)
        same = {k for k in started if k in old and old[k] == new.get(k)}
    keep = started if same is None else same
    future_weeks = {(r.agent_id, int(r.day) // 7) for r in w.roster.itertuples() if (r.agent_id, int(r.day)) not in started}
    future_nights = {(r.agent_id, int(r.day) // 7) for r in w.roster.itertuples()
                     if (r.agent_id, int(r.day)) not in started and is_night(w.policy, r.shift)}

    def history(v):
        if v["rule"] in ROW_RULES:
            return (v["agent_id"], v["day"]) in keep
        if v["rule"] in WEEK_RULES and v["day"] is not None:      # nothing left to change in that week
            wk = int(v["day"]) // 7
            mine = [k for k in started if k[0] == v["agent_id"] and k[1] // 7 == wk]
            pool = future_nights if v["rule"] == "max_nights_per_week" else future_weeks
            return (v["agent_id"], wk) not in pool and all(k in keep for k in mine)
        return False
    return {f"{v['rule']}: {v['agent_id']} on {day_label(w.policy, v['day']) if v['day'] is not None else '-'}"
            f" ({v['detail']})" for v in check(w.policy, w.data["agents"], w.data["leave"], w.roster)
            if not history(v)}


@contextmanager
def transaction(w: "World", guard: bool = True):
    """All-or-nothing change: if anything inside fails, the workspace is restored exactly as it was,
    so a failed decision never leaves a half-applied roster, request or alert behind.
    guard: after the change, the independent rule checker runs; a change that introduces any new
    hard-rule violation (skills, rest, hours, nights, leave...) is refused and rolled back."""
    snap = pickle.dumps(w, protocol=pickle.HIGHEST_PROTOCOL)
    before = _violations(w) if guard else set()
    roster0, leave0 = w.roster, w.data["leave"]
    try:
        yield w
        if guard and (w.roster is not roster0 or w.data["leave"] is not leave0):
            new = _violations(w, roster0) - before
            if new:
                raise ValueError(f"Blocked: this change would break {len(new)} hard rule(s), e.g. "
                                 f"{sorted(new)[0][:160]}. Nothing was changed.")
    except BaseException:
        lock = w.lock
        if pickle.dumps(w, protocol=pickle.HIGHEST_PROTOCOL) == snap:
            raise                                   # nothing was touched (typical validation error): keep objects
        w.__dict__.clear()
        w.__dict__.update(pickle.loads(snap).__dict__)
        w.lock = lock
        raise


def upgrade(w: "World") -> "World":
    """Fill fields added in newer versions when loading an older saved workspace."""
    import dataclasses
    from dataclasses import MISSING
    for cls, objs in ((World, [w]), (Incident, w.incidents)):
        for f in dataclasses.fields(cls):
            for o in objs:
                if f.name not in o.__dict__:
                    if f.default is not MISSING:
                        setattr(o, f.name, f.default)
                    elif f.default_factory is not MISSING:
                        setattr(o, f.name, f.default_factory())
    if w.req0 is None:
        w.req0 = w.req.copy()
    if not w.plans:
        w.plans.append({"id": "PLAN-0", "name": "Published plan", "status": "published", "roster": w.roster.copy(),
                        "created_by": "Planner", "created": now_label(w), "source": "initial", "notes": ""})
    return w


def new_world(policy: dict, data: dict, req: np.ndarray, roster: pd.DataFrame,
              fc: dict | None = None) -> World:
    per_day = intervals_per_day(policy)
    now_t = 1 * per_day + 18 * (per_day // 24)          # Tue 18:00 of week 1
    data = {**data, "leave": data["leave"].assign(reason="approved leave")}
    w = World(policy, data, req.copy(), roster.copy(), now_t, fc=fc)
    w.req0 = req.copy()
    w.plans.append({"id": "PLAN-0", "name": "Published plan", "status": "published", "roster": roster.copy(),
                    "created_by": "Planner", "created": now_label(w), "source": "initial", "notes": ""})
    return w


# ------------------------------------------------------------------ helpers
def now_label(w: World) -> str:
    return f"{day_label(w.policy, w.today)} {interval_label(w.policy, w.now_t)}"


def _log(w: World, event, state, rec, decision="", reason="", outcome="", actor="system", **metrics):
    w.audit.log(AuditRecord(event=event, sim_time=now_label(w), state=state, recommendation=rec,
                            decision=decision, reason=reason, outcome=outcome, actor=actor, metrics=metrics))


def notify(w: World, agent_id: str, text: str, kind: str = "info", incident: str | None = None,
           action_id: str | None = None):
    w.notifications.append({"id": w.nid("N"), "agent_id": agent_id, "text": text, "kind": kind,
                            "status": "new", "incident": incident, "action_id": action_id,
                            "time": now_label(w)})


def _add_unavailable(w: World, agent_id: str, day: int, reason: str):
    lv = w.data["leave"]
    if not ((lv.agent_id == agent_id) & (lv.day == day)).any():
        w.data = {**w.data, "leave": pd.concat(
            [lv, pd.DataFrame([{"agent_id": agent_id, "day": day, "reason": reason}])], ignore_index=True)}


def _ctx(w: World, inc: Incident) -> recovery.Context:
    return recovery.Context(w.policy, w.data, w.req, inc.plan_roster, w.roster, inc.day, w.now_t,
                            inc.absent + inc.declined_agents, inc.target_override)


def _team(w: World, agent_id: str) -> str:
    ag = w.data["agents"]
    hit = ag.loc[ag.agent_id == agent_id, "team"]
    if hit.empty and "former_agents" in w.data:              # left the company after a data change
        f = w.data["former_agents"]
        hit = f.loc[f.agent_id == agent_id, "team"]
    if hit.empty:
        raise KeyError(agent_id)
    return str(hit.iloc[0])


def _shift_end_t(w: World, row) -> int:
    return shift_intervals(w.policy, int(row["day"]), row["shift"], row["ext_before"], row["ext_after"]).stop


def _recommend(w: World, inc: Incident):
    inc.rec = recovery.recommend(_ctx(w, inc), exclude=inc.excluded or None)
    if inc.rec["eval"]["deficit_before"] == 0:
        inc.status = "no_gap"


def shift_text(policy: dict, r) -> str:
    s = policy["shifts"][r["shift"]]
    start = s["start"] - r.get("ext_before", 0)
    end = (s["start"] + s["span"] + r.get("ext_after", 0)) % 24
    return f"{r['shift']} {int(start):02d}:{int(round(start % 1 * 60)):02d}–{int(end):02d}:{int(round(end % 1 * 60)):02d}"


def action_message(policy: dict, a: recovery.Action) -> str:
    day = day_label(policy, a.day)
    if a.kind == "redeploy":
        return (f"On {day} you'll help {a.queue} from {interval_label(policy, a.alt_start)} to "
                f"{interval_label(policy, a.alt_end)} (you're trained for it), then return to {a.from_queue}.")
    if a.kind == "extension":
        side = "start earlier" if a.ext_before else "stay later"
        return f"Can you {side} by {a.ext_before or a.ext_after:g} h on {day} ({a.queue})? Paid as overtime."
    if a.kind == "open_shift":
        return f"Extra shift offered: {a.shift} on {day} in {a.queue}. Paid as overtime."
    return (f"Swap request: work {a.shift} on {day} in {a.queue} and take "
            f"{day_label(policy, a.swap_day)} off instead. Your weekly hours stay the same.")


# ------------------------------------------------------------------ absences / incidents
def report_absence(w: World, agent_ids: list[str], day: int, by: str, reason: str = "sick",
                   kind: str = "absence") -> Incident:
    """Agents won't work their shift on `day` (sick call, or a no-show at shift start)."""
    if day < w.today:
        raise ValueError("That day is already over.")
    rows = w.roster[(w.roster.day == day) & w.roster.agent_id.isin(agent_ids)]
    agent_ids = [r.agent_id for r in rows.itertuples() if _shift_end_t(w, r._asdict()) > w.now_t]
    if not agent_ids:
        raise ValueError("None of these agents has a shift still to come that day.")
    snapshot = w.roster.copy()
    w.roster = w.roster[~(w.roster.agent_id.isin(agent_ids) & (w.roster.day == day))].reset_index(drop=True)
    for a in agent_ids:
        _add_unavailable(w, a, day, f"absent ({reason})")
    names = ", ".join(agent_ids)
    inc = Incident(id=w.nid("INC"), day=day, absent=list(agent_ids), reported_by=by, plan_roster=snapshot,
                   kind=kind, owner_team=_team(w, agent_ids[0]),
                   title=f"{names} {'did not show up' if kind == 'no_show' else 'absent'} ({reason})")
    return _open(w, inc, f"{by} reported {names} absent ({reason})")


def report_left_early(w: World, agent_id: str, by: str, reason: str = "unwell") -> Incident:
    """An agent on shift right now has to leave: the rest of the shift is uncovered."""
    rows = w.roster[w.roster.agent_id == agent_id].to_dict("records")
    cur = next((r for r in rows if shift_intervals(w.policy, r["day"], r["shift"], r["ext_before"], r["ext_after"]).start
                <= w.now_t < _shift_end_t(w, r)), None)
    if cur is None:
        raise ValueError("Not on shift right now.")
    snapshot = w.roster.copy()
    per_h = 60 // w.policy["horizon"]["interval_minutes"]
    cut = (_shift_end_t(w, cur) - w.now_t) / per_h
    for r in rows:
        if r is cur:
            r.update(ext_after=r["ext_after"] - cut, kind="left_early")
    others = w.roster[w.roster.agent_id != agent_id]
    w.roster = pd.concat([others, pd.DataFrame(rows)], ignore_index=True).sort_values(["day", "agent_id"]).reset_index(drop=True)
    inc = Incident(id=w.nid("INC"), day=int(cur["day"]), absent=[agent_id], reported_by=by, plan_roster=snapshot,
                   kind="left_early", owner_team=_team(w, agent_id),
                   title=f"{agent_id} left at {interval_label(w.policy, w.now_t)} ({reason})")
    return _open(w, inc, f"{by}: {agent_id} left early ({reason})")


def demand_spike(w: World, queue: str, pct: float, hours: float, by: str) -> Incident:
    """Calls on a queue run above forecast for the next `hours`: recompute the people needed."""
    p = w.policy
    qi = queue_names(p).index(queue)
    per_h = 60 // p["horizon"]["interval_minutes"]
    window = range(w.now_t, min(w.req.shape[1], w.now_t + int(hours * per_h)))
    qp = p["queues"][queue]
    override = np.full_like(w.req, -1)
    for t in window:
        calls = float(w.fc["mid"][qi, t]) * (1 + pct / 100) if w.fc else 0.0
        prod = staffing.agents_required(round(calls, 1), qp["aht_sec"], p["horizon"]["interval_minutes"] * 60,
                                        qp["sla_pct"], qp["sla_sec"], p["staffing"]["max_occupancy"])
        need = max(int(w.req[qi, t]), staffing.scheduled_heads(prod, p["staffing"]["shrinkage"]))
        w.req[qi, t] = need
        override[qi, t] = need
    day = w.today
    working = w.roster[(w.roster.day == day) & (w.roster.queue == queue)]
    team = working.agent_id.map(lambda a: _team(w, a)).mode()
    owner = str(team.iloc[0]) if len(team) else "TL-1"
    label = f"{interval_label(p, window.start)}–{interval_label(p, window.stop)}"
    inc = Incident(id=w.nid("INC"), day=day, absent=[], reported_by=by, plan_roster=w.roster.copy(), kind="spike",
                   owner_team=owner, title=f"{queue} calls +{pct:g}% {label}", target_override=override)
    w.spikes.append({"queue": queue, "pct": pct, "window": label})
    return _open(w, inc, f"{by}: {queue} calls running +{pct:g}% {label}")


def requirement_change(w: World, queue: str, day: int, override: np.ndarray, by: str, title: str) -> Incident:
    """Required staffing rose (new service level, situation, volumes) where the roster is now short: an alert with
    a recovery fix, like a call spike but caused by a reference-data change."""
    qi = queue_names(w.policy).index(queue)
    working = w.roster[(w.roster.day == day) & (w.roster.queue == queue)]
    team = working.agent_id.map(lambda a: _team(w, a)).mode()
    owner = str(team.iloc[0]) if len(team) else sorted(set(w.data["agents"].team))[0]
    inc = Incident(id=w.nid("INC"), day=day, absent=[], reported_by=by, plan_roster=w.roster.copy(),
                   kind="requirement", owner_team=owner, title=title, target_override=override)
    return _open(w, inc, f"{by}: {title}")


def _open(w: World, inc: Incident, history: str) -> Incident:
    inc.created_t = w.now_t
    _recommend(w, inc)
    w.incidents.append(inc)
    ev = inc.rec["eval"]
    inc.history.append(f"{now_label(w)} · {history}")
    _log(w, {"spike": "DemandSpike", "no_show": "NoShow", "data_change": "DataChange",
             "requirement": "RequirementChange"}.get(inc.kind, "AgentAbsent"), inc.title,
         f"{len(inc.rec['actions'])} steps, ₹{ev['cost']:,.0f}, fixes {ev['restored_pct']:.0f}%",
         actor=inc.reported_by, outcome=inc.status)
    return inc


# ------------------------------------------------------------------ clock
def advance_live(w: World, hours: float, by: str) -> dict:
    """LIVE clock: moves represented time forward and nothing else. No attendance is generated and no
    no-shows are simulated; live attendance comes only from genuine check-ins. Open alerts are re-ranked
    because gaps that are now in the past no longer count (a recalculation, not an event)."""
    if w.scenario:
        raise ValueError("This is the live clock. Inside the demo scenario use the simulation clock.")
    if hours <= 0:
        raise ValueError("Time only moves forward.")
    per_h = 60 // w.policy["horizon"]["interval_minutes"]
    start = w.now_t
    w.now_t = min(w.req.shape[1] - 1, w.now_t + int(round(hours * per_h)))
    for inc in w.incidents:
        if inc.status in ("open", "needs_new_fix"):
            _recommend(w, inc)
    moved = (w.now_t - start) / per_h
    _log(w, "LiveClock", f"moved {moved:g} h to {now_label(w)}", "", actor=by,
         outcome="live time only; no simulated attendance or events")
    return {"hours": moved, "at_end": w.now_t >= w.req.shape[1] - 1}


def advance(w: World, hours: float, by: str, protected: set | None = None,
            rate: float | None = None) -> dict:
    """Move the simulated clock. At every shift start, attendance is checked: a small
    deterministic share of agents do not show up, which raises a no-show alert.
    Returns what happened: shifts started, attended, and the no-show alerts raised.
    SIMULATION ONLY: refused on the live workspace, so simulated attendance never becomes a live record."""
    if not w.scenario:
        raise ValueError("The simulation clock runs only in the demo scenario; the live workspace has its own "
                         "live clock that generates no simulated events.")
    import zlib
    p = w.policy
    per_h = 60 // p["horizon"]["interval_minutes"]
    start_t = w.now_t
    end = min(w.req.shape[1] - 1, w.now_t + int(hours * per_h))
    rate = p["recovery"].get("no_show_rate", 0.0) if rate is None else rate
    protected = protected or set()
    no_shows, n_started, n_in = [], 0, 0
    while w.now_t < end:
        w.now_t += 1
        started = []
        for r in w.roster.to_dict("records"):
            if shift_intervals(p, r["day"], r["shift"], r["ext_before"], r["ext_after"]).start == w.now_t - 1:
                started.append(r)
        missing = []
        for r in started:
            key = (r["agent_id"], int(r["day"]))
            if key in w.attendance:
                continue
            rng = np.random.default_rng([p["seed"], zlib.crc32(r["agent_id"].encode()), int(r["day"])])
            gone = rng.random() < rate and r["agent_id"] not in protected
            w.attendance[key] = "no-show" if gone else "in"
            n_started += 1
            n_in += not gone
            if gone:
                missing.append(r)
        for r in missing:
            no_shows.append(report_absence(w, [r["agent_id"]], int(r["day"]), "Attendance system",
                                           "no-show, not clocked in", kind="no_show"))
    for inc in w.incidents:
        if inc.status in ("open", "needs_new_fix"):
            _recommend(w, inc)                     # time moved: gaps in the past no longer count
    moved = (w.now_t - start_t) / per_h
    _log(w, "Clock", f"moved {moved:g} h to {now_label(w)}", "", actor=by,
         outcome=f"{n_started} shift start(s), {n_in} attended, {len(no_shows)} no-show(s)")
    return {"no_shows": no_shows, "started": n_started, "attended": n_in, "hours": moved,
            "at_end": w.now_t >= w.req.shape[1] - 1}


def gap_summary(w: World, inc: Incident) -> list[str]:
    """Plain-language list of the gaps an incident causes (current state)."""
    ctx = _ctx(w, inc)
    out = []
    for qi, q in enumerate(ctx.queues):
        ts = np.nonzero(ctx.deficit[qi])[0]
        if not len(ts):
            continue
        runs = recovery._runs(ts.tolist())
        for run in runs:
            worst = int(ctx.deficit[qi, run].max())
            out.append(f"{q} short by {worst} {'person' if worst == 1 else 'people'} "
                       f"{interval_label(w.policy, run[0])}–{interval_label(w.policy, run[-1] + 1)}")
    return out


DECIDABLE = ("open", "needs_new_fix")


def _require_open(inc: Incident):
    if inc.status not in DECIDABLE:
        by = f" by {inc.decided_by}" if inc.decided_by else ""
        raise ValueError(f"Conflict: this alert was already handled{by} (now: {inc.status.replace('_', ' ')}). "
                         "Close the card to see the latest state before deciding again.")


def approve_fix(w: World, inc: Incident, by: str, reason: str = "") -> Incident:
    _require_open(inc)
    acts = inc.rec["actions"]
    chosen = {a.agent_id for a in acts}
    for a in acts:
        if a.kind == "open_shift":
            _make_offer(w, inc, a, chosen)
            continue
        days = {a.day} | ({a.swap_day} if a.swap_day is not None else set())
        inc.before[a.id] = w.roster[(w.roster.agent_id == a.agent_id) & w.roster.day.isin(days)].copy()
        w.roster = recovery.apply_actions(w.roster, [a])
        inc.applied.append(a)
        needs_ok = a.kind in CONFIRM_KINDS
        inc.action_status[a.id] = "waiting" if needs_ok else "done"
        notify(w, a.agent_id, action_message(w.policy, a), "confirm" if needs_ok else "info", inc.id, a.id)
    v = check(w.policy, w.data["agents"], w.data["leave"], w.roster)
    if v:
        raise RuntimeError(f"Fix would break rules: {v[:3]}")
    residual = inc.rec["eval"]["deficit_after"] > 0
    inc.status = "escalated" if residual else "fixed"
    inc.decided_by = by
    if not residual:
        inc.resolved_t = w.now_t
    inc.history.append(f"{now_label(w)} · {by} approved the fix ({len(acts)} steps)"
                       + (" — remaining gap sent to Ops" if residual else ""))
    ev = inc.rec["eval"]
    _log(w, "Recommendation", f"{inc.id}", "; ".join(a.description for a in acts), decision="Accept",
         reason=reason, actor=by, outcome=f"fixed {ev['restored_pct']:.0f}%, ₹{ev['cost']:,.0f}"
         + (", remaining gap escalated" if residual else ""),
         ttc_min=round(inc.rec["ttc_min"], 1), cost=ev["cost"])
    return inc


def _make_offer(w: World, inc: Incident, a: recovery.Action, chosen: set, backups: int = 2):
    """Offer an open shift to the suggested agent plus qualified backups; first to accept gets it."""
    alts = [c for c in inc.rec.get("candidates", []) if c.kind == "open_shift" and c.day == a.day
            and c.shift == a.shift and c.queue == a.queue and c.agent_id not in chosen][:backups]
    offer = {"id": w.nid("OFF"), "actions": [a] + alts, "status": "open", "declined": [], "filled_by": None}
    inc.offers.append(offer)
    inc.action_status[a.id] = "offered"
    n = len(offer["actions"])
    for act in offer["actions"]:
        chosen.add(act.agent_id)
        notify(w, act.agent_id, action_message(w.policy, act)
               + (f" Sent to {n} colleagues — first to accept gets it." if n > 1 else ""),
               "offer", inc.id, act.id)
        w.notifications[-1]["offer"] = offer["id"]


def _answer_offer(w: World, n: dict, accept: bool, by: str):
    inc = next(i for i in w.incidents if i.id == n["incident"])
    offer = next(o for o in inc.offers if o["id"] == n["offer"])
    primary = offer["actions"][0]
    if offer["status"] != "open":
        n["status"] = "withdrawn"
        return
    act = next(a for a in offer["actions"] if a.id == n["action_id"])
    if accept:
        trial = recovery.apply_actions(w.roster[w.roster.agent_id == act.agent_id], [act])
        if check(w.policy, w.data["agents"], w.data["leave"], trial):
            n["status"] = "expired"
            return
        inc.before[act.id] = w.roster[(w.roster.agent_id == act.agent_id) & (w.roster.day == act.day)].copy()
        w.roster = recovery.apply_actions(w.roster, [act])
        inc.applied.append(act)
        inc.action_status[act.id] = "confirmed"
        if act.id != primary.id:
            inc.action_status[primary.id] = f"taken by {act.agent_id}"
        offer["status"], offer["filled_by"] = "filled", act.agent_id
        n["status"] = "accepted"
        for other in w.notifications:
            if other.get("offer") == offer["id"] and other is not n and other["status"] == "new":
                other["status"] = "withdrawn"
        inc.history.append(f"{now_label(w)} · {act.agent_id} accepted the open shift ({act.shift}, {act.queue})")
        _log(w, "OfferAccepted", inc.id, act.description, decision="Accept", actor=by)
        return
    n["status"] = "declined"
    offer["declined"].append(act.agent_id)
    inc.history.append(f"{now_label(w)} · {act.agent_id} declined the open shift")
    _log(w, "OfferDeclined", inc.id, act.description, decision="Reject", reason="agent declined", actor=by)
    if len(offer["declined"]) == len(offer["actions"]):
        offer["status"] = "unfilled"
        inc.action_status[primary.id] = "declined"
        inc.declined_agents += offer["declined"]
        inc.excluded |= {a.id for a in offer["actions"]}
        _recommend(w, inc)
        if inc.status != "no_gap":
            inc.status = "needs_new_fix"
        inc.history.append(f"{now_label(w)} · nobody took the open shift — new fix suggested")


def change_fix(w: World, inc: Incident, remove_ids: list[str], by: str, reason: str) -> Incident:
    _require_open(inc)
    if not reason.strip():
        raise ValueError("A reason is required to change the suggested fix.")
    inc.excluded |= set(remove_ids)
    _recommend(w, inc)
    inc.history.append(f"{now_label(w)} · {by} removed {len(remove_ids)} step(s): {reason}")
    _log(w, "Recommendation", inc.id, "re-planned without removed steps", decision="Modify",
         reason=reason, actor=by, outcome=f"{len(inc.rec['actions'])} steps")
    return inc


def reject_fix(w: World, inc: Incident, by: str, reason: str) -> Incident:
    """Reject the suggested fix: none of its steps will be proposed again; a new fix is computed."""
    _require_open(inc)
    if not reason.strip():
        raise ValueError("A reason is required to reject the suggested fix.")
    inc.excluded |= {a.id for a in inc.rec["actions"]}
    _recommend(w, inc)
    if inc.status == "needs_new_fix":
        inc.status = "open"
    inc.decided_by = by
    left = len(inc.rec["actions"])
    inc.history.append(f"{now_label(w)} · {by} rejected the fix: {reason} — "
                       + (f"new fix with {left} step(s)" if left else "no other feasible fix; escalate to Ops"))
    _log(w, "Recommendation", inc.id, "fix rejected", decision="Reject", reason=reason, actor=by,
         outcome="new fix proposed" if left else "no feasible alternative")
    return inc


def escalate_fix(w: World, inc: Incident, by: str, reason: str) -> Incident:
    """Send the alert to Operations (beyond team-lead authority or no feasible fix)."""
    _require_open(inc)
    if not reason.strip():
        raise ValueError("A reason is required to escalate.")
    inc.status = "escalated"
    inc.decided_by = by
    inc.history.append(f"{now_label(w)} · {by} escalated to Ops: {reason}")
    _log(w, "Escalation", inc.id, "escalated by team lead", decision="Escalate", reason=reason, actor=by,
         outcome="with Operations")
    return inc


def ops_return(w: World, inc: Incident, by: str, note: str) -> Incident:
    """Operations sends an escalation back to the team lead with a clarification request."""
    if inc.status != "escalated":
        raise ValueError(f"Conflict: this item is no longer with Operations (now: {inc.status.replace('_', ' ')}). "
                         "Close the card to see the latest state.")
    if not note.strip():
        raise ValueError("Add a note for the team lead.")
    inc.status = "open"
    inc.excluded = set()
    _recommend(w, inc)
    if inc.status == "no_gap":
        inc.resolved_t = w.now_t
    inc.history.append(f"{now_label(w)} · {by}: returned to team lead — {note}")
    _log(w, "Escalation", inc.id, "returned", decision="Return", reason=note, actor=by, outcome="back with team lead")
    return inc


def respond(w: World, notif_id: str, accept: bool, by: str) -> None:
    """Agent confirms or declines a change. A decline undoes it and proposes a new fix."""
    n = next(x for x in w.notifications if x["id"] == notif_id)
    if n["status"] != "new":
        return
    if n["kind"] == "offer":
        return _answer_offer(w, n, accept, by)
    if n["kind"] == "swap_ask":
        return _answer_swap(w, n, accept, by)
    if n["kind"] != "confirm":
        n["status"] = "read"
        return
    inc = next(i for i in w.incidents if i.id == n["incident"])
    a = next(x for x in inc.applied if x.id == n["action_id"])
    if accept:
        n["status"] = "accepted"
        inc.action_status[a.id] = "confirmed"
        inc.history.append(f"{now_label(w)} · {a.agent_id} confirmed: {a.description}")
        _log(w, "AgentResponse", inc.id, a.description, decision="Accept", actor=by)
        return
    n["status"] = "declined"
    inc.action_status[a.id] = "declined"
    days = {a.day} | ({a.swap_day} if a.swap_day is not None else set())
    keep = w.roster[~((w.roster.agent_id == a.agent_id) & w.roster.day.isin(days))]
    w.roster = pd.concat([keep, inc.before[a.id]], ignore_index=True).sort_values(["day", "agent_id"]).reset_index(drop=True)
    inc.declined_agents.append(a.agent_id)
    inc.excluded.add(a.id)
    _recommend(w, inc)
    if inc.status != "no_gap":
        inc.status = "needs_new_fix"
    inc.history.append(f"{now_label(w)} · {a.agent_id} declined: {a.description} — new fix suggested")
    _log(w, "AgentResponse", inc.id, a.description, decision="Reject", reason="agent declined", actor=by,
         outcome="step undone; new fix proposed to team lead")


def ops_close(w: World, inc: Incident, decision: str, note: str, by: str, residual_ph: float | None = None) -> Incident:
    """Ops records a decision on an escalated gap. The roster is NOT changed here: any people-hours
    still uncovered are kept on the alert (residual_ph) so the risk stays visible, never 'resolved'."""
    if inc.status != "escalated":
        raise ValueError(f"Conflict: this item is no longer with Operations (now: {inc.status.replace('_', ' ')}). "
                         "Close the card to see the latest state.")
    if not note.strip():
        raise ValueError("Please record what was decided.")
    inc.status = "closed"
    inc.resolved_t = w.now_t
    inc.decided_by = by
    inc.residual_ph = residual_ph
    inc.note = f"{decision}: {note}" + (f" · {residual_ph:g} people-hours still uncovered in the roster"
                                         if residual_ph else "")
    inc.history.append(f"{now_label(w)} · {by}: {decision} — {note}"
                       + (f" · {residual_ph:g} people-hours still uncovered (risk carried)" if residual_ph else ""))
    _log(w, "Escalation", inc.id, "remaining gap", decision=decision, reason=note, actor=by, outcome="closed by Ops")
    return inc


# ------------------------------------------------------------------ leave requests
def leave_check(w: World, agent_id: str, day: int) -> dict:
    st = rq.status_for(w.policy, w.req, w.roster, agent_id, day)
    alts = [] if st["status"] == "Green" else rq.alternatives(
        w.policy, w.data, w.req, w.roster, agent_id, day, min_day=w.first_day)
    return {**st, "alternatives": alts}


def _no_duplicate_request(w: World, agent_id: str, day: int):
    if not ((w.roster.agent_id == agent_id) & (w.roster.day == day)).any():
        raise ValueError("You have no shift that day, so there is nothing to request.")
    if any(r["agent_id"] == agent_id and r["day"] == day and r["state"] in ("Pending", "Waiting for colleague")
           for r in w.requests):
        raise ValueError("You already have an open request for that day. Withdraw it first to ask for something else.")


def submit_leave(w: World, agent_id: str, day: int, by: str, alternative: dict | None = None,
                 note: str = "", leave_type: str | None = None, hold: str = "") -> dict:
    """hold: a reason the request must wait for a person (e.g. HR review) even when the check is Green."""
    if day < w.first_day:
        raise ValueError("Leave can be requested for tomorrow onwards.")
    _no_duplicate_request(w, agent_id, day)
    chk = leave_check(w, agent_id, day)
    r = {"id": w.nid("REQ"), "kind": "leave", "agent_id": agent_id, "day": day, "status": chk["status"],
         "detail": chk["detail"],
         "alternative": alternative, "note": note, "state": "Pending", "reason": "", "time": now_label(w),
         "leave_type": leave_type, "hold": hold}
    w.requests.append(r)
    if chk["status"] == "Green" and alternative is None and not hold:
        _apply_leave(w, r)
        r["state"] = "Approved"
        notify(w, agent_id, f"Your leave on {day_label(w.policy, day)} is approved (team stays covered).")
        _log(w, "LeaveRequested", f"{agent_id} {day_label(w.policy, day)}", "Green", decision="Auto-approve",
             actor="system", outcome="approved under policy")
    else:
        _log(w, "LeaveRequested", f"{agent_id} {day_label(w.policy, day)}", chk["status"],
             decision="Pending", actor=by)
    return r


def request_swap(w: World, agent_id: str, day: int, option: dict, by: str, note: str = "") -> dict:
    """Ask a colleague directly to swap shifts. They accept first, then the team lead approves."""
    if day < w.first_day:
        raise ValueError("Swaps can be requested for tomorrow onwards.")
    _no_duplicate_request(w, agent_id, day)
    r = {"id": w.nid("REQ"), "kind": "swap", "agent_id": agent_id, "day": day, "status": "Green",
         "detail": "Both of you are trained for the shifts you take over and keep your weekly hours.",
         "alternative": option, "note": note, "state": "Waiting for colleague", "reason": "", "time": now_label(w)}
    w.requests.append(r)
    me = w.data["agents"].set_index("agent_id").loc[agent_id, "name"]
    mine = w.roster[(w.roster.agent_id == agent_id) & (w.roster.day == day)].iloc[0]
    theirs = w.roster[(w.roster.agent_id == option["partner"]) & (w.roster.day == option["partner_day"])].iloc[0]
    notify(w, option["partner"], f"{me} asks to swap: you'd work their {mine['shift']} on {day_label(w.policy, day)} "
           f"({mine.queue}) and they'd work your {theirs['shift']} on {day_label(w.policy, option['partner_day'])}. "
           "Your team lead approves after you agree.", "swap_ask")
    w.notifications[-1]["request"] = r["id"]
    _log(w, "SwapRequested", f"{agent_id} ↔ {option['partner']}", option["option"], decision="Pending", actor=by)
    return r


def _answer_swap(w: World, n: dict, accept: bool, by: str):
    r = next(x for x in w.requests if x["id"] == n["request"])
    if r["state"] != "Waiting for colleague":
        n["status"] = "read"
        return
    partner = r["alternative"]["partner"]
    if accept and _too_late(w, r):
        raise ValueError("Too late to agree: the swap day has already started.")
    n["status"] = "accepted" if accept else "declined"
    if accept:
        r["state"] = "Pending"
        notify(w, r["agent_id"], f"{partner} agreed to your swap — waiting for your team lead.")
    else:
        r["state"] = "Declined by colleague"
        notify(w, r["agent_id"], f"{partner} can't do the swap. Try another colleague or request time off.")
    _log(w, "SwapAnswer", r["id"], r["alternative"]["option"], decision="Accept" if accept else "Reject",
         reason="" if accept else "colleague declined", actor=by)


def _apply_leave(w: World, r: dict):
    alt = r["alternative"]
    r["roster_before"] = w.roster.copy()
    w.roster = rq.apply_alternative(w.roster, r["agent_id"], r["day"], alt)
    if alt is None:
        _add_unavailable(w, r["agent_id"], r["day"], "approved leave")
    elif alt["type"] == "Other day":
        _add_unavailable(w, r["agent_id"], alt["day"], "approved leave")
    elif alt["type"] == "Swap":
        notify(w, alt["partner"], f"Shift swap agreed with {r['agent_id']}: you now work their shift on "
               f"{day_label(w.policy, r['day'])} and are off on {day_label(w.policy, alt['partner_day'])}.")


def _request_days(r: dict) -> set[int]:
    alt = r.get("alternative") or {}
    return {r["day"]} | {d for d in (alt.get("day"), alt.get("partner_day")) if d is not None}


def _too_late(w: World, r: dict) -> bool:
    """A request can no longer change the roster once any day it touches has started (frozen)."""
    return min(_request_days(r)) * intervals_per_day(w.policy) <= w.now_t


def decide_leave(w: World, req_id: str, approve: bool, by: str, reason: str = "") -> dict:
    r = next(x for x in w.requests if x["id"] == req_id)
    if r["state"] != "Pending":
        by = f" by {r['decided_by']}" if r.get("decided_by") else ""
        raise ValueError(f"Conflict: this request was already decided{by} (now: {r['state']}). "
                         "Close the card to see the latest state.")
    if not approve and not reason.strip():
        raise ValueError("A reason is required to reject a request.")
    if approve and _too_late(w, r):
        raise ValueError("Too late to approve: a day this request changes has already started. Decline it, or "
                         "handle it as an absence.")
    what = r["alternative"]["option"] if r["alternative"] else f"leave on {day_label(w.policy, r['day'])}"
    if approve:
        _apply_leave(w, r)
        r["state"] = "Approved"
        notify(w, r["agent_id"], f"Approved: {what}.")
    else:
        r["state"] = "Rejected"
        notify(w, r["agent_id"], f"Not approved: {what}. Reason: {reason}")
    r["reason"] = reason
    r["decided_by"] = by
    r["decided_t"] = w.now_t
    _log(w, "LeaveRequested", f"{r['agent_id']} {day_label(w.policy, r['day'])}", r["status"],
         decision="Accept" if approve else "Reject", reason=reason, actor=by,
         outcome="approved" if approve else "rejected")
    return r


def withdraw_request(w: World, req_id: str, by: str) -> dict:
    """The requesting agent withdraws a request that nobody has decided yet."""
    r = next(x for x in w.requests if x["id"] == req_id)
    if r["state"] not in ("Pending", "Waiting for colleague"):
        raise ValueError(f"Cannot withdraw: request is {r['state']}.")
    r["state"] = "Withdrawn"
    _log(w, "LeaveRequested", f"{r['agent_id']} {day_label(w.policy, r['day'])}", r["status"], decision="Withdraw",
         reason="withdrawn by agent", actor=by, outcome="withdrawn")
    return r


def revoke_leave(w: World, req_id: str, by: str, reason: str) -> dict:
    """Controlled reversal of an approved request before the day starts. The original decision
    stays in history; the roster goes back to how it was for the affected agents and days."""
    from .constraints import check
    r = next(x for x in w.requests if x["id"] == req_id)
    if r["state"] != "Approved":
        raise ValueError("Only approved requests can be revoked.")
    if not reason.strip():
        raise ValueError("A reason is required to revoke.")
    alt = r["alternative"]
    days = {r["day"]} | ({alt["day"]} if alt and alt["type"] == "Other day" else set()) \
        | ({alt["partner_day"]} if alt and alt["type"] == "Swap" else set())
    if min(days) < w.first_day:
        raise ValueError("Too late to revoke: the day has started.")
    people = {r["agent_id"]} | ({alt["partner"]} if alt and alt["type"] == "Swap" else set())
    old = r.get("roster_before")
    if old is None:
        raise ValueError("No earlier roster recorded for this request.")
    keep = w.roster[~(w.roster.agent_id.isin(people) & w.roster.day.isin(days))]
    back = old[old.agent_id.isin(people) & old.day.isin(days)]
    new = pd.concat([keep, back], ignore_index=True).sort_values(["day", "agent_id"]).reset_index(drop=True)
    lv = w.data["leave"]
    lv2 = lv[~(lv.agent_id.isin(people) & lv.day.isin(days) & (lv.reason == "approved leave"))]
    if check(w.policy, w.data["agents"], lv2, new[new.agent_id.isin(people)]):
        raise ValueError("Restoring the shift would break a rule; handle it as a new change instead.")
    w.roster = new
    w.data = {**w.data, "leave": lv2.reset_index(drop=True)}
    r["state"] = "Revoked"
    r["reason"] = reason
    for a in people:
        notify(w, a, f"Revoked: {alt['option'] if alt else 'leave'} on {day_label(w.policy, r['day'])}. Reason: {reason}")
    _log(w, "LeaveRequested", f"{r['agent_id']} {day_label(w.policy, r['day'])}", "revoked", decision="Revoke",
         reason=reason, actor=by, outcome="original decision kept in history; shift restored")
    return r


# ------------------------------------------------------------------ planner
def replan(w: World, by: str) -> dict:
    """Re-optimise from the day after tomorrow, staying close to the current roster."""
    freeze = w.first_day + 1
    res = optimizer.plan(w.policy, w.data["agents"], w.data["leave"], w.req,
                         optimizer.PlanOptions(stability_ref=w.roster, freeze_before_day=freeze))
    if res["roster"].empty:
        return {"ok": False, "message": f"No feasible re-plan found ({res['status']}). Roster unchanged."}
    new = res["roster"]
    # keep the frozen days exactly as they are (incl. extensions and redeployments)
    new = pd.concat([w.roster[w.roster.day < freeze], new[new.day >= freeze]], ignore_index=True)
    new = new.sort_values(["day", "agent_id"]).reset_index(drop=True)
    v = check(w.policy, w.data["agents"], w.data["leave"], new)
    if v:
        return {"ok": False, "message": f"Re-plan would break {len(v)} rule(s); roster unchanged."}
    old = w.roster
    changed = diff_count(old, new)
    ka = {(r.agent_id, r.day): r for r in old.itertuples(index=False)}
    kb = {(r.agent_id, r.day): r for r in new.itertuples(index=False)}
    for key in sorted(set(ka) | set(kb)):
        a, b = ka.get(key), kb.get(key)
        if (a and (a.shift, a.queue)) == (b and (b.shift, b.queue)):
            continue
        before = f"{a.shift} on {a.queue}" if a else "day off"
        after = f"{b.shift} on {b.queue}" if b else "day off"
        notify(w, key[0], f"Schedule update for {day_label(w.policy, key[1])}: {before} → {after}.")
    w.roster = new
    _log(w, "Replan", f"from {day_label(w.policy, freeze)}", "stable re-optimisation", decision="Publish",
         actor=by, outcome=f"{changed} shift changes")
    return {"ok": True, "changes": changed, "seconds": res["build_seconds"]}


# ------------------------------------------------------------------ demo story
def load_story(w: World) -> World:
    """Pre-fill a realistic evening so a demo starts mid-flow (deterministic):
    - TL-1: a sick call for tomorrow waiting for approval
    - TL-2: an absence already fixed, with agents still to confirm
    - Ops: a gap the team lead could not close, escalated
    - two time-off requests waiting with team leads
    Demo login agents (A01 Aarav, A06 Sneha, A02 Priya) are kept free for the live walkthrough."""
    team = w.data["agents"].set_index("agent_id").team
    keep = {"A01", "A02", "A06"}

    def pick(day, tl, n=1, queue=None):
        r = w.roster[(w.roster.day == day) & ~w.roster.agent_id.isin(keep)]
        r = r[r.agent_id.map(team) == tl]
        if queue:
            r = r[r.queue == queue]
        return r.agent_id.tolist()[:n]

    d1, d2 = w.first_day, w.first_day + 1
    who = pick(d1, "TL-1", 1, "Claims-EN") or pick(d1, "TL-1", 1)
    report_absence(w, who, d1, "Attendance desk", "unwell")
    inc = report_absence(w, pick(d2, "TL-2", 1), d2, "Fatima Khan (Team lead)", "family emergency")
    if inc.status == "open" and inc.rec["actions"]:
        approve_fix(w, inc, "Fatima Khan (Team lead)", "standard cover")
    inc = report_absence(w, pick(d2, "TL-3", 2, "Claims-EN") or pick(d2, "TL-3", 2), d2,
                         "Team lead TL-3", "unwell")
    if inc.status == "open":
        escalate_fix(w, inc, "Team lead TL-3", "both suggested agents are on a client audit that day")
    # simulated prior-period decisions by the TL-2 lead (drives the governance demo; labelled as simulated)
    tl2 = [a for a in w.data["agents"].agent_id if team.get(a) == "TL-2" and a != "A02"][:3]
    history_rows = [("A02", 0, "Approved", "Red"), ("A02", 0, "Approved", "Amber"), ("A02", 1, "Approved", "Red"),
                    ("A02", 1, "Approved", "Green")] + [(a, 0, st, "Amber") for a, st in
                                                         zip(tl2, ("Rejected", "Rejected", "Approved"))]
    for agent, day, state, status in history_rows:
        w.requests.append({"id": w.nid("REQ"), "kind": "leave", "agent_id": agent, "day": day, "status": status,
                           "detail": "Prior-period record (simulated)", "alternative": None, "note": "prior period",
                           "state": state, "reason": "" if state == "Approved" else "team short that day",
                           "time": "prior period", "decided_by": "Fatima Khan (Team lead)", "decided_t": 0,
                           "simulated_history": True})
    for aid in ("A06", "A02"):
        if any(r["agent_id"] == aid and r["state"] in ("Pending", "Waiting for colleague") for r in w.requests):
            continue                                   # the copy already has an open request for this agent
        days = [d for d in range(w.first_day + 2, w.policy["horizon"]["days"])
                if ((w.roster.agent_id == aid) & (w.roster.day == d)).any()]
        busy = next((d for d in days if leave_check(w, aid, d)["status"] != "Green"), None)
        if busy is not None:
            submit_leave(w, aid, busy, aid, note="family function")
    return w
