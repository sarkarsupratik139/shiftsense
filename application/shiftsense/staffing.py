"""Forecast band + staffing chain: volume x AHT -> Erlang C -> productive heads -> shrinkage -> scheduled band."""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import pandas as pd

from .config import intervals_per_day, n_intervals, queue_names


# ---------------------------------------------------------------- Erlang C
def erlang_b(n: int, a: float) -> float:
    """Erlang B blocking probability, stable recursive form."""
    b = 1.0
    for k in range(1, n + 1):
        b = a * b / (k + a * b)
    return b


def erlang_c(n: int, a: float) -> float:
    """Probability an arriving contact waits (n agents, a Erlangs of traffic)."""
    if n <= a:
        return 1.0
    b = erlang_b(n, a)
    return n * b / (n - a * (1 - b))


def service_level(n: int, a: float, aht_sec: float, target_sec: float) -> float:
    """Share of contacts answered within target_sec."""
    if a <= 0:
        return 1.0
    if n <= a:
        return 0.0
    return 1.0 - erlang_c(n, a) * math.exp(-(n - a) * target_sec / aht_sec)


@lru_cache(maxsize=200_000)
def agents_required(calls: float, aht_sec: float, interval_sec: int, sla_pct: float,
                    sla_sec: float, max_occupancy: float = 1.0) -> int:
    """Minimum productive agents to meet the SLA (and occupancy cap) for one interval."""
    if calls <= 0:
        return 0
    a = calls * aht_sec / interval_sec
    n = max(1, math.ceil(a / max_occupancy))
    if n <= a:
        n = math.floor(a) + 1
    while service_level(n, a, aht_sec, sla_sec) < sla_pct:
        n += 1
    return n


def scheduled_heads(productive: int, shrinkage: float) -> int:
    return 0 if productive == 0 else math.ceil(productive / (1 - shrinkage) - 1e-9)


# ---------------------------------------------------------------- forecast
def forecast(policy: dict, history: pd.DataFrame) -> dict[str, np.ndarray]:
    """Transparent seasonal baseline: mean of the same weekday x interval over the
    last 4 history weeks, scaled by recent trend; band = +/- z * residual std."""
    queues = queue_names(policy)
    per_day = intervals_per_day(policy)
    days = policy["horizon"]["days"]
    z = policy["staffing"]["forecast_band_z"]
    weeks = history.week.max() + 1
    recent = history[history.week >= weeks - 4]
    mid = np.zeros((len(queues), days * per_day))
    sd = np.zeros_like(mid)
    for qi, q in enumerate(queues):
        h = recent[recent.queue == q]
        piv = h.pivot_table(index=["dow", "interval"], columns="week", values="calls")
        mean = piv.mean(axis=1).to_numpy().reshape(7, per_day)
        std = piv.std(axis=1, ddof=1).to_numpy().reshape(7, per_day)
        by_week = h.groupby("week").calls.sum()
        trend = float(by_week.iloc[-2:].mean() / by_week.iloc[:2].mean()) ** 0.5   # damped
        for d in range(days):
            mid[qi, d * per_day:(d + 1) * per_day] = mean[d % 7] * trend
            sd[qi, d * per_day:(d + 1) * per_day] = std[d % 7]
    low = np.maximum(mid - z * sd, 0)
    high = mid + z * sd
    return {"mid": mid, "low": low, "high": high}


# ---------------------------------------------------------------- staffing chain
def staffing_chain(policy: dict, fc: dict[str, np.ndarray]) -> dict:
    """Required productive and scheduled heads per queue x interval, for the band."""
    queues = queue_names(policy)
    interval_sec = policy["horizon"]["interval_minutes"] * 60
    shrink = policy["staffing"]["shrinkage"]
    occ = policy["staffing"]["max_occupancy"]
    out = {}
    for key in ("low", "mid", "high"):
        prod = np.zeros(fc[key].shape, dtype=int)
        for qi, q in enumerate(queues):
            p = policy["queues"][q]
            for t, calls in enumerate(fc[key][qi]):
                prod[qi, t] = agents_required(round(float(calls), 1), p["aht_sec"], interval_sec,
                                              p["sla_pct"], p["sla_sec"], occ)
        sched = np.vectorize(lambda x: scheduled_heads(int(x), shrink))(prod) if prod.size else prod
        out[f"prod_{key}"] = prod
        out[f"req_{key}"] = sched.astype(int)
    out["req"] = out["req_mid"]                      # planning target
    mid = np.maximum(out["req_mid"], 1)
    out["uncertainty"] = np.where(out["req_mid"] > 0, (out["req_high"] - out["req_low"]) / mid, 0.0)
    out["calls"] = fc["mid"]
    out["calls_low"], out["calls_high"] = fc["low"], fc["high"]
    out["queues"] = queues
    return out


def staffing_frame(policy: dict, st: dict) -> pd.DataFrame:
    """Tidy frame of the staffing chain for the UI."""
    per_day = intervals_per_day(policy)
    rows = []
    for qi, q in enumerate(st["queues"]):
        for t in range(n_intervals(policy)):
            rows.append({
                "queue": q, "t": t, "day": t // per_day, "interval": t % per_day,
                "calls": st["calls"][qi, t], "calls_low": st["calls_low"][qi, t],
                "calls_high": st["calls_high"][qi, t],
                "productive": st["prod_mid"][qi, t], "req_low": st["req_low"][qi, t],
                "req": st["req_mid"][qi, t], "req_high": st["req_high"][qi, t],
            })
    return pd.DataFrame(rows)
