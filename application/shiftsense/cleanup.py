"""Targeted removal of records left in the LIVE workspace by the old 'Load demo story' flow (which, before the
demo was isolated, replaced the live workspace with the story).

Identification is conservative. A record is removed only if BOTH hold:
  1. it matches a demo-story signature (the story's fixed reporters, notes and seeded history), and
  2. no genuine user action ever touched it: genuine actions go through the services layer and leave a
     hash-chained audit row naming the record; story records were written directly and have none.
Anything that matches a signature but was touched by a genuine action is reported as 'uncertain' and left as is.

Kept: the published plan, every genuine record, accounts, the hash-chained audit log, and the append-only
schedule change history. The in-workspace decision-log entries written by the story are removed from the
workspace (they would otherwise count as live decisions); the cleanup itself is audited with the full list.
"""
from __future__ import annotations

from . import comms
from .config import day_label

STORY_REPORTERS = {"Attendance desk", "Team lead TL-3"}
STORY_TL2 = "Fatima Khan (Team lead)"           # old role label; genuine actions today say "Team Lead"


def _genuine_records(w) -> set[str]:
    return {str(r["record"]) for r in comms.audit_rows(w.run_id)}


def plan(w) -> dict:
    """Dry run: what would be removed, restored, kept and left uncertain. Changes nothing."""
    if getattr(w, "scenario", False):
        raise ValueError("Cleanup applies to the live workspace only.")
    touched = _genuine_records(w)
    pub = next((p for p in w.plans if p["status"] == "published"), None)
    out = {"incidents": [], "requests": [], "leave_rows": [], "inbox": [], "decision_log": [], "roster_restore": [],
           "notices": [], "uncertain": [], "kept": []}

    def story_incident(i):
        first = i.history[0] if i.history else ""
        return i.reported_by in STORY_REPORTERS or (i.reported_by == STORY_TL2 and "family emergency" in first)

    story_inc = []
    for i in w.incidents:
        if not story_incident(i):
            continue
        if i.id in touched:
            out["uncertain"].append(f"{i.id}: matches the story but a genuine action touched it")
        else:
            story_inc.append(i)
    log_actor = {(r.actor, r.state) for r in w.audit.records if r.event == "LeaveRequested"}
    story_req = []
    for r in w.requests:
        sig = r.get("simulated_history") or (
            r.get("note") == "family function"
            and (r["agent_id"], f"{r['agent_id']} {day_label(w.policy, r['day'])}") in log_actor)
        if not sig:
            continue
        if r["id"] in touched:
            out["uncertain"].append(f"{r['id']}: matches the story but a genuine action touched it")
        else:
            story_req.append(r)

    # agent-days the story changed, and whether a genuine record also touches them
    genuine_days = set()
    for i in w.incidents:
        if i not in story_inc:
            genuine_days |= {(a, i.day) for a in i.absent} | {(a.agent_id, a.day) for a in i.applied}
    for r in w.requests:
        if r not in story_req:
            genuine_days.add((r["agent_id"], r["day"]))
    days = set()
    for i in story_inc:
        days |= {(a, i.day) for a in i.absent}
        for a in i.applied:
            days.add((a.agent_id, a.day))
            if getattr(a, "swap_day", None) is not None:
                days.add((a.agent_id, a.swap_day))
    lv = w.data["leave"]
    absent_keys = {(a, i.day) for i in story_inc for a in i.absent}
    for idx, row in lv.iterrows():
        if (row.agent_id, int(row.day)) in absent_keys and str(row.get("reason", "")).startswith("absent ("):
            out["leave_rows"].append({"index": int(idx), "agent_id": row.agent_id, "day": int(row.day),
                                      "reason": row.get("reason", "")})
    for key in sorted(days, key=lambda k: (k[1], k[0])):
        if key in genuine_days:
            out["uncertain"].append(f"{key[0]} {day_label(w.policy, key[1])}: changed by the story AND by a genuine "
                                    "record; shift left as is")
            continue
        if pub is None:
            out["uncertain"].append("no published plan to restore from")
            break
        cur = w.roster[(w.roster.agent_id == key[0]) & (w.roster.day == key[1])]
        orig = pub["roster"][(pub["roster"].agent_id == key[0]) & (pub["roster"].day == key[1])]
        desc = lambda df: "Off" if df.empty else f"{df.iloc[0]['shift']} · {df.iloc[0]['queue']}"   # noqa: E731
        if desc(cur) != desc(orig) or not cur.reset_index(drop=True).equals(orig.reset_index(drop=True)):
            out["roster_restore"].append({"agent_id": key[0], "day": key[1], "date": day_label(w.policy, key[1]),
                                          "now": desc(cur), "published": desc(orig)})
    ids = {i.id for i in story_inc} | {r["id"] for r in story_req}
    out["incidents"] = [{"id": i.id, "what": f"{i.kind} {', '.join(i.absent)} {day_label(w.policy, i.day)}",
                         "status": i.status, "reported_by": i.reported_by} for i in story_inc]
    out["requests"] = [{"id": r["id"], "what": f"{r['agent_id']} {day_label(w.policy, r['day'])}", "state": r["state"],
                        "why": "simulated prior-period record" if r.get("simulated_history") else "story request"}
                       for r in story_req]
    out["inbox"] = [{"id": n["id"], "agent_id": n["agent_id"], "text": n["text"][:80]} for n in w.notifications
                    if n.get("incident") in ids or n.get("request") in ids]
    absent_states = {f"{a} absent" for i in story_inc for a in i.absent}
    req_states = {f"{r['agent_id']} {day_label(w.policy, r['day'])}" for r in story_req}
    for k, rec in enumerate(w.audit.records):
        story = (rec.event == "AgentAbsent" and any(rec.state.startswith(s) for s in absent_states)
                 and (rec.actor in STORY_REPORTERS or rec.actor == STORY_TL2)) \
            or (rec.event == "Recommendation" and rec.state in ids and (rec.actor in STORY_REPORTERS or rec.actor == STORY_TL2)) \
            or (rec.event == "LeaveRequested" and rec.state in req_states and rec.actor in {r["agent_id"] for r in story_req})
        if story:
            out["decision_log"].append({"index": k, "event": rec.event, "actor": rec.actor, "state": rec.state})
    out["notices"] = [{"id": n["id"], "title": n["title"]} for n in comms.all_notices(w.run_id)
                      if n["sender"] == "Demo story" or n["related_ref"] in ids]
    out["kept"] = [f"published plan {pub['id']}" if pub else "no published plan",
                   f"{len(w.incidents) - len(story_inc)} other alerts",
                   f"{len(w.requests) - len(story_req)} other requests",
                   f"{len(lv) - len(out['leave_rows'])} leave rows (approved leave etc.)",
                   f"{len(w.attendance)} attendance records",
                   f"{len(comms.audit_rows(w.run_id))} hash-chained audit rows (append-only)",
                   "schedule change history (append-only)", "accounts", "the demo scenario slot (separate sandbox)"]
    return out


