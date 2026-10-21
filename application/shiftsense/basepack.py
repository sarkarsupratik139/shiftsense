"""The base input data ShiftSense needs to run, as ordinary files people could have written themselves.

    python -m shiftsense.basepack build [small|full]   -> writes basedata/<size>/*.csv (deterministic, seed 42)

Two sizes: small (default; 50 employees, 5 teams, 4 queues for 2 clients, like the original demo data) and full
(120 employees, 8 teams, 6 queues for 3 clients).

Files (one per kind of data): employees.csv, contracts.csv, service_levels.csv, rules.csv, leave.csv,
call_volumes.csv, situations.csv. They are loaded through the same smart converter and change engine as any
upload, so the base pack is also a working example of the accepted formats. The Analyst can replace any file,
or remove records, afterwards; every change is versioned.

All people, clients and figures are fictional and simulated.
"""
from __future__ import annotations

import copy
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from .config import load_policy

ROOT = Path(__file__).resolve().parent.parent / "basedata"
DEFAULT_SIZE = "small"


def folder(size: str = DEFAULT_SIZE) -> Path:
    return ROOT / size


DIR = folder()
FILES = {"contracts": "contracts.csv", "service_levels": "service_levels.csv", "rules": "rules.csv",
         "employees": "employees.csv", "leave": "leave.csv", "volumes": "call_volumes.csv",
         "situations": "situations.csv"}
ORDER = ["contracts", "service_levels", "rules", "employees", "leave", "volumes", "situations"]
HISTORY_WEEKS = 8
SIZES = {"small": {"agents": 50, "teams": 5, "queues": ["Claims-EN", "Claims-HI", "Banking-EN", "Banking-HI"],
                   "peak_scale": 0.55},
         "full": {"agents": 120, "teams": 8, "queues": None, "peak_scale": 1.0}}

QUEUES = {   # queue: (client, aht s, sla %, sla s, open, close, priority, peak calls / 30 min)
    "Claims-EN": ("InsureCo (fictional)", 360, 80, 20, 0, 24, "High", 23),
    "Claims-HI": ("InsureCo (fictional)", 420, 80, 30, 9, 21, "Medium", 8),
    "Banking-EN": ("PaySecure Bank (fictional)", 300, 80, 20, 0, 24, "High", 20),
    "Banking-HI": ("PaySecure Bank (fictional)", 330, 80, 30, 9, 21, "Medium", 7),
    "Telecom-EN": ("TelcoOne (fictional)", 280, 80, 20, 7, 23, "Medium", 12),
    "Telecom-HI": ("TelcoOne (fictional)", 320, 80, 30, 8, 22, "Low", 6),
}
MIX = {"Claims-EN": 0.25, "Claims-HI": 0.12, "Banking-EN": 0.22, "Banking-HI": 0.11, "Telecom-EN": 0.2,
       "Telecom-HI": 0.1}
CONTRACTS = [
    {"Contract name": "Full-time 40h", "Contracted paid hours per week": 40, "Contracted shifts per week": 5,
     "Maximum paid hours per week": 50, "Maximum shifts per week": 6, "Night shifts allowed": "Yes",
     "Maximum night shifts per week": 4, "Allowed shift types": "", "Minimum rest (h)": 11,
     "Overtime days per fortnight": 1, "Maximum extension (h)": 2, "Hourly rate (Rs)": 250, "OT multiplier": 2},
    {"Contract name": "Part-time 24h", "Contracted paid hours per week": 24, "Contracted shifts per week": 3,
     "Maximum paid hours per week": 32, "Maximum shifts per week": 4, "Night shifts allowed": "No",
     "Maximum night shifts per week": 0, "Allowed shift types": "Morning; Day; Evening", "Minimum rest (h)": 11,
     "Overtime days per fortnight": 1, "Maximum extension (h)": 1, "Hourly rate (Rs)": 260, "OT multiplier": 2},
    {"Contract name": "Evening-Night 40h", "Contracted paid hours per week": 40, "Contracted shifts per week": 5,
     "Maximum paid hours per week": 48, "Maximum shifts per week": 6, "Night shifts allowed": "Yes",
     "Maximum night shifts per week": 4, "Allowed shift types": "Evening; Night", "Minimum rest (h)": 12,
     "Overtime days per fortnight": 1, "Maximum extension (h)": 2, "Hourly rate (Rs)": 275, "OT multiplier": 2},
]
DAY = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _queues(size: str) -> dict:
    keep = SIZES[size]["queues"]
    return {q: v for q, v in QUEUES.items() if keep is None or q in keep}


