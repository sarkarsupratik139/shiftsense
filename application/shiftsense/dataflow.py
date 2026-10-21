"""The change engine: how a reference-data change becomes part of the live operation.

    propose  ->  impact preview  ->  approve (where needed)  ->  apply on the effective date
             ->  re-check the roster and staffing need  ->  alerts / re-plan recommendation / notices  ->  log

Rules that never bend:
  * Nothing edits a published shift silently. Shifts that a change makes impossible are released, and each
    release is either an alert with a ready fix (today and tomorrow) or part of a re-plan recommendation.
  * Shifts already started are never touched.
  * Changes are made only in the live workspace, never from the demo scenario.
  * Every change set keeps who, when, source, how it was converted, before and after, and the outcome.
"""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import pandas as pd

from . import comms, db, refdata, workflow
from .config import day_label, load_policy, queue_names, shift_intervals
from .constraints import check
from .roster import coverage
from .schemas import DOMAINS

NEAR_DAYS = 1           # default; the rule recovery.data_alert_days overrides it (0 = today only, up to 3)


def near_days(w) -> int:
    return int(max(0, min(3, (w.policy.get("recovery") or {}).get("data_alert_days", NEAR_DAYS))))


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def as_of(w) -> str:
    return refdata.iso_of_day(w.policy, w.today)


# ------------------------------------------------------------------ context for conversion
def context(w) -> dict:
    refdata.ensure()
    emps = refdata.current("employees", as_of(w))
    sls = refdata.current("service_levels", as_of(w))
    names = {}
    for e in emps.values():
        full = e["name"].lower().strip()
        names[full] = e["employee_id"]
        first = full.split()[0] if full else ""
        if first and sum(1 for x in emps.values() if x["name"].lower().split()[:1] == [first]) == 1:
            names[first] = e["employee_id"]
    queues = sorted(sls) or list(w.policy["queues"])
    teams = sorted({e["team"] for e in emps.values()} | set(w.data["agents"].team))
    return {"queues": queues, "teams": teams, "contracts": sorted(refdata.current("contracts", as_of(w))),
            "shifts": list(w.policy["shifts"]), "floor": dict(load_policy()["rules"], **{
                k: v["value"] for k, v in refdata.current("rules", as_of(w)).items() if "." not in k}),
            "employees": set(emps) or set(w.data["agents"].agent_id), "employee_names": names,
            "today": as_of(w), "day_first": True}


# ------------------------------------------------------------------ operations from converted rows
def to_ops(domain: str, rows: list[dict], *, mode: str = "upsert", deletes: list[dict] | None = None) -> list[dict]:
    """Converted rows -> operations with their before-state: new | changed | same | delete."""
    cur = refdata.current(domain)
    ops = []
    if domain == "volumes":
        rows = _volume_records(rows, cur)
    seen = set()
    for r in rows:
        k = refdata.key_of(domain, r)
        seen.add(k)
        before = cur.get(k)
        merged = {**(before or {}), **r} if before and domain in ("employees",) else r
        kind = "new" if before is None else "same" if _same(before, merged) else "changed"
        ops.append({"op": "upsert", "kind": kind, "key": k, "data": merged, "before": before})
    if mode == "replace":
        for k, before in cur.items():
            if k not in seen:
                ops.append({"op": "delete", "kind": "delete", "key": k, "data": before, "before": before})
    for d in deletes or []:
        k = refdata.key_of(domain, d)
        if k in cur:
            ops.append({"op": "delete", "kind": "delete", "key": k, "data": cur[k], "before": cur[k]})
    return ops


def _same(a: dict, b: dict) -> bool:
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _volume_records(rows: list[dict], cur: dict) -> list[dict]:
    """Half-hour rows -> one record per date and queue holding the 48 values (missing slots keep old values).
    A row without a time is a daily total: stored as such and spread over the day when the forecast is built
    (marked "estimated split")."""
    per, totals = {}, {}
    for r in rows:
        k = (r["date"], r["queue"])
        if r.get("time") is None:
            totals[k] = totals.get(k, 0) + int(r["calls"])
            continue
        if k not in per:
            old = cur.get(f"{r['date']}|{r['queue']}|")
            per[k] = list((old or {}).get("series") or [0] * 48)
        i = int(round(float(r["time"]) * 2)) % 48
        per[k][i] = int(r["calls"])
        for extra in ("answered", "answered_in_target"):            # optional actual-service detail
            if r.get(extra) is not None:
                ex = per.setdefault((k, extra), list((cur.get(f"{r['date']}|{r['queue']}|") or {}).get(extra) or
                                                     [None] * 48))
                ex[i] = int(r[extra])
    out = []
    for k, series in per.items():
        if isinstance(k[1], str) and k[1] in ("answered", "answered_in_target"):
            continue
        rec = {"date": k[0], "queue": k[1], "time": "", "series": series}
        for extra in ("answered", "answered_in_target"):
            if (k, extra) in per:
                rec[extra] = per[(k, extra)]
        out.append(rec)
    for (d, q), total in totals.items():
        if (d, q) in per:
            continue                                    # half-hour detail wins over a total for the same day
        out.append({"date": d, "queue": q, "time": "", "series": None, "daily_total": total, "estimated_split": True})
    return out


