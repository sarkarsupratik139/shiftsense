"""Import agents (and optional approved leave) from CSV/Excel, with validation.

Demand stays the synthetic forecast in the PoC; importing real volumes is a later phase.
"""
from __future__ import annotations

import pandas as pd

from .config import queue_names, shift_names
from .data_gen import skill_matrix

AGENT_COLUMNS = ["agent_id", "name", "primary", "secondary", "night_ok", "team", "pref_shift", "pref_off"]
DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def template(agents: pd.DataFrame) -> pd.DataFrame:
    """Current agents in the import format (secondary queues separated by ';')."""
    t = agents.copy()
    t["secondary"] = t.secondary.map(lambda xs: ";".join(xs))
    t["pref_off"] = t.pref_off.map(lambda i: DAY_NAMES[int(i)].capitalize())
    return t[AGENT_COLUMNS]


TRUE, FALSE = {"1", "true", "yes", "y"}, {"0", "false", "no", "n"}
REQUIRED = {"agent_id": "Unique agent ID, e.g. A01", "name": "Agent's name", "primary": "Main queue",
            "team": "Team, e.g. TL-1"}
OPTIONAL = {"secondary": "Other queues the agent is trained for, separated by ';'",
            "night_ok": "yes / no (default yes)", "pref_shift": "Morning / Day / Evening / Night (default Day)",
            "pref_off": "Preferred day off, Mon–Sun (default Sun)"}
LEAVE_COLUMNS = {"agent_id": "Agent ID", "date": "Leave date, YYYY-MM-DD"}
ALIASES = {   # common header variations -> template column (compared after normalising case and separators)
    "agent_id": ["agent id", "agentid", "id", "agent", "agent code", "emp id", "employee id"],
    "name": ["full name", "agent name", "employee name", "employee"],
    "primary": ["primary queue", "main queue", "queue", "skill", "primary skill"],
    "secondary": ["secondary queues", "secondary queue", "other queues", "cross skills", "cross skill", "secondary skills"],
    "night_ok": ["night", "nights", "night ok", "can work nights", "night shift ok", "nights ok"],
    "team": ["team id", "team name", "tl", "team lead team"],
    "pref_shift": ["preferred shift", "shift preference", "shift"],
    "pref_off": ["preferred day off", "day off", "weekly off", "preferred off"],
    "date": ["leave date", "day", "leave day", "date of leave", "absence date"],
}


def _norm(h) -> str:
    return " ".join(str(h).strip().lower().replace("_", " ").replace("-", " ").split())


def suggest_mapping(columns, targets) -> dict:
    """{template column: uploaded column or None}, matching exact names first, then known variations."""
    cols = {_norm(c): c for c in columns}
    out = {}
    for t in targets:
        hit = cols.get(_norm(t)) or next((cols[_norm(a)] for a in ALIASES.get(t, []) if _norm(a) in cols), None)
        out[t] = hit
    return out


def apply_mapping(df: pd.DataFrame, mapping: dict) -> pd.DataFrame:
    """Rename mapped columns to the template names; unmapped template columns are left out."""
    used = {src: t for t, src in mapping.items() if src}
    return df[list(used)].rename(columns=used)


def _blank(v) -> bool:
    return v is None or (isinstance(v, float) and pd.isna(v)) or str(v).strip().lower() in ("", "nan", "none")


def validate_agents(df: pd.DataFrame, policy: dict, known_teams: set[str] | None = None) -> dict:
    """Check every row before anything changes. Returns accepted agents (only if NOTHING was rejected),
    one result per row and a summary. Rows are numbered as in the spreadsheet (header = row 1)."""
    queues, shifts = queue_names(policy), shift_names(policy)
    missing = [c for c in REQUIRED if c not in df.columns]
    if missing:
        return {"agents": None, "rows": [], "summary": {"accepted": 0, "rejected": 0, "skipped": 0},
                "errors": [f"Missing column(s): {', '.join(missing)}. Map them to a column in your file."]}
    results, rows, seen = [], [], {}
    for i, r in enumerate(df.to_dict("records")):
        line, probs, notes = i + 2, [], []
        if all(_blank(v) for v in r.values()):
            results.append({"row": line, "agent_id": "", "status": "skipped", "problems": "empty row"})
            continue
        aid = "" if _blank(r.get("agent_id")) else str(r["agent_id"]).strip()
        if not aid:
            probs.append("agent_id is empty")
        elif aid in seen:
            probs.append(f"duplicate agent_id {aid} (also row {seen[aid]})")
        else:
            seen[aid] = line
        name = "" if _blank(r.get("name")) else str(r["name"]).strip()
        if not name:
            probs.append("name is empty")
        primary = "" if _blank(r.get("primary")) else str(r["primary"]).strip()
        if primary not in queues:
            probs.append(f"unknown queue '{primary}' (use one of {', '.join(queues)})")
        sec_raw = r.get("secondary", "")
        secondary = [] if _blank(sec_raw) else [x.strip() for x in str(sec_raw).replace(",", ";").split(";") if x.strip()]
        bad = [x for x in secondary if x not in queues]
        if bad:
            probs.append(f"unknown secondary queue(s) {', '.join(bad)}")
        secondary = [x for x in secondary if x in queues and x != primary]
        team = "" if _blank(r.get("team")) else str(r["team"]).strip()
        if not team:
            probs.append("team is empty")
        elif known_teams and team not in known_teams:
            probs.append(f"unknown team '{team}' (known: {', '.join(sorted(known_teams))})")
        night_raw = r.get("night_ok", "")
        if _blank(night_raw):
            night = True
        elif str(night_raw).strip().lower() in TRUE | FALSE:
            night = str(night_raw).strip().lower() in TRUE
        else:
            probs.append(f"night_ok '{night_raw}' is not yes/no")
            night = True
        ps_raw = r.get("pref_shift", "")
        pref_shift = "Day" if _blank(ps_raw) else str(ps_raw).strip().capitalize()
        if pref_shift not in shifts:
            probs.append(f"pref_shift '{ps_raw}' is not a shift ({', '.join(shifts)})")
        off_raw = r.get("pref_off", "")
        off = "sun" if _blank(off_raw) else str(off_raw).strip().lower()[:3]
        if off not in DAY_NAMES:
            probs.append(f"pref_off '{off_raw}' is not a weekday")
        results.append({"row": line, "agent_id": aid, "status": "rejected" if probs else "accepted",
                        "problems": "; ".join(probs + notes)})
        if not probs:
            rows.append({"agent_id": aid, "name": name, "primary": primary, "secondary": secondary,
                         "eligible": [primary] + secondary, "proficiency": 2, "night_ok": night, "pref_shift": pref_shift,
                         "pref_off": DAY_NAMES.index(off), "team": team})
    errors = []
    rejected = sum(x["status"] == "rejected" for x in results)
    if not rejected:
        if len(rows) < 10:
            errors.append("At least 10 agents are needed to build a roster.")
        for q in queues:
            if not any(q in x["eligible"] for x in rows):
                errors.append(f"Nobody is trained for {q}.")
    summary = {"accepted": len(rows), "rejected": rejected, "skipped": sum(x["status"] == "skipped" for x in results)}
    ok = not rejected and not errors
    return {"agents": pd.DataFrame(rows) if ok else None, "rows": results, "summary": summary, "errors": errors}


