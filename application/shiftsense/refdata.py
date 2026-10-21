"""Versioned reference data: employees, contracts, client service levels, rules, leave, call volumes, situations.

Every change is a new version of a record with an effective date; nothing is overwritten or deleted (a removal
is a version marked deleted). `current()` answers "what is true on this date". `dataset()` turns the reference
data into what the planning engines use: the policy (queues, service levels, rules, contracts), the agent table,
leave, demand history and the active situations.

Storage goes through `db` (SQLite for the demo, PostgreSQL for the pilot).
"""
from __future__ import annotations

import copy
import json
import zlib
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from . import db
from .schemas import DOMAINS, SITUATION_TYPES

db.migration(101, "reference data", """
CREATE TABLE IF NOT EXISTS ref_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL, rkey TEXT NOT NULL, version INTEGER NOT NULL,
    effective_from TEXT NOT NULL, data TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, change_id INTEGER,
    changed_by TEXT, source TEXT, created TEXT);
CREATE INDEX IF NOT EXISTS ix_ref_domain_key ON ref_records (domain, rkey, version);
CREATE TABLE IF NOT EXISTS change_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, domain TEXT, status TEXT, submitted_by TEXT,
    submitted_role TEXT, submitted_at TEXT, decided_by TEXT, decided_at TEXT, decision_reason TEXT, applied_at TEXT,
    effective_from TEXT, reason TEXT, source TEXT, converted_by TEXT, ops TEXT, summary TEXT, impact TEXT,
    issues TEXT, outcome TEXT);
CREATE TABLE IF NOT EXISTS import_mappings (
    signature TEXT PRIMARY KEY, domain TEXT, mapping TEXT, updated TEXT, uses INTEGER DEFAULT 0);
""")

STATEFUL = ("employees", "contracts", "service_levels", "rules")      # "true from a date until changed"
_ready = {"done": False}


def ensure():
    if not _ready.get(db.key()):
        db.migrate()
        _ready[db.key()] = True


def key_of(domain: str, rec: dict) -> str:
    return "|".join(str(rec.get(k, "")) for k in DOMAINS[domain].key)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------------ read
def history(domain: str, key: str | None = None) -> list[dict]:
    ensure()
    q, args = "SELECT rkey, version, effective_from, data, deleted, change_id, changed_by, source, created " \
              "FROM ref_records WHERE domain=?", [domain]
    if key is not None:
        q, args = q + " AND rkey=?", args + [key]
    with db.connect() as con:
        rows = con.execute(q + " ORDER BY rkey, version", args).fetchall()
    return [{"key": r[0], "version": r[1], "effective_from": r[2], "data": json.loads(r[3]), "deleted": bool(r[4]),
             "change_id": r[5], "changed_by": r[6], "source": r[7], "created": r[8]} for r in rows]


def current(domain: str, as_of: str | None = None) -> dict[str, dict]:
    """key -> data of the version in force on `as_of` (ISO date; None = latest). Deleted records are left out.
    Dated facts (leave, volumes, situations) ignore `as_of` for selection: each is about its own date."""
    out: dict[str, dict] = {}
    for r in history(domain):
        if as_of and domain in STATEFUL and r["effective_from"] > as_of:
            continue
        out[r["key"]] = None if r["deleted"] else r
    return {k: v["data"] for k, v in out.items() if v is not None}


def scheduled(domain: str, as_of: str) -> list[dict]:
    """Versions with an effective date after `as_of` (waiting to take effect)."""
    return [r for r in history(domain) if domain in STATEFUL and r["effective_from"] > as_of]


def is_empty() -> bool:
    ensure()
    with db.connect() as con:
        return con.execute("SELECT COUNT(*) FROM ref_records").fetchone()[0] == 0


def counts() -> dict[str, int]:
    return {d: len(current(d)) for d in DOMAINS}


# ------------------------------------------------------------------ write
def write(domain: str, ops: list[tuple[str, dict]], *, effective_from: str, change_id: int | None, by: str,
          source: str, con=None) -> int:
    """ops: [("upsert", record) | ("delete", record)]. Appends a version per record. Returns rows written."""
    ensure()
    n = 0

    def run(c):
        nonlocal n
        for op, rec in ops:
            k = key_of(domain, rec)
            v = (c.execute("SELECT MAX(version) FROM ref_records WHERE domain=? AND rkey=?", (domain, k)).fetchone()[0]
                 or 0) + 1
            c.execute("INSERT INTO ref_records (domain, rkey, version, effective_from, data, deleted, change_id, "
                      "changed_by, source, created) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (domain, k, v, effective_from, json.dumps(rec, default=str, sort_keys=True),
                       1 if op == "delete" else 0, change_id, by, source, _now()))
            n += 1
    if con is not None:
        run(con)
    else:
        with db.connect() as c:
            run(c)
    return n


