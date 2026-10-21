# API and integration catalogue

ShiftSense has no public HTTP API. Its **internal API** is `shiftsense/services.py`: every screen calls these
functions, and each one checks permission, scope and state on the server and writes an audit row. Integrations
are file-based (upload, paste, drop folder) plus optional email (SMTP), single sign-on (OIDC) and an optional
AI provider. Generated from the code.

## External integrations

| Integration | Direction | How | Configuration | Status |
|---|---|---|---|---|
| Drop folder | in | files in `data/inbox/<kind>/` (employees, contracts, leave, volumes, attendance, leave_balances, photos) | `scripts/stage_drop_folder.sh` or the app | Built |
| Excel / CSV / paste / typed text | in | Data hub and HR screens | — | Built |
| Email (SMTP) | out | notification copies, test email, health alerts | `SHIFTSENSE_EMAIL=smtp`, `SMTP_*` | Built; needs a relay |
| Single sign-on (OIDC) | in | Streamlit `st.login` | `.streamlit/secrets.toml` `[auth]`, `SHIFTSENSE_AUTH` | Built; needs an identity provider |
| AI provider (Anthropic) | out | explanations and import help only, never decisions | `ANTHROPIC_API_KEY`, `SHIFTSENSE_AI*` | Optional |
| PostgreSQL | storage | `SHIFTSENSE_DATABASE_URL` | Docker stack | Built and tested |
| Exports | out | Excel, CSV, calendar (.ics), print HTML | role-scoped | Built |

## Internal API: `shiftsense/services.py`

