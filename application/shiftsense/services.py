"""Permission-checked actions. The UI calls these, never the workflow directly.

Every action: 1) checks permission and scope (team, own record, no self-approval) on the server,
2) checks workflow state (the workflow refuses invalid transitions), 3) updates the record,
4) notifies only the people who need to know, 5) writes a hash-chained audit row.
"""
from __future__ import annotations

import os
from datetime import datetime

from . import accounts, attendance, auth, comms, governance, hr, mailer, planning, priority, workflow
from .config import day_label, shift_intervals


class Denied(PermissionError):
    pass


def _active(user: dict | None) -> dict:
    """The account must still be active, with the same role and team it signed in with."""
    cur = auth.current(user)
    if cur is None:
        raise Denied("Your account is not active. Contact an administrator.")
    if (cur.get("role") != user.get("role") or (cur.get("team") or None) != (user.get("team") or None)
            or (cur.get("agent_id") or None) != (user.get("agent_id") or None)):
        raise Denied("Your access has changed. Sign out and sign in again.")
    return cur


def _need(user: dict | None, perm: str):
    if not user or not auth.can(_active(user), perm):
        from .logs import get
        get().warning("permission denied user=%s role=%s needs=%s", (user or {}).get("username"),
                      (user or {}).get("role"), perm)
        raise Denied("You are not authorised to do this.")


def _log(msg: str):
    from .logs import get
    get().warning(msg)


def _who(user: dict) -> str:
    return f"{user['name']} ({user['role_label']})"


def _team_of(w, agent_id: str) -> str:
    return str(w.data["agents"].set_index("agent_id").loc[agent_id, "team"])


def _audit(w, user, action, record, before="", after="", reason="", status=""):
    comms.audit(w.run_id, _who(user) if user else "system", user["role"] if user else "system", action, record,
                str(before), str(after), reason, status, workflow.now_label(w))


def _note(w, to: dict, title: str, **kw):
    return comms.notify(w.run_id, to, title, sim_time=workflow.now_label(w), **kw)


def _email(w, to: dict, *, kind: str, ref: str, subject: str, body: str) -> list[dict]:
    """Optional email copy of an in-app notice to every active account the notice is addressed to. Never replaces
    the in-app notice; never raises; at most one email per (record, recipient) however often this is called."""
    if "agent" in to:
        targets = [a for a in [accounts.for_agent(to["agent"])] if a]
    elif "user" in to:
        targets = [a for a in [accounts.get(to["user"])] if a and a["active"]]
    else:
        targets = [a for a in accounts.all_accounts() if a["active"] and a["role"] == to.get("role")
                   and to.get("team") in (None, a["team"])]
    out = []
    for a in targets:
        try:
            out.append({"username": a["username"], **mailer.send(
                w.run_id, f"{w.run_id}|{kind}|{ref}|{a['username']}", kind=kind, ref=str(ref), account=a,
                subject=f"ShiftSense: {subject}", body=body + "\n\nOpen ShiftSense to see the details and act. "
                "This email is a copy of an in-app notification.", sim_time=workflow.now_label(w),
                live=not getattr(w, "scenario", False))})
        except Exception as e:                      # email is optional: the in-app notice already exists
            _log(f"email error {kind} {ref}: {type(e).__name__}")
    return out


def _incident(w, inc_id):
    return next(i for i in w.incidents if i.id == inc_id)


def _request(w, req_id):
    return next(r for r in w.requests if r["id"] == req_id)


def _alert_notice(w, inc, sender):
    if inc.status in ("no_gap",):
        return
    s = priority.assess(w, inc)
    per_day = 48
    due = inc.created_t + 4 if inc.created_t is not None else None
    if s["hours_left"] is not None:
        due = min(due or 10 ** 9, w.now_t + int(s["hours_left"] * 2))
    names = w.data["agents"].set_index("agent_id")["name"]
    who = ", ".join(names.get(a, a) for a in inc.absent)
    what = {"no_show": "no-show", "left_early": "left early", "spike": ""}.get(inc.kind, "absent")
    title = inc.title if inc.kind == "spike" else f"{who} · {what}"
    _note(w, {"role": "team_lead", "team": inc.owner_team}, f"Staffing alert: {title}",
          body=f"{s['short_ph']:g} people-hours short from {s['starts']}; fix ready for review.",
          priority=s["severity"], sender=sender, related=("incident", inc.id), action_required=True,
          due_t=due, dedupe=f"alert|{w.run_id}|{inc.id}|{len(inc.history)}")


def notify_alert(w, inc, sender: str = "system"):
    """Public hook for alerts raised by the system itself (attendance no-shows)."""
    _alert_notice(w, inc, sender)


# ------------------------------------------------------------------ absences, spikes
def report_absence(w, user, agent_ids: list[str], day: int, reason: str = "unwell"):
    if user["role"] == "agent":
        _need(user, "report_self")
        if agent_ids != [user["agent_id"]]:
            raise Denied("Agents can only report their own absence.")
    else:
        _need(user, "report_absence")
        if any(_team_of(w, a) != user.get("team") for a in agent_ids):
            raise Denied("You can only report absences for your own team.")
    inc = workflow.report_absence(w, agent_ids, day, _who(user), reason)
    _alert_notice(w, inc, _who(user))
    _audit(w, user, "Report absence", inc.id, "scheduled", "absent", reason, inc.status)
    return inc


def report_left_early(w, user, agent_id: str, reason: str = "unwell"):
    if user["role"] == "agent":
        _need(user, "report_self")
        if agent_id != user["agent_id"]:
            raise Denied("Agents can only report for themselves.")
    else:
        _need(user, "report_absence")
        if _team_of(w, agent_id) != user.get("team"):
            raise Denied("Not your team.")
    inc = workflow.report_left_early(w, agent_id, _who(user), reason)
    _alert_notice(w, inc, _who(user))
    _audit(w, user, "Left early", inc.id, "on shift", "left", reason, inc.status)
    return inc


def report_spike(w, user, queue: str, pct: float, hours: float):
    _need(user, "report_spike")
    inc = workflow.demand_spike(w, queue, pct, hours, _who(user))
    _alert_notice(w, inc, _who(user))
    _audit(w, user, "Call spike", inc.id, "forecast", f"+{pct:g}% for {hours:g} h", "", inc.status)
    return inc


# ------------------------------------------------------------------ team lead decisions
def decide_incident(w, user, inc_id: str, choice: str, reason: str = "", remove: list[str] | None = None):
    _need(user, "decide_fix")
    inc = _incident(w, inc_id)
    if inc.owner_team != user.get("team"):
        raise Denied("This alert belongs to another team.")
    before = inc.status
    with workflow.transaction(w):
        if choice == "Approve":
            workflow.approve_fix(w, inc, _who(user), reason)
        elif choice == "Modify":
            if not remove:
                raise ValueError("Pick at least one step to remove.")
            workflow.change_fix(w, inc, remove, _who(user), reason)
        elif choice == "Reject":
            workflow.reject_fix(w, inc, _who(user), reason)
        elif choice == "Escalate":
            workflow.escalate_fix(w, inc, _who(user), reason)
        else:
            raise ValueError("Unknown decision.")
    if inc.status not in workflow.DECIDABLE:
        comms.resolve_related(w.run_id, "incident", inc.id)
    if inc.status == "escalated":
        s = priority.assess(w, inc)
        _note(w, {"role": "ops_head"}, f"Escalation from {inc.owner_team}: {inc.title or inc.id}",
              body=f"{s['short_ph']:g} people-hours uncovered from {s['starts']}. Reason: {reason or 'remaining gap'}",
              priority="Critical", sender=_who(user), related=("incident", inc.id), action_required=True,
              due_t=w.now_t + 4, dedupe=f"esc|{w.run_id}|{inc.id}|{len(inc.history)}")
        _email(w, {"role": "ops_head"}, kind="escalation", ref=f"{inc.id}#{len(inc.history)}",
               subject=f"Escalation from {inc.owner_team}: {inc.title or inc.id}",
               body=f"{s['short_ph']:g} people-hours uncovered from {s['starts']}. An Ops decision is needed.")
    _audit(w, user, f"{choice} fix", inc.id, before, inc.status, reason, choice)
    return inc


def respond(w, user, notif_id: str, accept: bool):
    _active(user)
    n = next(x for x in w.notifications if x["id"] == notif_id)
    if user["role"] != "agent" or n["agent_id"] != user.get("agent_id"):
        raise Denied("Only the addressed agent can respond.")
    if n["status"] != "new":
        raise ValueError(f"Already answered ({n['status']}). Refresh to see the latest state.")
    with workflow.transaction(w):
        workflow.respond(w, notif_id, accept, _who(user))
    if n.get("incident"):
        inc = _incident(w, n["incident"])
        if not accept and inc.status == "needs_new_fix":
            _note(w, {"role": "team_lead", "team": inc.owner_team}, f"{user['name']} declined · new fix ready",
                  priority="Warning", sender=_who(user), related=("incident", inc.id), action_required=True,
                  due_t=w.now_t + 2, dedupe=f"decl|{w.run_id}|{inc.id}|{len(inc.history)}")
    if n["kind"] == "swap_ask" and accept:
        r = _request(w, n["request"])
        _note(w, {"role": "team_lead", "team": _team_of(w, r["agent_id"])}, f"Swap to approve: {r['agent_id']} ↔ "
              f"{user['agent_id']}", priority="Normal", sender=_who(user), related=("request", r["id"]),
              action_required=True, dedupe=f"swapok|{w.run_id}|{r['id']}")
    _audit(w, user, "Accept" if accept else "Decline", n.get("incident") or n.get("request") or notif_id,
           "asked", n["status"])


# ------------------------------------------------------------------ requests
def _paid(leave_type: str | None) -> bool:
    return hr.LEAVE_TYPES.get(leave_type or "", ("", True, ""))[1]


def _hold_reason(w, agent_id: str, day: int, leave_type: str, alternative) -> tuple[list[dict], str]:
    """HR-03 triggers plus HR-02 eligibility: a paid day off without a verified, sufficient balance is never
    auto-approved (unresolved, not assumed)."""
    if alternative is not None:
        return [], ""
    trig = hr.evaluate_triggers(w, agent_id, day, leave_type)
    if trig:
        return trig, "HR review: " + "; ".join(t["reason"] for t in trig)
    if _paid(leave_type):
        b = hr.balance(agent_id, leave_type, w.run_id)
        if b["state"] != "verified" or b["available"] < 1:
            return [], "Leave balance unresolved (no verified balance); a person must decide."
    return [], ""


def _hr_reviewer() -> str | None:
    """Assign review tasks to the active HR user with the fewest open tasks (None = HR queue)."""
    pool = [a["username"] for a in accounts.all_accounts() if a["active"] and a["role"] == "hr"]
    if not pool:
        return None
    from collections import Counter
    load = Counter(t["assigned_to"] for t in hr._q("SELECT assigned_to FROM hr_review_tasks WHERE status='open'"))
    return min(pool, key=lambda u: (load[u], u))


def submit_leave(w, user, day: int, alternative=None, note: str = "", leave_type: str = "annual"):
    _need(user, "request_leave")
    if leave_type not in hr.LEAVE_TYPES:
        raise ValueError(f"Choose a leave type: {', '.join(hr.LEAVE_TYPES)}.")
    agent_id = user["agent_id"]
    trig, hold = _hold_reason(w, agent_id, day, leave_type, alternative)
    with hr._db() as con, workflow.transaction(w):         # balance debit and approval succeed or fail together
        r = workflow.submit_leave(w, agent_id, day, _who(user), alternative, note, leave_type, hold)
        if r["state"] == "Approved" and _paid(leave_type):
            hr.add_transaction(con, agent_id, leave_type, "debit", -1, r["id"], "approved leave (automatic)",
                               "ShiftSense", w.run_id)
    for t in trig:
        tid = hr.create_review_task(w.run_id, r["id"], agent_id, t["trigger"], t["reason"], _hr_reviewer())
        _note(w, {"role": "hr"}, f"Leave review needed: {r['id']}", body=t["reason"], priority="Normal",
              sender="ShiftSense", related=("hr_review", tid), action_required=True,
              dedupe=f"hrrev|{w.run_id}|{tid}")
    if r["state"] == "Pending":
        sick = leave_type == "sick"
        _note(w, {"role": "team_lead", "team": _team_of(w, agent_id)},
              f"Time-off request: {user['name']} · {day_label(w.policy, day)}",
              body=r["detail"] + (" Waiting for HR review before approval." if trig else
                                  f" {hold}" if hold else "") + (" Leave type is held by HR." if sick else ""),
              priority={"Red": "Warning", "Amber": "Normal"}.get(r["status"], "Info"),
              sender=_who(user), related=("request", r["id"]), action_required=True, due_t=day * 48,
              dedupe=f"req|{w.run_id}|{r['id']}")
    _audit(w, user, "Request time off", r["id"], "", r["state"], "" if leave_type == "sick" else note, r["status"])
    return r


def request_view(user: dict, r: dict) -> dict:
    """What a viewer may see of a request: sick-leave type and the agent's note stay with HR and the requester;
    others see only what the operational decision needs (the day, the coverage check, HR review status)."""
    if r.get("leave_type") != "sick" or user.get("role") == "hr" or user.get("agent_id") == r["agent_id"]:
        return r
    return {**r, "leave_type": "leave (details held by HR)", "note": ""}


def request_swap(w, user, day: int, option: dict):
    _need(user, "request_leave")
    r = workflow.request_swap(w, user["agent_id"], day, option, _who(user))
    _audit(w, user, "Request swap", r["id"], "", r["state"], "", option["partner"])
    return r


def get_request(w, user, req_id: str) -> dict:
    """Read one request: the requesting agent, their team lead (own team) or an all-teams role."""
    _active(user)
    r = _request(w, req_id)
    if user["role"] == "agent":
        if r["agent_id"] != user.get("agent_id"):
            raise Denied("You can only see your own requests.")
    elif user["role"] == "team_lead":
        if _team_of(w, r["agent_id"]) != user.get("team"):
            raise Denied("This request belongs to another team.")
    elif not auth.can(user, "all_teams"):
        raise Denied("You are not authorised to see this request.")
    return r


def withdraw(w, user, req_id: str):
    _active(user)
    r = _request(w, req_id)
    if user["role"] != "agent" or r["agent_id"] != user.get("agent_id"):
        raise Denied("Only the requesting agent can withdraw.")
    before = r["state"]
    workflow.withdraw_request(w, req_id, _who(user))
    hr.close_tasks(w.run_id, req_id, "request withdrawn")
    comms.resolve_related(w.run_id, "request", req_id)
    _audit(w, user, "Withdraw request", req_id, before, r["state"])
    return r


