"""Synthetic, seeded data: agents, skill matrix, leave and demand history. All simulated."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import intervals_per_day, queue_names, shift_names

FIRST_NAMES = [
    "Aarav", "Priya", "Rohan", "Ananya", "Vikram", "Sneha", "Arjun", "Kavya", "Rahul", "Meera",
    "Karan", "Divya", "Aditya", "Pooja", "Siddharth", "Neha", "Manish", "Riya", "Amit", "Isha",
    "Nikhil", "Shreya", "Varun", "Tanvi", "Harsh", "Nisha", "Gaurav", "Aditi", "Kunal", "Sakshi",
    "Deepak", "Anjali", "Rajesh", "Swati", "Vivek", "Pallavi", "Sanjay", "Kriti", "Abhishek", "Ritu",
    "Mohit", "Simran", "Tarun", "Jyoti", "Ashish", "Nandini", "Yash", "Payal", "Sameer", "Megha",
]
SURNAME_INITIALS = "KSMRPGVDBTNAJ"


def intraday_profile(policy: dict, queue: str) -> np.ndarray:
    """Relative volume per interval of a day: two business peaks, low nights."""
    per_day = intervals_per_day(policy)
    hours = (np.arange(per_day) + 0.5) * 24 / per_day
    q = policy["queues"][queue]
    shift = 0.5 if "Banking" in queue else 0.0          # banking peaks a little later
    prof = (
        0.12
        + 1.00 * np.exp(-0.5 * ((hours - (11.0 + shift)) / 2.0) ** 2)
        + 0.80 * np.exp(-0.5 * ((hours - (16.0 + shift)) / 2.2) ** 2)
        + 0.25 * np.exp(-0.5 * ((hours - 20.5) / 1.5) ** 2)
    )
    prof = prof / prof.max()
    open_mask = (hours >= q["open"]) & (hours < q["close"])
    return np.where(open_mask, prof, 0.0)


WEEKLY_FACTOR = np.array([1.15, 1.05, 1.00, 1.00, 0.95, 0.72, 0.58])   # Mon..Sun


def demand_history(policy: dict, rng: np.random.Generator) -> pd.DataFrame:
    """`history_weeks` of past 30-min volumes per queue with multiplicative noise."""
    per_day = intervals_per_day(policy)
    weeks = policy["staffing"]["history_weeks"]
    noise = policy["staffing"]["history_noise"]
    rows = []
    for queue, q in policy["queues"].items():
        prof = intraday_profile(policy, queue)
        for w in range(weeks):
            trend = 1.0 + 0.01 * (w - weeks + 1)                   # mild growth
            for dow in range(7):
                base = q["peak"] * prof * WEEKLY_FACTOR[dow] * trend
                calls = rng.poisson(np.maximum(base * rng.lognormal(0, noise, per_day), 0))
                for i in range(per_day):
                    rows.append((w, dow, i, queue, int(calls[i])))
    return pd.DataFrame(rows, columns=["week", "dow", "interval", "queue", "calls"])


def generate_agents(policy: dict, rng: np.random.Generator) -> pd.DataFrame:
    wf = policy["workforce"]
    queues = queue_names(policy)
    primaries = [q for q, n in wf["primary_mix"].items() for _ in range(n)]
    assert len(primaries) == wf["n_agents"], "primary_mix must sum to n_agents"
    rng.shuffle(primaries)
    n = wf["n_agents"]
    n_cross = int(round(wf["cross_skill_share"] * n))
    cross_idx = set(rng.choice(n, size=n_cross, replace=False).tolist())
    shifts = shift_names(policy)
    rows = []
    for i in range(n):
        primary = primaries[i]
        secondary: list[str] = []
        if i in cross_idx:
            # cross-skill: same process other language, or same language other process
            proc, lang = primary.split("-")
            options = [q for q in queues if q != primary and (q.startswith(proc) or q.endswith(lang))]
            k = 2 if rng.random() < 0.25 else 1
            secondary = sorted(rng.choice(options, size=min(k, len(options)), replace=False).tolist())
        night_ok = bool(rng.random() < wf["night_ok_share"])
        pref_options = shifts if night_ok else [s for s in shifts if not policy["shifts"][s].get("night")]
        rows.append({
            "agent_id": f"A{i + 1:02d}",
            "name": f"{FIRST_NAMES[i % len(FIRST_NAMES)]} {SURNAME_INITIALS[i % len(SURNAME_INITIALS)]}.",
            "primary": primary,
            "secondary": secondary,
            "eligible": [primary] + secondary,
            "proficiency": int(rng.integers(1, 4)),
            "night_ok": night_ok,
            "pref_shift": str(rng.choice(pref_options)),
            "pref_off": int(rng.integers(0, 7)),
            "team": f"TL-{(i % 5) + 1}",
        })
    return pd.DataFrame(rows)


def generate_leave(policy: dict, agents: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    days = policy["horizon"]["days"]
    n = len(agents)
    k = int(round(policy["workforce"]["leave_share"] * n))
    who = rng.choice(n, size=k, replace=False)
    rows = []
    for i in who:
        start = int(rng.integers(0, days - 2))
        length = int(rng.integers(1, 4))
        for d in range(start, min(days, start + length)):
            rows.append({"agent_id": agents.agent_id.iloc[i], "day": d})
    return pd.DataFrame(rows, columns=["agent_id", "day"])


def skill_matrix(agents: pd.DataFrame, queues: list[str]) -> pd.DataFrame:
    """2 = primary queue, 1 = eligible secondary, 0 = not eligible."""
    m = pd.DataFrame(0, index=agents.agent_id, columns=queues)
    for _, a in agents.iterrows():
        m.loc[a.agent_id, a.primary] = 2
        for q in a.secondary:
            m.loc[a.agent_id, q] = 1
    return m


def generate(policy: dict, seed: int | None = None) -> dict:
    """Build the whole synthetic dataset deterministically from the seed."""
    seed = policy["seed"] if seed is None else seed
    rng = np.random.default_rng(seed)
    agents = generate_agents(policy, rng)
    leave = generate_leave(policy, agents, rng)
    history = demand_history(policy, rng)
    return {
        "agents": agents,
        "leave": leave,
        "history": history,
        "skills": skill_matrix(agents, queue_names(policy)),
        "seed": seed,
    }
