"""Analyst planning: plan metrics, isolated what-if scenarios, draft -> validated -> published
plans, infeasibility diagnosis and planned-versus-actual.

Scenarios never touch the live roster: they work on copies until a plan is explicitly published.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import optimizer, records, staffing
from .config import day_label, intervals_per_day, is_night, queue_names, warmup_mask, week_of
from .constraints import check, summary as violation_summary
from .kpis import burden
from .roster import coverage, diff_count

PLAN_STATES = ("draft", "validated", "published", "superseded")


# ------------------------------------------------------------------ metrics
def metrics(policy: dict, agents: pd.DataFrame, leave: pd.DataFrame, req: np.ndarray,
            roster: pd.DataFrame, days, ref: pd.DataFrame | None = None) -> dict:
    """Plan quality over `days` (by default the days that can still change)."""
    per_day = intervals_per_day(policy)
    mask = warmup_mask(policy)
    cov = coverage(policy, roster)
    idx = np.concatenate([np.arange(d * per_day, (d + 1) * per_day) for d in days]) if len(days) else np.array([], int)
    idx = idx[mask[idx]]
    r, c = req[:, idx], cov[:, idx]
    need, got = r.sum(), np.minimum(r, c).sum()
    sens = np.array([policy["queues"][q]["sensitivity"] for q in queue_names(policy)])[:, None]
    ro = roster[roster.day.isin(list(days))]
    rate, mult = policy["costs"]["hourly_rate"], policy["costs"]["ot_multiplier"]
    paid = float(sum(policy["shifts"][s]["paid"] for s in ro["shift"]))
    wk = ro.assign(week=ro.day.map(week_of)).groupby(["agent_id", "week"]).day.nunique()
    ot_days = float((wk - policy["rules"]["contracted_shifts_per_week"]).clip(lower=0).sum())
    ot_h = ot_days * 8 + float(ro.ext_before.clip(lower=0).sum() + ro.ext_after.clip(lower=0).sum())
    prim = dict(zip(agents.agent_id, agents.primary))
    b = burden(policy, ro, agents)
    viol = check(policy, agents, leave, roster)
    return {
        "Coverage %": round(100 * got / max(need, 1), 1),
        "Uncovered people-hours": round(float(np.clip(r - c, 0, None).sum()) / 2, 1),
        "SLA exposure": round(float((np.clip(r - c, 0, None) * sens).sum()) / 2, 1),
        "Surplus people-hours": round(float(np.clip(c - r, 0, None).sum()) / 2, 1),
        "Paid hours": paid,
        "OT hours": ot_h,
        "Labour cost ₹": labour_cost(policy, agents, ro),
        "Fairness (std)": round(float(b.std(ddof=0)), 2),
        "Primary-queue %": round(100 * float(np.mean([prim.get(a) == q for a, q in zip(ro.agent_id, ro.queue)])), 1)
        if len(ro) else 0.0,
        "Violations": len(viol),
        "Changes vs live": diff_count(ref[ref.day.isin(list(days))], ro) if ref is not None else 0,
    }


def open_days(w) -> list[int]:
    return list(range(w.first_day + 1, w.policy["horizon"]["days"]))


# ------------------------------------------------------------------ scenarios (isolated)
@dataclass
class Scenario:
    name: str = "Scenario"
    demand_pct: dict = field(default_factory=dict)     # queue -> +/- % change in forecast calls
    extra_absences: int = 0                            # random agent-days removed (seeded)
    headcount_change: int = 0                          # negative = fewer agents
    max_nights_per_week: int | None = None
    seed: int = 7


def scenario_inputs(w, sc: Scenario):
    """Copies of policy, agents, leave and requirement with the scenario applied."""
    policy = copy.deepcopy(w.policy)
    if sc.max_nights_per_week is not None:
        policy["rules"]["max_nights_per_week"] = int(sc.max_nights_per_week)
    rng = np.random.default_rng(sc.seed)
    agents = w.data["agents"].copy()
    removed = []
    if sc.headcount_change < 0:
        removed = sorted(rng.choice(agents.agent_id, size=min(-sc.headcount_change, len(agents) - 10), replace=False))
        agents = agents[~agents.agent_id.isin(removed)].reset_index(drop=True)
    leave = w.data["leave"][w.data["leave"].agent_id.isin(agents.agent_id)].copy()
    if sc.extra_absences > 0:
        pool = w.roster[w.roster.day.isin(open_days(w)) & w.roster.agent_id.isin(agents.agent_id)]
        pick = pool.iloc[rng.choice(len(pool), size=min(sc.extra_absences, len(pool)), replace=False)]
        leave = pd.concat([leave, pick[["agent_id", "day"]].assign(reason="scenario absence")], ignore_index=True)
    req = w.req.copy()
    if sc.demand_pct and w.fc is not None:
        fc = {k: v.copy() for k, v in w.fc.items()}
        start = (w.first_day + 1) * intervals_per_day(policy)
        for q, pct in sc.demand_pct.items():
            qi = queue_names(policy).index(q)
            for k in fc:
                fc[k][qi, start:] *= 1 + pct / 100
        new = staffing.staffing_chain(policy, fc)["req"]
        req[:, start:] = new[:, start:]
    return policy, agents, leave, req, removed


def labour_cost(policy: dict, agents: pd.DataFrame, ro: pd.DataFrame) -> int:
    """Paid hours at each person's contract rate; extra shifts and extensions at their overtime rate."""
    from .config import agent_rules, pay_table
    pay = pay_table(policy, agents)
    default = (policy["costs"]["hourly_rate"], policy["costs"]["ot_multiplier"])
    cons = dict(zip(agents.agent_id, agents["contract"])) if "contract" in agents.columns else {}
    total = 0.0
    for (a, wk), g in ro.assign(week=ro.day.map(week_of)).groupby(["agent_id", "week"]):
        rate, mult = pay.get(a, default)
        contracted = agent_rules(policy, cons.get(a) if isinstance(cons.get(a), str) else None)["contracted_shifts_per_week"]
        paid = sum(policy["shifts"][s]["paid"] for s in g["shift"])
        extra_days = max(0, g.day.nunique() - contracted)
        ext = float(g.ext_before.clip(lower=0).sum() + g.ext_after.clip(lower=0).sum())
        total += (paid - extra_days * 8) * rate + (extra_days * 8 + ext) * rate * mult
    return round(total)


