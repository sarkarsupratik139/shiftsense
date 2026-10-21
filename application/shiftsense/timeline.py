"""Shared history service: one event model for all roles, scoped on the server.

Sources (nothing is invented; every row comes from a stored record):
  * the hash-chained audit log (every action taken through services)
  * schedule change history (append-only before -> after per agent-day)
  * alert histories (incl. system-originated events such as simulated no-shows and the demo story)
  * workflow log entries written by the system itself (auto-approvals, attendance system, demo story)
  * attendance reminders (in-app; created / in inbox / opened / checked in kept apart) and email attempts
  * notifications and messages the user may see
  * governance case events

Scope is derived from the signed-in account, never from a caller-supplied identifier:
  Agent -> own records · Team Lead -> own team · Ops -> cross-team operations (no account admin, no private
  messages) · Analyst -> planning, publication, imports, demo, what-if, schedule changes · Admin -> everything
  except other people's private messages.
"""
from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

from . import attendance, comms, history, mailer

AID = re.compile(r"\bA\d{2}\b")
PLANNING_ACTIONS = ("Create draft", "Validate plan", "Publish plan", "Restore roster version", "Import committed",
                    "Import failed", "Load demo story", "Unload demo scenario", "Advance clock", "Advance live clock",
                    "What-if run", "Escalate risk to Ops", "Legacy demo cleanup")
ACCOUNT_ACTIONS = ("Create account", "Update account", "Email verification sent", "Email verified")
COLUMNS = ["When", "Type", "Event", "Record", "Actor", "Agents", "Team", "Before", "After", "Reason", "Outcome",
           "Context", "Source"]


def sim_t(policy: dict, label: str | None) -> int | None:
    """'Wed 07 Oct 06:30' -> interval index (None if not a simulated timestamp)."""
    if not label:
        return None
    m = re.match(r"\w{3} (\d{2}) (\w{3}) (\d{2}):(\d{2})", label)
    if not m:
        return None
    start = datetime.strptime(policy["horizon"]["start_date"], "%Y-%m-%d").date()
    try:
        d = datetime.strptime(f"{m.group(1)} {m.group(2)} {start.year}", "%d %b %Y").date()
    except ValueError:
        return None
    per_day = 24 * 60 // policy["horizon"]["interval_minutes"]
    return (d - start).days * per_day + (int(m.group(3)) * 60 + int(m.group(4))) // policy["horizon"]["interval_minutes"]


def _team_of(w) -> dict:
    return dict(zip(w.data["agents"].agent_id, w.data["agents"].team))


def _ev(**k) -> dict:
    base = {c: "" for c in COLUMNS}
    base.update(k)
    base["Agents"] = ", ".join(sorted(k.get("agents", set()))) if not isinstance(k.get("Agents"), str) else k["Agents"]
    base.pop("agents", None)
    return base