def apply(w, p: dict) -> dict:
    """Execute a dry-run plan produced by plan(w) on the same workspace. Call inside a transaction."""
    import pandas as pd
    ids_inc = {x["id"] for x in p["incidents"]}
    ids_req = {x["id"] for x in p["requests"]}
    w.incidents = [i for i in w.incidents if i.id not in ids_inc]
    w.requests = [r for r in w.requests if r["id"] not in ids_req]
    drop = [x["index"] for x in p["leave_rows"]]
    w.data = {**w.data, "leave": w.data["leave"].drop(index=drop).reset_index(drop=True)}
    inbox = {x["id"] for x in p["inbox"]}
    w.notifications = [n for n in w.notifications if n["id"] not in inbox]
    logs = {x["index"] for x in p["decision_log"]}
    w.audit.records = [r for k, r in enumerate(w.audit.records) if k not in logs]
    if p["roster_restore"]:
        pub = next(x for x in w.plans if x["status"] == "published")["roster"]
        keys = {(x["agent_id"], x["day"]) for x in p["roster_restore"]}
        keep = w.roster[[(a, d) not in keys for a, d in zip(w.roster.agent_id, w.roster.day)]]
        back = pub[[(a, d) in keys for a, d in zip(pub.agent_id, pub.day)]]
        w.roster = pd.concat([keep, back], ignore_index=True).sort_values(["day", "agent_id"]).reset_index(drop=True)
    comms.delete_notices(w.run_id, [x["id"] for x in p["notices"]])
    return {k: len(v) for k, v in p.items() if k not in ("kept", "uncertain")}
