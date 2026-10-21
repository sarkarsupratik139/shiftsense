"""Coverage-aware leave requests: Green (auto-approve) / Amber (TL approves) / Red (offer
alternatives). Swap partners must be eligible for the queue they take over."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import day_label, interval_label, queue_names, shift_intervals, shift_names, warmup_mask, week_of
from .constraints import check
from .roster import coverage, make_roster


def _margin(policy, req, cov, qi, rng) -> int:
    """Worst (coverage - requirement) over the shift once the agent is removed."""
    mask = warmup_mask(policy)
    vals = [cov[qi, t] - 1 - req[qi, t] for t in rng if mask[t] and req[qi, t] > 0]
    return int(min(vals)) if vals else 99


def status_for(policy: dict, req: np.ndarray, roster: pd.DataFrame, agent_id: str, day: int) -> dict:
    row = roster[(roster.agent_id == agent_id) & (roster.day == day)]
    if row.empty:
        return {"status": "Green", "margin": 99, "detail": "Not scheduled that day — no coverage impact."}
    r = row.iloc[0]
    queues = queue_names(policy)
    qi = queues.index(r.queue)
    cov = coverage(policy, roster)
    rng = shift_intervals(policy, day, r["shift"])
    margin = _margin(policy, req, cov, qi, rng)
    green = policy["requests"]["green_buffer"]
    if margin >= green:
        s, d = "Green", f"{r.queue} stays at least {margin} above requirement all shift."
    elif margin >= 0:
        s, d = "Amber", f"{r.queue} stays covered but the buffer becomes thin (margin {margin})."
    else:
        worst = min((t for t in rng if req[qi, t] > 0), key=lambda t: cov[qi, t] - req[qi, t], default=rng.start)
        s, d = "Red", (f"Approval would leave {r.queue} {-margin} short around "
                       f"{interval_label(policy, worst)} ({r['shift']} shift).")
    return {"status": s, "margin": margin, "detail": d, "shift": r["shift"], "queue": r.queue}


def _ok(policy, data, roster, agent_ids) -> bool:
    sub = roster[roster.agent_id.isin(agent_ids)]
    return not check(policy, data["agents"], data["leave"], sub)


def alternatives(policy: dict, data: dict, req: np.ndarray, roster: pd.DataFrame,
                 agent_id: str, day: int, limit: int = 3, min_day: int = 0) -> list[dict]:
    """Options that keep coverage intact: another day, a swap with an eligible partner,
    or a shift adjustment on the same day."""
    out = []
    days = policy["horizon"]["days"]
    rows = roster.to_dict("records")
    me = roster[(roster.agent_id == agent_id) & (roster.day == day)]
    if me.empty:
        return out
    mine = me.iloc[0]
    agents = data["agents"].set_index("agent_id")
    on_leave = set(zip(data["leave"].agent_id, data["leave"].day))

    # 1) another day in the same or next week
    for d in range(max(min_day, day - 3), min(days, day + 5)):
        if d == day:
            continue
        s = status_for(policy, req, roster, agent_id, d)
        if s["status"] == "Green" and s.get("shift"):
            out.append({"type": "Other day", "option": f"Take {day_label(policy, d)} instead",
                        "status": "Green", "detail": s["detail"], "day": d})
        if sum(o["type"] == "Other day" for o in out) >= 2:
            break

    # 2) swap with an eligible colleague
    out += swap_options(policy, data, roster, agent_id, day, limit=2, min_day=min_day)

    # 3) shift adjustment: work a different shift that day where you are less needed
    for s in shift_names(policy):
        if s == mine["shift"]:
            continue
        new = [r for r in rows if not (r["agent_id"] == agent_id and r["day"] == day)]
        new.append({"agent_id": agent_id, "day": day, "shift": s, "queue": mine.queue, "kind": "adjust"})
        new_roster = make_roster(new)
        if not _ok(policy, data, new_roster, [agent_id]):
            continue
        cov_new = coverage(policy, new_roster)
        qi = queue_names(policy).index(mine.queue)
        old_rng = shift_intervals(policy, day, mine["shift"])
        if all(cov_new[qi, t] >= req[qi, t] for t in old_rng if warmup_mask(policy)[t]):
            out.append({"type": "Shift adjustment", "option": f"Keep the day but work {s} instead of {mine['shift']}",
                        "status": "Amber", "detail": "Frees your usual hours without creating a gap.",
                        "shift": s})
            break
    return out[:limit + 2]


def swap_options(policy: dict, data: dict, roster: pd.DataFrame, agent_id: str, day: int,
                 limit: int = 5, min_day: int = 0) -> list[dict]:
    """Colleagues who can take my shift on `day` (trained for my queue, off that day) while I
    take one of their shifts in the same week (I must be trained for it). Rules checked for both."""
    me = roster[(roster.agent_id == agent_id) & (roster.day == day)]
    if me.empty:
        return []
    mine = me.iloc[0]
    rows = roster.to_dict("records")
    agents = data["agents"].set_index("agent_id")
    on_leave = set(zip(data["leave"].agent_id, data["leave"].day))
    out = []
    for pid, p in agents.iterrows():
        if pid == agent_id or mine.queue not in p.eligible or (pid, day) in on_leave:
            continue
        if ((roster.agent_id == pid) & (roster.day == day)).any():
            continue
        theirs = roster[(roster.agent_id == pid) & (roster.day != day)
                        & (roster.day.map(week_of) == week_of(day))]
        for t in theirs.itertuples(index=False):
            if t.queue not in agents.loc[agent_id].eligible or t.day < min_day:
                continue
            if ((roster.agent_id == agent_id) & (roster.day == t.day)).any() or (agent_id, t.day) in on_leave:
                continue
            new = [r for r in rows if not ((r["agent_id"] == agent_id and r["day"] == day)
                                           or (r["agent_id"] == pid and r["day"] == t.day))]
            new += [{"agent_id": pid, "day": day, "shift": mine["shift"], "queue": mine.queue, "kind": "swap"},
                    {"agent_id": agent_id, "day": t.day, "shift": t.shift, "queue": t.queue, "kind": "swap"}]
            if _ok(policy, data, make_roster(new), [agent_id, pid]):
                out.append({"type": "Swap", "option": f"Swap with {p['name']} ({pid}): they cover your "
                            f"{mine['shift']} on {mine.queue}; you work their {t.shift} on "
                            f"{day_label(policy, t.day)} ({t.queue})",
                            "status": "Green", "partner": pid, "partner_day": int(t.day),
                            "detail": f"{pid} is trained for {mine.queue}; both keep their weekly hours."})
                break
        if len(out) >= limit:
            break
    return out


def apply_leave(roster: pd.DataFrame, agent_id: str, day: int) -> pd.DataFrame:
    return roster[~((roster.agent_id == agent_id) & (roster.day == day))].reset_index(drop=True)


def apply_alternative(roster: pd.DataFrame, agent_id: str, day: int, alt: dict | None) -> pd.DataFrame:
    """Apply an approved request: plain leave (alt None), another day, a swap or a shift change."""
    if alt is None:
        return apply_leave(roster, agent_id, day)
    if alt["type"] == "Other day":
        return apply_leave(roster, agent_id, alt["day"])
    rows = roster.to_dict("records")
    if alt["type"] == "Swap":
        pid, pday = alt["partner"], alt["partner_day"]
        mine = next(r for r in rows if r["agent_id"] == agent_id and r["day"] == day)
        theirs = next(r for r in rows if r["agent_id"] == pid and r["day"] == pday)
        rows = [r for r in rows if r is not mine and r is not theirs]
        rows += [{**mine, "agent_id": pid, "kind": "swap"}, {**theirs, "agent_id": agent_id, "kind": "swap"}]
        return make_roster(rows)
    if alt["type"] == "Shift adjustment":
        for r in rows:
            if r["agent_id"] == agent_id and r["day"] == day:
                r.update(shift=alt["shift"], kind="adjust", ext_before=0.0, ext_after=0.0, alt_queue="")
        return make_roster(rows)
    raise ValueError(f"unknown alternative {alt['type']}")
