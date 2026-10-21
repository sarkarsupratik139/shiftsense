-- ShiftSense database schema (SQLite), generated from a freshly initialised database.
-- The application creates and upgrades these tables itself on first use (see README.md here).
-- Versioned migrations applied: 101 reference data, 201 hr decision record, 301 employee photos

CREATE TABLE accounts (
        username TEXT PRIMARY KEY, name TEXT, employee_id TEXT UNIQUE, role TEXT, team TEXT, agent_id TEXT,
        phone TEXT, contact_pref TEXT, sms_consent INTEGER, active INTEGER, password_hash TEXT, version INTEGER,
        created_at TEXT, created_by TEXT, updated_at TEXT, updated_by TEXT, email TEXT DEFAULT '', email_verified INTEGER DEFAULT 0, email_code_hash TEXT, email_code_exp TEXT, extra_permissions TEXT DEFAULT '', failed_logins INTEGER DEFAULT 0, locked_until TEXT, must_change INTEGER DEFAULT 0, password_changed_at TEXT, theme_pref TEXT DEFAULT 'System');

CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, created TEXT, sim_time TEXT, actor TEXT, role TEXT,
        action TEXT, record TEXT, before TEXT, after TEXT, reason TEXT, status TEXT, prev_hash TEXT, hash TEXT);

CREATE TABLE case_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, created TEXT, sim_time TEXT, actor TEXT,
        role TEXT, action TEXT, from_status TEXT, to_status TEXT, note TEXT);

CREATE TABLE cases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, flag_key TEXT UNIQUE, created TEXT, sim_time TEXT,
        kind TEXT, title TEXT, evidence TEXT, team TEXT, subject_user TEXT, reviewer TEXT, status TEXT,
        due_t INTEGER, outcome TEXT);

CREATE TABLE change_sets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, domain TEXT, status TEXT, submitted_by TEXT,
    submitted_role TEXT, submitted_at TEXT, decided_by TEXT, decided_at TEXT, decision_reason TEXT, applied_at TEXT,
    effective_from TEXT, reason TEXT, source TEXT, converted_by TEXT, ops TEXT, summary TEXT, impact TEXT,
    issues TEXT, outcome TEXT);

CREATE TABLE emails (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, idem_key TEXT UNIQUE, kind TEXT, ref TEXT,
        username TEXT, recipient TEXT, subject TEXT, status TEXT, detail TEXT, attempts INTEGER,
        created TEXT, updated TEXT, sim_time TEXT);

CREATE TABLE employee_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL, status TEXT NOT NULL, sha256 TEXT NOT NULL,
    image BLOB NOT NULL, thumb BLOB NOT NULL, source TEXT, uploaded_by TEXT, uploaded_at TEXT, removed_by TEXT,
    removed_at TEXT);

CREATE TABLE hr_absence_flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, flag_key TEXT NOT NULL UNIQUE,
    employee_id TEXT NOT NULL, window_start INTEGER, window_end INTEGER, absences INTEGER, threshold INTEGER,
    status TEXT NOT NULL, created_at TEXT, updated_by TEXT, updated_at TEXT, note TEXT);

CREATE TABLE hr_attendance_corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, employee_id TEXT NOT NULL, day INTEGER NOT NULL,
    original TEXT, requested TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL, requested_by TEXT,
    requested_by_user TEXT, requested_at TEXT, reviewed_by TEXT, reviewed_at TEXT, review_reason TEXT);

CREATE TABLE hr_case_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER, username TEXT, action TEXT, allowed INTEGER, at TEXT);

CREATE TABLE hr_case_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL, kind TEXT NOT NULL, body TEXT, due TEXT,
    by_name TEXT, by_user TEXT, at TEXT);

CREATE TABLE hr_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, confidentiality TEXT NOT NULL, status TEXT NOT NULL,
    caseworker TEXT, subject_employee TEXT, summary TEXT, opened_by TEXT, opened_at TEXT, updated_at TEXT,
    deadline TEXT, outcome TEXT, closed_by TEXT, closed_at TEXT);

CREATE TABLE hr_leave_balances (
    id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL, leave_type_id TEXT NOT NULL,
    opening_balance REAL NOT NULL, effective_date TEXT NOT NULL, unit TEXT NOT NULL, entered_by TEXT,
    entered_by_user TEXT, entered_at TEXT, verified_by TEXT, verified_at TEXT, source_reference TEXT,
    status TEXT NOT NULL);