def run_scenario(w, sc: Scenario, time_limit: float | None = None) -> dict:
    """Re-optimise the open days under the scenario, keeping frozen days. Live state untouched."""
    policy, agents, leave, req, removed = scenario_inputs(w, sc)
    days = open_days(w)
    freeze = w.first_day + 1
    live = w.roster[w.roster.agent_id.isin(agents.agent_id)]
    away = set(zip(leave.agent_id, leave.day))
    live_as_is = live[[(a, d) not in away for a, d in zip(live.agent_id, live.day)]]
    as_is = metrics(policy, agents, leave, req, live_as_is, days, ref=w.roster)
    res = optimizer.plan(policy, agents, leave, req,
                         optimizer.PlanOptions(stability_ref=live_as_is, freeze_before_day=freeze, time_limit=time_limit))
    out = {"name": sc.name, "scenario": sc, "removed": removed, "status": res["status"], "as_is": as_is,
           "roster": None, "after": None, "diagnosis": []}
    if res["roster"].empty:
        out["diagnosis"] = diagnose(policy, agents, leave, live_as_is, freeze)
        return out
    new = pd.concat([live_as_is[live_as_is.day < freeze], res["roster"][res["roster"].day >= freeze]], ignore_index=True)
    new = new.sort_values(["day", "agent_id"]).reset_index(drop=True)
    out["roster"] = new
    out["after"] = metrics(policy, agents, leave, req, new, days, ref=w.roster)
    out["req"] = req
    return out


