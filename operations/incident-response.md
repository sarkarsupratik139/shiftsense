# Incident response

Severity: **P1** app down or data integrity at risk · **P2** a workflow blocked for a role · **P3** cosmetic / single user.

| Step | Action |
|---|---|
| 1. Detect | Health alert email, `scripts/health.sh`, user report, `data/shiftsense.log` |
| 2. Contain | P1: stop the app (`docker compose stop app`) to prevent further writes. Security: deactivate affected accounts (Admin) and rotate secrets in `.env` / `secrets.toml` |
| 3. Preserve | Take a backup **before** changing anything (`scripts/backup.sh create`); copy the log |
| 4. Diagnose | Health output; log tracebacks; audit trail (Admin → Audit) for who did what |
| 5. Recover | Fix configuration or restore the last verified backup (see `backup-and-restore.md`); never edit audit rows |
| 6. Verify | `scripts/health.sh` all essential checks pass; spot-check the published roster and open alerts |
| 7. Communicate | Tell affected roles; staffing decisions made outside the app during downtime must be entered afterwards |
| 8. Review | Record cause, timeline and fix; add a test for the failure |

**Specific cases**
- **Audit chain broken:** treat as a security incident. Do not "repair" rows; restore and investigate access.
- **Suspected account compromise:** deactivate, reset password (Admin, reason), review the account's audit rows.
- **Wrong roster published:** Planning → restore an earlier version → validate → publish (rollback is a normal,
  audited plan action).
- **Wrong data import:** apply a corrected import (new version); reference data is versioned, history is kept.
- **Personal data exposure:** stop sharing, record what and to whom, notify the data owner (EXL HR/legal).
