"""Roster representation and coverage computation.

A roster is a DataFrame with one row per worked shift:
    agent_id, day, shift, queue, ext_before, ext_after, alt_queue, alt_start, alt_end, kind
`alt_queue` over [alt_start, alt_end) (global interval indices) is a time-bounded
cross-skill redeployment. `kind` is "plan" or the recovery action that changed the row.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import n_intervals, queue_names, shift_intervals

COLUMNS = ["agent_id", "day", "shift", "queue", "ext_before", "ext_after",
           "alt_queue", "alt_start", "alt_end", "kind"]
DTYPES = {"day": int, "ext_before": float, "ext_after": float, "alt_start": int, "alt_end": int}


def empty_roster() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS).astype(DTYPES)


def make_roster(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return empty_roster()
    for c, default in (("ext_before", 0.0), ("ext_after", 0.0), ("alt_queue", ""),
                       ("alt_start", 0), ("alt_end", 0), ("kind", "plan")):
        if c not in df:
            df[c] = default
        df[c] = df[c].fillna(default)
    df = df[COLUMNS].astype(DTYPES)
    return df.sort_values(["day", "agent_id"]).reset_index(drop=True)


def coverage(policy: dict, roster: pd.DataFrame) -> np.ndarray:
    """Heads on each queue in each interval, shape (Q, T)."""
    queues = queue_names(policy)
    cov = np.zeros((len(queues), n_intervals(policy)), dtype=int)
    qidx = {q: i for i, q in enumerate(queues)}
    for r in roster.itertuples(index=False):
        rng = shift_intervals(policy, r.day, r.shift, r.ext_before, r.ext_after)
        cov[qidx[r.queue], rng.start:rng.stop] += 1
        if r.alt_queue:
            lo, hi = max(r.alt_start, rng.start), min(r.alt_end, rng.stop)
            cov[qidx[r.queue], lo:hi] -= 1
            cov[qidx[r.alt_queue], lo:hi] += 1
    return cov


def assignment_grid(roster: pd.DataFrame, agents: pd.DataFrame, days: int) -> pd.DataFrame:
    """Agent x day grid of 'Shift·Queue' labels for display ('' = off)."""
    grid = pd.DataFrame("", index=agents.agent_id, columns=range(days))
    for r in roster.itertuples(index=False):
        label = f"{r.shift[:1]}·{r.queue}"
        if r.alt_queue:
            label += f"→{r.alt_queue}"
        if r.ext_before or r.ext_after:
            label += f" +{r.ext_before + r.ext_after:g}h"
        grid.loc[r.agent_id, r.day] = label
    return grid


def diff_count(a: pd.DataFrame, b: pd.DataFrame) -> int:
    """Number of agent-days whose assignment (shift, queue, working/off) differs."""
    ka = {(r.agent_id, r.day): (r.shift, r.queue) for r in a.itertuples(index=False)}
    kb = {(r.agent_id, r.day): (r.shift, r.queue) for r in b.itertuples(index=False)}
    keys = set(ka) | set(kb)
    return sum(ka.get(k) != kb.get(k) for k in keys)
