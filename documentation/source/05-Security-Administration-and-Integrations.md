# ShiftSense — Security, Administration and Integrations

*Version 1.0 · October 2026.*

## 1. Security model

| Control | Implementation |
|---|---|
| Authentication | Password (PBKDF2-SHA256, 600,000 iterations, random salt) and/or OIDC single sign-on; break-glass account in SSO mode |
| Password policy | ≥ 12 characters, 3 of 4 kinds, not name/username, not a common password; forced change of temporary passwords |
| Brute force | 5 failed attempts → 15-minute lock; every attempt audited (never the password) |
| Sessions | Idle sign-out after 30 minutes; the stored account is re-read on every action |
| Authorisation | Server-side in `services.py` for every action: role permission, own team, own record, no self-approval |
| Separation of duties | Second HR user confirms separations and verifies balances; Ops approves contracts; Admin has no operational decisions |
| Restricted data | Sick-leave details, balances, absence flags and restricted cases visible only to HR (cases: caseworker and Senior HR) |
| Audit | Append-only `audit_log` (database trigger refuses UPDATE/DELETE), SHA-256 hash chain verified by the health check |
| Secrets | From the environment or `.streamlit/secrets.toml` only; never stored, logged, exported or shown |
| Transport | HTTPS through Caddy (automatic certificate) in the Docker stack; PostgreSQL has no public port |
| Uploads | Photos: type, size, dimensions, decodable; re-encoded and metadata stripped. Imports: validated, path traversal refused |
| Privacy | Emails masked in tables, logs and exports; verification codes stored only as hashes (30-minute expiry) |
| Demo isolation | Demo scenario cannot change live data (separate slot and run id) |

**Known gaps:** no external penetration test; SSO not yet run against a real identity provider; multi-server
deployment untested; retention periods not set (HR-08).

## 2. Roles and permissions

The complete matrix is in `supporting/governance/role-permission-matrix.xlsx`: role permissions, every server action ×
role (asserted by `tests/test_matrix.py`), screens by role and data visibility.

Role permissions as configured in `config/users.yaml` (Team Lead permissions apply to their own team only; Senior HR is an extra permission on an HR account):

| Permission | Admin | Analyst | Team Lead | Ops | HR | Agent |
|---|---|---|---|---|---|---|
| `all_teams` | ✓ | ✓ |  | ✓ | ✓ |  |
| `approve_data` |  |  |  | ✓ |  |  |
| `approve_requests` |  |  | ✓ | ✓ |  |  |
| `decide_fix` |  |  | ✓ |  |  |  |
| `escalate_risk` |  | ✓ |  |  |  |  |
| `governance` | ✓ |  |  |  |  |  |
| `import` |  | ✓ |  |  |  |  |
| `live_clock` |  | ✓ |  |  |  |  |
| `manage_accounts` | ✓ |  |  |  |  |  |
| `manage_data` |  | ✓ |  |  |  |  |
| `manage_demo` |  | ✓ |  |  |  |  |
| `manage_people` |  |  |  |  | ✓ |  |
| `manage_service_levels` |  |  |  | ✓ |  |  |
| `message` |  |  | ✓ | ✓ |  | ✓ |
| `ops_decide` |  |  |  | ✓ |  |  |
| `own_preferences` |  |  |  |  |  | ✓ |
| `propose_data` |  |  | ✓ |  |  |  |
| `propose_governance_rules` | ✓ |  |  |  |  |  |
| `replan` |  | ✓ |  |  |  |  |
| `report_absence` |  |  | ✓ |  |  |  |
| `report_self` |  |  |  |  |  | ✓ |
| `report_situation` |  | ✓ | ✓ | ✓ |  |  |
| `report_spike` |  |  |  | ✓ |  |  |
| `request_leave` |  |  |  |  |  | ✓ |
| `run_clock` |  | ✓ |  |  |  |  |
| `send_reminder` |  |  | ✓ |  |  |  |
| `view_audit` | ✓ |  |  | ✓ |  |  |

{{include: ../supporting/workflows/authentication-and-account-lifecycle.md}}
{{include: ../supporting/workflows/administration-and-audit.md}}

## 3. Integrations

{{include: ../supporting/data/API-and-integration-catalogue.md#integrations}}

**Single sign-on setup.** Register ShiftSense as an OIDC web application with the identity provider (redirect URI
`https://<domain>/oauth2callback`). Put the client ID, secret and server metadata URL in the `[auth]` block of
`.streamlit/secrets.toml` (see `secrets.toml.example`), set `SHIFTSENSE_AUTH=sso` (or `both`), and create a
ShiftSense account for each person with the same email. The role always comes from ShiftSense.

**Email setup.** Set `SHIFTSENSE_EMAIL=smtp`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_TLS` and
`SHIFTSENSE_EMAIL_FROM`. Admin → Accounts & access → *Email relay check* sends one test message; "accepted by relay"
is not proof of delivery, so check the inbox.

**AI.** Optional. `ANTHROPIC_API_KEY` enables it; `SHIFTSENSE_AI=off` disables it; `SHIFTSENSE_AI_CONVERT` controls
import help (`nonpersonal` by default). Personal data is not sent by default.

## 4. Data handling

Register of every table, its personal data and current handling: `supporting/governance/retention-and-data-handling-register.xlsx`.
Backups contain accounts and contact details and must be stored as confidential.
