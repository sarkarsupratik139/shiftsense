"""Schedule history: every change to the roster is saved with who, when, why and the
exact before -> after for each agent-day, plus a full snapshot of the roster after it."""
from __future__ import annotations

import io
import uuid

import pandas as pd

from . import store
from .config import day_label
from .roster import make_roster

KEY_COLS = ["shift", "queue", "ext_before", "ext_after", "alt_queue", "alt_start", "alt_end"]


def new_run_id() -> str:
    return uuid.uuid4().hex[:8]


def describe(policy: dict, r) -> str:
    if r is None:
        return "Off"
    from .workflow import shift_text
    txt = f"{shift_text(policy, r)} · {r['queue']}"
    if r.get("alt_queue"):
        txt += f" → {r['alt_queue']} part-time"
    return txt


def diff(policy: dict, before: pd.DataFrame, after: pd.DataFrame) -> list[dict]:
    """Per agent-day differences between two rosters."""
    def index(df):
        return {(r["agent_id"], int(r["day"])): r for r in df.to_dict("records")}
    a, b = index(before), index(after)
    out = []
    for key in sorted(set(a) | set(b), key=lambda k: (k[1], k[0])):
        x, y = a.get(key), b.get(key)
        same = x is not None and y is not None and all(x.get(c) == y.get(c) for c in KEY_COLS)
        if same or (x is None and y is None):
            continue
        out.append({"agent_id": key[0], "day": key[1], "date": day_label(policy, key[1]),
                    "before": describe(policy, x), "after": describe(policy, y)})
    return out


def _snapshot(roster: pd.DataFrame) -> str:
    return roster.to_csv(index=False)


def roster_from_snapshot(text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(text), keep_default_na=False)
    return make_roster(df.to_dict("records"))


def _role(actor: str) -> str:
    return actor.rsplit("(", 1)[-1].rstrip(")") if "(" in actor else ("System" if actor in ("system", "") else actor)


def status_of(rec) -> str:
    """Applied, or Reversed: a reversal is a new event; the original change stays in the history."""
    if rec is None:
        return "Applied"
    if rec.decision == "Revoke":
        return "Reversed · approval revoked"
    if rec.event in ("OfferDeclined", "AgentResponse") and rec.decision == "Reject":
        return "Reversed · agent declined"
    return "Applied"


def record(w, before: pd.DataFrame, audit_from: int, fallback_actor: str = "system") -> dict | None:
    """Save a history entry if the roster changed since `before`. The newest audit records
    written during the change give its event, actor and reason."""
    details = diff(w.policy, before, w.roster)
    if not details:
        return None
    new_audit = w.audit.records[audit_from:]
    main = next((r for r in reversed(new_audit) if r.decision in ("Accept", "Modify", "Reject", "Publish",
                                                                    "Auto-approve", "Revoke")), None) \
        or (new_audit[-1] if new_audit else None)
    entry = {
        "run_id": getattr(w, "run_id", "legacy"),
        "sim_time": main.sim_time if main else "",
        "actor": main.actor if main else fallback_actor,
        "event": main.event if main else "Change",
        "summary": (main.recommendation or main.state) if main else "",
        "reason": "; ".join(x for x in (main.reason, main.outcome) if x) if main else "",
        "details": details,
        "snapshot": _snapshot(w.roster),
        "role": _role(main.actor if main else fallback_actor),
        "status": status_of(main),
        "related": main.state if main else "",
    }
    entry["id"] = store.append_change(entry)
    return entry


def record_baseline(w, event: str, actor: str) -> dict:
    """A full-roster entry for a fresh start (published roster, demo story, import)."""
    from .workflow import now_label
    entry = {"run_id": getattr(w, "run_id", "legacy"), "sim_time": now_label(w), "actor": actor,
             "event": event, "summary": f"{len(w.roster)} shifts published", "reason": "",
             "details": [], "snapshot": _snapshot(w.roster), "role": _role(actor), "status": "Baseline",
             "related": "full roster"}
    entry["id"] = store.append_change(entry)
    return entry


def changes(run_id: str | None = None) -> list[dict]:
    rows = store.list_changes()
    return [r for r in rows if run_id is None or r["run_id"] == run_id]


def context_of(run_id: str | None) -> str:
    """Demo-scenario runs carry a 'sim-' prefix so their records are never mistaken for live ones."""
    return "Demo scenario (simulated)" if str(run_id or "").startswith("sim-") else "Live"


def export_rows(rows: list[dict]) -> pd.DataFrame:
    """The CSV export: one line per changed agent-day, built from exactly the rows shown on screen."""
    flat = [{"id": r["id"], "context": context_of(r["run_id"]), "run": r["run_id"], "saved_at": r["saved_at"], "time": r["sim_time"], "event": r["event"],
             "by": r["actor"], "role": r.get("role", ""), "status": r.get("status", "") or "Applied",
             "related": r.get("related", ""), "what": r["summary"], "why": r["reason"], "date": d["date"],
             "agent": d["agent_id"], "before": d["before"], "after": d["after"]}
            for r in rows for d in (r["details"] or [{"date": "", "agent_id": "", "before": "", "after": ""}])]
    return pd.DataFrame(flat)


def roster_at(change_id: int) -> pd.DataFrame | None:
    snap = store.change_snapshot(change_id)
    return roster_from_snapshot(snap) if snap else None