def collect(w) -> list[dict]:
    """Every history event of this workspace run, unscoped (internal; use events() from outside)."""
    ctx = history.context_of(w.run_id)
    team = _team_of(w)
    inc = {i.id: i for i in w.incidents}
    req = {r["id"]: r for r in w.requests}
    out = []

    def agents_for(record: str) -> set[str]:
        if record in inc:
            i = inc[record]
            return set(i.absent) | {a.agent_id for a in i.applied}
        if record in req:
            r = req[record]
            return {r["agent_id"]} | ({r["alternative"]["partner"]} if r.get("alternative", {}) and
                                      r["alternative"].get("partner") else set())
        return set(AID.findall(record or ""))

    def team_for(record: str, ags: set[str]) -> str:
        if record in inc:
            return inc[record].owner_team
        ts = {team.get(a) for a in ags if team.get(a)}
        return ts.pop() if len(ts) == 1 else ""

    for r in comms.audit_rows(w.run_id):
        ags = agents_for(r["record"])
        kind = ("Account" if r["action"] in ACCOUNT_ACTIONS else "Planning" if r["action"] in PLANNING_ACTIONS
                else "Governance" if r["action"].startswith(("Case:", "Flag raised")) else
                "Attendance" if r["action"] in ("Check-in", "Attendance reminder") else "Decision")
        out.append(_ev(When=r["sim_time"], Type=kind, Event=r["action"], Record=r["record"], Actor=r["actor"],
                       agents=ags, Team=team_for(r["record"], ags), Before=r["before"], After=r["after"],
                       Reason=r["reason"], Outcome=r["status"], Context=ctx, Source=f"audit #{r['id']}"))
    for c in history.changes(w.run_id):
        for d in c["details"] or [None]:
            ags = {d["agent_id"]} if d else set()
            out.append(_ev(When=c["sim_time"], Type="Schedule change", Event=c["event"], Record=c.get("related") or "",
                           Actor=c["actor"], agents=ags, Team=team.get(d["agent_id"], "") if d else "",
                           Before=f"{d['date']}: {d['before']}" if d else "", After=d["after"] if d else c["summary"],
                           Reason=c["reason"], Outcome=c.get("status") or "Applied", Context=ctx,
                           Source=f"change #{c['id']}"))
    for i in w.incidents:
        ags = set(i.absent) | {a.agent_id for a in i.applied}
        for line in i.history:
            when, _, text = line.partition(" · ")
            out.append(_ev(When=when, Type="Alert", Event=text[:140], Record=i.id, Actor="", agents=ags,
                           Team=i.owner_team, Outcome=i.status, Context=ctx, Source=f"alert {i.id}"))
    for rec in w.audit.records:                     # only what the system itself did (named users are in the audit)
        if "(" in rec.actor:
            continue
        ags = set(AID.findall(f"{rec.state} {rec.recommendation}")) | ({rec.actor} if AID.fullmatch(rec.actor) else set())
        out.append(_ev(When=rec.sim_time, Type="System", Event=f"{rec.event}: {rec.decision or rec.state}"[:140],
                       Record=rec.state[:40], Actor=rec.actor or "system", agents=ags, Team=team_for("", ags),
                       Reason=rec.reason, Outcome=rec.outcome, Context=ctx, Source="workflow log"))
    for r in attendance.log(w.run_id):
        n = comms.related_notice(w.run_id, "checkin", r["id"])
        opened = "opened" if n and n["read_by"] else ("not opened" if n else "")
        out.append(_ev(When=r["sim_time"], Type="Attendance",
                       Event="Reminder (in-app)" if r["channel"] == "in_app" else "Reminder (SMS, retired)",
                       Record=f"{r['agent_id']} {r['shift']}", Actor=r["sent_by"], agents={r["agent_id"]},
                       Team=team.get(r["agent_id"], ""), After=attendance.state_text(r) + (f" · {opened}" if opened else ""),
                       Reason=r["error"], Outcome=f"checked in {r['used_sim']} ({r['used_via']})" if r["used_via"]
                       else ("" if not r["used_sim"] else str(r["used_sim"])), Context=ctx,
                       Source=f"reminder #{r['id']}"))
    rem = {str(r["id"]): r["agent_id"] for r in attendance.log(w.run_id)}
    for e in mailer.log(w.run_id):
        if e["kind"] == "reminder":
            ags, tm = {rem[e["ref"]]} if e["ref"] in rem else set(), team.get(rem.get(e["ref"], ""), "")
        elif e["kind"] == "request":
            rid = e["ref"].split(":")[0]
            ags = {req[rid]["agent_id"]} if rid in req else set()
            tm = team.get(next(iter(ags), ""), "")
        elif e["kind"] == "escalation":
            iid = e["ref"].split("#")[0]
            ags, tm = set(), inc[iid].owner_team if iid in inc else ""
        else:
            ags, tm = set(), ""
        out.append(_ev(When=e["sim_time"], Type="Account" if e["kind"] == "verify" else "Email",
                       Event=f"Email: {e['subject'][:110]}", Record=e["ref"], Actor="ShiftSense", agents=ags, Team=tm,
                       After=mailer.LABEL.get(e["status"], e["status"]), Reason=e["detail"],
                       Outcome=f"attempts {e['attempts']}", Context=ctx, Source=f"email #{e['id']}",
                       _to=e["username"]))
    for c in comms.cases(w.run_id):
        for e in comms.case_events(c["id"]):
            out.append(_ev(When=e["sim_time"], Type="Governance", Event=f"Case {c['id']}: {e['action']}",
                           Record=f"case {c['id']}", Actor=e["actor"], agents=set(), Team=c["team"] or "",
                           Before=e["from_status"] or "", After=e["to_status"], Reason=e["note"] or "",
                           Context=ctx, Source=f"case {c['id']}", _subject=c["subject_user"] or ""))
    return out


def _personal(w, user) -> list[dict]:
    """Notifications and messages this user may read (never other people's)."""
    from . import services
    ctx = history.context_of(w.run_id)
    out = []
    for n in comms.notices_for(user, w.run_id, w.now_t):
        out.append(_ev(When=n["sim_time"], Type="Notification", Event=n["title"], Record=n["related_ref"] or "",
                       Actor=n["sender"], agents=set(), After=n["status"], Outcome=n["priority"], Context=ctx,
                       Source=f"notice #{n['id']}"))
    if user.get("agent_id"):
        for n in w.notifications:
            if n["agent_id"] == user["agent_id"]:
                out.append(_ev(When=n["time"], Type="Notification", Event=n["text"][:140], Record=n.get("incident") or
                               n.get("request") or "", Actor="ShiftSense", agents={n["agent_id"]}, After=n["status"],
                               Context=ctx, Source=f"inbox {n['id']}"))
    with comms._db() as con:
        threads = [r[0] for r in con.execute("SELECT DISTINCT thread FROM messages WHERE run_id = ?", (w.run_id,))]
    for t in threads:
        try:
            ok = services.can_access_thread(w, user, t)
        except Exception:                              # thread refers to a record that no longer exists
            ok = False
        if ok:
            for m in comms.thread(w.run_id, t):
                out.append(_ev(When=m["sim_time"], Type="Message", Event=m["body"][:140], Record=t, Actor=m["sender"],
                               agents=set(), Context=ctx, Source=f"message #{m['id']}"))
    return out