def decide_request(w, user, req_id: str, approve: bool, reason: str = ""):
    _need(user, "approve_requests")
    r = _request(w, req_id)
    if user.get("agent_id") == r["agent_id"]:
        raise Denied("You cannot decide your own request.")
    if not auth.can(user, "all_teams") and _team_of(w, r["agent_id"]) != user.get("team"):
        raise Denied("This request belongs to another team.")
    before = r["state"]
    if approve and r.get("kind") == "leave" and hr.blocking_review(w.run_id, req_id):
        raise ValueError("HR review is in progress: approval waits for HR's recommendation. You can still decline "
                         "with a reason.")
    with hr._db() as con, workflow.transaction(w):  # rules re-checked at commit; balance debit in the same step
        workflow.decide_leave(w, req_id, approve, _who(user), reason)
        if approve and r.get("kind") == "leave" and r.get("alternative") is None and _paid(r.get("leave_type")):
            hr.add_transaction(con, r["agent_id"], r.get("leave_type") or "annual", "debit", -1, req_id,
                               "approved leave", _who(user), w.run_id)
    hr.close_tasks(w.run_id, req_id, f"request {r['state'].lower()}")
    comms.resolve_related(w.run_id, "request", req_id)
    _audit(w, user, "Approve request" if approve else "Reject request", req_id, before, r["state"], reason, r["status"])
    _email(w, {"agent": r["agent_id"]}, kind="request", ref=f"{req_id}:{r['state']}",
           subject=f"Your request {req_id} was {r['state'].lower()}",
           body=f"Request {req_id}: {r['state']}." + (f" Reason: {reason}" if reason else ""))
    return r


def revoke(w, user, req_id: str, reason: str):
    _need(user, "approve_requests")
    r = _request(w, req_id)
    if not auth.can(user, "all_teams") and _team_of(w, r["agent_id"]) != user.get("team"):
        raise Denied("This request belongs to another team.")
    with hr._db() as con, workflow.transaction(w):  # the day goes back to the balance in the same step
        workflow.revoke_leave(w, req_id, _who(user), reason)
        deb = hr.debited(req_id, w.run_id)
        if deb:
            hr.add_transaction(con, r["agent_id"], deb["leave_type_id"], "credit", 1, req_id,
                               f"approval revoked: {reason}", _who(user), w.run_id)
    _audit(w, user, "Revoke approval", req_id, "Approved", "Revoked", reason)
    return r


# ------------------------------------------------------------------ operations
def ops_decide(w, user, inc_id: str, decision: str, note: str):
    _need(user, "ops_decide")
    inc = _incident(w, inc_id)
    before = inc.status
    if decision == "Return to team lead":
        workflow.ops_return(w, inc, _who(user), note)
        _note(w, {"role": "team_lead", "team": inc.owner_team}, f"Ops returned {inc.id}: {note}", priority="Warning",
              sender=_who(user), related=("incident", inc.id), action_required=True, due_t=w.now_t + 4,
              dedupe=f"ret|{w.run_id}|{inc.id}|{len(inc.history)}")
    else:
        residual = priority.assess(w, inc)["short_ph"]
        workflow.ops_close(w, inc, decision, note, _who(user), residual_ph=residual)
        _note(w, {"role": "team_lead", "team": inc.owner_team}, f"Ops decision on {inc.id}: {decision}",
              body=note, priority="Info", sender=_who(user), related=("incident", inc.id))
    comms.resolve_related(w.run_id, "incident", inc.id) if inc.status == "closed" else None
    _audit(w, user, f"Ops: {decision}", inc.id, before, inc.status, note, decision)
    return inc


def assign_followup(w, user, team: str, note: str, due_hours: float, related=None):
    _need(user, "ops_decide")
    if not note.strip():
        raise ValueError("Describe the follow-up.")
    nid = _note(w, {"role": "team_lead", "team": team}, f"Follow-up from Operations: {note}", priority="Warning",
                sender=_who(user), related=related, action_required=True, due_t=w.now_t + int(due_hours * 2),
                dedupe=f"fu|{w.run_id}|{team}|{note}")
    _audit(w, user, "Assign follow-up", team, "", "assigned", note)
    return nid


def complete_followup(w, user, notice_id: int, note: str):
    """Team lead reports a follow-up as done; Ops is told. Only the addressed team lead can do this."""
    _need(user, "decide_fix")
    if not note.strip():
        raise ValueError("Say what was done.")
    if not comms.complete(notice_id, user["username"], lambda rec: comms._matches(rec, user)):
        raise Denied("This follow-up is not addressed to you.")
    _note(w, {"role": "ops_head"}, f"Follow-up done by {user['name']}", body=note[:200], priority="Normal",
          sender=_who(user), dedupe=f"fudone|{w.run_id}|{notice_id}")
    _audit(w, user, "Follow-up done", f"notice {notice_id}", "assigned", "done", note)


# ------------------------------------------------------------------ analyst
def publish_plan(w, user, plan_id: str, reviewed: bool = False):
    """Publish a validated plan. Requires the planner's review confirmation; the plan is re-checked
    against the hard rules at publish time and frozen days are kept as they are."""
    _need(user, "replan")
    if getattr(w, "scenario", False):
        raise ValueError("A demo scenario is never published. Drafts are published to the live roster from "
                         "Review & publish.")
    if not reviewed:
        raise ValueError("Confirm that you reviewed the changes and affected agents before publishing.")
    out = planning.publish(w, plan_id, _who(user))
    teams = sorted({_team_of(w, a) for a in w.data["agents"].agent_id})
    for t in teams:
        _note(w, {"role": "team_lead", "team": t}, f"New roster published ({plan_id})",
              body=f"{out['changes']} shift changes across all teams.", priority="Info", sender=_who(user),
              dedupe=f"pub|{w.run_id}|{plan_id}|{t}")
    _audit(w, user, "Publish plan", plan_id, "validated", "published", "", f"{out['changes']} changes")
    return out


def draft_plan(w, user, roster, name: str, source: str):
    """Create a draft in the LIVE workspace `w` (a separate proposal; the published roster is unchanged).
    A draft built from a demo scenario says so in its source."""
    _need(user, "replan")
    if getattr(w, "scenario", False):
        raise ValueError("Drafts belong to the live workspace; create them from the planner, not inside the scenario.")
    plan = planning.create_draft(w, roster, name, source, _who(user))
    _audit(w, user, "Create draft", plan["id"], "", "draft", name)
    return plan


def restore_plan_as_draft(w, user, plan_id: str, reason: str):
    """Roll back to an earlier published roster version the SAFE way: it becomes a new draft that must be
    validated, reviewed and published like any other plan. Days that have started stay as they are."""
    _need(user, "replan")
    if getattr(w, "scenario", False):
        raise ValueError("Rollback applies to the live roster only.")
    if not reason.strip():
        raise ValueError("A reason is required to restore an earlier roster.")
    old = next((p for p in w.plans if p["id"] == plan_id), None)
    if old is None or old["status"] not in ("published", "superseded"):
        raise ValueError("Only a previously published roster version can be restored.")
    plan = planning.create_draft(w, old["roster"], f"Restore of {plan_id}", f"rollback of {plan_id}", _who(user),
                                 notes=reason)
    _audit(w, user, "Restore roster version", plan["id"], plan_id, "draft", reason)
    return plan


def validate_plan(w, user, plan_id: str):
    _need(user, "replan")
    rv = planning.validate(w, plan_id)
    _audit(w, user, "Validate plan", plan_id, "draft", rv["plan"]["status"], f"{len(rv['violations'])} violations")
    return rv


# ------------------------------------------------------------------ governance
def scan(w, user) -> list[int]:
    _need(user, "governance")
    new = []
    for f in governance.detect(w):
        cid = comms.open_case(w.run_id, f, workflow.now_label(w))
        if cid:
            new.append(cid)
            _audit(w, user, "Flag raised", f"case {cid}", "", "Flagged", f["threshold"])
    _audit(w, user, "Governance scan", "discrepancy scan", "", f"{len(new)} new case(s)", "", "done")
    return new


ADMIN_ACTIONS = {"Acknowledge": "Under Review", "Request explanation": "Explanation Requested",
                 "Add note": None, "Refer to Operations": "Referred to Operations",
                 "Request corrective action": "Action Required", "Dismiss": "Dismissed", "Close": "Closed",
                 "Mark resolved": "Resolved"}


def case_action(w, user, case_id: int, action: str, note: str):
    """Admin oversight actions on a governance case. No operational data is changed."""
    _active(user)
    c = next(x for x in comms.cases(w.run_id) if x["id"] == case_id)
    if action == "Explain":
        if user["role"] != "team_lead" or c["subject_user"] != user["username"]:
            raise Denied("Only the team lead asked can explain.")
        if c["status"] != "Explanation Requested":
            raise ValueError("No explanation is pending.")
        comms.case_event(case_id, _who(user), user["role"], "Explanation", note, "Under Review", workflow.now_label(w))
        comms.resolve_related(w.run_id, "case", case_id)
        _note(w, {"role": "admin"}, f"Explanation received for case {case_id}", body=note[:200], priority="Normal",
              sender=_who(user), related=("case", case_id))
    elif action in ("Authorise corrective action", "Decline corrective action", "Resolve referral"):
        _need(user, "ops_decide")
        to = {"Authorise corrective action": "Resolved", "Decline corrective action": "Under Review",
              "Resolve referral": "Resolved"}[action]
        if not note.strip():
            raise ValueError("A documented reason is required.")
        if action == "Authorise corrective action" and c["status"] != "Action Required":
            raise ValueError("No corrective action is awaiting authorisation.")
        comms.case_event(case_id, _who(user), user["role"], action, note, to, workflow.now_label(w),
                         outcome=note if to == "Resolved" else None)
        comms.resolve_related(w.run_id, "case", case_id)
        _note(w, {"role": "admin"}, f"Ops: {action} · case {case_id}", body=note[:200], priority="Normal",
              sender=_who(user), related=("case", case_id))
        if action == "Authorise corrective action" and c["subject_user"]:
            _note(w, {"user": c["subject_user"]}, f"Formal corrective action recorded (case {case_id})", body=note[:200],
                  priority="Warning", sender=_who(user), related=("case", case_id))
    else:
        _need(user, "governance")
        if action not in ADMIN_ACTIONS:
            raise ValueError("Unknown action.")
        if action in ("Dismiss", "Close", "Request corrective action", "Request explanation", "Refer to Operations",
                      "Mark resolved") and not note.strip():
            raise ValueError("A documented reason is required.")
        if action == "Request explanation" and not c["subject_user"]:
            raise ValueError("No team lead is linked to this case.")
        to = ADMIN_ACTIONS[action]
        due = w.now_t + 2 * w.policy.get("governance", {}).get("explanation_hours", 24) \
            if action == "Request explanation" else None
        comms.case_event(case_id, _who(user), user["role"], action, note, to, workflow.now_label(w),
                         reviewer=_who(user) if action == "Acknowledge" else None, due_t=due,
                         outcome=note if to in ("Closed", "Dismissed", "Resolved") else None)
        if action == "Request explanation":
            _note(w, {"user": c["subject_user"]}, f"Explanation requested · case {case_id}", body=note[:200],
                  priority="Warning", sender=_who(user), related=("case", case_id), action_required=True, due_t=due,
                  dedupe=f"expl|{w.run_id}|{case_id}|{note[:40]}")
        if action in ("Refer to Operations", "Request corrective action"):
            _note(w, {"role": "ops_head"}, f"{'Referral' if action.startswith('Refer') else 'Authorise corrective action'}"
                  f" · case {case_id}: {c['title']}", body=note[:200], priority="Warning", sender=_who(user),
                  related=("case", case_id), action_required=True, due_t=w.now_t + 8,
                  dedupe=f"ref|{w.run_id}|{case_id}|{action}")
    _audit(w, user, f"Case: {action}", f"case {case_id}", c["status"], "", note)


# ------------------------------------------------------------------ messages
def thread_members(w, thread_key: str) -> dict:
    """Who may read/write a thread: {'agents': {...}, 'teams': {...}, 'roles': {...}, 'users': {...}}."""
    kind, ref = thread_key.split(":", 1)
    if kind == "agent":
        return {"agents": {ref}, "teams": {_team_of(w, ref)}, "roles": set(), "users": set()}
    if kind == "incident":
        inc = _incident(w, ref)
        return {"agents": set(), "teams": {inc.owner_team}, "roles": {"ops_head"}, "users": set()}
    if kind == "request":
        r = _request(w, ref)
        return {"agents": {r["agent_id"]}, "teams": {_team_of(w, r["agent_id"])}, "roles": set(), "users": set()}
    if kind == "case":
        c = next(x for x in comms.cases(w.run_id) if x["id"] == int(ref))
        return {"agents": set(), "teams": set(), "roles": {"admin", "ops_head"},
                "users": {c["subject_user"]} if c["subject_user"] else set()}
    raise ValueError("Unknown thread.")


def can_access_thread(w, user, thread_key: str) -> bool:
    m = thread_members(w, thread_key)
    return (user.get("agent_id") in m["agents"] or user.get("username") in m["users"] or user["role"] in m["roles"]
            or (user["role"] == "team_lead" and user.get("team") in m["teams"]))


def post_message(w, user, thread_key: str, body: str):
    _active(user)
    if not can_access_thread(w, user, thread_key):
        raise Denied("You are not part of this conversation.")
    mid = comms.post(w.run_id, thread_key, _who(user), user["role"], body, workflow.now_label(w))
    m = thread_members(w, thread_key)
    for a in m["agents"] - {user.get("agent_id")}:
        _note(w, {"agent": a}, f"Message from {user['name']}", body=body[:160], sender=_who(user),
              related=("thread", thread_key))
    for t in m["teams"]:
        if not (user["role"] == "team_lead" and user.get("team") == t):
            _note(w, {"role": "team_lead", "team": t}, f"Message from {user['name']}", body=body[:160],
                  sender=_who(user), related=("thread", thread_key))
    for r in m["roles"] - {user["role"]}:
        _note(w, {"role": r}, f"Message from {user['name']}", body=body[:160], sender=_who(user),
              related=("thread", thread_key))
    for u in m["users"] - {user.get("username")}:
        _note(w, {"user": u}, f"Message from {user['name']}", body=body[:160], sender=_who(user),
              related=("thread", thread_key))
    return mid