# ------------------------------------------------------------------ impact
def preview(w, domain: str, ops: list[dict], effective_from: str | None = None) -> dict:
    """What the change would do, computed on copies. Nothing is saved."""
    eff = effective_from or as_of(w)
    overrides = {domain: {o["key"]: (None if o["op"] == "delete" else o["data"]) for o in ops if o["kind"] != "same"}}
    counts = {k: sum(o["kind"] == k for o in ops) for k in ("new", "changed", "same", "delete")}
    out = {"counts": counts, "effective_from": eff, "linked": bool(getattr(w, "ref_linked", False))}
    if not out["linked"]:
        out["note"] = ("The live workspace is not built from reference data yet, so this change is stored and takes "
                       "effect when the workspace is rebuilt from reference data.")
        return out
    try:
        new = refdata.dataset(load_policy(), max(eff, as_of(w)), w.data.get("history"), overrides)
    except Exception as e:                                  # an unusable combination is reported, not raised
        out["error"] = f"This change can't be applied: {type(e).__name__}: {e}"
        return out
    if list(new["policy"]["queues"]) != queue_names(w.policy):
        out["queue_change"] = ("Adds or removes a queue. It is stored now and takes effect when the Analyst rebuilds "
                               "the plan from reference data (new queues need a new roster).")
    from_day = max(w.today, refdata.day_index(w.policy, eff))
    viol = relevant(check(new["policy"], new["agents"], new["leave"], w.roster), from_day) \
        if not out.get("queue_change") else []
    gone = set(w.data["agents"].agent_id) - set(new["agents"].agent_id)
    pairs = _release_pairs(w, new, viol, gone, from_day)
    near = sorted({(a, d) for a, d, _ in pairs if d <= w.today + near_days(w)})
    later = sorted({(a, d) for a, d, _ in pairs if d > w.today + near_days(w)})
    out.update({"violations": len(viol), "release_near": len(near), "release_later": len(later),
                "released": [{"Agent": a, "Day": day_label(w.policy, d), "Why": why} for a, d, why in pairs][:200],
                "agents_affected": len({a for a, _, _ in pairs})})
    if not out.get("queue_change"):
        per_day = w.req.shape[1] // w.policy["horizon"]["days"]
        t0 = from_day * per_day
        cov = coverage(new["policy"], w.roster)
        old_gap = np.maximum(w.req[:, t0:] - coverage(w.policy, w.roster)[:, t0:], 0).sum() / 2
        new_gap = np.maximum(new["req"][:, t0:] - cov[:, t0:], 0).sum() / 2
        out.update({"gap_before_ph": round(float(old_gap), 1), "gap_after_ph": round(float(new_gap), 1),
                    "need_before_ph": round(float(w.req[:, t0:].sum() / 2), 1),
                    "need_after_ph": round(float(new["req"][:, t0:].sum() / 2), 1)})
    if domain == "leave":
        pend = [r for r in w.requests if r["state"] in ("Pending", "Waiting for colleague")]
        newly = {(o["data"]["employee_id"], o["data"]["date"]) for o in ops if o["op"] == "upsert"}
        out["requests_affected"] = sum((r["agent_id"], refdata.iso_of_day(w.policy, r["day"])) in newly for r in pend)
    return out