Permissions are detected automatically from the code. "—" means the check happens in a helper the function calls (for example the thread or case gate, or the submitting account's own permissions), or that the function only reads data the caller already sees. The authoritative per-role result for every action is `../governance/role-permission-matrix.xlsx` (sheet *Server actions by role*), asserted by `tests/test_matrix.py`.

| Function | Permission checked | Purpose |
|---|---|---|
| `absence_flags` | manage_people (HR) |  |
| `absence_validation` | manage_people (HR) | Senior HR / HR dry run of the absence-flag rules against current attendance: creates nothing. |
| `add_case_entry` | caseworker or Senior HR | Restricted comment, evidence reference (where the evidence is kept, not the file) or follow-up. |
| `adjust_leave_balance` | manage_people (HR) | A correction is an adjustment transaction with a reason, never an overwrite of the balance. |
| `advance_clock` | run_clock | Moves SIMULATED time in the demo scenario only. Its attendance results and no-shows are simulated; |
| `advance_live_clock` | live_clock | Move LIVE time (so genuine check-in windows open). Generates no attendance, no no-shows, no events. |
| `answer_risk` | ops_decide | Ops records its decision on an Analyst risk, or returns it to the Analyst for more work. |
| `apply_queued` | — | Apply change sets staged by a server run, with the submitting account's own permissions (re-checked now). |
| `assign_followup` | ops_decide |  |
| `assign_review` | manage_people (HR) |  |
| `attendance_corrections` | manage_people (HR) |  |
| `audit_hr_download` | manage_people (HR) |  |
| `can_access_thread` | — |  |
| `case_action` | ops_decide, governance | Admin oversight actions on a governance case. No operational data is changed. |
| `change_own_password` | signed-in, own scope | Anyone signed in changes their own password (current password required; policy checked). |
| `check_absence_flags` | manage_people (HR) |  |
| `check_in_self` | report_self | The signed-in agent checks in for their own shift: identity from the stored account, the reminder must be |
| `check_overtime` | manage_people (HR) |  |
| `cleanup_legacy_story` | manage_demo | Dry run by default. With execute=True, removes only the confidently identified old demo-story records |
| `commit_import` | import | Build and validate a roster for a fully validated import. Returns the new workspace; the caller |
| `complete_followup` | decide_fix | Team lead reports a follow-up as done; Ops is told. Only the addressed team lead can do this. |
| `confirm_email_code` | signed-in, own scope |  |
| `confirm_separation` | manage_people (HR) |  |
| `convert_data` | signed-in, own scope | Any input -> converted rows + issues (nothing is saved). Returns (Result, sheet names). |
| `correct_attendance` | decide_fix | Team Lead records that an own-team agent was present for a started shift with no record (e.g. the |
| `correct_separation` | manage_people (HR) | A correction is a new record that replaces the old one (kept, marked corrected); it needs confirming again. |
| `create_account` | manage_accounts | Admin creates a business-role account. The Admin role cannot be created here. |
| `data_changes` | signed-in, own scope |  |
| `data_context` | signed-in, own scope |  |
| `decide_change` | signed-in, own scope | The approver accepts or rejects a pending change set. Never one's own submission. |
| `decide_incident` | decide_fix |  |
| `decide_overtime` | manage_people (HR) | HR approves or rejects worked overtime above the threshold. No pay is calculated; coverage decisions |
| `decide_request` | approve_requests |  |
| `draft_plan` | replan | Create a draft in the LIVE workspace `w` (a separate proposal; the published roster is unchanged). |
| `enter_leave_balance` | manage_people (HR) |  |
| `escalate_risk` | escalate_risk | The Analyst asks Ops for an operational decision on a material risk. This is a request, not a |
| `get_request` | signed-in, own scope | Read one request: the requesting agent, their team lead (own team) or an all-teams role. |
| `history` | — | Scoped history for the signed-in user (see shiftsense/timeline.py). |
| `history_export` | signed-in, own scope |  |
| `hr_cases` | signed-in, own scope | Cases this user may see (caseworker: own; Senior HR: all). Others get nothing. |
| `hr_policy` | manage_people (HR) |  |
| `hr_records_workbook` | manage_people (HR) | HR download: balances (with their state), the transaction ledger, separations, review tasks and attendance |
| `hr_reminders` | — | Due and overdue HR work gets a reminder (at most one per item per day). Notices carry no case details. |
| `import_leave_balances` | manage_people (HR) | HR-system export of opening balances, read with flexible headers. Every usable row becomes an UNVERIFIED |
| `import_roster_grid` | replan | Analyst: a roster grid becomes a DRAFT plan (validated, reviewed and published like any draft). Days that |
| `leave_balance` | signed-in, own scope | HR sees any balance; an agent sees their own. Team Leads do not see balances. |
| `load_base_pack` | manage_data | Analyst: load the base input data (basedata/*.csv) into reference data through the normal converter. |
| `load_demo_story` | manage_demo | Analyst loads the prepared scenario into a SEPARATE sandbox: a copy of the live workspace with its |
| `move_absence_flag` | manage_people (HR) |  |
| `notify_alert` | — | Public hook for alerts raised by the system itself (attendance no-shows). |
| `open_hr_case` | manage_people (HR) |  |
| `open_reminder` | report_self | The agent opened the reminder notification (recorded as 'opened'; nothing else changes). |
| `ops_decide` | ops_decide |  |
| `overtime_view` | manage_people (HR) |  |
| `photo_uris` | — | employee_id -> data URI for the people this user may see; others (and people without a photo) are left |
| `post_message` | signed-in, own scope |  |
| `preview_change` | signed-in, own scope |  |
| `publish_plan` | replan | Publish a validated plan. Requires the planner's review confirmation; the plan is re-checked |
| `queue_change` | signed-in, own scope | Stage converted rows for the running app to apply (server-side drop-folder runs). |
| `read_situation_text` | report_situation | A few sentences -> draft situation. Rules first; the AI (if switched on for non-personal data) only when the |
| `reassign_hr_case` | caseworker or Senior HR |  |
| `reassign_review` | manage_people (HR) | Move a leave review task to another HR reviewer (reason required). |
| `rebuild_from_reference` | manage_data | Analyst: build a new live workspace from reference data in force today, with a fresh optimised roster. |
| `recommend_on_review` | manage_people (HR) | HR's recommendation. It never approves or rejects the request; the Team Lead decides. |
| `record_separation` | manage_people (HR) |  |
| `record_whatif` | replan | What-if runs work on copies; this records that the analysis was done and what it showed. With `result` |
| `refdata_current_employee` | — |  |
| `remember_mapping` | — |  |
| `remind_open_situations` | — | Open-ended situations: remind Ops to update or close them every N hours (N chosen per situation). |
| `reminder_view` | — | Delivery facts of one reminder, kept apart: in inbox / opened / checked in / email status. |
| `remove_employee_photo` | manage_people (HR) | HR removes a photo: the person shows initials again. The employee record and history are untouched. |
| `report_absence` | report_self, report_absence |  |
| `report_discrepancy` | report_self | Agent reports that an attendance or schedule record looks wrong. Goes to their Team Lead; nothing is |
| `report_left_early` | report_self, report_absence |  |
| `report_situation` | report_situation | Ops / Analyst / Team Lead (by situation type) report a situation; continuity mode needs Ops. |
| `report_spike` | report_spike |  |
| `request_attendance_correction` | decide_fix | Team Lead (own team) or the agent (own record) asks for a change to recorded attendance. Applied only |
| `request_email_code` | signed-in, own scope | Send a 6-digit code to the address on the signed-in user's own account. |
| `request_hr_status` | — | What the requester sees as the next step of their own leave request. |
| `request_swap` | request_leave |  |
| `request_view` | — | What a viewer may see of a request: sick-leave type and the agent's note stay with HR and the requester; |
| `require_demo` | manage_demo |  |
| `rescind_separation` | manage_people (HR) | Withdraws a separation. If it already deactivated the account, the account is reactivated (audited). |
| `reset_password` | manage_accounts | Admin sets a temporary password; the person must choose their own at the next sign-in. |
| `respond` | signed-in, own scope |  |
| `restore_plan_as_draft` | replan | Roll back to an earlier published roster version the SAFE way: it becomes a new draft that must be |
| `review_attendance_correction` | manage_people (HR) | HR reviewer applies or rejects. Applying writes an auditable adjustment; the original value stays in the |
| `review_status` | — | For the deciding Team Lead / Ops: trigger and HR recommendation, without sick details. |
| `review_tasks` | manage_people (HR) |  |
| `revoke` | approve_requests |  |
| `run_separations` | manage_people (HR) | Deactivate accounts whose confirmed separation is due: access ends at 00:00 in the business timezone |
| `saved_mapping` | — |  |
| `scan` | governance |  |
| `seed_notices` | — | After a demo story is loaded: route its open items to the right inboxes (deduplicated). |
| `send_attendance_reminder` | send_reminder | Team lead (own team) sends an in-app attendance reminder, plus an email copy when configured and the |
| `send_test_email` | manage_accounts | Admin checks the mail relay end to end with a test message to an address they choose. Records the relay's |
| `separations_view` | manage_people (HR) |  |
| `set_employee_photo` | manage_people (HR) | HR uploads or replaces a person's profile photo (validated and resized on the server; audited). |
| `set_hr_case_status` | caseworker or Senior HR |  |
| `set_hr_policy` | manage_people (HR), senior_hr | Senior HR changes an HR policy item. Every change is a new version; approving needs a policy reference. |
| `set_own_theme` | signed-in, own scope | The signed-in person's display theme (System, Light or Dark). Never affects data. |
| `set_senior_hr` | manage_accounts | Admin grants or removes the Senior HR permission (HR role only). Audited. |
| `submit_change` | signed-in, own scope | Save a change set. Applied at once when no approval is needed; otherwise waits for the approver. |
| `submit_leave` | request_leave |  |
| `sweep_overdue` | — | Escalate overdue critical team-lead alerts to Operations once (deduplicated). |
| `thread_members` | — | Who may read/write a thread: {'agents': {...}, 'teams': {...}, 'roles': {...}, 'users': {...}}. |
| `unload_demo` | manage_demo | Remove the demo sandbox. Live records are untouched; the scenario's audit, notices and messages stay |
| `unlock_account` | manage_accounts |  |
| `update_account` | manage_accounts | Admin edits an account. Role changes need a reason; the version check prevents silent overwrites. |
| `update_own_preferences` | own_preferences | Agent: preferred shift and day off. Wishes for the next plan only; applied without approval. |
| `validate_plan` | replan |  |
| `verify_leave_balance` | manage_people (HR) |  |
| `verify_leave_balances` | manage_people (HR) | Bulk verification by a second HR user (each one still refuses balances this user entered). |
| `view_hr_case` | caseworker or Senior HR |  |
| `whatif_runs` | replan |  |
| `withdraw` | signed-in, own scope |  |