# ------------------------------------------------------------------ overdue sweep
def sweep_overdue(w) -> int:
    """Escalate overdue critical team-lead alerts to Operations once (deduplicated)."""
    raised = 0
    limit = int(2 * w.policy["recovery"].get("tl_overdue_hours", 2))     # intervals of 30 min
    for inc in w.incidents:
        if inc.status in workflow.DECIDABLE and inc.created_t is not None and w.now_t - inc.created_t >= limit:
            s = priority.assess(w, inc)
            if s["severity"] == "Critical":
                if comms.notify(w.run_id, {"role": "ops_head"}, f"Overdue at team lead: {inc.title or inc.id}",
                                body=f"Open for {(w.now_t - inc.created_t) / 2:g} h; {s['short_ph']:g} people-hours short.",
                                priority="Critical", sender="system", related=("incident", inc.id),
                                sim_time=workflow.now_label(w), dedupe=f"overdue|{w.run_id}|{inc.id}"):
                    raised += 1
    if getattr(w, "ref_linked", False) and not getattr(w, "scenario", False):
        raised += remind_open_situations(w)
    return raised


def remind_open_situations(w) -> int:
    """Open-ended situations: remind Ops to update or close them every N hours (N chosen per situation)."""
    from . import refdata
    n = 0
    default_h = (w.policy.get("recovery") or {}).get("situation_review_hours", 2)
    for sit in refdata.current("situations").values():
        if (sit.get("status") or "Active") != "Active" or sit.get("end"):
            continue
        every = max(1, int(sit.get("review_hours") or default_h)) * 2          # in 30-minute intervals
        t0 = refdata.t_index(w.policy, sit["start"])
        if w.now_t < t0 + every:
            continue
        k = (w.now_t - t0) // every
        if comms.notify(w.run_id, {"role": "ops_head"}, f"Update or close situation: {sit['title']}",
                        body=f"{sit['id']} has no end yet. Is it still going on? Update its effect or close it.",
                        priority="Warning", sender="system", related=("situation", sit["id"]), action_required=True,
                        sim_time=workflow.now_label(w), dedupe=f"sitrev|{w.run_id}|{sit['id']}|{k}"):
            n += 1
    return n


def _agent_name(w, agent_id: str) -> str:
    names = dict(zip(w.data["agents"].agent_id, w.data["agents"].name))
    return names.get(agent_id, agent_id)


def seed_notices(w):
    """After a demo story is loaded: route its open items to the right inboxes (deduplicated)."""
    for inc in w.incidents:
        if inc.status in workflow.DECIDABLE:
            _alert_notice(w, inc, "Demo story")
        elif inc.status == "escalated":
            _note(w, {"role": "ops_head"}, f"Escalation from {inc.owner_team}: {inc.title or inc.id}", priority="Critical",
                  sender="Demo story", related=("incident", inc.id), action_required=True, due_t=w.now_t + 4,
                  dedupe=f"esc|{w.run_id}|{inc.id}|seed")
    for r in w.requests:
        if r["state"] == "Pending":
            _note(w, {"role": "team_lead", "team": _team_of(w, r["agent_id"])},
                  f"Time-off request: {_agent_name(w, r['agent_id'])} · {day_label(w.policy, r['day'])}",
                  body=r["detail"],
                  priority="Warning" if r["status"] == "Red" else "Normal", sender="Demo story",
                  related=("request", r["id"]), action_required=True, due_t=r["day"] * 48,
                  dedupe=f"req|{w.run_id}|{r['id']}")


# ------------------------------------------------------------------ demo handling (Analyst)
def require_demo(user):
    _need(user, "manage_demo")


# ------------------------------------------------------------------ accounts (Admin: people and contact details)
def _account_view(a: dict | None) -> str:
    if not a:
        return ""
    keep = ("name", "employee_id", "role", "team", "agent_id", "contact_pref", "active", "email_verified")
    return "; ".join(f"{k}={a.get(k)}" for k in keep) + (f"; email={accounts.mask_email(a['email'])}" if a.get("email") else "")


def _teams(w) -> set[str]:
    return set(w.data["agents"].team)


def create_account(w, user, username: str, fields: dict, password: str) -> dict:
    """Admin creates a business-role account. The Admin role cannot be created here."""
    _need(user, "manage_accounts")
    a = accounts.create(username, fields, password, by=_who(user), editor=user["username"],
                        agents=w.data["agents"], teams=_teams(w))
    _audit(w, user, "Create account", f"user {a['username']}", "", _account_view(a), "", a["role"])
    return a


def update_account(w, user, username: str, fields: dict, expected_version: int, reason: str = "") -> dict:
    """Admin edits an account. Role changes need a reason; the version check prevents silent overwrites."""
    _need(user, "manage_accounts")
    old = accounts.get(username)
    if old and fields.get("role", old["role"]) != old["role"] and not reason.strip():
        raise ValueError("A reason is required to change someone's role.")
    if old and not fields.get("active", old["active"]) and old["active"] and not reason.strip():
        raise ValueError("A reason is required to deactivate an account.")
    before, after = accounts.update(username, fields, expected_version, by=_who(user), editor=user["username"],
                                    agents=w.data["agents"], teams=_teams(w))
    _audit(w, user, "Update account", f"user {username}", _account_view(before), _account_view(after), reason,
           after["role"])
    return after


# ------------------------------------------------------------------ attendance reminders and check-in
def _shift_row(w, agent_id: str, day: int):
    ro = w.roster[(w.roster.agent_id == agent_id) & (w.roster.day == day)]
    return None if ro.empty else ro.iloc[0].to_dict()


def send_attendance_reminder(w, user, agent_id: str, day: int, *, resend: bool = False) -> dict:
    """Team lead (own team) sends an in-app attendance reminder, plus an email copy when configured and the
    agent has a verified address. A reminder never records attendance; only the agent's own check-in does."""
    _need(user, "send_reminder")
    if _team_of(w, agent_id) != user.get("team"):
        raise Denied("You can only remind agents in your own team.")
    r = _shift_row(w, agent_id, day)
    if r is None:
        raise ValueError("This agent has no shift that day.")
    start = shift_intervals(w.policy, day, r["shift"], r["ext_before"], r["ext_after"]).start
    if w.now_t > start + attendance.CLOSES_AFTER:
        raise ValueError("That shift has already started; check-in is closed.")
    if w.attendance.get((agent_id, int(day))):
        raise ValueError("Attendance is already recorded for that shift.")
    acct = accounts.for_agent(agent_id)
    shift = f"{day_label(w.policy, day)} {workflow.shift_text(w.policy, r)}"
    out = attendance.create(w.run_id, agent_id=agent_id, day=int(day), shift=shift, start_t=start, account=acct,
                            sim_time=workflow.now_label(w), sent_by=_who(user), resend=resend)
    if acct:
        nid = _note(w, {"agent": agent_id}, f"Check in for your shift · {day_label(w.policy, day)}",
                    body=f"{workflow.shift_text(w.policy, r)}. Check in when you arrive (opens 30 min before the start, "
                         f"closes 1 h after).", priority="Normal", sender=_who(user), related=("checkin", out["id"]),
                    action_required=True, due_t=out["expires_t"], dedupe=f"chk|{w.run_id}|{out['id']}")
        attendance.set_inbox(out["id"], nid)
        out["status"] = "in_inbox" if nid else out["status"]
        out["notice_id"] = nid
    em = _email(w, {"agent": agent_id}, kind="reminder", ref=str(out["id"]),
                subject=f"Check in for your {shift} shift",
                body=f"Your team lead sent a reminder for your {shift} shift. Check-in opens 30 minutes before the "
                     f"start and closes 1 hour after it. Sign in to ShiftSense and press Check in.") if acct else []
    out["email"] = em[0] if em else {"status": "not_sent", "detail": "No account to email."}
    _audit(w, user, "Attendance reminder", f"{agent_id} {day_label(w.policy, day)}", "",
           f"in-app: {attendance.state_text(out)}; email: {mailer.LABEL.get(out['email']['status'], out['email']['status'])}",
           out["error"] or out["email"].get("detail", ""), out["status"])
    return out


def reminder_view(w, r: dict) -> dict:
    """Delivery facts of one reminder, kept apart: in inbox / opened / checked in / email status."""
    n = comms.related_notice(w.run_id, "checkin", r["id"])
    em = mailer.log(w.run_id, kind="reminder", ref=str(r["id"]))
    return {"in_app": attendance.state_text(r),
            "opened": "Opened" if n and n["read_by"] else ("Not opened yet" if n else "—"),
            "checked_in": (f"{r['used_sim']} · {attendance.lateness(r)}" if r["used_via"] else
                           ("—" if not r["used_sim"] else "Superseded")),
            "email": mailer.LABEL.get(em[0]["status"], em[0]["status"]) + (f" ({em[0]['detail']})" if em[0]["detail"]
                                                                            else "") if em else "—"}


def _record_checkin(w, agent_id: str, day: int, actor: str, role: str, via: str, late: str = ""):
    if w.attendance.get((agent_id, int(day))):
        raise ValueError("Attendance is already recorded for this shift.")
    w.attendance[(agent_id, int(day))] = "checkin"          # genuine check-in, distinct from simulated "in"
    comms.audit(w.run_id, actor, role, "Check-in", f"{agent_id} {day_label(w.policy, day)}", "",
                "checked in" + (f" · {late}" if late else ""), "", via, workflow.now_label(w))


def check_in_self(w, user, reminder_id: int) -> tuple[str, int]:
    """The signed-in agent checks in for their own shift: identity from the stored account, the reminder must be
    theirs, inside the window and not already used; attendance must not already be recorded."""
    _need(user, "report_self")
    user = _active(user) or user
    aid = user.get("agent_id") or ""
    r = next((x for x in attendance.log(w.run_id, {aid}) if x["id"] == int(reminder_id)), None)
    if r is not None and w.attendance.get((aid, int(r["day"]))):
        raise ValueError("Attendance is already recorded for this shift.")
    if r is not None and _shift_row(w, aid, int(r["day"])) is None:
        raise ValueError("You no longer have a shift that day.")
    agent_id, day = attendance.redeem_in_app(w.run_id, int(reminder_id), aid, w.now_t, workflow.now_label(w),
                                             w.policy["horizon"]["interval_minutes"])
    used = next(x for x in attendance.log(w.run_id, {aid}) if x["id"] == int(reminder_id))
    _record_checkin(w, agent_id, day, _who(user), user["role"], "in-app", attendance.lateness(used))
    comms.resolve_related(w.run_id, "checkin", reminder_id)
    return agent_id, day


def open_reminder(w, user, reminder_id: int):
    """The agent opened the reminder notification (recorded as 'opened'; nothing else changes)."""
    _need(user, "report_self")
    n = comms.related_notice(w.run_id, "checkin", reminder_id)
    if n and n["recipient"].get("agent") == user.get("agent_id"):
        comms.mark_read(n["id"], user["username"])


# ------------------------------------------------------------------ email address verification (own account)
CODE_TTL_MIN = 30


def request_email_code(w, user) -> dict:
    """Send a 6-digit code to the address on the signed-in user's own account."""
    import hashlib, secrets
    from datetime import timedelta
    _active(user)
    acct = accounts.get(user["username"])
    if not acct or not acct.get("email"):
        raise ValueError("No email address on your account. Ask an administrator to add it.")
    if acct.get("email_verified"):
        raise ValueError("Your email address is already verified.")
    if not mailer.configured():
        raise ValueError("Email is not configured on this server, so the address cannot be verified. In-app "
                         "messages still reach you.")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    exp = (datetime.now() + timedelta(minutes=CODE_TTL_MIN)).isoformat(timespec="seconds")
    accounts.set_email_code(user["username"], hashlib.sha256(f"{user['username']}:{code}".encode()).hexdigest(), exp)
    res = mailer.send(w.run_id, f"verify|{user['username']}|{secrets.token_hex(4)}", kind="verify",
                      ref=user["username"], account=acct, subject="ShiftSense: confirm your email address",
                      body=f"Your ShiftSense code is {code}. It expires in {CODE_TTL_MIN} minutes.",
                      sim_time=workflow.now_label(w), require_verified=False, address=acct["email"])
    _audit(w, user, "Email verification sent", f"user {user['username']}", "", mailer.LABEL.get(res["status"], ""),
           res["detail"], res["status"])
    if res["status"] != "sent":
        raise ValueError(f"The code could not be sent: {res['detail'] or res['status']}")
    return res


def confirm_email_code(w, user, code: str) -> bool:
    import hashlib, hmac
    _active(user)
    acct = accounts.get(user["username"])
    h, exp = accounts.email_code(user["username"])
    if not acct or not h:
        raise ValueError("Request a code first.")
    if exp and datetime.now().isoformat(timespec="seconds") > exp:
        accounts.set_email_code(user["username"], None, None)
        raise ValueError("That code has expired. Request a new one.")
    if not hmac.compare_digest(h, hashlib.sha256(f"{user['username']}:{(code or '').strip()}".encode()).hexdigest()):
        raise ValueError("That code is not correct.")
    if not accounts.mark_email_verified(user["username"], acct["email"]):
        raise ValueError("Your address changed meanwhile. Request a new code.")
    _audit(w, user, "Email verified", f"user {user['username']}", "unverified", "verified", "", "verified")
    return True


# ------------------------------------------------------------------ demo handling: import (Analyst)
def commit_import(w, user, agents, leave, base: dict, summary: dict):
    """Build and validate a roster for a fully validated import. Returns the new workspace; the caller
    swaps it in. Nothing is replaced unless a roster passes the independent rule check."""
    from . import importer, optimizer
    from .constraints import check
    _need(user, "import")
    data = importer.build_data(agents, leave, base["data"], w.policy)
    res = optimizer.plan(w.policy, data["agents"], data["leave"], base["req"])
    if res["roster"].empty:
        _audit(w, user, "Import failed", summary.get("files", "import"), "", res["status"],
               planning.explain_status(res["status"]), "failed")
        raise ValueError(planning.explain_status(res["status"]))
    viol = check(w.policy, data["agents"], data["leave"], res["roster"])
    if viol:
        _audit(w, user, "Import failed", summary.get("files", "import"), "", f"{len(viol)} violations", "", "failed")
        raise ValueError(f"The built roster breaks {len(viol)} hard rule(s); nothing was changed.")
    new = workflow.new_world(w.policy, data, base["req"], res["roster"], fc=base.get("fc"))
    detail = (f"{summary.get('agents', 0)} agents, {summary.get('leave', 0)} leave days, "
              f"{summary.get('skipped', 0)} rows skipped; roster {res['status'].lower()}, 0 violations")
    _audit(w, user, "Import committed", summary.get("files", "import"), f"run {w.run_id}", f"run {new.run_id}", detail,
           "applied")
    comms.audit(new.run_id, _who(user), user["role"], "Import committed", summary.get("files", "import"),
                f"run {w.run_id}", f"run {new.run_id}", detail, "applied", workflow.now_label(new))
    return new