def relevant(viol: list[dict], from_day: int) -> list[dict]:
    """Violations a change can still act on: shifts from `from_day`, weekly limits of that week onwards."""
    week_rules = ("max_weekly_hours", "weekly_off", "max_nights_per_week")
    return [v for v in viol if v["day"] is None or
            (v["day"] // 7 >= from_day // 7 if v["rule"] in week_rules else v["day"] >= from_day)]


def _release_pairs(w, new: dict, viol: list[dict], gone: set, from_day: int) -> list[tuple[str, int, str]]:
    """(agent, day, why) for future shifts the change makes impossible."""
    started = _started(w)
    out, seen = [], set()
    ro = w.roster
    for a in sorted(gone):
        for d in sorted(ro[(ro.agent_id == a) & (ro.day >= from_day)].day):
            if (a, d) not in started and (a, d) not in seen:
                seen.add((a, d))
                out.append((a, int(d), "no longer in the workforce data"))
    for v in viol:
        a, d = v["agent_id"], v["day"]
        if a in gone:
            continue
        if d is None or v["rule"] in ("max_weekly_hours", "weekly_off", "max_nights_per_week"):
            wk = (d // 7) if d is not None else None
            rows = ro[(ro.agent_id == a) & (ro.day >= from_day)]
            if wk is not None:
                rows = rows[rows.day // 7 == wk]
            if v["rule"] == "max_nights_per_week":
                from .config import is_night
                rows = rows[rows["shift"].map(lambda x: is_night(w.policy, x))]
            cand = [int(x) for x in sorted(rows.day, reverse=True) if (a, int(x)) not in started and (a, int(x)) not in seen]
            if cand:
                d = cand[0]
            else:
                continue
        if (a, d) in started or (a, d) in seen:
            continue
        seen.add((a, d))
        out.append((a, int(d), v["detail"]))
    return out


def _started(w) -> set:
    out = set()
    for r in w.roster.itertuples():
        if shift_intervals(w.policy, r.day, r.shift, r.ext_before, r.ext_after).start <= w.now_t:
            out.add((r.agent_id, int(r.day)))
    return out


# ------------------------------------------------------------------ change sets
def needs_approval(domain: str, user: dict, rows: list[dict] | None = None) -> str | None:
    d = DOMAINS[domain]
    if domain == "rules" and rows and all(r.get("rule", "").startswith("governance.") for r in rows) \
            and user["role"] == "admin":
        return "ops_head"
    if domain == "situations" and rows and any(r.get("continuity_sla_pct") or r.get("type") == "rule_exception"
                                               for r in rows) and user["role"] != "ops_head":
        return "ops_head"
    if d.approval and user["role"] != d.approval:
        return d.approval
    return None


def save_set(w, user, domain: str, ops: list[dict], *, effective_from: str, reason: str, source: str,
             converted_by: str, impact: dict, issues: list, status: str) -> int:
    refdata.ensure()
    with db.connect() as con:
        cur = con.execute(
            "INSERT INTO change_sets (run_id, domain, status, submitted_by, submitted_role, submitted_at, effective_from, "
            "reason, source, converted_by, ops, summary, impact, issues) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (w.run_id, domain, status, f"{user['name']} ({user.get('role_label', user['role'])})", user["role"], _now(),
             effective_from, reason, source, converted_by,
             json.dumps([o for o in ops if o["kind"] != "same"], default=str),
             json.dumps(impact.get("counts", {})), json.dumps(impact, default=str), json.dumps(issues, default=str)))
        return int(cur.lastrowid)


def get_set(cid: int) -> dict | None:
    refdata.ensure()
    cols = ["id", "run_id", "domain", "status", "submitted_by", "submitted_role", "submitted_at", "decided_by",
            "decided_at", "decision_reason", "applied_at", "effective_from", "reason", "source", "converted_by", "ops",
            "summary", "impact", "issues", "outcome"]
    with db.connect() as con:
        r = con.execute(f"SELECT {', '.join(cols)} FROM change_sets WHERE id=?", (cid,)).fetchone()
    if not r:
        return None
    d = dict(zip(cols, r))
    for k in ("ops", "summary", "impact", "issues", "outcome"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d


def sets(status: str | None = None, domain: str | None = None, limit: int = 200) -> list[dict]:
    refdata.ensure()
    q, args = "SELECT id FROM change_sets WHERE 1=1", []
    if status:
        q, args = q + " AND status=?", args + [status]
    if domain:
        q, args = q + " AND domain=?", args + [domain]
    with db.connect() as con:
        ids = [r[0] for r in con.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()]
    return [get_set(i) for i in ids]


def _update_set(cid: int, **fields):
    sets_ = ", ".join(f"{k}=?" for k in fields)
    vals = [json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for v in fields.values()]
    with db.connect() as con:
        con.execute(f"UPDATE change_sets SET {sets_} WHERE id=?", vals + [cid])


# ------------------------------------------------------------------ apply
def _resolve_photos(cs: dict, by: str):
    """Employee imports may name a photo file in the drop folder's photos/ folder. A valid file is stored and the
    record keeps only its reference; anything else (missing file, outside link, bad image) is dropped from the
    record so the person shows initials. The employee record itself is never rejected because of a photo."""
    if cs["domain"] != "employees":
        return
    from . import dropfolder, photos
    for o in cs["ops"]:
        d = o["data"]
        ref = str(d.get("photo_ref") or "").strip()
        if not ref or ref.startswith("photo:") or o["op"] != "upsert":
            continue
        path, _ = photos.resolve_ref(ref, dropfolder.inbox())
        d["photo_ref"] = None
        if path:
            try:
                from pathlib import Path
                d["photo_ref"] = photos.save(d["employee_id"], Path(path).read_bytes(), by, f"import {ref}")["ref"]
            except photos.PhotoError:
                pass


def write_set(cs: dict, by: str):
    _resolve_photos(cs, by)
    ops = [(o["op"], o["data"]) for o in cs["ops"]]
    refdata.write(cs["domain"], ops, effective_from=cs["effective_from"], change_id=cs["id"], by=by,
                  source=cs["source"])


def _overrides(cs: dict) -> dict:
    return {cs["domain"]: {o["key"]: (None if o["op"] == "delete" else o["data"]) for o in cs["ops"]}}


def propagate(w, cs: dict, by: str, pending: bool = False) -> dict:
    """Bring the live workspace in line with reference data in force today, then act on what no longer fits.
    pending=True: the change set's records are not written yet (they are written only if this succeeds)."""
    if not getattr(w, "ref_linked", False):
        return {"note": "Stored. The live workspace is not built from reference data yet; rebuild to use it."}
    new = refdata.dataset(load_policy(), as_of(w), w.data.get("history"), _overrides(cs) if pending else None)
    if list(new["policy"]["queues"]) != queue_names(w.policy):
        return {"note": "Stored. Queue list changed: takes effect when the plan is rebuilt from reference data."}
    old_req = w.req.copy()
    from_day = w.today
    gone = set(w.data["agents"].agent_id) - set(new["agents"].agent_id)
    viol = relevant(check(new["policy"], new["agents"], new["leave"], w.roster), from_day)
    pairs = _release_pairs(w, new, viol, gone, from_day)

    # 1. switch the workspace to the new reference data (operational leave rows are kept)
    lv = w.data["leave"]
    ops_leave = lv[lv.get("source", pd.Series(["op"] * len(lv), index=lv.index)).fillna("op") != "ref"] \
        if "source" in lv.columns else lv
    ref_leave = new["leave"].assign(source="ref")
    keep_ops = ops_leave[~ops_leave.set_index(["agent_id", "day"]).index.isin(
        ref_leave.set_index(["agent_id", "day"]).index)]
    old_agents = w.data["agents"]
    former = pd.concat([w.data.get("former_agents", old_agents.iloc[0:0]), old_agents[old_agents.agent_id.isin(gone)]],
                       ignore_index=True).drop_duplicates("agent_id", keep="last")
    w.policy = {**new["policy"]}
    w.data = {**w.data, "agents": new["agents"], "skills": new["skills"], "history": new["history"],
              "leave": pd.concat([keep_ops, ref_leave], ignore_index=True), "former_agents": former}
    w.fc = new["fc"]
    req = old_req.copy()
    req[:, w.now_t:] = new["req"][:, w.now_t:]
    for inc in w.incidents:                                  # same-day spike revisions stay in force
        if inc.status not in ("closed", "no_gap") and inc.target_override is not None and inc.kind == "spike":
            mask = inc.target_override >= 0
            req[mask] = np.maximum(req[mask], inc.target_override[mask])
    w.req = req

    # 2. shifts that no longer fit: alerts for today/tomorrow, release for later days
    reason = f"data change #{cs['id']} ({DOMAINS[cs['domain']].label.lower()})"
    near = {}
    later = []
    for a, d, why in pairs:
        (near.setdefault((d, workflow._team(w, a)), []).append((a, why))
         if d <= w.today + near_days(w) else later.append((a, d, why)))
    alerts = []
    for (d, _team), items in sorted(near.items()):
        ids = [a for a, _ in items if ((w.roster.agent_id == a) & (w.roster.day == d)).any()]
        if not ids:
            continue
        try:
            inc = workflow.report_absence(w, ids, d, by, reason=f"{reason}: {items[0][1]}", kind="data_change")
            alerts.append(inc.id)
        except ValueError:
            continue
    released = []
    for a, d, why in later:
        mask = (w.roster.agent_id == a) & (w.roster.day == d)
        if mask.any():
            w.roster = w.roster[~mask].reset_index(drop=True)
            released.append({"agent": a, "day": d, "why": why})
    # anything still violating (rare: weekly limits needing more than one release) is released too
    for _ in range(3):
        rest = relevant(check(w.policy, w.data["agents"], w.data["leave"], w.roster), from_day)
        more = _release_pairs(w, {"agents": w.data["agents"]}, rest, set(), from_day)
        if not more:
            break
        for a, d, why in more:
            mask = (w.roster.agent_id == a) & (w.roster.day == d)
            w.roster = w.roster[~mask].reset_index(drop=True)
            released.append({"agent": a, "day": d, "why": why})

    # 3. staffing need went up where the roster is now short (today and tomorrow): alerts with a fix
    per_day = w.req.shape[1] // w.policy["horizon"]["days"]
    cov = coverage(w.policy, w.roster)
    queues = queue_names(w.policy)
    for d in range(w.today, min(w.policy["horizon"]["days"], w.today + near_days(w) + 1)):
        for qi, q in enumerate(queues):
            t0, t1 = max(w.now_t, d * per_day), (d + 1) * per_day
            if t1 <= t0:
                continue
            up = (w.req[qi, t0:t1] > old_req[qi, t0:t1]) & (cov[qi, t0:t1] < w.req[qi, t0:t1])
            if not up.any():
                continue
            override = np.full_like(w.req, -1)
            override[qi, t0:t1] = np.where(up, w.req[qi, t0:t1], -1)
            short = float(np.maximum(w.req[qi, t0:t1] - cov[qi, t0:t1], 0)[up].sum() / 2)
            try:
                inc = workflow.requirement_change(w, q, d, override, by,
                                                  f"{q} needs more people on {day_label(w.policy, d)} after {reason} "
                                                  f"({short:g} people-h short)")
                alerts.append(inc.id)
            except Exception:
                continue

    # 4. later days: re-plan recommendation (a person builds and publishes it)
    t_later = (w.today + near_days(w) + 1) * per_day
    later_gap = float(np.maximum(w.req[:, t_later:] - cov[:, t_later:], 0).sum() / 2) if t_later < w.req.shape[1] else 0
    gap_rise = float(np.maximum(w.req[:, t_later:] - old_req[:, t_later:], 0).sum() / 2) if t_later < w.req.shape[1] else 0
    replan = bool(released) or gap_rise > 0
    outcome = {"alerts": alerts, "released": released, "replan_recommended": replan,
               "later_gap_ph": round(later_gap, 1), "need_rise_ph": round(gap_rise, 1)}
    if replan:
        comms.notify(w.run_id, {"role": "analyst"}, f"Re-plan recommended after {reason}",
                     body=f"{len(released)} later shift(s) released; staffing need rose by {gap_rise:g} people-h on later "
                          f"days. Build a new draft in Review & publish.", priority="Warning", sender=by,
                     related=("change", cs["id"]), action_required=True, sim_time=workflow.now_label(w),
                     dedupe=f"replan|{w.run_id}|{cs['id']}")
    affected_teams = {workflow._team(w, r["agent"]) for r in released}
    for t in sorted(affected_teams):
        comms.notify(w.run_id, {"role": "team_lead", "team": t}, f"Shifts released by {reason}",
                     body="; ".join(f"{r['agent']} {day_label(w.policy, r['day'])}: {r['why']}" for r in released
                                    if workflow._team(w, r["agent"]) == t)[:400],
                     priority="Normal", sender=by, related=("change", cs["id"]), sim_time=workflow.now_label(w),
                     dedupe=f"rel|{w.run_id}|{cs['id']}|{t}")
    if alerts or released:
        comms.notify(w.run_id, {"role": "ops_head"}, f"Data change applied: {reason}",
                     body=f"{len(alerts)} alert(s) raised, {len(released)} later shift(s) released.",
                     priority="Info", sender=by, related=("change", cs["id"]), sim_time=workflow.now_label(w),
                     dedupe=f"ops|{w.run_id}|{cs['id']}")
    workflow._log(w, "DataChange", reason, f"{len(alerts)} alerts, {len(released)} released", actor=by,
                  outcome="applied")
    return outcome


def apply_set(w, cs: dict, by: str) -> dict:
    """Propagate first (on the records as proposed), write them only when that succeeded; future-dated sets are
    written now and propagate when their date arrives."""
    if cs["domain"] == "attendance":                     # recorded facts, not planning data
        write_set(cs, by)
        outcome = apply_attendance(w, cs, by)
        _update_set(cs["id"], status="applied", applied_at=_now(), outcome=outcome)
        return outcome
    if cs["effective_from"] > as_of(w):
        write_set(cs, by)
        _update_set(cs["id"], status="scheduled", applied_at=_now(),
                    outcome={"note": f"Takes effect on {cs['effective_from']}."})
        return {"note": f"Scheduled for {cs['effective_from']}."}
    try:
        outcome = propagate(w, cs, by, pending=True)
    except Exception as e:
        _update_set(cs["id"], status="failed", outcome={"note": f"Not applied: {type(e).__name__}: {e}"})
        raise
    write_set(cs, by)
    _update_set(cs["id"], status="applied", applied_at=_now(), outcome=outcome)
    return outcome


def apply_due(w, by: str = "ShiftSense (scheduled change)") -> list[int]:
    """Propagate scheduled change sets whose effective date has arrived, and re-check the roster when a temporary
    rule exception has ended (its value no longer applies)."""
    done = []
    for cs in sorted(sets(status="scheduled"), key=lambda c: c["id"]):
        if cs["effective_from"] <= as_of(w):
            outcome = propagate(w, cs, by)
            _update_set(cs["id"], status="applied", outcome=outcome)
            done.append(cs["id"])
    if getattr(w, "ref_linked", False):
        now_rules = refdata.dataset(load_policy(), as_of(w), w.data.get("history"))["policy"]
        if now_rules["rules"] != w.policy["rules"] or now_rules.get("rule_exceptions") != w.policy.get("rule_exceptions"):
            propagate(w, {"id": "rules", "domain": "situations", "ops": []}, by)
    return done


def apply_attendance(w, cs: dict, by: str) -> dict:
    """Login records -> attendance: only for rostered shifts that have started and have no record yet. Lateness
    in minutes from the login time. Nothing is overwritten and a missing login is never turned into a no-show."""
    from .config import shift_window
    recorded, skipped = [], []
    for o in cs["ops"]:
        r = o["data"]
        if o["op"] != "upsert":
            continue
        a, d = r["employee_id"], refdata.day_index(w.policy, r["date"])
        row = w.roster[(w.roster.agent_id == a) & (w.roster.day == d)]
        if row.empty:
            skipped.append(f"{a} {r['date']}: not rostered")
            continue
        if (a, d) not in _started(w):
            skipped.append(f"{a} {r['date']}: shift not started yet")
            continue
        if w.attendance.get((a, d)):
            skipped.append(f"{a} {r['date']}: already recorded ({w.attendance[(a, d)]})")
            continue
        from . import hr
        if hr.is_locked(w, a, d):
            skipped.append(f"{a} {r['date']}: attendance locked; needs an HR-reviewed correction request")
            continue
        x = row.iloc[0]
        start_h = shift_window(w.policy, d, x["shift"], x["ext_before"], x["ext_after"])[0] - 24 * d
        login = float(r["login"]) + (24 if float(r["login"]) < start_h - 12 else 0)
        late = max(0, round((login - start_h) * 60))
        w.attendance[(a, d)] = "feed"
        w.attendance_detail[(a, d)] = {"login": r["login"], "logout": r.get("logout"), "late_min": late,
                                       "source": r.get("source") or "feed"}
        recorded.append(a)
        comms.audit(w.run_id, by, "system", "Attendance feed", f"{a} {day_label(w.policy, d)}", "no record",
                    "present (attendance feed)" + (f" · {late} min after start" if late else " · on time"), "",
                    r.get("source") or "feed", workflow.now_label(w))
    return {"note": f"{len(recorded)} attendance record(s) added, {len(skipped)} skipped",
            "recorded": len(recorded), "skipped": skipped[:50]}


def decide(cid: int, approve: bool, by: str, reason: str):
    _update_set(cid, status="approved" if approve else "rejected", decided_by=by, decided_at=_now(),
                decision_reason=reason)
