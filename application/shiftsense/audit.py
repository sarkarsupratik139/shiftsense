"""Decision audit trail: event -> system state -> recommendation -> decision -> outcome."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "audit.db"
DECISIONS = ("Accept", "Modify", "Reject")


@dataclass
class AuditRecord:
    event: str                   # AgentAbsent, LeaveRequested, ...
    sim_time: str                # simulated clock when the event was detected
    state: str                   # system state summary at the time
    recommendation: str
    decision: str = ""           # Accept / Modify / Reject / Auto-approve / Escalate
    reason: str = ""
    outcome: str = ""
    actor: str = "system"
    metrics: dict = field(default_factory=dict)
    logged_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class AuditLog:
    def __init__(self):
        self.records: list[AuditRecord] = []

    def log(self, rec: AuditRecord) -> AuditRecord:
        if rec.decision in ("Modify", "Reject", "Escalate", "Return", "Revoke") and not rec.reason.strip():
            raise ValueError("A reason is mandatory when modifying or rejecting a recommendation.")
        self.records.append(rec)
        return rec

    def frame(self) -> pd.DataFrame:
        if not self.records:
            return pd.DataFrame(columns=list(AuditRecord.__dataclass_fields__))
        df = pd.DataFrame([asdict(r) for r in self.records])
        df["metrics"] = df.metrics.map(lambda m: json.dumps(m, default=str))
        return df

    def save_sqlite(self, path: Path = DB_PATH) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as con:
            self.frame().to_sql("audit", con, if_exists="replace", index=False)
        return path