def load_demo_story(live, user):
    """Analyst loads the prepared scenario into a SEPARATE sandbox: a copy of the live workspace with its
    own 'sim-' run id. The story runs through the normal workflow rules on the copy only; the live
    workspace (published roster, genuine requests, attendance, notifications) is not touched.
    Loading again replaces only the previous scenario."""
    _need(user, "manage_demo")
    sc = workflow.load_story(workflow.make_scenario(live))
    seed_notices(sc)
    comms.audit(sc.run_id, _who(user), user["role"], "Load demo story", "demo scenario", "",
                f"scenario {sc.run_id} from live {live.run_id}", "simulated scenario; live roster unchanged", "applied",
                workflow.now_label(sc))
    _audit(live, user, "Load demo story", f"scenario {sc.run_id}", "", "loaded (separate sandbox)",
           "live roster unchanged", "applied")
    return sc


def unload_demo(live, user, sc):
    """Remove the demo sandbox. Live records are untouched; the scenario's audit, notices and messages stay
    in history under its 'sim-' run id."""
    _need(user, "manage_demo")
    _audit(live, user, "Unload demo scenario", f"scenario {sc.run_id if sc else '-'}", "loaded", "unloaded",
           "live roster unchanged", "applied")


def advance_live_clock(w, user, hours: float) -> dict:
    """Move LIVE time (so genuine check-in windows open). Generates no attendance, no no-shows, no events."""
    _need(user, "live_clock")
    res = workflow.advance_live(w, hours, _who(user))
    _audit(w, user, "Advance live clock", "live clock", "", workflow.now_label(w), "time only; nothing simulated",
           "applied")
    if getattr(w, "ref_linked", False):              # scheduled reference-data changes whose date has arrived
        from . import dataflow
        due = dataflow.apply_due(w)
        if due:
            _audit(w, user, "Scheduled data changes applied", ", ".join(f"#{i}" for i in due), "", "in force today",
                   "", "applied")
        res["data_changes_applied"] = due
    res["separations"] = run_separations(w, None)
    res["absence_flags"] = hr.evaluate_absence_flags(w)
    _flag_notice(w, res["absence_flags"])
    res["overtime"] = hr.evaluate_overtime(w)
    res["hr_reminders"] = hr_reminders(w)
    return res


def advance_clock(w, user, hours: float, protected=None, rate=None) -> dict:
    """Moves SIMULATED time in the demo scenario only. Its attendance results and no-shows are simulated;
    they never reach the live workspace, whose attendance comes only from real check-ins."""
    _need(user, "run_clock")
    if not getattr(w, "scenario", False):
        raise ValueError("The simulation clock runs only in the demo scenario. Load the demo story first.")
    res = workflow.advance(w, hours, _who(user), protected=protected, rate=rate)
    for inc in res["no_shows"]:
        notify_alert(w, inc, "Attendance system")
    _audit(w, user, "Advance clock", "simulation clock", "", workflow.now_label(w),
           f"{res['started']} starts, {res['attended']} attended, {len(res['no_shows'])} no-shows", "applied")
    return res


# ------------------------------------------------------------------ Analyst -> Ops risk escalation
def escalate_risk(w, user, summary: str, detail: str, related: tuple[str, str] | None = None) -> int:
    """The Analyst asks Ops for an operational decision on a material risk. This is a request, not a
    transfer of authority: Ops decides; the Analyst keeps doing analysis."""
    _need(user, "escalate_risk")
    if not summary.strip() or not detail.strip():
        raise ValueError("Describe the risk and the evidence.")
    nid = _note(w, {"role": "ops_head"}, f"Risk from Analyst: {summary.strip()[:90]}", body=detail[:400],
                priority="Warning", sender=_who(user), related=related or ("risk", summary[:40]),
                action_required=True, due_t=w.now_t + 8, dedupe=f"risk|{w.run_id}|{summary.strip()[:60]}")
    if nid is None:
        raise ValueError("This risk was already sent to Ops.")
    _audit(w, user, "Escalate risk to Ops", f"notice {nid}", "", "with Ops", detail[:200])
    return nid


def answer_risk(w, user, notice_id: int, decision: str, note: str):
    """Ops records its decision on an Analyst risk, or returns it to the Analyst for more work."""
    _need(user, "ops_decide")
    if decision not in ("Decision recorded", "Returned to Analyst"):
        raise ValueError("Unknown decision.")
    if not note.strip():
        raise ValueError("A documented reason is required.")
    if not comms.complete(notice_id, user["username"], lambda rec: comms._matches(rec, user)):
        raise Denied("This item is not addressed to Ops.")
    _note(w, {"role": "analyst"}, f"Ops: {decision}", body=note[:300],
          priority="Warning" if decision.startswith("Returned") else "Normal", sender=_who(user),
          action_required=decision.startswith("Returned"), dedupe=f"riskans|{w.run_id}|{notice_id}")
    _audit(w, user, f"Risk: {decision}", f"notice {notice_id}", "with Ops", decision, note)


# ------------------------------------------------------------------ targeted cleanup of the old demo story (Analyst)
def cleanup_legacy_story(w, user, execute: bool = False) -> dict:
    """Dry run by default. With execute=True, removes only the confidently identified old demo-story records
    from the LIVE workspace (see shiftsense/cleanup.py), all-or-nothing and rule-checked, and audits the list."""
    from . import cleanup
    _need(user, "manage_demo")
    p = cleanup.plan(w)
    if not execute:
        return p
    with workflow.transaction(w):
        counts = cleanup.apply(w, p)
        if cleanup.plan(w)["incidents"] or cleanup.plan(w)["requests"]:
            raise ValueError("Cleanup did not converge; nothing was changed.")
    detail = ("removed incidents " + ", ".join(x["id"] for x in p["incidents"]) + "; requests "
              + ", ".join(x["id"] for x in p["requests"]) + f"; {counts['leave_rows']} leave rows; "
              f"{counts['inbox']} inbox items; {counts['decision_log']} story decision-log entries; "
              f"{counts['notices']} notices; restored {counts['roster_restore']} agent-days to the published plan")
    _audit(w, user, "Legacy demo cleanup", "live workspace", "old demo-story records present", "removed", detail,
           "applied")
    return {"plan": p, "counts": counts}


# ------------------------------------------------------------------ history-related actions
def history(w, user, **filters) -> list[dict]:
    """Scoped history for the signed-in user (see shiftsense/timeline.py)."""
    from . import timeline
    return timeline.events(w, user, **filters)


def history_export(w, user, **query) -> str:
    from . import timeline
    _active(user)
    out = timeline.export_csv(w, user, **query)
    _audit(w, user, "History export", "history", "", f"{out.count(chr(10)) - 1} rows", "", "exported")
    return out


def record_whatif(w, user, name: str, summary: str, result: dict | None = None) -> int | None:
    """What-if runs work on copies; this records that the analysis was done and what it showed. With `result`
    (from planning.run_scenario) the inputs and full metric comparison are saved for later comparison."""
    from dataclasses import asdict
    from . import whatif_store
    _need(user, "replan")
    _audit(w, user, "What-if run", name[:60], "", summary[:300], "", "analysis only · live roster unchanged")
    if result is None:
        return None
    sc = result.get("scenario")
    return whatif_store.save(w.run_id, actor=_who(user), sim_time=workflow.now_label(w), name=name[:60],
                             inputs=asdict(sc) if sc is not None else {}, status=str(result.get("status")),
                             as_is=result.get("as_is"), after=result.get("after"))


def whatif_runs(w, user) -> list[dict]:
    from . import whatif_store
    _need(user, "replan")
    return whatif_store.runs(w.run_id)


def report_discrepancy(w, user, day: int, note: str) -> int:
    """Agent reports that an attendance or schedule record looks wrong. Goes to their Team Lead; nothing is
    changed until the Team Lead records a correction."""
    _need(user, "report_self")
    if not note.strip():
        raise ValueError("Describe what looks wrong.")
    aid = user["agent_id"]
    nid = _note(w, {"role": "team_lead", "team": _team_of(w, aid)},
                f"Record problem reported: {user['name']} · {day_label(w.policy, day)}", body=note[:300],
                priority="Normal", sender=_who(user), related=("thread", f"agent:{aid}"), action_required=True,
                due_t=w.now_t + 48, dedupe=f"disc|{w.run_id}|{aid}|{day}|{note.strip()[:40]}")
    if nid is None:
        raise ValueError("You already reported this.")
    comms.post(w.run_id, f"agent:{aid}", _who(user), user["role"],
               f"Record problem for {day_label(w.policy, day)}: {note.strip()[:300]}", workflow.now_label(w))
    _audit(w, user, "Record problem reported", f"{aid} {day_label(w.policy, day)}", "", "reported", note[:200])
    return nid


def correct_attendance(w, user, agent_id: str, day: int, reason: str):
    """Team Lead records that an own-team agent was present for a started shift with no record (e.g. the
    agent could not check in). Stored as 'confirmed' (recorded by the Team Lead, distinct from a self check-in
    and from simulated attendance). Never overwrites an existing record; before/after go to the audit."""
    _need(user, "decide_fix")
    if _team_of(w, agent_id) != user.get("team"):
        raise Denied("You can only correct records for your own team.")
    if not reason.strip():
        raise ValueError("A reason is required for an attendance correction.")
    r = _shift_row(w, agent_id, day)
    if r is None:
        raise ValueError("That agent had no shift that day.")
    start = shift_intervals(w.policy, day, r["shift"], r["ext_before"], r["ext_after"]).start
    if w.now_t < start:
        raise ValueError("The shift has not started yet; nothing to correct.")
    if hr.is_locked(w, agent_id, int(day)):
        raise ValueError(f"Attendance for this shift is locked ({hr.value('attendance.lock_hours')} h after the shift "
                         "ended). Raise a correction request; HR reviews it.")
    before = w.attendance.get((agent_id, int(day)))
    if before:
        raise ValueError(f"Attendance is already recorded ({before}); corrections never overwrite a record. Raise a "
                         "correction request instead.")
    w.attendance[(agent_id, int(day))] = "confirmed"
    _audit(w, user, "Attendance correction", f"{agent_id} {day_label(w.policy, day)}", "no record",
           "present (confirmed by team lead)", reason)


# ================================================================== reference data: input, change control, rebuild
def _live_only(w):
    if getattr(w, "scenario", False):
        raise Denied("Data changes are made in the live workspace. Switch to Live in the sidebar.")


def _may_submit(user: dict, domain: str, rows: list[dict] | None = None):
    """Who may put which data in (checked on the server)."""
    role = user["role"]
    if domain in ("employees", "contracts", "leave") and auth.can(user, "manage_people"):      # HR owns people data
        return
    if domain == "employees" and auth.can(user, "propose_data"):                             # team lead: proposal
        return
    if domain in ("volumes", "attendance") and auth.can(user, "manage_data"):
        return
    if domain == "service_levels" and (auth.can(user, "manage_data") or auth.can(user, "manage_service_levels")):
        return
    if domain == "rules":
        if auth.can(user, "manage_data"):
            return
        if auth.can(user, "propose_governance_rules") and rows and all(
                str(r.get("rule", "")).startswith("governance.") for r in rows):
            return
    if domain == "situations" and auth.can(user, "report_situation"):
        from .schemas import SITUATION_TYPES
        if all(role in SITUATION_TYPES.get(r.get("type"), ("", (), (), ()))[3] for r in rows or [{}]):
            return
        raise Denied("Your role can't report this kind of situation.")
    raise Denied("You are not authorised to change this data.")


def data_context(w, user) -> dict:
    _active(user)
    from . import dataflow
    return dataflow.context(w)


def convert_data(w, user, domain: str, *, data: bytes | None = None, filename: str = "", text: str | None = None,
                 sheet: str | None = None, manual: dict | None = None, day_first: bool = True):
    """Any input -> converted rows + issues (nothing is saved). Returns (Result, sheet names)."""
    from . import convert, dataflow
    _active(user)
    _live_only(w)
    _may_submit(user, domain, [{"rule": "governance."}] if domain == "rules" and user["role"] == "admin" else
                ([{"type": "other"}] if domain == "situations" else None))
    ctx = dataflow.context(w)
    ctx["day_first"] = day_first
    sig = None
    saved = None
    if data is not None:
        sheets = convert.read_bytes(data, filename)
        if sheets:
            first = sheets.get(sheet) if sheet in sheets else next(iter(sheets.values()))
            df, _ = convert.frame_from_grid(first, __import__("shiftsense.schemas", fromlist=["x"]).DOMAINS[domain])
            sig = convert.signature(df.columns, domain)
            saved = saved_mapping(sig)
    client = None
    if convert.ai_enabled(domain):
        from . import assistant
        client = assistant.get_client()
    res, names = convert.convert_input(domain_name=domain, ctx=ctx, data=data, filename=filename, text=text,
                                       sheet=sheet, saved=saved, client=client, manual=manual)
    return res, names


def saved_mapping(signature: str) -> dict | None:
    from . import db, refdata
    refdata.ensure()
    with db.connect() as con:
        r = con.execute("SELECT mapping FROM import_mappings WHERE signature=?", (signature,)).fetchone()
    return __import__("json").loads(r[0]) if r else None


def remember_mapping(signature: str, domain: str, mapping: dict):
    import json as _j
    from . import db, refdata
    refdata.ensure()
    with db.connect() as con:
        con.execute("DELETE FROM import_mappings WHERE signature=?", (signature,))
        con.execute("INSERT INTO import_mappings (signature, domain, mapping, updated, uses) VALUES (?,?,?,?,1)",
                    (signature, domain, _j.dumps(mapping), datetime.now().isoformat(timespec="seconds")))


def preview_change(w, user, domain: str, rows: list[dict], *, mode: str = "upsert", deletes: list[dict] | None = None,
                   effective_from: str | None = None) -> tuple[list[dict], dict]:
    from . import dataflow
    _active(user)
    _live_only(w)
    _may_submit(user, domain, rows)
    ops = dataflow.to_ops(domain, rows, mode=mode, deletes=deletes)
    return ops, dataflow.preview(w, domain, ops, effective_from)


