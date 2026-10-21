# Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `ModuleNotFoundError: ortools` (or other module) | dependencies not installed | `.venv/bin/pip install -r requirements.txt` |
| "Can't pickle … World is not the same object" | code changed while the app was running | restart the app (the failed action changed nothing) |
| Code change has no effect | `fileWatcherType = "none"` | restart the app |
| "Port 8501 is not available" | another instance is running | stop it, or use `--server.port` |
| Health: audit chain broken | an audit row was edited outside the app | restore the last verified backup; investigate file/database access |
| Planner: "No roster can meet every hard rule…" | infeasible inputs (frozen days, leave, limits) | read the listed blocking rules; change inputs, never the hard rules |
| Planner: "Conflict: the published roster changed…" | live roster changed after the draft | create a new draft |
| "Someone else changed this workspace…" | another save happened first | the latest version is loaded; repeat the action |
| Decision: "already handled by …" | someone acted first | close the card; the screen shows the latest state |
| Screen looks out of date | screens do not auto-refresh | sidebar → **Refresh** |
| Account locked | 5 wrong passwords | wait 15 min, or Admin → account card → Unlock |
| SSO: "no active account for this email" | account missing/inactive or email differs | Admin creates/activates the account with the same email |
| Email "not configured" | `SHIFTSENSE_EMAIL`, `SMTP_HOST` or `SHIFTSENSE_EMAIL_FROM` missing | set them, or leave email off (in-app still works) |
| Email "not sent" | in-app preference, unverified address, or demo scenario | reason is shown next to the status |
| Import rows held back | mandatory field missing or invalid value | download *rows to fix*, correct, re-upload |
| Photo shows initials | photo missing, invalid or not visible to you | HR → profile → Photo; check permissions |
| Theme switch does nothing | Streamlit upgraded past 1.64 | pin Streamlit 1.64 or update `ui_kit.apply_theme` |
