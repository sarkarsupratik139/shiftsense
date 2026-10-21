# Monitoring and health checks

```bash
scripts/health.sh            # human-readable; exit code 1 if an essential check fails
scripts/health.sh --json     # for monitoring tools
scripts/health.sh --alert    # emails SHIFTSENSE_ALERT_EMAIL on failure, at most once a day per failure
```

| Check | Fails when |
|---|---|
| Database | file/connection missing; SQLite `integrity_check` not ok |
| Policy | `config/policy.yaml` does not load |
| Roles | not exactly the six roles (admin, agent, team_lead, ops_head, analyst, hr) |
| Accounts | no active admin; unknown roles |
| Live workspace | does not load, or has any hard-rule violation |
| Audit trail | hash chain broken |
| Optimiser | OR-Tools cannot be imported |
| Backups (pilot mode) | newest backup older than 26 h |
| Sign-in (pilot mode) | password-only sign-in (`SHIFTSENSE_AUTH=password`); weak password hashing; a demo password still in use |
| Demo scenario (pilot mode) | a demo scenario is still loaded |
| Email / AI | reported as configured or not (never the secret itself) |
| Disk | less than 0.5 GB free |

**Docker stack.** The `jobs` container runs the health check every 15 minutes with `--alert`.

**Logs.** `data/shiftsense.log` (rotating, `SHIFTSENSE_LOG`): failed actions with traceback (rolled back) and
permission refusals (`permission denied user=… role=… needs=…`). It never contains passwords, tokens or message text.
Docker: `docker compose logs app`.

**What to watch in the app.** Admin → System status; Ops → Overview (open alerts, escalations, carried risk).