def submit_change(w, user, domain: str, rows: list[dict], *, mode: str = "upsert", deletes: list[dict] | None = None,
                  effective_from: str | None = None, reason: str = "", source: str = "form",
                  converted_by: str = "form", issues: list | None = None) -> dict:
    """Save a change set. Applied at once when no approval is needed; otherwise waits for the approver."""
    from . import dataflow
    from .schemas import DOMAINS
    _active(user)
    _live_only(w)
    _may_submit(user, domain, rows)
    if domain == "employees" and not auth.can(user, "manage_people"):        # team lead: own team, proposal only
        team = user.get("team")
        for r in rows:
            cur = refdata_current_employee(r.get("employee_id"))
            if (cur or {}).get("team") != team or r.get("team", team) != team:
                raise Denied("You can only propose changes for agents in your own team.")
    if domain in ("contracts", "service_levels", "rules") and not reason.strip():
        raise ValueError("A reason is required for this change.")
    eff = effective_from or dataflow.as_of(w)
    rows = [dict(r) for r in rows]
    ctx = dataflow.context(w)
    for r in rows:                                   # the same checks as converted input, for any caller
        missing = [DOMAINS[domain].field(f).label for f in DOMAINS[domain].required if r.get(f) in (None, "", [])]
        if missing:
            raise ValueError(f"Mandatory field(s) missing: {', '.join(missing)}")
        from . import convert as _cv
        errs = [m for f, lvl, m in _cv.validate_record(DOMAINS[domain], r, ctx) if lvl == "Error"]
        if errs:
            raise ValueError("; ".join(errs))
    ops = dataflow.to_ops(domain, rows, mode=mode, deletes=deletes)
    if not [o for o in ops if o["kind"] != "same"]:
        raise ValueError("Nothing to change: every record is already the same.")
    if DOMAINS[domain].partial_ok is False and issues and any(i.get("level") == "Error" for i in issues):
        raise ValueError("This kind of data is applied all-or-nothing; fix the errors first.")
    impact = dataflow.preview(w, domain, ops, eff)
    approver = "hr" if (domain == "employees" and not auth.can(user, "manage_people")) else \
        dataflow.needs_approval(domain, user, rows)
    status = "pending" if approver else "approved"
    cid = dataflow.save_set(w, user, domain, ops, effective_from=eff, reason=reason, source=source,
                            converted_by=converted_by, impact=impact, issues=issues or [], status=status)
    counts = impact.get("counts", {})
    _audit(w, user, f"Data change submitted ({DOMAINS[domain].label})", f"change #{cid}", "",
           f"{counts.get('new', 0)} new, {counts.get('changed', 0)} changed, {counts.get('delete', 0)} removed · "
           f"{status} · converted by {converted_by}", reason, status)
    if approver:
        roles = {"ops_head": {"role": "ops_head"}, "analyst": {"role": "analyst"}, "hr": {"role": "hr"}}
        _note(w, roles[approver], f"Data change to approve: {DOMAINS[domain].label}",
              body=f"#{cid} from {user['name']}: {counts.get('new', 0)} new, {counts.get('changed', 0)} changed, "
                   f"{counts.get('delete', 0)} removed. {reason}"[:300], priority="Warning", sender=_who(user),
              related=("change", cid), action_required=True, dedupe=f"chg|{w.run_id}|{cid}")
        return {"id": cid, "status": "pending", "approver": approver, "impact": impact}
    cs = dataflow.get_set(cid)
    outcome = dataflow.apply_set(w, cs, _who(user))
    _audit(w, user, f"Data change applied ({DOMAINS[domain].label})", f"change #{cid}", "", _outcome_text(outcome),
           reason, "applied")
    _tell_analyst(w, user, cid, domain, counts, outcome)
    return {"id": cid, "status": dataflow.get_set(cid)["status"], "impact": impact, "outcome": outcome}


def _tell_analyst(w, user, cid: int, domain: str, counts: dict, outcome: dict):
    """The Analyst plans from people data, so every applied HR change is announced with its effect on the plan."""
    from .schemas import DOMAINS
    if domain not in ("employees", "contracts", "leave") or user["role"] == "analyst":
        return
    effect = _outcome_text(outcome)
    _note(w, {"role": "analyst"}, f"People data changed: {DOMAINS[domain].label} (#{cid})",
          body=f"By {user['name']}: {counts.get('new', 0)} new, {counts.get('changed', 0)} changed, "
               f"{counts.get('delete', 0)} removed. Effect on the plan: {effect}"[:300],
          priority="Warning" if outcome.get("replan_recommended") else "Info", sender=_who(user),
          related=("change", cid), action_required=bool(outcome.get("replan_recommended")),
          dedupe=f"hrinfo|{w.run_id}|{cid}")


def refdata_current_employee(eid) -> dict | None:
    from . import refdata
    return refdata.current("employees").get(str(eid))


def _outcome_text(o: dict) -> str:
    if o.get("note"):
        return o["note"]
    return (f"{len(o.get('alerts', []))} alert(s), {len(o.get('released', []))} later shift(s) released"
            + ("; re-plan recommended" if o.get("replan_recommended") else ""))


def decide_change(w, user, cid: int, approve: bool, reason: str = "") -> dict:
    """The approver accepts or rejects a pending change set. Never one's own submission."""
    from . import dataflow
    _active(user)
    _live_only(w)
    cs = dataflow.get_set(cid)
    if cs is None or cs["status"] != "pending":
        raise ValueError("This change is not waiting for a decision.")
    need = "hr" if cs["domain"] == "employees" and cs["submitted_role"] == "team_lead" else \
        ("ops_head" if cs["domain"] != "employees" else None)
    if need == "ops_head" and not auth.can(user, "approve_data"):
        raise Denied("Only Ops can approve this change.")
    if need == "hr" and not auth.can(user, "manage_people"):
        raise Denied("Only HR can accept a team lead's proposal.")
    if cs["submitted_by"].startswith(f"{user['name']} ("):
        raise Denied("You cannot approve your own change.")
    if not approve and not reason.strip():
        raise ValueError("A reason is required to reject a change.")
    dataflow.decide(cid, approve, _who(user), reason)
    _comms_resolve(w, cid)
    if not approve:
        _audit(w, user, "Data change rejected", f"change #{cid}", "pending", "rejected", reason, "rejected")
        return {"status": "rejected"}
    outcome = dataflow.apply_set(w, dataflow.get_set(cid), _who(user))
    _audit(w, user, "Data change approved", f"change #{cid}", "pending", _outcome_text(outcome), reason, "applied")
    _tell_analyst(w, user, cid, cs["domain"], cs["summary"] or {}, outcome)
    return {"status": dataflow.get_set(cid)["status"], "outcome": outcome}


def _comms_resolve(w, cid):
    comms.resolve_related(w.run_id, "change", cid)


def update_own_preferences(w, user, pref_shift: str | None = None, pref_off: int | None = None) -> dict:
    """Agent: preferred shift and day off. Wishes for the next plan only; applied without approval."""
    from . import dataflow
    _need(user, "own_preferences")
    _live_only(w)
    aid = user.get("agent_id")
    cur = refdata_current_employee(aid)
    if cur is None:
        raise ValueError("Your employee record isn't in the reference data yet.")
    rec = dict(cur)
    if pref_shift:
        if pref_shift not in w.policy["shifts"]:
            raise ValueError("Unknown shift.")
        rec["pref_shift"] = pref_shift
    if pref_off is not None:
        if not 0 <= int(pref_off) <= 6:
            raise ValueError("Pick a weekday.")
        rec["pref_off"] = int(pref_off)
    ops = dataflow.to_ops("employees", [rec])
    if not [o for o in ops if o["kind"] != "same"]:
        raise ValueError("Nothing changed.")
    impact = {"counts": {"changed": 1}, "note": "Used at the next re-plan; no shift changes now."}
    cid = dataflow.save_set(w, user, "employees", ops, effective_from=dataflow.as_of(w), reason="own preferences",
                            source="agent form", converted_by="form", impact=impact, issues=[], status="approved")
    from . import refdata
    refdata.write("employees", [("upsert", rec)], effective_from=dataflow.as_of(w), change_id=cid, by=_who(user),
                  source="agent form")
    dataflow._update_set(cid, status="applied", applied_at=datetime.now().isoformat(timespec="seconds"),
                         outcome={"note": "Preferences saved; used at the next re-plan."})
    _audit(w, user, "Preferences updated", aid, f"{cur.get('pref_shift')} / {cur.get('pref_off')}",
           f"{rec.get('pref_shift')} / {rec.get('pref_off')}", "", "applied")
    return rec


def load_base_pack(w, user, *, replace: bool = False, size: str = "small") -> dict:
    """Analyst: load the base input data (basedata/*.csv) into reference data through the normal converter.
    Refused if reference data already exists, unless `replace` (the old versions stay in history)."""
    from . import basepack, convert, dataflow, refdata
    _need(user, "manage_data")
    _live_only(w)
    if not refdata.is_empty() and not replace:
        raise ValueError("Reference data already exists. Choose 'replace' to load the base pack over it.")
    if size not in basepack.SIZES:
        raise ValueError(f"Unknown base pack size '{size}'.")
    out = {}
    for dom in basepack.ORDER:
        ctx = dataflow.context(w)
        data = (basepack.folder(size) / basepack.FILES[dom]).read_bytes()
        res, _ = convert.convert_input(domain_name=dom, ctx=ctx, data=data, filename=basepack.FILES[dom])
        if res.errors:
            raise ValueError(f"Base pack file {basepack.FILES[dom]} has {len(res.errors)} error(s): "
                             f"{res.errors[0].message}")
        ops = dataflow.to_ops(dom, res.rows, mode="replace" if replace else "upsert")
        eff = refdata.iso_of_day(w.policy, 0)
        cid = dataflow.save_set(w, user, dom, ops, effective_from=eff, reason="base input data", source=basepack.FILES[dom],
                                converted_by=res.converted_by, impact={"counts": {"new": len(ops)}}, issues=[],
                                status="approved")
        dataflow.write_set(dataflow.get_set(cid), _who(user))
        dataflow._update_set(cid, status="applied", applied_at=datetime.now().isoformat(timespec="seconds"),
                             outcome={"note": "loaded from the base pack"})
        out[dom] = len(res.rows)
    _audit(w, user, "Base data loaded", "reference data", "", f"{size} pack: " + ", ".join(f"{k} {v}" for k, v in out.items()),
           "replace" if replace else "", "applied")
    return out


def rebuild_from_reference(w, user, time_limit: float | None = None):
    """Analyst: build a new live workspace from reference data in force today, with a fresh optimised roster.
    Returns the new workspace (the caller swaps it in after explicit confirmation). Nothing changes if no valid
    roster can be built."""
    from . import dataflow, optimizer, refdata
    from .constraints import check
    _need(user, "manage_data")
    _live_only(w)
    if refdata.is_empty():
        raise ValueError("There is no reference data yet. Load the base data first.")
    ds = refdata.dataset(__import__("shiftsense.config", fromlist=["x"]).load_policy(), refdata.iso_of_day(w.policy, 0))
    res = optimizer.plan(ds["policy"], ds["agents"], ds["leave"], ds["req"],
                         optimizer.PlanOptions(time_limit=time_limit))
    if res["roster"].empty:
        _audit(w, user, "Rebuild failed", "reference data", "", res["status"], planning.explain_status(res["status"]),
               "failed")
        raise ValueError(planning.explain_status(res["status"]))
    viol = check(ds["policy"], ds["agents"], ds["leave"], res["roster"])
    if viol:
        raise ValueError(f"The built roster breaks {len(viol)} hard rule(s); nothing was changed.")
    data = {"agents": ds["agents"], "leave": ds["leave"].assign(source="ref"), "history": ds["history"],
            "skills": ds["skills"], "seed": 42}
    new = workflow.new_world(ds["policy"], data, ds["req"], res["roster"], fc=ds["fc"])
    new.data["leave"] = ds["leave"].assign(source="ref")
    new.ref_linked = True
    detail = f"{len(ds['agents'])} agents, {len(ds['policy']['queues'])} queues, {len(res['roster'])} shifts, 0 violations"
    _audit(w, user, "Rebuilt from reference data", "live workspace", f"run {w.run_id}", f"run {new.run_id}", detail,
           "applied")
    comms.audit(new.run_id, _who(user), user["role"], "Rebuilt from reference data", "live workspace",
                f"run {w.run_id}", f"run {new.run_id}", detail, "applied", workflow.now_label(new))
    return new


def report_situation(w, user, rec: dict, reason: str = "") -> dict:
    """Ops / Analyst / Team Lead (by situation type) report a situation; continuity mode needs Ops."""
    from . import convert, dataflow
    from .schemas import DOMAINS
    _need(user, "report_situation")
    rec = dict(rec)
    rec.setdefault("id", f"SIT-{datetime.now():%y%m%d%H%M%S}")
    rec.setdefault("status", "Active")
    if user["role"] == "team_lead":
        rec["teams"] = [user.get("team")] if not rec.get("agents") else rec.get("teams") or []
        own = set(w.data["agents"][w.data["agents"].team == user.get("team")].agent_id)
        if any(a not in own for a in rec.get("agents") or []):
            raise Denied("You can only report situations for your own team.")
    problems = [m for f, lvl, m in convert.validate_record(DOMAINS["situations"], rec, dataflow.context(w))
                if lvl == "Error"]
    for f in DOMAINS["situations"].required:
        if not rec.get(f):
            problems.append(f"{DOMAINS['situations'].field(f).label} is mandatory")
    if problems:
        raise ValueError("; ".join(problems))
    return submit_change(w, user, "situations", [rec], reason=reason or rec.get("description", ""), source="form",
                         converted_by="form")


def data_changes(w, user, status: str | None = None) -> list[dict]:
    from . import dataflow
    _active(user)
    if not (auth.can(user, "manage_data") or auth.can(user, "approve_data") or auth.can(user, "view_audit")
            or auth.can(user, "propose_data") or auth.can(user, "manage_people")):
        raise Denied("You are not authorised to see data changes.")
    rows = dataflow.sets(status=status)
    if user["role"] == "team_lead":
        rows = [r for r in rows if r["submitted_by"].startswith(f"{user['name']} (")]
    return rows