# ------------------------------------------------------------------ infeasibility diagnosis
SOLVER_TEXT = {
    "INFEASIBLE": "No roster can meet every hard rule with these agents, leave days and frozen days. "
                  "Nothing was changed: the current roster stays live.",
    "UNKNOWN": "The optimiser ran out of time before it found any valid roster. Nothing was changed: the current "
               "roster stays live. Try fewer simultaneous changes, or run it again.",
    "MODEL_INVALID": "The optimiser rejected the model (an internal error). Nothing was changed.",
}


def explain_status(status: str) -> str:
    """Plain-language meaning of a solver result that produced no roster."""
    return SOLVER_TEXT.get(status, f"The optimiser stopped without a valid roster ({status}). Nothing was changed.")



def diagnose(policy: dict, agents: pd.DataFrame, leave: pd.DataFrame, ref: pd.DataFrame, freeze: int) -> list[dict]:
    """'proven' = a rule that cannot be met by construction; 'hypothesis' = likely pressure point."""
    out = []
    rules = policy["rules"]
    for (a, wk), g in ref[ref.day < freeze].assign(week=ref.day.map(week_of)).groupby(["agent_id", "week"]):
        nights = sum(is_night(policy, s) for s in g["shift"])
        if nights > rules["max_nights_per_week"]:
            out.append({"type": "proven", "text": f"{a} already works {nights} nights in frozen days of week "
                                                  f"{wk + 1}, above the limit of {rules['max_nights_per_week']}."})
    for q in queue_names(policy):
        if not agents.eligible.map(lambda e: q in e).any():
            out.append({"type": "proven", "text": f"No remaining agent is trained for {q}."})
    if not agents.night_ok.any():
        out.append({"type": "hypothesis", "text": "No agent may work nights; overnight queues cannot be staffed."})
    if len(agents) < 10:
        out.append({"type": "hypothesis", "text": f"Only {len(agents)} agents remain."})
    if not out:
        out.append({"type": "hypothesis", "text": "Solver stopped without a feasible plan within the time limit; "
                                                  "try fewer simultaneous changes."})
    return out


# ------------------------------------------------------------------ plans: draft -> validated -> published
def _plan(w, plan_id):
    return next(p for p in w.plans if p["id"] == plan_id)


def create_draft(w, roster: pd.DataFrame, name: str, source: str, by: str, notes: str = "") -> dict:
    from .workflow import now_label
    for p in w.plans:                       # one live draft at a time
        if p["status"] in ("draft", "validated"):
            p["status"] = "discarded"
    from .workflow import roster_fp
    plan = {"id": w.nid("PLAN"), "name": name, "status": "draft", "roster": roster.copy(), "created_by": by,
            "created": now_label(w), "source": source, "notes": notes, "base_fp": roster_fp(w.roster)}
    w.plans.append(plan)
    return plan


def review(w, plan_id: str) -> dict:
    """Pre-publication checklist for a plan against the live state."""
    plan = _plan(w, plan_id)
    ro = plan["roster"]
    days = open_days(w)
    agents, leave = w.data["agents"], w.data["leave"]
    viol = check(w.policy, agents, leave, ro)
    m_new = metrics(w.policy, agents, leave, w.req, ro, days, ref=w.roster)
    m_live = metrics(w.policy, agents, leave, w.req, w.roster, days)
    from .history import diff
    changes = [c for c in diff(w.policy, w.roster, ro) if c["day"] in days]
    open_alerts = [i for i in w.incidents if i.status in ("open", "needs_new_fix", "escalated")]
    checks = [
        ("Hard rules", not viol, "No violations" if not viol else f"{len(viol)} violation(s)"),
        ("Coverage not worse", m_new["Coverage %"] >= m_live["Coverage %"] - 0.5,
         f"{m_live['Coverage %']}% → {m_new['Coverage %']}%"),
        ("Overtime", m_new["OT hours"] <= m_live["OT hours"] + 0.01, f"{m_live['OT hours']:g} → {m_new['OT hours']:g} h"),
        ("Fairness", m_new["Fairness (std)"] <= m_live["Fairness (std)"] + 0.2,
         f"{m_live['Fairness (std)']} → {m_new['Fairness (std)']}"),
        ("Unresolved alerts", not open_alerts, f"{len(open_alerts)} open"),
    ]
    return {"plan": plan, "violations": viol, "by_rule": violation_summary(viol), "live": m_live, "new": m_new,
            "changes": changes, "affected_agents": sorted({c["agent_id"] for c in changes}), "checks": checks}