def _policy(size: str):
    cfg = SIZES[size]
    n = cfg["agents"]
    qs = _queues(size)
    p = copy.deepcopy(load_policy())
    p["queues"] = {q: {"aht_sec": v[1], "sla_pct": v[2] / 100, "sla_sec": v[3], "open": v[4], "close": v[5],
                       "sensitivity": 1.0, "peak": max(1, round(v[7] * cfg["peak_scale"]))} for q, v in qs.items()}
    share = {q: MIX[q] for q in qs}
    tot = sum(share.values())
    mix = {q: int(round(f / tot * n)) for q, f in share.items()}
    mix["Claims-EN"] += n - sum(mix.values())
    p["workforce"] = {**p["workforce"], "n_agents": n, "primary_mix": mix}
    p["staffing"] = {**p["staffing"], "history_weeks": HISTORY_WEEKS}
    return p


def build(size: str = DEFAULT_SIZE, out: Path | None = None) -> dict[str, int]:
    from .data_gen import generate
    out = out or folder(size)
    n_teams = SIZES[size]["teams"]
    p = _policy(size)
    d = generate(p)
    rng = np.random.default_rng(p["seed"] + 1)
    start = date.fromisoformat(p["horizon"]["start_date"])
    out.mkdir(parents=True, exist_ok=True)
    n = {}

    # contracts
    pd.DataFrame(CONTRACTS).to_csv(out / FILES["contracts"], index=False)
    # service levels, written the way people write them
    rows = []
    for q, (client, aht, pct, sec, o, c, pri, _) in _queues(size).items():
        rows.append({"Client": client, "Queue": q, "Service level": f"{pct}% in {sec}s",
                     "Opening hours": "24x7" if (o, c) == (0, 24) else f"{o:02d}:00-{c:02d}:00",
                     "AHT": f"{aht // 60} min {aht % 60} s" if aht % 60 else f"{aht // 60} min", "Priority": pri})
    pd.DataFrame(rows).to_csv(out / FILES["service_levels"], index=False)
    # rules (current company-wide floor and settings)
    from .schemas import RULE_LABELS
    rr = []
    for k, (label, unit) in RULE_LABELS.items():
        sect, _, name = k.rpartition(".")
        rr.append({"Rule": k, "Description": label, "Value": (p[sect] if sect else p["rules"])[name], "Unit": unit})
    pd.DataFrame(rr).to_csv(out / FILES["rules"], index=False)
    # employees
    ag = d["agents"].copy()
    ag["team"] = [f"TL-{(i % n_teams) + 1}" for i in range(len(ag))]
    ag.loc[ag.agent_id == "A06", "team"] = "TL-1"          # keeps the demo agent accounts' teams (A01/A06 TL-1, A02 TL-2)
    contract = []
    for i, a in enumerate(ag.itertuples()):
        r = rng.random()
        contract.append("Part-time 24h" if r < 0.10 else "Evening-Night 40h" if r < 0.15 and a.night_ok
                        else "Full-time 40h")
    ag["contract"] = contract
    for a in ("A01", "A02", "A06"):
        ag.loc[ag.agent_id == a, "contract"] = "Full-time 40h"
    emp = []
    for a in ag.itertuples():
        joining = start - timedelta(days=int(rng.integers(60, 1500)))
        emp.append({"Employee ID": a.agent_id, "Full name": a.name, "Team": a.team, "Primary queue": a.primary,
                    "Other trained queues": "; ".join(a.secondary), "Contract type": a.contract,
                    "Night shifts allowed": "Yes" if a.night_ok else "No",
                    "Preferred shift": a.pref_shift if not (a.contract == "Part-time 24h" and a.pref_shift == "Night")
                    else "Day", "Preferred day off": DAY[a.pref_off],
                    "Start date": joining.strftime("%d/%m/%Y"), "Last working day": "", "Status": "Active"})
    # one joiner and one leaver inside the horizon, to show dated changes
    emp[-1].update({"Start date": (start + timedelta(days=7)).strftime("%d/%m/%Y")})
    emp[-2].update({"Last working day": (start + timedelta(days=10)).strftime("%d/%m/%Y"), "Status": "On notice"})
    pd.DataFrame(emp).to_csv(out / FILES["employees"], index=False)
    # leave (date ranges)
    lv = d["leave"].sort_values(["agent_id", "day"])
    lrows = []
    for aid, g in lv.groupby("agent_id"):
        days = list(g.day)
        s0 = prev = days[0]
        for x in days[1:] + [None]:
            if x is not None and x == prev + 1:
                prev = x
                continue
            lrows.append({"Employee ID": aid, "From": (start + timedelta(days=s0)).strftime("%d/%m/%Y"),
                          "To": (start + timedelta(days=prev)).strftime("%d/%m/%Y"),
                          "Type": "Annual", "Status": "Approved"})
            if x is not None:
                s0 = prev = x
    pd.DataFrame(lrows).to_csv(out / FILES["leave"], index=False)
    # call volumes: 8 weeks before the plan starts, per half hour
    hist = d["history"]
    first = start - timedelta(days=7 * HISTORY_WEEKS)
    hist = hist.assign(date=[(first + timedelta(days=int(w) * 7 + int(dw))).isoformat()
                             for w, dw in zip(hist.week, hist.dow)],
                       time=[f"{i // 2:02d}:{30 * (i % 2):02d}" for i in hist.interval])
    hist[["date", "time", "queue", "calls"]].rename(columns={"date": "Date", "time": "Interval start",
                                                            "queue": "Queue", "calls": "Calls offered"}) \
        .to_csv(out / FILES["volumes"], index=False)
    # situations
    sat = start + timedelta(days=5)
    thu = start + timedelta(days=10)
    camp_q = "Telecom-EN; Telecom-HI" if "Telecom-EN" in p["queues"] else "Claims-EN"
    camp_t = "TelcoOne festive offer" if "Telecom-EN" in p["queues"] else "InsureCo renewal campaign"
    pd.DataFrame([
        {"Reference": "SIT-1", "Situation": "planned_outage", "Title": "PaySecure core banking maintenance",
         "Starts": f"{sat.isoformat()} 01:00", "Ends": f"{sat.isoformat()} 05:00", "Queues affected": "Banking-EN; Banking-HI",
         "Capacity lost %": 50, "Volume change %": "", "Teams affected": "", "Description": "Vendor maintenance window"},
        {"Reference": "SIT-2", "Situation": "campaign", "Title": camp_t,
         "Starts": f"{thu.isoformat()} 09:00", "Ends": f"{(thu + timedelta(days=1)).isoformat()} 21:00",
         "Queues affected": camp_q, "Capacity lost %": "", "Volume change %": 15, "Teams affected": "",
         "Description": "Marketing campaign; expect more calls"},
    ]).to_csv(out / FILES["situations"], index=False)
    for k, f in FILES.items():
        n[k] = len(pd.read_csv(out / f))
    return n


if __name__ == "__main__":
    if sys.argv[1:2] == ["build"]:
        for sz in (sys.argv[2:] or list(SIZES)):
            print(sz, build(sz))
    else:
        print(__doc__)