def queue_change(w, user, domain: str, rows: list[dict], *, source: str, converted_by: str,
                 effective_from: str | None = None) -> int:
    """Stage converted rows for the running app to apply (server-side drop-folder runs)."""
    import json as _j
    from . import dataflow, db, refdata
    _active(user)
    _may_submit(user, domain, rows)
    refdata.ensure()
    with db.connect() as con:
        cur = con.execute("INSERT INTO change_sets (run_id, domain, status, submitted_by, submitted_role, submitted_at, "
                          "effective_from, reason, source, converted_by, ops) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (w.run_id, domain, "queued", f"{user['name']} ({user.get('role_label', user['role'])})",
                           user["role"], datetime.now().isoformat(timespec="seconds"), effective_from or "",
                           f"staged from {source}", source, converted_by, _j.dumps(rows, default=str)))
        return int(cur.lastrowid)


def apply_queued(w) -> list[int]:
    """Apply change sets staged by a server run, with the submitting account's own permissions (re-checked now)."""
    from . import accounts, dataflow
    done = []
    for cs in sorted(dataflow.sets(status="queued"), key=lambda c: c["id"]):
        uname = None
        acc = next((a for a in accounts.all_accounts()
                    if cs["submitted_by"].startswith(f"{a['name']} (") and a["role"] == cs["submitted_role"]), None)
        if acc is None or not acc["active"]:
            dataflow._update_set(cs["id"], status="failed", outcome={"note": "Submitting account no longer active."})
            continue
        uname = acc["username"]
        user = {**{k: v for k, v in acc.items() if v not in ("", None)}, "username": uname,
                "role_label": auth.config()["roles"][acc["role"]]["label"]}
        try:
            out = submit_change(w, user, cs["domain"], cs["ops"] or [], effective_from=cs["effective_from"] or None,
                                reason=cs["reason"], source=cs["source"], converted_by=cs["converted_by"])
            dataflow._update_set(cs["id"], status="superseded", outcome={"note": f"applied as change #{out['id']}"})
            done.append(out["id"])
        except (ValueError, Denied) as e:
            dataflow._update_set(cs["id"], status="failed", outcome={"note": str(e)})
    return done


def import_roster_grid(w, user, *, data: bytes | None = None, filename: str = "", text: str | None = None,
                       name: str = "Imported roster", day_first: bool = True) -> dict:
    """Analyst: a roster grid becomes a DRAFT plan (validated, reviewed and published like any draft). Days that
    have started keep the live roster. Returns {plan, issues, violations}."""
    from . import convert, dataflow
    from .constraints import check
    from .roster import make_roster
    _need(user, "replan")
    _live_only(w)
    ctx = dataflow.context(w)
    ctx["day_first"] = day_first
    ctx["primary"] = dict(zip(w.data["agents"].agent_id, w.data["agents"].primary))
    ctx["employees"] = set(w.data["agents"].agent_id)
    if data is not None:
        sheets = convert.read_bytes(data, filename)
        grid = next(iter(sheets.values()))
    else:
        grid = convert.read_text_table(text or "")
    h = 0
    for i in range(min(10, len(grid))):                         # header = first row with at least 2 dates
        if sum(bool(convert.to_date(c, day_first)[0]) for c in grid.iloc[i].tolist() if not convert.blank(c)) >= 2:
            h = i
            break
    df = grid.iloc[h + 1:].copy()
    df.columns = [str(c) for c in grid.iloc[h].tolist()]
    rows, issues = convert.roster_grid(df, ctx, w.policy)
    if not rows:
        raise ValueError(issues[0].message if issues else "No shifts found in this grid.")
    first = w.first_day
    imported = make_roster([r for r in rows if r["day"] >= first])
    kept = w.roster[w.roster.day < first]
    roster = make_roster(kept.to_dict("records") + imported.to_dict("records"))
    viol = check(w.policy, w.data["agents"], w.data["leave"], roster)
    plan = draft_plan(w, user, roster, name, f"roster grid {filename or 'pasted'}")
    _audit(w, user, "Roster grid imported", plan["id"], "", f"{len(imported)} shifts from {filename or 'paste'}",
           f"{len(viol)} rule problem(s) to resolve before publishing", "draft")
    return {"plan": plan, "issues": issues, "violations": viol, "shifts": len(imported)}


def read_situation_text(w, user, text: str) -> dict:
    """A few sentences -> draft situation. Rules first; the AI (if switched on for non-personal data) only when the
    rules recognised nothing beyond the wording. The result is a draft for the person to check, never applied."""
    from . import convert, dataflow
    from .schemas import DOMAINS
    _need(user, "report_situation")
    ctx = dataflow.context(w)
    r = convert.text_to_situation(text, ctx)
    r["read_by"] = "rules"
    if r["record"]["type"] == "other" and len(r["found"]) <= 1 and convert.ai_enabled("situations"):
        from . import assistant
        client = assistant.get_client()
        rows = convert.ai_text_to_rows(text, DOMAINS["situations"], ctx, client) if client else []
        if rows:
            res = convert.convert_table(__import__("pandas").DataFrame(rows[:1]), "situations", ctx)
            if res.rows:
                rec = {**r["record"], **{k: v for k, v in res.rows[0].items() if v not in (None, "", [])}}
                r.update(record=rec, read_by="AI", found=r["found"] + ["read by AI: check every field"])
    return r


# ================================================================== HR decision record (HR-01..HR-08)
def _hr(user):
    _need(user, "manage_people")


def _senior(user) -> bool:
    return auth.can(_active(user), "senior_hr")


def _not_self(user, employee_id: str, what: str):
    if employee_id and employee_id in (user.get("agent_id"), user.get("employee_id")):
        raise Denied(f"You cannot {what} for yourself.")


def _accounts_of(employee_id: str) -> list[dict]:
    return [a for a in accounts.all_accounts() if employee_id in (a.get("agent_id"), a.get("employee_id"))]


def set_senior_hr(w, user, username: str, grant: bool, reason: str) -> dict:
    """Admin grants or removes the Senior HR permission (HR role only). Audited."""
    _need(user, "manage_accounts")
    if not reason.strip():
        raise ValueError("A reason is required.")
    before = accounts.get(username)
    after = accounts.set_extra_permission(username, "senior_hr", grant, _who(user))
    _audit(w, user, "Senior HR permission", f"user {username}", ",".join(before["extra_permissions"]) or "none",
           ",".join(after["extra_permissions"]) or "none", reason, "granted" if grant else "removed")
    return after


def hr_policy(user) -> list[dict]:
    _hr(user)
    return hr.policy_rows()


def set_hr_policy(w, user, key: str, value, status: str, reference: str = "", note: str = "") -> dict:
    """Senior HR changes an HR policy item. Every change is a new version; approving needs a policy reference."""
    _hr(user)
    if not _senior(user):
        raise Denied("Only Senior HR can change HR policy settings.")
    old = next(r for r in hr.policy_rows() if r["key"] == key) if key in hr.POLICY else None
    row = hr.set_policy(key, value, status, reference, note, _who(user))
    _audit(w, user, "HR policy change", key, f"{old['value']} ({old['status']})" if old else "",
           f"{row['value']} ({row['status']})", note or reference, row["status"])
    return row


# ---------------------------------------------------------------- HR-01 separation
def record_separation(w, user, employee_id: str, effective_date: str, reason_category: str, note: str = "") -> dict:
    _hr(user)
    _live_only(w)
    _not_self(user, employee_id, "record a separation")
    if employee_id not in set(w.data["agents"].agent_id) and not _accounts_of(employee_id):
        raise ValueError(f"No employee {employee_id}.")
    acc = _accounts_of(employee_id)
    s = hr.record_separation(employee_id, acc[0]["username"] if acc else None, effective_date, reason_category, note,
                             _who(user), user["username"])
    _audit(w, user, "Separation recorded", f"separation #{s['id']} · {employee_id}", "",
           f"effective {effective_date} (not confirmed)", "", "recorded")
    return s


def confirm_separation(w, user, sid: int) -> dict:
    _hr(user)
    _live_only(w)
    s0 = hr.separation(sid)
    _not_self(user, s0["employee_id"], "confirm a separation")
    s = hr.confirm_separation(sid, _who(user), user["username"])
    _audit(w, user, "Separation confirmed", f"separation #{sid} · {s['employee_id']}", "recorded",
           f"confirmed, effective {s['effective_date']}", "", "confirmed")
    _employee_record(s, "On notice", s["effective_date"], f"separation #{sid} confirmed", _who(user))
    if s["employee_id"] in set(w.data["agents"].agent_id):
        out = _plan_around_leaver(w, s, user)
        msg = (f"{s['employee_id']} leaves after {s['effective_date']}. "
               + (f"{len(out['alerts'])} shift(s) in the next days became staffing alerts with a cover proposal. "
                  if out["alerts"] else "")
               + (f"{out['released']} later shift(s) were released: re-plan those days to cover them."
                  if out["released"] else "")).strip()
        for to in ({"role": "team_lead", "team": _team_of(w, s["employee_id"])}, {"role": "analyst"}):
            _note(w, to, "Employee leaving: plan cover", body=msg, priority="Normal", sender=_who(user),
                  related=("separation", sid), dedupe=f"sep|{sid}|{to['role']}")
        _audit(w, user, "Leaver removed from later shifts", f"separation #{sid} · {s['employee_id']}", "",
               f"{len(out['alerts'])} alert(s), {out['released']} released", "", "planned")
    return s


def _employee_record(s: dict, status: str, end_date: str | None, source: str, by: str):
    """Keep the versioned employee record in step (only when the reference data holds this employee)."""
    from . import refdata
    refdata.ensure()
    rec = refdata.current("employees").get(s["employee_id"])
    if rec is None:
        return
    refdata.write("employees", [("upsert", {**rec, "status": status, "end_date": end_date})],
                  effective_from=hr.local_today(), change_id=None, by=by, source=source)


def _plan_around_leaver(w, s: dict, user) -> dict:
    """After the last working day the person is unavailable. Shifts that have started are never touched. Shifts
    within the alert window become staffing alerts (with a cover proposal for the team lead); later shifts are
    released and the Analyst re-plans them."""
    from . import dataflow
    from .refdata import day_index
    a, last = s["employee_id"], day_index(w.policy, s["effective_date"])
    horizon = int(w.policy["horizon"]["days"])
    started = workflow.started_shifts(w)
    alerts, released = [], 0
    with workflow.transaction(w):
        for d in range(max(last + 1, w.today), horizon):
            has = ((w.roster.agent_id == a) & (w.roster.day == d)).any()
            if has and (a, d) in started:
                continue
            if has and d <= w.today + dataflow.near_days(w):
                inc = workflow.report_absence(w, [a], d, _who(user), "left the company")
                _alert_notice(w, inc, _who(user))
                alerts.append(inc.id)
                continue
            if has:
                w.roster = w.roster[~((w.roster.agent_id == a) & (w.roster.day == d))].reset_index(drop=True)
                released += 1
            workflow._add_unavailable(w, a, d, "left the company")
    return {"alerts": alerts, "released": released}


def correct_separation(w, user, sid: int, effective_date: str, reason_category: str, reason: str) -> dict:
    """A correction is a new record that replaces the old one (kept, marked corrected); it needs confirming again."""
    _hr(user)
    _live_only(w)
    old = hr.separation(sid)
    _not_self(user, old["employee_id"], "correct a separation")
    if old["status"] not in ("recorded", "confirmed"):
        raise ValueError(f"Only an open separation can be corrected (this one is {old['status']}). Rescind an executed "
                         "one instead.")
    if not reason.strip():
        raise ValueError("A reason is required for a correction.")
    s = hr.record_separation(old["employee_id"], old["username"], effective_date, reason_category, reason,
                             _who(user), user["username"], correction_of=sid)
    _audit(w, user, "Separation corrected", f"separation #{sid} -> #{s['id']}", f"effective {old['effective_date']}",
           f"effective {effective_date} (needs confirming)", reason, "corrected")
    return s


def rescind_separation(w, user, sid: int, reason: str) -> dict:
    """Withdraws a separation. If it already deactivated the account, the account is reactivated (audited)."""
    _hr(user)
    _live_only(w)
    s = hr.separation(sid)
    _not_self(user, s["employee_id"], "rescind a separation")
    hr.rescind_separation(sid, reason, _who(user))
    after = "rescinded"
    if s["status"] in ("confirmed", "executed"):
        _employee_record(s, "Active", None, f"separation #{sid} rescinded", _who(user))
        lv = w.data["leave"]
        back = (lv.agent_id == s["employee_id"]) & (lv.reason == "left the company")
        if back.any():
            with workflow.transaction(w):
                w.data = {**w.data, "leave": lv[~back].reset_index(drop=True)}
            after += "; available again (released shifts are not restored automatically: re-plan to use them)"
            _note(w, {"role": "analyst"}, "Separation rescinded: person available again",
                  body=f"{s['employee_id']} stays. Re-plan the open days to use them again.", priority="Normal",
                  sender=_who(user), related=("separation", sid), dedupe=f"seprs|{sid}")
    if s["status"] == "executed" and s["deactivated"] and s["username"]:
        b, a = accounts.set_active(s["username"], True, _who(user))
        after += "; account reactivated" if a["active"] and not b["active"] else ""
    _audit(w, user, "Separation rescinded", f"separation #{sid} · {s['employee_id']}", s["status"], after, reason,
           "rescinded")
    return hr.separation(sid)


def run_separations(w, user=None) -> list[dict]:
    """Deactivate accounts whose confirmed separation is due: access ends at 00:00 in the business timezone
    (Asia/Kolkata) on the day after the last working day. Access is already refused from that moment by
    auth.current; this run records it (deactivation, revoked codes, audit). Idempotent. Unconfirmed separations are
    never executed. Never runs from the demo scenario."""
    if user is not None:
        _hr(user)
    if getattr(w, "scenario", False):
        return []
    today, done = hr.local_today(), []              # business timezone, real time (approved 19 Oct 2026)
    for s in hr.separations():
        if not hr.is_due(s, today):
            continue
        accs = [a for a in _accounts_of(s["employee_id"]) if a["role"] != "admin"]
        deact = []
        for a in accs:
            b, aft = accounts.set_active(a["username"], False, "ShiftSense (separation)")
            accounts.set_email_code(a["username"], None, None)       # outstanding sign-in / email codes revoked
            if b["active"] and not aft["active"]:
                deact.append(a["username"])
        if hr.mark_executed(s["id"], bool(deact)):
            _employee_record(s, "Exited", s["effective_date"], f"separation #{s['id']} executed",
                             "ShiftSense (separation)")
            comms.audit(w.run_id, _who(user) if user else "ShiftSense (separation)", user["role"] if user else "system",
                        "Separation executed", f"separation #{s['id']} · {s['employee_id']}", "confirmed",
                        "account deactivated; sessions and codes revoked" if deact else
                        "no active account to deactivate", "", "executed", workflow.now_label(w))
            done.append({"id": s["id"], "employee_id": s["employee_id"], "deactivated": deact})
    return done