def validate(w, plan_id: str) -> dict:
    rv = review(w, plan_id)
    plan = rv["plan"]
    if plan["status"] == "draft" and not rv["violations"]:
        plan["status"] = "validated"
    return rv


def publish(w, plan_id: str, by: str) -> dict:
    """Make a validated plan live: frozen days stay as they are, agents are notified."""
    from .workflow import _log, notify
    from .workflow import roster_fp
    plan = _plan(w, plan_id)
    if plan["status"] != "validated":
        raise ValueError("Only a validated plan can be published.")
    if plan.get("base_fp") and plan["base_fp"] != roster_fp(w.roster):
        raise ValueError("Conflict: the published roster changed after this draft was created (for example an "
                         "approved absence or swap). Create a new draft so it includes those changes.")
    freeze = w.first_day + 1
    new = pd.concat([w.roster[w.roster.day < freeze], plan["roster"][plan["roster"].day >= freeze]],
                    ignore_index=True).sort_values(["day", "agent_id"]).reset_index(drop=True)
    viol = check(w.policy, w.data["agents"], w.data["leave"], new)
    if viol:
        raise ValueError(f"Publishing would break {len(viol)} rule(s); re-validate the plan.")
    from .history import diff
    changes = diff(w.policy, w.roster, new)
    for c in changes:
        notify(w, c["agent_id"], f"Schedule update for {c['date']}: {c['before']} → {c['after']}.")
    for p in w.plans:
        if p["status"] == "published":
            p["status"] = "superseded"
    plan["status"] = "published"
    plan["roster"] = new.copy()
    w.roster = new
    _log(w, "Replan", plan["name"], f"{len(changes)} shift changes", decision="Publish", actor=by,
         outcome=f"published {plan['id']}; {len({c['agent_id'] for c in changes})} agents notified")
    return {"changes": len(changes), "agents": len({c["agent_id"] for c in changes})}


# ------------------------------------------------------------------ planned vs actual
def planned_vs_actual(w) -> pd.DataFrame:
    """Past days: the original published plan versus the live roster after changes, plus RECORDED attendance.
    Coverage columns are roster-vs-forecast (scheduled), not measured service: there are no actual volumes."""
    base = w.plans[0]["roster"] if w.plans else w.roster
    per_day = intervals_per_day(w.policy)
    mask = warmup_mask(w.policy)
    req0 = w.req0 if w.req0 is not None else w.req
    cov_plan, cov_now = coverage(w.policy, base), coverage(w.policy, w.roster)
    rows = []
    for d in range(0, w.today + 1):
        sl = slice(d * per_day, min((d + 1) * per_day, w.now_t))
        if sl.stop <= sl.start:
            continue
        m = mask[sl]
        need0, need = (req0[:, sl] * m).sum(), (w.req[:, sl] * m).sum()
        planned = int((base.day == d).sum())
        codes = [records.day_record(w, a, d)["code"] for a in w.data["agents"].agent_id]
        rows.append({
            "Date": day_label(w.policy, d) + (" (so far)" if d == w.today else ""),
            "Planned shifts": planned,
            "Worked (recorded)": sum(c in ("worked", "overtime", "extra", "swap", "left_early") for c in codes),
            "Absent / no-show": sum(c in ("sick", "no_show") for c in codes),
            "No attendance record": sum(c == "unrecorded" for c in codes),
            "Planned coverage %": round(100 * (np.minimum(req0[:, sl], cov_plan[:, sl]) * m).sum() / max(need0, 1), 1),
            "Live-roster coverage %": round(100 * (np.minimum(w.req[:, sl], cov_now[:, sl]) * m).sum() / max(need, 1), 1),
            "Demand revision %": round(100 * (need - need0) / max(need0, 1), 1),
        })
    return pd.DataFrame(rows)