# ------------------------------------------------------------------ dates
def start_date(policy: dict) -> date:
    return datetime.strptime(policy["horizon"]["start_date"], "%Y-%m-%d").date()


def day_index(policy: dict, iso: str | date) -> int:
    d = iso if isinstance(iso, date) else datetime.strptime(str(iso)[:10], "%Y-%m-%d").date()
    return (d - start_date(policy)).days


def iso_of_day(policy: dict, day: int) -> str:
    return (start_date(policy) + timedelta(days=int(day))).isoformat()


def t_index(policy: dict, iso_dt: str) -> int:
    """'YYYY-MM-DD HH:MM' -> global interval index (may be outside the horizon)."""
    dt = datetime.strptime(str(iso_dt)[:16], "%Y-%m-%d %H:%M") if len(str(iso_dt)) > 10 else \
        datetime.strptime(str(iso_dt)[:10], "%Y-%m-%d")
    per_h = 60 // policy["horizon"]["interval_minutes"]
    return (dt.date() - start_date(policy)).days * 24 * per_h + dt.hour * per_h + dt.minute // policy["horizon"][
        "interval_minutes"]


# ------------------------------------------------------------------ build what the engines use
def _num(v):
    """Whole numbers stay whole (the solver needs integers for counts)."""
    return int(v) if isinstance(v, float) and v.is_integer() else v


PRIORITY_SENS = {"High": 1.0, "Medium": 0.85, "Low": 0.7}


def build_policy(base: dict, sls: dict, rules: dict, contracts: dict) -> dict:
    p = copy.deepcopy(base)
    if sls:
        queues = {}
        for q, s in sorted(sls.items()):
            open_h, close_h = s["hours"]
            queues[q] = {"aht_sec": float(s["aht_sec"]), "sla_pct": float(s["sla"][0]), "sla_sec": float(s["sla"][1]),
                         "open": float(open_h), "close": float(close_h),
                         "sensitivity": PRIORITY_SENS.get(s.get("priority") or "Medium", 0.85),
                         "peak": base["queues"].get(q, {}).get("peak", 8), "client": s.get("client", ""),
                         "penalty": s.get("penalty"), "volume_pct": s.get("volume_pct") or 0}
        p["queues"] = queues
    for k, r in rules.items():
        sect, _, name = k.rpartition(".")
        (p[sect] if sect else p["rules"])[name] = _num(r["value"])
    p["contracts"] = {n: {k: _num(v) for k, v in c.items() if v not in (None, "")} for n, c in contracts.items()}
    return p