def separations_view(user) -> list[dict]:
    _hr(user)
    return hr.separations()


# ---------------------------------------------------------------- HR-02 balances
def enter_leave_balance(w, user, employee_id: str, leave_type: str, opening: float, effective_date: str,
                        source_reference: str) -> dict:
    _hr(user)
    _live_only(w)
    _not_self(user, employee_id, "enter a leave balance")
    b = hr.enter_opening_balance(employee_id, leave_type, opening, effective_date, source_reference, _who(user),
                                 user["username"])
    _audit(w, user, "Opening leave balance entered", f"{employee_id} · {leave_type}", "",
           f"{opening:g} {b['unit']} from {effective_date} (unverified)", source_reference, "unverified")
    return b


def verify_leave_balance(w, user, balance_id: int) -> dict:
    _hr(user)
    _live_only(w)
    b0 = next((b for b in hr.balances() if b["id"] == balance_id), None)
    if b0 is None:
        raise ValueError("Balance not found.")
    _not_self(user, b0["employee_id"], "verify a leave balance")
    b = hr.verify_balance(balance_id, _who(user), user["username"])
    _audit(w, user, "Leave balance verified", f"{b['employee_id']} · {b['leave_type_id']}", "unverified", "verified",
           b["source_reference"], "verified")
    return b


def adjust_leave_balance(w, user, employee_id: str, leave_type: str, amount: float, reason: str) -> dict:
    """A correction is an adjustment transaction with a reason, never an overwrite of the balance."""
    _hr(user)
    _live_only(w)
    _not_self(user, employee_id, "adjust a leave balance")
    if not amount:
        raise ValueError("The adjustment amount cannot be zero.")
    with hr._db() as con:
        hr.add_transaction(con, employee_id, leave_type, "adjustment", float(amount), None, reason, _who(user),
                           w.run_id)
    b = hr.balance(employee_id, leave_type, w.run_id)
    _audit(w, user, "Leave balance adjustment", f"{employee_id} · {leave_type}", "", f"{amount:+g}", reason,
           b["state"])
    return b


def leave_balance(w, user, employee_id: str, leave_type: str) -> dict:
    """HR sees any balance; an agent sees their own. Team Leads do not see balances."""
    _active(user)
    if not (auth.can(user, "manage_people") or user.get("agent_id") == employee_id):
        raise Denied("You can only see your own leave balance.")
    return hr.balance(employee_id, leave_type, w.run_id)


# ---------------------------------------------------------------- HR-03 review tasks
def review_tasks(w, user, status: tuple | None = ("open", "recommended")) -> list[dict]:
    _hr(user)
    return hr.review_tasks(w.run_id, status=status)


def review_status(w, user, req_id: str) -> list[dict]:
    """For the deciding Team Lead / Ops: trigger and HR recommendation, without sick details."""
    r = get_request(w, user, req_id)
    out = []
    for t in hr.review_tasks(w.run_id, req_id):
        t = dict(t)
        if r.get("leave_type") == "sick" and user["role"] != "hr":
            t["reason"] = "HR review required (details held by HR)."
            t["note"] = ""
        out.append(t)
    return out


def recommend_on_review(w, user, task_id: int, recommendation: str, note: str) -> dict:
    """HR's recommendation. It never approves or rejects the request; the Team Lead decides."""
    _hr(user)
    t = hr.review_task(task_id)
    if t["run_id"] != w.run_id:
        raise Denied("This task belongs to another workspace.")
    _not_self(user, t["employee_id"], "review a request")
    t = hr.recommend(task_id, recommendation, note, _who(user))
    r = _request(w, t["request_id"])
    if r["state"] == "Pending":
        sick = r.get("leave_type") == "sick"
        _note(w, {"role": "team_lead", "team": _team_of(w, r["agent_id"])}, f"HR review done: {r['id']}",
              body=f"HR recommends: {recommendation}." + ("" if sick else f" {note}") + " The decision is yours.",
              priority="Normal", sender=_who(user), related=("request", r["id"]),
              dedupe=f"hrrec|{w.run_id}|{task_id}|{recommendation}")
    comms.resolve_related(w.run_id, "hr_review", task_id)
    _audit(w, user, "HR review recommendation", f"{t['request_id']} · task #{task_id}", "open", recommendation,
           "" if r.get("leave_type") == "sick" else note, "recommended")
    return t


def assign_review(w, user, task_id: int, username: str):
    _hr(user)
    a = accounts.get(username)
    if not a or a["role"] != "hr" or not a["active"]:
        raise ValueError("Assign to an active HR user.")
    hr.assign_task(task_id, username)
    _audit(w, user, "HR review assigned", f"task #{task_id}", "", username, "", "assigned")


# ---------------------------------------------------------------- HR-04 attendance corrections
def request_attendance_correction(w, user, agent_id: str, day: int, requested: str, reason: str) -> dict:
    """Team Lead (own team) or the agent (own record) asks for a change to recorded attendance. Applied only
    after an HR reviewer agrees; the original value stays on record."""
    _active(user)
    if user["role"] == "agent":
        if agent_id != user.get("agent_id"):
            raise Denied("You can only ask about your own attendance.")
    elif user["role"] == "team_lead":
        _need(user, "decide_fix")
        if _team_of(w, agent_id) != user.get("team"):
            raise Denied("You can only raise corrections for your own team.")
    else:
        raise Denied("Only the agent or their team lead raises attendance corrections.")
    end = hr.shift_end_t(w, agent_id, int(day))
    if end is None:
        raise ValueError("That agent had no shift that day.")
    r = _shift_row(w, agent_id, day)
    if w.now_t < shift_intervals(w.policy, day, r["shift"], r["ext_before"], r["ext_after"]).start:
        raise ValueError("The shift has not started yet; nothing to correct.")
    c = hr.request_correction(w.run_id, agent_id, int(day), w.attendance.get((agent_id, int(day))), requested,
                              reason, _who(user), user["username"])
    _note(w, {"role": "hr"}, f"Attendance correction to review: #{c['id']}", body=f"{agent_id} "
          f"{day_label(w.policy, day)}: {c['original']} -> {requested}", priority="Normal", sender=_who(user),
          related=("attendance_correction", c["id"]), action_required=True, dedupe=f"attc|{w.run_id}|{c['id']}")
    _audit(w, user, "Attendance correction requested", f"{agent_id} {day_label(w.policy, day)}", c["original"],
           requested, reason, "pending")
    return c


ATT_VALUE = {"present": "confirmed", "absent": "no-show"}


def review_attendance_correction(w, user, correction_id: int, apply: bool, reason: str) -> dict:
    """HR reviewer applies or rejects. Applying writes an auditable adjustment; the original value stays in the
    correction record, in the attendance detail and in the audit. Absence counts are re-evaluated."""
    _hr(user)
    c = hr.correction(correction_id)
    if c["run_id"] != w.run_id:
        raise Denied("This request belongs to another workspace.")
    _not_self(user, c["employee_id"], "review an attendance correction")
    if c["requested_by_user"] == user["username"]:
        raise Denied("You cannot review a correction you raised.")
    if not reason.strip():
        raise ValueError("A reason is required.")
    key = (c["employee_id"], int(c["day"]))
    now = w.attendance.get(key) or "no record"
    if apply and now != c["original"]:
        raise ValueError(f"The record changed since the request was raised (now: {now}). Reject it and raise a new one.")
    hr.close_correction(correction_id, apply, reason, _who(user))
    if apply:
        if c["requested"] == "no record":
            w.attendance.pop(key, None)
        else:
            w.attendance[key] = ATT_VALUE[c["requested"]]
        w.attendance_detail[key] = {**w.attendance_detail.get(key, {}), "corrected_from": c["original"],
                                    "correction_id": correction_id, "source": "HR-reviewed correction"}
        hr.evaluate_absence_flags(w)
    comms.resolve_related(w.run_id, "attendance_correction", correction_id)
    outcome = (f"Attendance correction for {day_label(w.policy, int(c['day']))}: "
               + (f"applied ({c['original']} → {c['requested']})." if apply else "not applied.")
               + f" HR: {reason}")
    _note(w, {"user": c["requested_by_user"]}, outcome[:120], body=outcome, priority="Normal", sender=_who(user),
          related=("attendance_correction", correction_id), dedupe=f"attc_done|{correction_id}|req")
    if accounts.for_agent(c["employee_id"]) and accounts.for_agent(c["employee_id"])["username"] != \
            c["requested_by_user"]:
        _note(w, {"agent": c["employee_id"]}, outcome[:120], body=outcome, priority="Normal", sender=_who(user),
              related=("attendance_correction", correction_id), dedupe=f"attc_done|{correction_id}|agent")
    _audit(w, user, "Attendance correction " + ("applied" if apply else "rejected"),
           f"{c['employee_id']} {day_label(w.policy, int(c['day']))}", c["original"],
           c["requested"] if apply else c["original"], reason, "applied" if apply else "rejected")
    return hr.correction(correction_id)


def attendance_corrections(w, user, status: str | None = None) -> list[dict]:
    _hr(user)
    return hr.corrections(w.run_id, status)


# ---------------------------------------------------------------- HR-05 absence flags (HR only)
def absence_flags(w, user, status: tuple | None = None) -> list[dict]:
    _hr(user)
    return hr.absence_flags(w.run_id, status)


def _flag_notice(w, res: dict):
    """HR is told that new confidential flags exist; the notice carries no names or details."""
    if res.get("created"):
        _note(w, {"role": "hr"}, f"{res['created']} new confidential absence-pattern flag(s)",
              body="Open People data → Absence and overtime. A flag is a prompt for a support conversation, not a "
                   "disciplinary finding.", priority="Normal", sender="ShiftSense", related=("hr_flags", "new"),
              action_required=True, dedupe=f"flags|{w.run_id}|{hr.local_today()}|{res['created']}")


def check_absence_flags(w, user) -> dict:
    _hr(user)
    res = hr.evaluate_absence_flags(w)
    _flag_notice(w, res)
    _audit(w, user, "Absence-pattern check", "absence flags", "", res["note"], "",
           f"{res['created']} new, {res['updated']} re-counted" if res["active"] else "inactive")
    return res


def move_absence_flag(w, user, flag_id: int, to: str, note: str) -> dict:
    _hr(user)
    f = next((x for x in hr.absence_flags(w.run_id) if x["id"] == flag_id), None)
    if f is None:
        raise ValueError("Flag not found in this workspace.")
    _not_self(user, f["employee_id"], "handle an absence flag")
    out = hr.move_flag(flag_id, to, note, _who(user))
    _audit(w, user, "Absence flag", f"flag #{flag_id}", f["status"], to, "confidential", to)
    return out


# ---------------------------------------------------------------- HR-06 overtime approval
def overtime_view(w, user) -> dict:
    _hr(user)
    on, why = hr.overtime_active()
    return {"active": on, "note": why, "tasks": hr.overtime_tasks(w.run_id),
            "hours": {a: hr.overtime_hours(w, a) for a in sorted(set(w.data["agents"].agent_id))}}


def check_overtime(w, user) -> dict:
    _hr(user)
    res = hr.evaluate_overtime(w)
    _audit(w, user, "Overtime check", "overtime approval", "", res["note"], "",
           f"{res['created']} new" if res["active"] else "off")
    return res


def decide_overtime(w, user, task_id: int, approve: bool, reason: str) -> dict:
    """HR approves or rejects worked overtime above the threshold. No pay is calculated; coverage decisions
    stay with Ops."""
    _hr(user)
    t = next((x for x in hr.overtime_tasks(w.run_id) if x["id"] == task_id), None)
    if t is None:
        raise ValueError("Task not found in this workspace.")
    _not_self(user, t["employee_id"], "decide overtime")
    out = hr.decide_overtime(task_id, approve, reason, _who(user))
    _audit(w, user, "Overtime " + ("approved" if approve else "rejected"), f"{t['employee_id']} {t['period']}",
           "pending", out["status"], reason, out["status"])
    return out


# ---------------------------------------------------------------- HR-07 restricted cases
def _case_gate(w, user, cid: int | None, action: str, senior_only: bool = False) -> dict | None:
    """Checked on every request: only the assigned caseworker or Senior HR. Every attempt is logged."""
    try:
        cur = _active(user)
    except Denied:
        hr.log_access(cid, (user or {}).get("username", "?"), action, False)
        raise
    c = hr.case(cid) if cid is not None else None
    senior = cur["role"] == "hr" and auth.can(cur, "senior_hr")
    ok = cur["role"] == "hr" and (senior or (not senior_only and c is not None and c["caseworker"] == cur["username"]))
    hr.log_access(cid, cur.get("username", "?"), action, ok)
    if not ok:
        comms.audit(w.run_id, _who(cur), cur["role"], "HR case access denied", f"case #{cid}", "", action, "",
                    "denied", workflow.now_label(w))
        raise Denied("This is a restricted HR case. Only the assigned caseworker or Senior HR can open it.")
    return c


def open_hr_case(w, user, category: str, confidentiality: str, subject_employee: str, summary: str,
                 caseworker: str, deadline: str = "") -> dict:
    _hr(user)
    _not_self(user, subject_employee, "open a case")
    a = accounts.get(caseworker)
    if not a or a["role"] != "hr" or not a["active"]:
        raise ValueError("The caseworker must be an active HR user.")
    if a.get("agent_id") == subject_employee or a.get("employee_id") == subject_employee:
        raise ValueError("The caseworker cannot be the subject of the case.")
    c = hr.open_case(category, confidentiality, subject_employee, summary, caseworker, deadline, _who(user),
                     user["username"])
    hr.log_access(c["id"], user["username"], "open", True)
    _note(w, {"user": caseworker}, f"Restricted HR case #{c['id']} assigned to you", priority="Normal",
          sender="ShiftSense", related=("hr_case", c["id"]), action_required=True, dedupe=f"case|{c['id']}|{caseworker}")
    _audit(w, user, "HR case opened", f"case #{c['id']}", "", f"assigned to {caseworker}", "", "open")
    return c


