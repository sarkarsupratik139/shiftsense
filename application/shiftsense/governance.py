"""Discrepancy detection for Admin oversight. Every flag carries its evidence, comparison group,
period and the threshold that triggered it. A flag is a signal for review, never proof."""
from __future__ import annotations

from collections import Counter, defaultdict

from . import auth
from .config import day_label

NOTE = "Signal for review, not a finding. Check eligibility, workload and legitimate exceptions."


def _tl_user(name_with_role: str) -> tuple[str | None, str | None]:
    """'Rohit Verma (Team lead)' -> ('tl1', 'TL-1')."""
    name = name_with_role.split(" (")[0]
    for u, r in auth.config()["users"].items():
        if r["name"] == name and r["role"] == "team_lead":
            return u, r.get("team")
    return None, None


def detect(w) -> list[dict]:
    g = w.policy.get("governance", {})
    team_of = dict(zip(w.data["agents"].agent_id, w.data["agents"].team))
    days = w.policy["horizon"]["days"]
    period = f"{day_label(w.policy, 0)} – {day_label(w.policy, days - 1)}"
    flags = []
    decided = [r for r in w.requests if r.get("decided_by") and r["state"] in ("Approved", "Rejected", "Revoked")]

    # 1) approval-rate differences: one agent vs peers under the same team lead
    by_tl = defaultdict(list)
    for r in decided:
        by_tl[r["decided_by"]].append(r)
    for tl, rs in by_tl.items():
        per_agent = defaultdict(list)
        for r in rs:
            per_agent[r["agent_id"]].append(r)
        for agent, mine in per_agent.items():
            n = len(mine)
            if n < g.get("min_requests", 3):
                continue
            appr = sum(r["state"] in ("Approved", "Revoked") for r in mine)
            peers = [r for a, lst in per_agent.items() if a != agent for r in lst]
            if not peers:
                continue
            p_appr = sum(r["state"] in ("Approved", "Revoked") for r in peers)
            rate, prate = appr / n, p_appr / len(peers)
            if rate - prate >= g.get("approval_gap", 0.35):
                user, team = _tl_user(tl)
                flags.append({
                    "key": f"approval_gap|{tl}|{agent}", "type": "Approval-rate difference",
                    "title": f"{tl.split(' (')[0]} approves {agent} far more often than peers",
                    "team": team or team_of.get(agent), "subject_user": user,
                    "metrics": {"Requests": n, "Approved": appr, "Rejected": n - appr, "Approval rate": f"{rate:.0%}",
                                "Peer requests": len(peers), "Peer approval rate": f"{prate:.0%}",
                                "Short-coverage (Red) requests approved": sum(r["status"] == "Red" and r["state"] != "Rejected"
                                                                              for r in mine)},
                    "comparison": f"Other agents decided by the same team lead ({len({r['agent_id'] for r in peers})} agents)",
                    "period": period, "request_types": dict(Counter("Swap" if r.get("kind") == "swap" else "Time off"
                                                                    for r in mine)),
                    "eligibility": dict(Counter(r["status"] for r in mine)),
                    "consequence": "Approvals while the team was short reduce coverage on those days.",
                    "threshold": f"≥ {g.get('min_requests', 3)} requests and approval rate ≥ "
                                 f"{g.get('approval_gap', 0.35):.0%} above peers",
                    "agent": agent, "note": NOTE})

    # 2) approvals that left the team short
    for tl, rs in by_tl.items():
        red = [r for r in rs if r["status"] == "Red" and r["state"] in ("Approved", "Revoked")]
        if len(red) >= g.get("shortage_approvals", 2):
            user, team = _tl_user(tl)
            flags.append({
                "key": f"shortage_approvals|{tl}", "type": "Approvals causing shortage",
                "title": f"{tl.split(' (')[0]} approved {len(red)} requests while the team was short",
                "team": team, "subject_user": user,
                "metrics": {"Short-coverage approvals": len(red), "All decisions": len(rs),
                            "Agents": len({r['agent_id'] for r in red})},
                "comparison": "Configured limit per team lead", "period": period,
                "request_types": dict(Counter("Swap" if r.get("kind") == "swap" else "Time off" for r in red)),
                "eligibility": {"Red": len(red)}, "consequence": "Each left at least one interval below requirement.",
                "threshold": f"≥ {g.get('shortage_approvals', 2)} Red approvals", "note": NOTE})

    # 3) repeated overtime for the same people
    ot = Counter()
    for inc in w.incidents:
        for a in inc.applied:
            if a.kind in ("open_shift", "extension") and inc.action_status.get(a.id) in ("confirmed", "waiting"):
                ot[a.agent_id] += 1
    for agent, n in ot.items():
        if n >= g.get("ot_repeat", 2):
            flags.append({
                "key": f"ot_repeat|{agent}", "type": "Repeated overtime",
                "title": f"{agent} received {n} overtime assignments", "team": team_of.get(agent), "subject_user": None,
                "metrics": {"Overtime assignments": n, "Team average": round(
                    sum(v for a, v in ot.items() if team_of.get(a) == team_of.get(agent)) /
                    max(1, sum(1 for a in team_of if team_of[a] == team_of.get(agent))), 2)},
                "comparison": "Agents in the same team", "period": period, "request_types": {},
                "eligibility": {}, "consequence": "Fatigue and fairness risk; overtime cost concentrates on one person.",
                "threshold": f"≥ {g.get('ot_repeat', 2)} overtime assignments", "agent": agent, "note": NOTE})

    # 4) repeated swaps between the same two people
    pairs = Counter(tuple(sorted((r["agent_id"], r["alternative"]["partner"])))
                    for r in decided if r.get("kind") == "swap" and r["state"] == "Approved")
    for (a1, a2), n in pairs.items():
        if n >= g.get("swap_pair_repeat", 2):
            flags.append({
                "key": f"swap_pair|{a1}|{a2}", "type": "Repeated swaps",
                "title": f"{a1} and {a2} swapped {n} times", "team": team_of.get(a1), "subject_user": None,
                "metrics": {"Approved swaps": n}, "comparison": "Configured limit per pair", "period": period,
                "request_types": {"Swap": n}, "eligibility": {}, "consequence": "May hide a standing schedule preference.",
                "threshold": f"≥ {g.get('swap_pair_repeat', 2)} swaps per pair", "note": NOTE})

    # 5) frequent overrides by one team lead
    over = Counter(r.actor for r in w.audit.records if r.decision in ("Reject", "Modify", "Escalate")
                   and "(Team lead)" in r.actor)
    for tl, n in over.items():
        if n >= g.get("overrides", 3):
            user, team = _tl_user(tl)
            flags.append({
                "key": f"overrides|{tl}", "type": "Frequent overrides",
                "title": f"{tl.split(' (')[0]} overrode {n} recommendations", "team": team, "subject_user": user,
                "metrics": {"Rejections / modifications / escalations": n},
                "comparison": "Configured limit per team lead", "period": period, "request_types": {},
                "eligibility": {}, "consequence": "Could point to poor recommendations or to local constraints the "
                                                  "system does not know.",
                "threshold": f"≥ {g.get('overrides', 3)} overrides", "note": NOTE})
    return flags
