"""Ablation study and hypotheses H1-H5 on one seeded synthetic dataset. All simulated."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import baseline, data_gen, optimizer, recovery, risk, staffing
from .config import queue_names
from .constraints import check
from .kpis import plan_kpis
from .roster import diff_count

APPROACHES = ["Manual heuristic", "Optimizer only", "Optimizer + fairness + stability", "ShiftSense full loop"]


def build_base(policy: dict) -> dict:
    """Data, staffing chain and the three plans every page and the evidence share."""
    data = data_gen.generate(policy)
    fc = staffing.forecast(policy, data["history"])
    st = staffing.staffing_chain(policy, fc)
    req = st["req"]
    heur = baseline.plan(policy, data["agents"], data["leave"], req)
    opt_only = optimizer.plan(policy, data["agents"], data["leave"], req,
                              optimizer.PlanOptions(fairness=False, preferences=False, label="Optimizer only"))
    full = optimizer.plan(policy, data["agents"], data["leave"], req)
    return {"data": data, "fc": fc, "st": st, "req": req,
            "plans": {"Manual heuristic": heur, "Optimizer only": opt_only, "ShiftSense optimizer": full}}


def replan_event(policy: dict, base: dict) -> dict:
    """Mid-horizon change: forecast revision + new approved leave. Returns new inputs."""
    ev = policy["evidence"]
    rng = np.random.default_rng(policy["seed"] + 7)
    fc = {k: v.copy() for k, v in base["fc"].items()}
    per_day = 48
    for q, up in ev["replan_demand_uplift"].items():
        qi = queue_names(policy).index(q)
        for k in fc:
            fc[k][qi, 7 * per_day:] *= up
    req2 = staffing.staffing_chain(policy, fc)["req"]
    plan = base["plans"]["ShiftSense optimizer"]["roster"]
    later = plan[plan.day >= ev["replan_day"] + 2]
    pick = later.iloc[rng.choice(len(later), size=ev["replan_new_leaves"], replace=False)]
    leave2 = pd.concat([base["data"]["leave"], pick[["agent_id", "day"]]], ignore_index=True)
    return {"req": req2, "leave": leave2, "new_leave": pick[["agent_id", "day"]].reset_index(drop=True)}


def _stability_changes(policy, base, event) -> dict:
    """Schedule changes each approach makes to absorb the re-plan event."""
    ev = policy["evidence"]
    data = base["data"]
    freeze = ev["replan_day"] + 1                         # inside 24 h: no changes
    out = {}
    # manual: patch one exception at a time - just remove the new leave shifts
    heur = base["plans"]["Manual heuristic"]["roster"]
    keys = set(zip(event["new_leave"].agent_id, event["new_leave"].day))
    patched = heur[[(a, d) not in keys for a, d in zip(heur.agent_id, heur.day)]]
    out["Manual heuristic"] = diff_count(heur, patched)
    ref = base["plans"]["Optimizer only"]["roster"]
    res = optimizer.plan(policy, data["agents"], event["leave"], event["req"],
                         optimizer.PlanOptions(fairness=False, preferences=False))
    out["Optimizer only"] = diff_count(ref, res["roster"])
    ref = base["plans"]["ShiftSense optimizer"]["roster"]
    res = optimizer.plan(policy, data["agents"], event["leave"], event["req"],
                         optimizer.PlanOptions(stability_ref=ref, freeze_before_day=freeze))
    out["Optimizer + fairness + stability"] = diff_count(ref, res["roster"])
    out["ShiftSense full loop"] = out["Optimizer + fairness + stability"]
    out["_stable_roster"] = res["roster"]
    return out


def _scenarios(policy: dict, base: dict) -> pd.DataFrame:
    """Inject K disruptions into each plan and recover (manual vs ShiftSense)."""
    ev = policy["evidence"]
    data, req = base["data"], base["req"]
    days = policy["horizon"]["days"]
    queues = queue_names(policy)
    rows = []
    plan_for = {"Manual heuristic": "Manual heuristic", "Optimizer only": "Optimizer only",
                "Optimizer + fairness + stability": "ShiftSense optimizer",
                "ShiftSense full loop": "ShiftSense optimizer"}
    for k in range(ev["scenarios"]):
        day = 1 + (k * 3) % (days - 3)
        focus = queues[k % 2 * 2]                        # alternate Claims-EN / Banking-EN
        now = day * 48 - (24 - ev["detect_hour"]) * 2
        for approach in APPROACHES:
            plan = base["plans"][plan_for[approach]]["roster"]
            absent = risk.pick_absentees(policy, plan, day, ev["absent_per_scenario"], seed=100 + k, focus_queue=focus)
            cur, lost = risk.inject_absence(plan, absent, day)
            ctx = recovery.Context(policy, data, req, plan, cur, day, now, absent)
            manual = recovery.baseline_recover(ctx, lost, seed=100 + k)
            if approach == "ShiftSense full loop":
                rec = recovery.recommend(ctx)
                e = rec["eval"]
                eq = recovery.cost_at_equal_coverage(ctx, rec["candidates"], manual["eval"]["deficit_after"])
                rows.append({"approach": approach, "scenario": k, "day": day, "ttc_min": rec["ttc_min"],
                             "restored_pct": e["restored_pct"], "cost": e["cost"], "ot_hours": e["ot_hours"],
                             "blast_agents": e["agents"], "violations": len(e["violations"]),
                             "solver_s": rec["latency_s"],                       # measured
                             "absent": ",".join(absent), "focus_queue": focus,
                             "gap_before_ph": float(e.get("short_head_hours_before", np.nan)),
                             "gap_after_ph": float(e["short_head_hours_after"]),
                             "manual_ttc_min": manual["ttc_min"], "manual_ot_hours": manual["eval"]["ot_hours"],
                             "manual_agents": manual["eval"]["agents"],
                             "manual_violations": len(manual["eval"]["violations"]),
                             "manual_gap_after_ph": float(manual["eval"]["short_head_hours_after"]),
                             "manual_cost": manual["eval"]["cost"], "manual_restored": manual["eval"]["restored_pct"],
                             "equal_cov_cost": eq["cost"] if eq else np.nan,
                             "escalated": e["deficit_after"] > 0})
            else:
                e = manual["eval"]
                rows.append({"approach": approach, "scenario": k, "day": day, "ttc_min": manual["ttc_min"],
                             "restored_pct": e["restored_pct"], "cost": e["cost"], "ot_hours": e["ot_hours"],
                             "blast_agents": e["agents"], "violations": len(e["violations"]),
                             "escalated": e["deficit_after"] > 0})
    return pd.DataFrame(rows)


def run(policy: dict, base: dict | None = None) -> dict:
    base = base or build_base(policy)
    data, req = base["data"], base["req"]
    ev = policy["evidence"]
    k = {
        "Manual heuristic": plan_kpis(policy, data, req, base["plans"]["Manual heuristic"]["roster"],
                                      base["plans"]["Manual heuristic"]["build_seconds"]),
        "Optimizer only": plan_kpis(policy, data, req, base["plans"]["Optimizer only"]["roster"],
                                    base["plans"]["Optimizer only"]["build_seconds"]),
        "Optimizer + fairness + stability": plan_kpis(policy, data, req, base["plans"]["ShiftSense optimizer"]["roster"],
                                                      base["plans"]["ShiftSense optimizer"]["build_seconds"]),
    }
    k["ShiftSense full loop"] = k["Optimizer + fairness + stability"]
    event = replan_event(policy, base)
    changes = _stability_changes(policy, base, event)
    sc = _scenarios(policy, base)
    med = sc.groupby("approach").median(numeric_only=True)

    n_agents = len(data["agents"])
    weeks = policy["horizon"]["days"] / 7
    manual_build = ev["manual_build_min_per_agent_week"] * n_agents * weeks
    rows = []
    for a in APPROACHES:
        solver_min = k[a]["Build time (s)"] / 60
        effort = manual_build if a == "Manual heuristic" else ev["review_minutes"] + solver_min
        rows.append({
            "Approach": a,
            "Coverage %": k[a]["Coverage %"],
            "Under-covered workload %": k[a]["Under-covered workload %"],
            "Understaffed intervals": k[a]["Understaffed intervals"],
            "Recovery OT hours (median)": med.loc[a, "ot_hours"],
            "Fairness (burden std)": k[a]["Fairness (burden std)"],
            "Schedule changes (re-plan)": changes[a],
            "Build effort (min)": effort,
            "Time-to-Cover (median min)": med.loc[a, "ttc_min"],
            "Gap restored % (median)": med.loc[a, "restored_pct"],
            "Recovery cost ₹ (median)": med.loc[a, "cost"],
            "Primary-queue share %": k[a]["Primary-queue share %"],
            "Hard violations": k[a]["Hard violations"],
        })
    ablation = pd.DataFrame(rows)

    # ---- hypotheses
    m, s = ablation.iloc[0], ablation.iloc[3]
    h = []
    red = 1 - s["Build effort (min)"] / m["Build effort (min)"]
    h.append(("H1", "Planning efficiency: roster preparation effort falls ≥ 70%",
              f"{red:.0%} less effort ({m['Build effort (min)']:.0f} → {s['Build effort (min)']:.1f} min); "
              f"coverage {s['Coverage %']:.1f}% vs {m['Coverage %']:.1f}%, fairness std {s['Fairness (burden std)']:.2f} vs {m['Fairness (burden std)']:.2f}",
              red >= 0.70 and s["Coverage %"] >= m["Coverage %"] and s["Fairness (burden std)"] <= m["Fairness (burden std)"]))
    uw_red = 1 - s["Under-covered workload %"] / m["Under-covered workload %"]
    paid_ok = k["ShiftSense full loop"]["Paid hours"] <= k["Manual heuristic"]["Paid hours"]
    h.append(("H2", "Service: under-covered workload falls ≥ 20% at equal or lower paid hours",
              f"{m['Under-covered workload %']:.1f}% → {s['Under-covered workload %']:.1f}% ({uw_red:.0%} lower); "
              f"paid hours {k['ShiftSense full loop']['Paid hours']:.0f} vs {k['Manual heuristic']['Paid hours']:.0f}",
              uw_red >= 0.20 and paid_ok))
    ss = sc[sc.approach == "ShiftSense full loop"]
    ttc = ss.ttc_min.median()
    h.append(("H3", "Recovery speed: median simulated Time-to-Cover under 10 minutes",
              f"median {ttc:.1f} min MODELLED (configured approval + confirmation minutes + measured solver time, median "
              f"{ss.solver_s.median():.2f} s); manual process on the SAME rosters and disruptions "
              f"{ss.manual_ttc_min.median():.0f} min MODELLED",
              ttc < 10))
    eq = ss.dropna(subset=["equal_cov_cost"])
    cheaper = (eq.equal_cov_cost < eq.manual_cost).mean() if len(eq) else 0
    h.append(("H4", "Recovery economics: equal coverage restored at lower cost than OT-first recovery",
              f"at the manual process's restored coverage, ShiftSense median ₹{eq.equal_cov_cost.median():,.0f} vs "
              f"₹{eq.manual_cost.median():,.0f} OT-first; cheaper in {cheaper:.0%} of {len(eq)} scenarios",
              len(eq) > 0 and eq.equal_cov_cost.median() < eq.manual_cost.median() and cheaper >= 0.5))
    all_v = (ablation["Hard violations"].sum() + sc.violations.sum()
             + len(check(policy, data["agents"], event["leave"], changes["_stable_roster"])))
    h.append(("H5", "Operational safety: zero hard-constraint violations",
              f"{all_v} violations across {len(ablation) - 1} plans, 1 re-plan and {len(sc)} recoveries (independent checker)",
              all_v == 0))
    hyp = pd.DataFrame(h, columns=["Hypothesis", "Statement", "Measured (simulated)", "Pass"])
    return {"ablation": ablation, "hypotheses": hyp, "scenarios": sc, "event": event,
            "same_roster": same_roster(ss), "provenance": provenance(policy)}


def same_roster(ss: pd.DataFrame) -> pd.DataFrame:
    """Fair recovery comparison: manual process vs ShiftSense on the SAME published roster, the SAME absent
    agents and the SAME demand, scenario by scenario (medians)."""
    rows = [("Gap left after recovery (people-h)", ss.manual_gap_after_ph.median(), ss.gap_after_ph.median(), "measured"),
            ("Gap restored %", ss.manual_restored.median(), ss.restored_pct.median(), "measured"),
            ("Incremental recovery cost ₹", ss.manual_cost.median(), ss.cost.median(), "configured rates"),
            ("Overtime hours", ss.manual_ot_hours.median(), ss.ot_hours.median(), "measured"),
            ("Employees affected", ss.manual_agents.median(), ss.blast_agents.median(), "measured"),
            ("Hard-rule violations (total)", int(ss.manual_violations.sum()), int(ss.violations.sum()),
             "independent checker"),
            ("Time-to-Cover (min)", ss.manual_ttc_min.median(), ss.ttc_min.median(), "MODELLED from configured minutes"),
            ("Solver runtime (s)", None, round(float(ss.solver_s.median()), 2), "measured on this machine"),
            ("Scenarios fully covered", int((ss.manual_gap_after_ph <= 0).sum()), int((ss.gap_after_ph <= 0).sum()),
             f"of {len(ss)}")]
    return pd.DataFrame(rows, columns=["Metric", "Manual process", "ShiftSense", "Basis"])


def provenance(policy: dict) -> pd.DataFrame:
    ev, r = policy["evidence"], policy["recovery"]
    return pd.DataFrame([
        ("Data", f"synthetic, seed {policy['seed']}"),
        ("Disruptions", f"{ev['scenarios']} scenarios × {ev['absent_per_scenario']} absent agents, reported "
                        f"{ev['detect_hour']}:00 the evening before"),
        ("Roster for the fair comparison", "the optimised published roster, identical for both approaches"),
        ("Manual process model", f"detect after {r['manual_detect_minutes']} min, {r['manual_call_minutes']} min per "
                                 f"call, {r['manual_accept_prob']:.0%} accept; one exception at a time (assumption)"),
        ("ShiftSense time model", f"solver time (measured) + {r['approval_minutes']} min approval + slowest "
                                  f"confirmation {r['confirm_minutes']} (assumption)"),
        ("Not claimed", "real-world recovery times; the time figures are a model to be validated with EXL"),
    ], columns=["Input", "Value"])