def events(w, user: dict, *, agent_id: str | None = None, team: str | None = None) -> list[dict]:
    """Events this user may see. A caller-supplied agent or team is only a filter inside the user's own scope:
    asking for anything outside it is refused (no identifier substitution)."""
    from . import services
    Denied = services.Denied
    services._active(user)
    role = user["role"]
    if role == "agent":
        own = user.get("agent_id")
        if agent_id not in (None, own) or team not in (None,):
            raise Denied("You can only see your own history.")
        rows = [e for e in collect(w) if own in e["Agents"].split(", ")
                and e["Type"] not in ("Account", "Governance", "Planning")]
    elif role == "team_lead":
        if team not in (None, user.get("team")):
            raise Denied("You can only see your own team's history.")
        tm = _team_of(w)
        if agent_id is not None and tm.get(agent_id) != user.get("team"):
            raise Denied("That agent is not in your team.")
        mine = user.get("team")
        rows = [e for e in collect(w) if e["Type"] not in ("Account", "Planning")
                and (e["Team"] == mine or any(tm.get(a) == mine for a in e["Agents"].split(", ") if a))
                and (e["Type"] != "Governance" or e.get("_subject") == user["username"])]
        rows = [_team_only(e, tm, mine) for e in rows]
    elif role == "ops_head":
        rows = [e for e in collect(w) if e["Type"] != "Account"]
    elif role == "analyst":
        rows = [e for e in collect(w) if e["Type"] in ("Planning", "Schedule change", "System")
                or e["Event"] in ("Report call spike", "Call spike")]
    elif role == "hr":
        hr_events = ("Data change", "Preferences updated", "Base data loaded", "Rebuilt from reference data")
        rows = [e for e in collect(w) if str(e["Event"]).startswith(hr_events)
                or e["Event"] in ("Approve request", "Reject request", "Revoke approval")]
    elif role == "admin":
        rows = collect(w)
    else:
        raise Denied("No history access.")
    rows += _personal(w, user)
    if agent_id:
        rows = [e for e in rows if agent_id in e["Agents"].split(", ")]
    for e in rows:
        e.pop("_subject", None)
        e.pop("_to", None)
        e["t"] = sim_t(w.policy, e["When"])
    rows.sort(key=lambda e: (e["t"] is None, -(e["t"] or 0)))
    return rows


def _team_only(e: dict, tm: dict, mine: str) -> dict:
    """Team Leads see other teams' agents only as a count."""
    ags = [a for a in e["Agents"].split(", ") if a]
    keep = [a for a in ags if tm.get(a) == mine]
    if len(keep) != len(ags):
        e = dict(e, Agents=", ".join(keep + [f"+{len(ags) - len(keep)} other team"]))
    return e


def query(rows: list[dict], *, types=None, text: str = "", day_from: int | None = None, day_to: int | None = None,
          per_day: int = 48, newest_first: bool = True, page: int = 1, size: int = 25) -> tuple[pd.DataFrame, int]:
    """Filter, sort and paginate scoped rows. Returns (page frame, total matching)."""
    out = rows
    if types:
        out = [e for e in out if e["Type"] in types]
    if text:
        q = text.lower()
        out = [e for e in out if q in " ".join(str(e[c]) for c in COLUMNS).lower()]
    if day_from is not None:
        out = [e for e in out if e["t"] is not None and e["t"] >= day_from * per_day]
    if day_to is not None:
        out = [e for e in out if e["t"] is not None and e["t"] < (day_to + 1) * per_day]
    if not newest_first:
        out = list(reversed(out))
    total = len(out)
    size = max(1, int(size))
    page = max(1, min(int(page), max(1, -(-total // size))))
    chunk = out[(page - 1) * size: page * size]
    return pd.DataFrame(chunk, columns=COLUMNS), total


def export_csv(w, user: dict, **filters) -> str:
    """CSV of exactly the rows this user may see (same scope as the screen)."""
    rows = events(w, user)
    df, _ = query(rows, size=10 ** 6, **filters)
    return df.to_csv(index=False)