def build_agents(policy: dict, emps: dict, as_of_day: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Agent table plus unavailability from start and leaving dates (as leave rows)."""
    days = policy["horizon"]["days"]
    queues = set(policy["queues"])
    rows, away = [], []
    for e in emps.values():
        if (e.get("status") or "Active") == "Exited":
            continue
        primary = e["primary"]
        secondary = [q for q in (e.get("secondary") or []) if q != primary and q in queues]
        if primary not in queues:
            continue
        night = e.get("night_ok")
        rows.append({"agent_id": e["employee_id"], "name": e["name"], "primary": primary, "secondary": secondary,
                     "eligible": [primary] + secondary, "proficiency": 2,
                     "night_ok": True if night is None else bool(night), "pref_shift": e.get("pref_shift") or "Day",
                     "pref_off": int(e.get("pref_off") if e.get("pref_off") is not None else 6),
                     "team": e["team"], "contract": e.get("contract")})
        s = day_index(policy, e["start_date"]) if e.get("start_date") else None
        en = day_index(policy, e["end_date"]) if e.get("end_date") else None
        for d in range(days):
            if (s is not None and d < s) or (en is not None and d > en):
                away.append({"agent_id": e["employee_id"], "day": d,
                             "reason": "not yet joined" if s is not None and d < s else "left the company"})
    cols = ["agent_id", "name", "primary", "secondary", "eligible", "proficiency", "night_ok", "pref_shift",
            "pref_off", "team", "contract"]
    agents = pd.DataFrame(rows, columns=cols).sort_values("agent_id").reset_index(drop=True)
    return agents, pd.DataFrame(away, columns=["agent_id", "day", "reason"])


def build_leave(policy: dict, leave: dict, agent_ids: set) -> pd.DataFrame:
    days = policy["horizon"]["days"]
    rows = []
    for r in leave.values():
        if (r.get("status") or "Approved") != "Approved" or r["employee_id"] not in agent_ids:
            continue
        d0 = day_index(policy, r["date"])
        d1 = day_index(policy, r.get("date_to") or r["date"])
        for d in range(max(0, d0), min(days - 1, d1) + 1):
            rows.append({"agent_id": r["employee_id"], "day": d, "reason": f"approved leave ({r.get('type') or 'Annual'})"})
    return pd.DataFrame(rows, columns=["agent_id", "day", "reason"])


def build_history(policy: dict, vols: dict) -> pd.DataFrame | None:
    """Call volumes -> the forecast's history frame (week, dow, interval, queue, calls), weeks before the horizon."""
    if not vols:
        return None
    per_day = 24 * 60 // policy["horizon"]["interval_minutes"]
    start = start_date(policy)
    rows = []
    shapes = day_shapes(policy, vols)
    for r in vols.values():
        d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        back = (start - d).days
        if back <= 0:
            continue                                     # actuals inside the horizon are not history
        week = -((back - 1) // 7) - 1                    # -1 = the week just before the horizon
        series = r.get("series") or (spread(policy, r["queue"], d.weekday(), r["daily_total"], shapes)
                                     if r.get("daily_total") is not None else None)
        if series:
            for i, c in enumerate(series[:per_day]):
                rows.append((week, d.weekday(), i, r["queue"], int(c)))
        else:
            i = int(r.get("interval") or 0)
            rows.append((week, d.weekday(), i, r["queue"], int(r["calls"])))
    if not rows:
        return None
    h = pd.DataFrame(rows, columns=["week", "dow", "interval", "queue", "calls"])
    h = h.groupby(["week", "dow", "interval", "queue"], as_index=False).calls.sum()
    h["week"] = h.week - h.week.min()                    # 0 .. n-1 as the forecast expects
    return complete_history(policy, h)


def day_shapes(policy: dict, vols: dict) -> dict:
    """Average share of the day per half hour, per (queue, weekday), from days that have half-hour detail."""
    acc = {}
    for r in vols.values():
        if not r.get("series") or r.get("estimated_split"):
            continue
        tot = sum(r["series"])
        if tot <= 0:
            continue
        k = (r["queue"], datetime.strptime(r["date"], "%Y-%m-%d").date().weekday())
        a = acc.setdefault(k, [np.zeros(48), 0])
        a[0] += np.array(r["series"][:48], dtype=float) / tot
        a[1] += 1
    return {k: v[0] / v[1] for k, v in acc.items()}


def spread(policy: dict, queue: str, dow: int, total: int, shapes: dict) -> list[int]:
    """A daily total over the day: this queue's usual shape for that weekday, else its standard daily profile."""
    shape = shapes.get((queue, dow))
    if shape is None:
        from .data_gen import intraday_profile
        prof = intraday_profile(policy, queue) if queue in policy["queues"] else np.ones(48)
        shape = prof / prof.sum() if prof.sum() else np.full(48, 1 / 48)
    raw = shape * total
    out = np.floor(raw).astype(int)
    rest = int(total - out.sum())                     # largest remainders keep the day's total exact
    for i in np.argsort(-(raw - out))[:max(0, rest)]:
        out[i] += 1
    return out.tolist()


def complete_history(policy: dict, h: pd.DataFrame) -> pd.DataFrame:
    """Fill gaps so the forecast always has a full week grid: a missing day takes that queue's average for the same
    weekday and slot; a queue with no history at all gets a seeded profile from its configured peak (assumed)."""
    from .data_gen import demand_history
    per_day = 24 * 60 // policy["horizon"]["interval_minutes"]
    weeks = range(int(h.week.max()) + 1) if len(h) else range(4)
    have = set(h.queue) if len(h) else set()
    parts = []
    for q in policy["queues"]:
        if q in have:
            g = h[h.queue == q]
            idx = pd.MultiIndex.from_product([weeks, range(7), range(per_day)], names=["week", "dow", "interval"])
            full = g.set_index(["week", "dow", "interval"]).calls.reindex(idx)
            avg = full.groupby(level=["dow", "interval"]).transform("mean")
            full = full.fillna(avg).fillna(0).round().astype(int).reset_index()
            full["queue"] = q
            parts.append(full)
        else:
            sub = copy.deepcopy(policy)
            sub["queues"] = {q: policy["queues"][q]}
            sub["staffing"] = {**policy["staffing"], "history_weeks": len(weeks)}
            parts.append(demand_history(sub, np.random.default_rng(zlib.crc32(q.encode()))))
    return pd.concat(parts, ignore_index=True)[["week", "dow", "interval", "queue", "calls"]]


def situation_effects(policy: dict, sits: dict, agents: pd.DataFrame):
    """Per-interval demand multipliers and unavailability from active situations."""
    queues = list(policy["queues"])
    per_day = 24 * 60 // policy["horizon"]["interval_minutes"]
    T = policy["horizon"]["days"] * per_day
    calls = np.ones((len(queues), T))
    aht = np.ones((len(queues), T))
    cap = np.ones((len(queues), T))
    sla = np.full((len(queues), T), np.nan)
    away = []
    team_of = dict(zip(agents.agent_id, agents.team))
    for s in sits.values():
        if (s.get("status") or "Active") != "Active":
            continue
        t0 = max(0, t_index(policy, s["start"]))
        t1 = T if not s.get("end") else min(T, t_index(policy, s["end"]) + (per_day if len(str(s["end"])) <= 10 else 0))
        if t1 <= t0:
            continue
        qi = [queues.index(q) for q in (s.get("queues") or []) if q in queues] or list(range(len(queues)))
        for i in qi:
            if s.get("closed"):
                calls[i, t0:t1] = 0
            if s.get("volume_pct"):
                calls[i, t0:t1] *= 1 + float(s["volume_pct"]) / 100
            if s.get("aht_pct"):
                aht[i, t0:t1] *= 1 + float(s["aht_pct"]) / 100
            if s.get("capacity_lost_pct"):
                lost = min(float(s["capacity_lost_pct"]), 100) / 100
                if lost >= 1:
                    calls[i, t0:t1] = 0                  # queue cannot take calls: no one is needed on it
                else:
                    cap[i, t0:t1] *= 1 - lost
            if s.get("continuity_sla_pct"):
                sla[i, t0:t1] = float(s["continuity_sla_pct"]) / 100
        typ = s.get("type")
        named = set(s.get("agents") or [])
        in_teams = {a for a, t in team_of.items() if t in set(s.get("teams") or [])}
        if typ in ("disaster", "training"):
            who = named | in_teams                       # safety first / booked elsewhere: not available
        elif typ == "protest":
            who = named or (in_teams if float(s.get("absence_pct") or 0) >= 100 else set())   # else: at risk only
        else:
            who = named
        d0, d1 = t0 // per_day, (t1 - 1) // per_day
        for a in sorted(who):
            for d in range(d0, d1 + 1):
                away.append({"agent_id": a, "day": d, "reason": f"{SITUATION_TYPES.get(typ, (typ,))[0]}: {s['title']}"})
    return {"calls": calls, "aht": aht, "capacity": cap, "sla": sla,
            "away": pd.DataFrame(away, columns=["agent_id", "day", "reason"])}


def apply_rule_exceptions(policy: dict, sits: dict, as_of: str | None):
    """Active temporary rule exceptions replace the rule (floor and contracts) while they last. They apply to the
    whole plan period while active and lapse at their end; Ops approves them."""
    ex = {}
    for s in sits.values():
        if s.get("type") != "rule_exception" or (s.get("status") or "Active") != "Active" or not s.get("rule_key"):
            continue
        if as_of and (str(s.get("end") or "9999")[:10] < as_of or str(s["start"])[:10] > iso_of_day(
                policy, policy["horizon"]["days"] - 1)):
            continue
        ex[s["rule_key"]] = _num(float(s["rule_value"]))
    for k, v in ex.items():
        sect, _, name = k.rpartition(".")
        (policy[sect] if sect else policy["rules"])[name] = v
    policy["rule_exceptions"] = {k: v for k, v in ex.items() if "." not in k}


def requirement(policy: dict, history: pd.DataFrame, effects: dict | None = None):
    """Forecast band and required heads, with situation effects and contracted volume changes applied."""
    from . import staffing
    fc = staffing.forecast(policy, history)
    queues = list(policy["queues"])
    vol = np.array([1 + float(policy["queues"][q].get("volume_pct") or 0) / 100 for q in queues])[:, None]
    fc = {k: v * vol for k, v in fc.items()}
    if effects is not None:
        fc = {k: v * effects["calls"] for k, v in fc.items()}
    chain = staffing.staffing_chain(policy, fc)
    req = chain["req"].copy()
    if effects is not None:
        interval_sec = policy["horizon"]["interval_minutes"] * 60
        shrink, occ = policy["staffing"]["shrinkage"], policy["staffing"]["max_occupancy"]
        changed = (effects["aht"] != 1) | (effects["capacity"] != 1) | ~np.isnan(effects["sla"])
        for qi, t in zip(*np.nonzero(changed)):
            p = policy["queues"][queues[qi]]
            pct = effects["sla"][qi, t] if not np.isnan(effects["sla"][qi, t]) else p["sla_pct"]
            prod = staffing.agents_required(round(float(fc["mid"][qi, t]), 1), p["aht_sec"] * effects["aht"][qi, t],
                                            interval_sec, pct, p["sla_sec"], occ)
            heads = staffing.scheduled_heads(prod, shrink)
            req[qi, t] = int(np.ceil(heads / effects["capacity"][qi, t])) if heads else 0
    return fc, req


def dataset(base_policy: dict, as_of: str | None = None, base_history: pd.DataFrame | None = None,
            overrides: dict | None = None) -> dict:
    """Everything the engines need, from reference data in force on `as_of`.
    `overrides` = {domain: {key: data or None}} lets a preview see proposed changes without saving them."""
    ov = overrides or {}

    def cur(d):
        c = dict(current(d, as_of))
        for k, v in (ov.get(d) or {}).items():
            if v is None:
                c.pop(k, None)
            else:
                c[k] = v
        return c
    policy = build_policy(base_policy, cur("service_levels"), cur("rules"), cur("contracts"))
    as_of_day = day_index(policy, as_of) if as_of else 0
    agents, away = build_agents(policy, cur("employees"), as_of_day)
    leave = build_leave(policy, cur("leave"), set(agents.agent_id))
    effects = situation_effects(policy, cur("situations"), agents)
    apply_rule_exceptions(policy, cur("situations"), as_of)
    leave = pd.concat([leave, away, effects["away"]], ignore_index=True).drop_duplicates(["agent_id", "day"])
    hist = build_history(policy, cur("volumes"))
    if hist is None and base_history is not None:
        hist = complete_history(policy, base_history)
    from .data_gen import skill_matrix
    fc, req = requirement(policy, hist, effects)
    return {"policy": policy, "agents": agents, "leave": leave.reset_index(drop=True), "history": hist,
            "skills": skill_matrix(agents, list(policy["queues"])) if len(agents) else pd.DataFrame(),
            "fc": fc, "req": req, "effects": effects}


# ------------------------------------------------------------------ actuals inside the plan period
def actuals(policy: dict, vols: dict | None = None) -> pd.DataFrame:
    """Imported call volumes for plan days: day, queue, interval, calls, answered, answered_in_target."""
    vols = current("volumes") if vols is None else vols
    days = policy["horizon"]["days"]
    rows = []
    for r in vols.values():
        d = day_index(policy, r["date"])
        if not 0 <= d < days or r["queue"] not in policy["queues"]:
            continue
        series = r.get("series") or spread(policy, r["queue"], datetime.strptime(r["date"], "%Y-%m-%d").weekday(),
                                           r.get("daily_total") or 0, day_shapes(policy, vols))
        for i, c in enumerate(series[:48]):
            rows.append({"day": d, "queue": r["queue"], "interval": i, "calls": c,
                         "answered": (r.get("answered") or [None] * 48)[i],
                         "answered_in_target": (r.get("answered_in_target") or [None] * 48)[i],
                         "estimated_split": bool(r.get("estimated_split"))})
    return pd.DataFrame(rows, columns=["day", "queue", "interval", "calls", "answered", "answered_in_target",
                                       "estimated_split"])


def forecast_accuracy(policy: dict, fc: dict | None, act: pd.DataFrame) -> pd.DataFrame:
    """Per day and queue: actual vs forecast calls, error % and bias. Empty when no actuals were imported."""
    if fc is None or act.empty:
        return pd.DataFrame(columns=["day", "queue", "actual", "forecast", "error_pct", "bias_pct", "in_band_pct"])
    queues = list(policy["queues"])
    per_day = 24 * 60 // policy["horizon"]["interval_minutes"]
    out = []
    for (d, q), g in act.groupby(["day", "queue"]):
        qi = queues.index(q)
        t = d * per_day + g.interval.to_numpy()
        f = fc["mid"][qi, t]
        lo, hi = fc["low"][qi, t], fc["high"][qi, t]
        a = g.calls.to_numpy(dtype=float)
        tot_a, tot_f = a.sum(), f.sum()
        out.append({"day": int(d), "queue": q, "actual": int(tot_a), "forecast": round(float(tot_f)),
                    "error_pct": round(100 * float(abs(a - f).sum()) / tot_a, 1) if tot_a else None,
                    "bias_pct": round(100 * float(tot_f - tot_a) / tot_a, 1) if tot_a else None,
                    "in_band_pct": round(100 * float(((a >= lo) & (a <= hi)).mean()), 1)})
    return pd.DataFrame(out)


def actual_service(act: pd.DataFrame) -> pd.DataFrame:
    """Per day and queue: share answered within target, only where that detail was imported."""
    have = act.dropna(subset=["answered_in_target"])
    if have.empty:
        return pd.DataFrame(columns=["day", "queue", "calls", "answered_in_target", "service_level_pct"])
    g = have.groupby(["day", "queue"]).agg(calls=("calls", "sum"), answered_in_target=("answered_in_target", "sum"))
    g["service_level_pct"] = (100 * g.answered_in_target / g.calls.where(g.calls > 0)).round(1)
    return g.reset_index()


# ------------------------------------------------------------------ data quality
def quality(policy: dict, as_of: str | None = None) -> list[tuple[str, str]]:
    """(level, message) findings about the reference data. Levels: Critical, Warning, Info."""
    out = []
    emps = current("employees", as_of)
    if not emps:
        return [("Info", "No reference data yet.")]
    sls = current("service_levels", as_of)
    cons = current("contracts", as_of)
    active = [e for e in emps.values() if (e.get("status") or "Active") != "Exited"]
    for q in sls:
        n = sum(1 for e in active if e["primary"] == q or q in (e.get("secondary") or []))
        if n < 5:
            out.append(("Critical" if n == 0 else "Warning", f"Queue {q}: only {n} trained agent(s)"))
    bad_contract = [e["employee_id"] for e in active if e.get("contract") not in cons]
    if bad_contract:
        out.append(("Critical", f"{len(bad_contract)} employee(s) on an unknown contract: {', '.join(bad_contract[:5])}"))
    bad_queue = [e["employee_id"] for e in active if e["primary"] not in sls]
    if bad_queue:
        out.append(("Critical", f"{len(bad_queue)} employee(s) whose main queue has no service level: "
                                f"{', '.join(bad_queue[:5])}"))
    names = {}
    for e in active:
        names.setdefault(e["name"].strip().lower(), []).append(e["employee_id"])
    dup = [v for v in names.values() if len(v) > 1]
    if dup:
        out.append(("Info", f"{len(dup)} name(s) shared by several employees (check they are different people): "
                            + "; ".join(", ".join(v) for v in dup[:3])))
    leave = current("leave")
    unknown = sorted({r["employee_id"] for r in leave.values() if r["employee_id"] not in emps})
    if unknown:
        out.append(("Warning", f"Leave for unknown employee(s): {', '.join(unknown[:5])}"))
    vols = current("volumes")
    start = start_date(policy)
    for q in sls:
        days = {r["date"] for r in vols.values() if r["queue"] == q}
        want = {(start - timedelta(days=i)).isoformat() for i in range(1, 29)}
        missing = len(want - days)
        if missing == 28:
            out.append(("Warning", f"No call history for {q} in the last 4 weeks: the forecast uses an assumed profile"))
        elif missing:
            out.append(("Info", f"{q}: {missing} of the last 28 days have no call history (filled from similar days)"))
    est = sum(1 for r in vols.values() if r.get("estimated_split"))
    if est:
        out.append(("Info", f"{est} day(s) of call volumes were daily totals, spread over the day (estimated split)"))
    notice = [e for e in active if e.get("status") == "On notice"]
    if notice:
        out.append(("Info", f"{len(notice)} employee(s) on notice"))
    no_night = [q for q in sls if (sls[q]["hours"][0], sls[q]["hours"][1]) == (0, 24)
                and not any(e.get("night_ok", True) and (e["primary"] == q or q in (e.get("secondary") or []))
                            for e in active)]
    if no_night:
        out.append(("Critical", f"24-hour queue(s) with nobody allowed to work nights: {', '.join(no_night)}"))
    return out
