# Workflow: authentication and account lifecycle

**Owner:** Admin. Implementation: `shiftsense/security.py`, `shiftsense/auth.py`, `shiftsense/accounts.py`, `views/login.py`.

## Sign-in modes (`SHIFTSENSE_AUTH`)
- `password` (default): username and password.
- `sso`: OIDC single sign-on (Microsoft Entra, Okta, Google) via Streamlit `st.login`. A person gets in only when one
  **active** ShiftSense account has the same email; the role is always ShiftSense's, never the identity provider's.
- `both`: either. `SHIFTSENSE_BREAKGLASS` keeps one emergency password account when SSO is the only mode.

## Password rules
- Stored as PBKDF2-SHA256, 600,000 iterations, random salt per person; older hashes upgrade at the next sign-in.
- At least 12 characters, 3 of 4 character kinds, not the name or username, not a common password.
- 5 wrong attempts lock the account for 15 minutes. Idle sign-out after 30 minutes. All configurable.
- Every sign-in attempt is audited; the password never is.

## Lifecycle
| State / event | Who | Effect |
|---|---|---|
| Create | Admin | Temporary password; must be changed at first sign-in |
| Change own password | Anyone (sidebar → Password) | Policy enforced |
| Reset / unlock | Admin (reason required) | New temporary password / lock cleared; audited |
| Role or team change | Admin | The next action re-reads the stored account, so old permissions stop at once |
| Senior HR permission | Admin grants to an HR account (reason) | `accounts.extra_permissions`; counts only for the HR role |
| Deactivate | Admin, or automatically at 00:00 Asia/Kolkata after a confirmed separation's last working day | Next action refused; verification codes revoked |
| Reactivate | Admin, or rescinding the separation that deactivated it | |

Demo accounts and their published passwords exist only for the demonstration. **Change or remove them before a
pilot**; the health check fails in pilot mode while a demo password is still in use.
