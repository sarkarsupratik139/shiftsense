# Workflow: administration and audit

**Owner:** Admin (oversight only: no operational decisions). Implementation: `comms.audit`, `timeline.py`, `views/admin.py`.

- **Accounts:** create, reset, unlock, deactivate, role/team change, Senior HR grant; all with reasons, all audited.
- **Governance:** scans for discrepancy signals (thresholds in `config/policy.yaml` → `governance`: approval-rate gaps between agents of one team lead, approvals that left the team short, repeated overtime, repeated swap pairs, repeated overrides); signals for review, not proof;, cases with states Flagged → Under
  Review → Explanation Requested / Referred to Operations → Action Required → Resolved → Closed (or Dismissed).
  Every scan is audited, including empty ones.
- **Audit trail:** one row per action in `audit_log` with actor, role, action, target, details. Rows are SHA-256
  hash-chained and append-only (a database trigger refuses UPDATE/DELETE). `python -m shiftsense.health` verifies the
  chain. Audit rows are never deleted.
- **History:** `services.history` / `history_export` gives the shared event → state → recommendation → decision →
  outcome timeline, role-scoped.
- **Decision log:** every team-lead and Ops decision with its mandatory reason.
- **System status:** health, backups, email relay check (Admin → People & accounts).