def parse_agents(df: pd.DataFrame, policy: dict, known_teams: set[str] | None = None) -> tuple[pd.DataFrame | None, list[str]]:
    """Validate an uploaded agent table (template headers or common variations). Returns (agents, errors)."""
    df = apply_mapping(df, suggest_mapping(df.columns, list(REQUIRED) + list(OPTIONAL)))
    res = validate_agents(df, policy, known_teams)
    errs = res["errors"] + [f"Row {x['row']}: {x['problems']}" for x in res["rows"] if x["status"] == "rejected"]
    return res["agents"], errs


def validate_leave(df: pd.DataFrame | None, agents: pd.DataFrame, policy: dict) -> dict:
    """Leave rows: malformed dates and unknown agents are rejected; dates outside the planning fortnight
    and duplicates are skipped (reported, not imported)."""
    empty = pd.DataFrame(columns=["agent_id", "day"])
    if df is None or df.empty:
        return {"leave": empty, "rows": [], "summary": {"accepted": 0, "rejected": 0, "skipped": 0}, "errors": []}
    if not {"agent_id", "date"} <= set(df.columns):
        return {"leave": None, "rows": [], "summary": {"accepted": 0, "rejected": 0, "skipped": 0},
                "errors": ["Leave file needs columns agent_id and date (map them below)."]}
    start = pd.Timestamp(policy["horizon"]["start_date"])
    ids, out, results, seen = set(agents.agent_id), [], [], set()
    for i, r in enumerate(df.to_dict("records")):
        line, aid = i + 2, str(r.get("agent_id", "")).strip()
        try:
            day = (pd.Timestamp(str(r["date"]).strip()) - start).days
        except (ValueError, TypeError):
            results.append({"row": line, "agent_id": aid, "status": "rejected",
                            "problems": f"date '{r.get('date')}' is not a valid date (use YYYY-MM-DD)"})
            continue
        if aid not in ids:
            results.append({"row": line, "agent_id": aid, "status": "rejected", "problems": f"unknown agent '{aid}'"})
        elif not 0 <= day < policy["horizon"]["days"]:
            results.append({"row": line, "agent_id": aid, "status": "skipped",
                            "problems": "date is outside the planning fortnight"})
        elif (aid, day) in seen:
            results.append({"row": line, "agent_id": aid, "status": "skipped", "problems": "duplicate leave day"})
        else:
            seen.add((aid, day))
            out.append({"agent_id": aid, "day": day})
            results.append({"row": line, "agent_id": aid, "status": "accepted", "problems": ""})
    rejected = sum(x["status"] == "rejected" for x in results)
    return {"leave": None if rejected else pd.DataFrame(out, columns=["agent_id", "day"]), "rows": results,
            "summary": {"accepted": len(out), "rejected": rejected,
                        "skipped": sum(x["status"] == "skipped" for x in results)}, "errors": []}


def parse_leave(df: pd.DataFrame | None, agents: pd.DataFrame, policy: dict) -> tuple[pd.DataFrame, list[str]]:
    if df is not None and not df.empty:
        df = apply_mapping(df, suggest_mapping(df.columns, list(LEAVE_COLUMNS)))
    res = validate_leave(df, agents, policy)
    errs = res["errors"] + [f"Leave row {x['row']}: {x['problems']}" for x in res["rows"] if x["status"] == "rejected"]
    leave = res["leave"] if res["leave"] is not None else pd.DataFrame(columns=["agent_id", "day"])
    return leave, errs


def build_data(agents: pd.DataFrame, leave: pd.DataFrame, base_data: dict, policy: dict) -> dict:
    return {**base_data, "agents": agents.reset_index(drop=True), "leave": leave,
            "skills": skill_matrix(agents, queue_names(policy))}