CREATE TABLE hr_leave_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL, leave_type_id TEXT NOT NULL, kind TEXT NOT NULL,
    amount REAL NOT NULL, request_id TEXT, reason TEXT, by_name TEXT, at TEXT, run_id TEXT);

CREATE TABLE hr_overtime_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, task_key TEXT NOT NULL UNIQUE,
    employee_id TEXT NOT NULL, period TEXT, worked_hours REAL, scheduled_unconfirmed_hours REAL, threshold REAL,
    status TEXT NOT NULL, created_at TEXT, decided_by TEXT, decided_at TEXT, reason TEXT);

CREATE TABLE hr_policy (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pkey TEXT NOT NULL, value TEXT, status TEXT NOT NULL, reference TEXT,
    note TEXT, changed_by TEXT, changed_at TEXT);

CREATE TABLE hr_review_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, request_id TEXT NOT NULL, employee_id TEXT,
    trigger_name TEXT NOT NULL, reason TEXT, status TEXT NOT NULL, assigned_to TEXT, created_at TEXT,
    recommendation TEXT, recommended_by TEXT, recommended_at TEXT, note TEXT, closed_at TEXT);

CREATE TABLE hr_separations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL, username TEXT, effective_date TEXT NOT NULL,
    reason_category TEXT NOT NULL, note TEXT, status TEXT NOT NULL, recorded_by TEXT, recorded_by_user TEXT,
    recorded_at TEXT, confirmed_by TEXT, confirmed_at TEXT, executed_at TEXT, deactivated INTEGER DEFAULT 0,
    rescinded_by TEXT, rescinded_at TEXT, rescind_reason TEXT, correction_of INTEGER, corrected_by_id INTEGER);

CREATE TABLE import_mappings (
    signature TEXT PRIMARY KEY, domain TEXT, mapping TEXT, updated TEXT, uses INTEGER DEFAULT 0);

CREATE TABLE live_roster (run_id TEXT, agent_id TEXT, day INTEGER, shift TEXT, queue TEXT, alt_queue TEXT, ext_before REAL, ext_after REAL, kind TEXT, saved_at TEXT);

CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, thread TEXT, created TEXT, sim_time TEXT,
        sender TEXT, sender_role TEXT, body TEXT, read_by TEXT);

CREATE TABLE notices (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, dedupe TEXT UNIQUE, created TEXT, sim_time TEXT,
        priority TEXT, title TEXT, body TEXT, sender TEXT, recipient TEXT, related_kind TEXT, related_ref TEXT,
        action_required INTEGER, due_t INTEGER, status TEXT, read_by TEXT);

CREATE TABLE plan_versions (run_id TEXT, plan_id TEXT, name TEXT, status TEXT, source TEXT, created_by TEXT, created TEXT, shifts INTEGER, saved_at TEXT);

CREATE TABLE ref_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL, rkey TEXT NOT NULL, version INTEGER NOT NULL,
    effective_from TEXT NOT NULL, data TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, change_id INTEGER,
    changed_by TEXT, source TEXT, created TEXT);

CREATE TABLE reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, agent_id TEXT, day INTEGER, shift TEXT, recipient TEXT,
        channel TEXT, body TEXT, status TEXT, provider TEXT, provider_id TEXT, error TEXT, attempt INTEGER,
        created TEXT, sim_time TEXT, sent_by TEXT, token_hash TEXT UNIQUE, opens_t INTEGER, expires_t INTEGER,
        used_sim TEXT, used_via TEXT, late_min INTEGER);

CREATE TABLE scenario_workspace (id INTEGER PRIMARY KEY CHECK (id = 1), saved_at TEXT, blob BLOB, version INTEGER DEFAULT 0);

CREATE TABLE schedule_changes (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, saved_at TEXT, sim_time TEXT, actor TEXT, event TEXT, summary TEXT, reason TEXT, n_changes INTEGER, details TEXT, snapshot TEXT, role TEXT DEFAULT '', status TEXT DEFAULT '', related TEXT DEFAULT '');

CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT, applied TEXT);

CREATE TABLE whatif_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, created TEXT, sim_time TEXT, actor TEXT, name TEXT,
        inputs TEXT, status TEXT, as_is TEXT, after TEXT);

CREATE TABLE workspace (id INTEGER PRIMARY KEY CHECK (id = 1), saved_at TEXT, blob BLOB, version INTEGER DEFAULT 0);

CREATE INDEX ix_photo_emp ON employee_photos (employee_id, status);

CREATE INDEX ix_ref_domain_key ON ref_records (domain, rkey, version);

CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'the audit trail is append-only'); END;

CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'the audit trail is append-only'); END;