def hr_cases(w, user) -> list[dict]:
    """Cases this user may see (caseworker: own; Senior HR: all). Others get nothing."""
    _active(user)
    if user["role"] != "hr":
        hr.log_access(None, user.get("username", "?"), "list", False)
        raise Denied("Restricted HR cases are visible only to HR caseworkers and Senior HR.")
    senior = auth.can(user, "senior_hr")
    return [c for c in hr.all_cases() if senior or c["caseworker"] == user["username"]]


def view_hr_case(w, user, cid: int) -> dict:
    c = _case_gate(w, user, cid, "view")
    _audit(w, user, "HR case viewed", f"case #{cid}", "", "", "", "viewed")
    return {**c, "events": hr.case_events(cid), "access_log": hr.access_log(cid) if auth.can(user, "senior_hr")
            else []}


def add_case_entry(w, user, cid: int, kind: str, body: str, due: str = "") -> None:
    """Restricted comment, evidence reference (where the evidence is kept, not the file) or follow-up."""
    _case_gate(w, user, cid, f"add {kind}")
    hr.add_case_event(cid, kind, body, due, _who(user), user["username"])
    _audit(w, user, "HR case entry", f"case #{cid}", "", kind, "", "added")


def reassign_hr_case(w, user, cid: int, caseworker: str, reason: str) -> None:
    c = _case_gate(w, user, cid, "reassign", senior_only=True)
    a = accounts.get(caseworker)
    if not a or a["role"] != "hr" or not a["active"]:
        raise ValueError("The caseworker must be an active HR user.")
    if not reason.strip():
        raise ValueError("A reason is required.")
    hr.reassign_case(cid, caseworker, _who(user), user["username"])
    _note(w, {"user": caseworker}, f"Restricted HR case #{cid} assigned to you", priority="Normal",
          sender="ShiftSense", related=("hr_case", cid), action_required=True, dedupe=f"case|{cid}|{caseworker}")
    _audit(w, user, "HR case reassigned", f"case #{cid}", c["caseworker"], caseworker, reason, "assigned")


def set_hr_case_status(w, user, cid: int, status: str, outcome: str = "") -> None:
    c = _case_gate(w, user, cid, f"status {status}")
    hr.set_case_status(cid, status, outcome, _who(user), user["username"])
    _audit(w, user, "HR case status", f"case #{cid}", c["status"], status, "", status)


def absence_validation(w, user) -> list[dict]:
    """Senior HR / HR dry run of the absence-flag rules against current attendance: creates nothing."""
    _hr(user)
    return hr.absence_validation(w)



# ================================================================== sign-in security (Admin and self-service)
def change_own_password(w, user, current: str, new: str) -> None:
    """Anyone signed in changes their own password (current password required; policy checked)."""
    from . import security
    cur = _active(user)
    rec = accounts.get(cur["username"], with_hash=True)
    if not security.verify(cur["username"], current, rec["password_hash"])[0]:
        raise ValueError("The current password is not correct.")
    if current == new:
        raise ValueError("Choose a password different from the current one.")
    accounts.set_password(cur["username"], new, must_change=False, by=_who(cur))
    _audit(w, cur, "Password changed", f"user {cur['username']}", "", "changed by the account holder", "", "changed")


def reset_password(w, user, username: str, temporary: str, reason: str) -> None:
    """Admin sets a temporary password; the person must choose their own at the next sign-in."""
    _need(user, "manage_accounts")
    if not reason.strip():
        raise ValueError("A reason is required.")
    if username == user["username"]:
        raise Denied("Change your own password from your account menu.")
    accounts.set_password(username, temporary, must_change=True, by=_who(user))
    _audit(w, user, "Password reset", f"user {username}", "", "temporary password; change required", reason, "reset")


def unlock_account(w, user, username: str, reason: str) -> None:
    _need(user, "manage_accounts")
    if not reason.strip():
        raise ValueError("A reason is required.")
    accounts.unlock(username, _who(user))
    _audit(w, user, "Account unlocked", f"user {username}", "locked", "unlocked", reason, "unlocked")



def send_test_email(w, user, address: str) -> dict:
    """Admin checks the mail relay end to end with a test message to an address they choose. Records the relay's
    answer and the Message-ID; 'sent' still means accepted by the relay, not delivered."""
    _need(user, "manage_accounts")
    if "@" not in (address or ""):
        raise ValueError("Enter an email address.")
    from datetime import datetime as _dt
    res = mailer.send("security", f"test|{user['username']}|{_dt.now().isoformat()}", kind="test", ref="relay test",
                      account={"username": user["username"]}, subject="ShiftSense: test email",
                      body="This is a test message from ShiftSense. If you received it, email copies work.",
                      address=address, require_verified=False, live=True)
    _audit(w, user, "Test email", accounts.mask_email(address), "", res["status"], res["detail"][:160], res["status"])
    return res



def import_leave_balances(w, user, data: bytes, filename: str) -> dict:
    """HR-system export of opening balances, read with flexible headers. Every usable row becomes an UNVERIFIED
    balance (a second HR user verifies); unusable rows are listed, never guessed. Unpaid leave is skipped (not
    tracked)."""
    _hr(user)
    _live_only(w)
    got = hr.read_balance_file(data, filename, set(w.data["agents"].agent_id))
    entered, skipped = [], []
    for r in got["rows"]:
        if not hr.LEAVE_TYPES[r["leave_type_id"]][1]:
            skipped.append((r["employee_id"], "unpaid leave is not tracked"))
            continue
        if r["employee_id"] in (user.get("agent_id"), user.get("employee_id")):
            skipped.append((r["employee_id"], "your own balance: another HR user enters it"))
            continue
        b = hr.enter_opening_balance(r["employee_id"], r["leave_type_id"], r["opening_balance"], r["effective_date"],
                                     r["source_reference"] or f"HR system file {filename}", _who(user),
                                     user["username"])
        entered.append(b["id"])
    _audit(w, user, "Leave balances imported", filename, "", f"{len(entered)} unverified", 
           f"{len(got['problems'])} row(s) held back", "unverified")
    return {"entered": entered, "problems": got["problems"], "skipped": skipped}


def verify_leave_balances(w, user, balance_ids: list[int]) -> dict:
    """Bulk verification by a second HR user (each one still refuses balances this user entered)."""
    _hr(user)
    ok, refused = [], []
    for bid in balance_ids:
        try:
            verify_leave_balance(w, user, bid)
            ok.append(bid)
        except (ValueError, Denied) as e:
            refused.append((bid, str(e)))
    return {"verified": ok, "refused": refused}



# ================================================================== HR reminders and downloads
def hr_reminders(w) -> int:
    """Due and overdue HR work gets a reminder (at most one per item per day). Notices carry no case details.
    * leave review tasks open for more than a day -> assigned reviewer (or HR);
    * attendance correction requests waiting more than a day -> HR;
    * restricted case deadlines today, tomorrow or past, and follow-ups due -> the caseworker only."""
    if getattr(w, "scenario", False):
        return 0
    from datetime import date, datetime as _dt, timedelta
    today = hr.local_today()
    soon = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    old = (_dt.now() - timedelta(days=1)).isoformat(timespec="seconds")
    n = 0

    def tell(to, title, kind, ref):
        nonlocal n
        if _note(w, to, title, priority="Warning", sender="ShiftSense", related=(kind, ref), action_required=True,
                 dedupe=f"hrrem|{kind}|{ref}|{today}") is not None:
            n += 1
    for t in hr.review_tasks(w.run_id, status=("open",)):
        if (t["created_at"] or "") < old:
            tell({"user": t["assigned_to"]} if t["assigned_to"] else {"role": "hr"},
                 f"Leave review waiting since {t['created_at'][:10]}: {t['request_id']}", "hr_review", t["id"])
    for c in hr.corrections(w.run_id, "pending"):
        if (c["requested_at"] or "") < old:
            tell({"role": "hr"}, f"Attendance correction #{c['id']} waiting since {c['requested_at'][:10]}",
                 "attendance_correction", c["id"])
    for c in hr.all_cases():
        if c["status"] == "closed" or not c["caseworker"]:
            continue
        if c["deadline"] and c["deadline"] <= soon:
            tell({"user": c["caseworker"]}, f"Restricted HR case #{c['id']}: deadline {c['deadline']}"
                 + (" (overdue)" if c["deadline"] < today else ""), "hr_case", c["id"])
        for e in hr.case_events(c["id"]):
            if e["kind"] == "followup" and e["due"] and e["due"] <= today:
                tell({"user": c["caseworker"]}, f"Restricted HR case #{c['id']}: follow-up due {e['due']}",
                     "hr_case_followup", e["id"])
    return n


def hr_records_workbook(w, user) -> bytes:
    """HR download: balances (with their state), the transaction ledger, separations, review tasks and attendance
    corrections. Restricted cases and absence flags are deliberately not included."""
    _hr(user)
    import io
    import pandas as pd
    names = w.data["agents"].set_index("agent_id")["name"].to_dict()
    bal = []
    for a in sorted(names):
        for lt, (lbl, paid, unit) in hr.LEAVE_TYPES.items():
            if paid:
                b = hr.balance(a, lt, w.run_id)
                bal.append({"Employee ID": a, "Name": names[a], "Leave type": lbl, "State": b["state"],
                            "Available": b["available"] if b["state"] == "verified" else "Unresolved",
                            "Unit": b["unit"]})
    sheets = {
        "Read me": pd.DataFrame({"About this file": [
            "ShiftSense HR records (simulated data in the PoC).",
            "Balances: only verified balances show a figure; others are 'Unresolved' (never assumed).",
            "Transactions: debits on approval, credits on revoke, adjustments with a reason. Nothing is overwritten.",
            "Restricted cases and absence-pattern flags are not included in this file."]}),
        "Leave balances": pd.DataFrame(bal),
        "Opening balances": pd.DataFrame(hr.balances()),
        "Transactions": pd.DataFrame(hr.transactions(run_id=w.run_id)),
        "Separations": pd.DataFrame(hr.separations()),
        "Review tasks": pd.DataFrame(hr.review_tasks(w.run_id)),
        "Attendance corrections": pd.DataFrame(hr.corrections(w.run_id)),
    }
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as x:
        for name, df in sheets.items():
            (df if len(df.columns) else pd.DataFrame({"(none yet)": []})).to_excel(x, sheet_name=name, index=False)
    return buf.getvalue()


def audit_hr_download(w, user):
    _hr(user)
    _audit(w, user, "HR records downloaded", "HR records workbook", "", "downloaded", "", "exported")


BALANCE_TEMPLATE = "Employee ID,Leave type,Balance,As of,Source\nA03,Annual,12,01/10/2026,HRIS export Oct 2026\n"


def reassign_review(w, user, task_id: int, username: str, reason: str):
    """Move a leave review task to another HR reviewer (reason required)."""
    _hr(user)
    if not reason.strip():
        raise ValueError("A reason is required.")
    t = hr.review_task(task_id)
    assign_review(w, user, task_id, username)
    _note(w, {"user": username}, f"Leave review assigned to you: {t['request_id']}", priority="Normal",
          sender=_who(user), related=("hr_review", task_id), action_required=True,
          dedupe=f"hrrev_as|{task_id}|{username}")
    _audit(w, user, "HR review reassigned", f"task #{task_id}", t["assigned_to"] or "HR queue", username, reason,
           "assigned")


def request_hr_status(w, user, req_id: str) -> str:
    """What the requester sees as the next step of their own leave request."""
    r = get_request(w, user, req_id)
    if r["state"] != "Pending":
        return "Done"
    return "HR review, then team lead" if hr.blocking_review(w.run_id, req_id) else "Team lead"



def set_own_theme(user, theme: str) -> str:
    """The signed-in person's display theme (System, Light or Dark). Never affects data."""
    cur = _active(user)
    accounts.set_theme(cur["username"], theme)
    return theme



# ================================================================== employee profile photos
def _may_see_person(w, user, employee_id: str) -> bool:
    """Who may see a person's photo: the person, their team lead (own team), and all-teams roles (Ops, Analyst,
    HR, Admin) who already see that person's operational record. Checked on the server for every request."""
    cur = _active(user)
    if employee_id in (cur.get("agent_id"), cur.get("employee_id")):
        return True
    if auth.can(cur, "all_teams") or auth.can(cur, "manage_people"):
        return True
    if cur["role"] == "team_lead":
        try:
            return _team_of(w, employee_id) == cur.get("team")
        except KeyError:
            return False
    return False


def photo_uris(w, user, employee_ids, size: str = "thumb") -> dict:
    """employee_id -> data URI for the people this user may see; others (and people without a photo) are left
    out, so the screen shows initials. One database read for the whole list."""
    from . import photos
    shas = photos.active_map()
    return {e: u for e in set(employee_ids) if e in shas and _may_see_person(w, user, e)
            and (u := photos.data_uri(e, size, shas))}


def set_employee_photo(w, user, employee_id: str, data: bytes) -> dict:
    """HR uploads or replaces a person's profile photo (validated and resized on the server; audited)."""
    _hr(user)
    _live_only(w)
    from . import photos, refdata
    known = set(w.data["agents"].agent_id) | set(refdata.current("employees"))
    if employee_id not in known:
        raise ValueError(f"No employee {employee_id}.")
    before = photos.info(employee_id)
    rec = photos.save(employee_id, data, _who(user))
    emp = refdata.current("employees").get(employee_id)
    if emp is not None:
        refdata.write("employees", [("upsert", {**emp, "photo_ref": rec["ref"]})], effective_from=hr.local_today(),
                      change_id=None, by=_who(user), source="profile photo")
    _audit(w, user, "Profile photo " + ("replaced" if before else "added"), f"employee {employee_id}",
           "photo" if before else "no photo", "photo", "", "saved")
    return rec


def remove_employee_photo(w, user, employee_id: str) -> bool:
    """HR removes a photo: the person shows initials again. The employee record and history are untouched."""
    _hr(user)
    _live_only(w)
    from . import photos, refdata
    done = photos.remove(employee_id, _who(user))
    emp = refdata.current("employees").get(employee_id)
    if done and emp is not None and emp.get("photo_ref"):
        refdata.write("employees", [("upsert", {**emp, "photo_ref": None})], effective_from=hr.local_today(),
                      change_id=None, by=_who(user), source="profile photo removed")
    if done:
        _audit(w, user, "Profile photo removed", f"employee {employee_id}", "photo", "initials", "", "removed")
    return done
