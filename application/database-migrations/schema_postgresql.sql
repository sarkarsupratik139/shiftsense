-- ShiftSense database schema (PostgreSQL 16), generated from a freshly initialised schema.
-- The application creates and upgrades these tables itself (see README.md here). Column types as created.

CREATE TABLE accounts (
    username TEXT NOT NULL,
    name TEXT,
    employee_id TEXT,
    role TEXT,
    team TEXT,
    agent_id TEXT,
    phone TEXT,
    contact_pref TEXT,
    sms_consent INTEGER,
    active INTEGER,
    password_hash TEXT,
    version INTEGER,
    created_at TEXT,
    created_by TEXT,
    updated_at TEXT,
    updated_by TEXT,
    email TEXT DEFAULT ''::text,
    email_verified INTEGER DEFAULT 0,
    email_code_hash TEXT,
    email_code_exp TEXT,
    extra_permissions TEXT DEFAULT ''::text,
    failed_logins INTEGER DEFAULT 0,
    locked_until TEXT,
    must_change INTEGER DEFAULT 0,
    password_changed_at TEXT,
    theme_pref TEXT DEFAULT 'System'::text,
    PRIMARY KEY (username)
);

CREATE TABLE audit_log (
    id BIGSERIAL NOT NULL,
    run_id TEXT,
    created TEXT,
    sim_time TEXT,
    actor TEXT,
    role TEXT,
    action TEXT,
    record TEXT,
    before TEXT,
    after TEXT,
    reason TEXT,
    status TEXT,
    prev_hash TEXT,
    hash TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE case_events (
    id BIGSERIAL NOT NULL,
    case_id INTEGER,
    created TEXT,
    sim_time TEXT,
    actor TEXT,
    role TEXT,
    action TEXT,
    from_status TEXT,
    to_status TEXT,
    note TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE cases (
    id BIGSERIAL NOT NULL,
    run_id TEXT,
    flag_key TEXT,
    created TEXT,
    sim_time TEXT,
    kind TEXT,
    title TEXT,
    evidence TEXT,
    team TEXT,
    subject_user TEXT,
    reviewer TEXT,
    status TEXT,
    due_t INTEGER,
    outcome TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE change_sets (
    id BIGSERIAL NOT NULL,
    run_id TEXT,
    domain TEXT,
    status TEXT,
    submitted_by TEXT,
    submitted_role TEXT,
    submitted_at TEXT,
    decided_by TEXT,
    decided_at TEXT,
    decision_reason TEXT,
    applied_at TEXT,
    effective_from TEXT,
    reason TEXT,
    source TEXT,
    converted_by TEXT,
    ops TEXT,
    summary TEXT,
    impact TEXT,
    issues TEXT,
    outcome TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE emails (
    id BIGSERIAL NOT NULL,
    run_id TEXT,
    idem_key TEXT,
    kind TEXT,
    ref TEXT,
    username TEXT,
    recipient TEXT,
    subject TEXT,
    status TEXT,
    detail TEXT,
    attempts INTEGER,
    created TEXT,
    updated TEXT,
    sim_time TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE employee_photos (
    id BIGSERIAL NOT NULL,
    employee_id TEXT NOT NULL,
    status TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    image BYTEA NOT NULL,
    thumb BYTEA NOT NULL,
    source TEXT,
    uploaded_by TEXT,
    uploaded_at TEXT,
    removed_by TEXT,
    removed_at TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE hr_absence_flags (
    id BIGSERIAL NOT NULL,
    run_id TEXT NOT NULL,
    flag_key TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    window_start INTEGER,
    window_end INTEGER,
    absences INTEGER,
    threshold INTEGER,
    status TEXT NOT NULL,
    created_at TEXT,
    updated_by TEXT,
    updated_at TEXT,
    note TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE hr_attendance_corrections (
    id BIGSERIAL NOT NULL,
    run_id TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    day INTEGER NOT NULL,
    original TEXT,
    requested TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    requested_by TEXT,
    requested_by_user TEXT,
    requested_at TEXT,
    reviewed_by TEXT,
    reviewed_at TEXT,
    review_reason TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE hr_case_access (
    id BIGSERIAL NOT NULL,
    case_id INTEGER,
    username TEXT,
    action TEXT,
    allowed INTEGER,
    at TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE hr_case_events (
    id BIGSERIAL NOT NULL,
    case_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    body TEXT,
    due TEXT,
    by_name TEXT,
    by_user TEXT,
    at TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE hr_cases (
    id BIGSERIAL NOT NULL,
    category TEXT NOT NULL,
    confidentiality TEXT NOT NULL,
    status TEXT NOT NULL,
    caseworker TEXT,
    subject_employee TEXT,
    summary TEXT,
    opened_by TEXT,
    opened_at TEXT,
    updated_at TEXT,
    deadline TEXT,
    outcome TEXT,
    closed_by TEXT,
    closed_at TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE hr_leave_balances (
    id BIGSERIAL NOT NULL,
    employee_id TEXT NOT NULL,
    leave_type_id TEXT NOT NULL,
    opening_balance REAL NOT NULL,
    effective_date TEXT NOT NULL,
    unit TEXT NOT NULL,
    entered_by TEXT,
    entered_by_user TEXT,
    entered_at TEXT,
    verified_by TEXT,
    verified_at TEXT,
    source_reference TEXT,
    status TEXT NOT NULL,
    PRIMARY KEY (id)
);

CREATE TABLE hr_leave_transactions (
    id BIGSERIAL NOT NULL,
    employee_id TEXT NOT NULL,
    leave_type_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    amount REAL NOT NULL,
    request_id TEXT,
    reason TEXT,
    by_name TEXT,
    at TEXT,
    run_id TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE hr_overtime_tasks (
    id BIGSERIAL NOT NULL,
    run_id TEXT NOT NULL,
    task_key TEXT NOT NULL,
    employee_id TEXT NOT NULL,
    period TEXT,
    worked_hours REAL,
    scheduled_unconfirmed_hours REAL,
    threshold REAL,
    status TEXT NOT NULL,
    created_at TEXT,
    decided_by TEXT,
    decided_at TEXT,
    reason TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE hr_policy (
    id BIGSERIAL NOT NULL,
    pkey TEXT NOT NULL,
    value TEXT,
    status TEXT NOT NULL,
    reference TEXT,
    note TEXT,
    changed_by TEXT,
    changed_at TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE hr_review_tasks (
    id BIGSERIAL NOT NULL,
    run_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    employee_id TEXT,
    trigger_name TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL,
    assigned_to TEXT,
    created_at TEXT,
    recommendation TEXT,
    recommended_by TEXT,
    recommended_at TEXT,
    note TEXT,
    closed_at TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE hr_separations (
    id BIGSERIAL NOT NULL,
    employee_id TEXT NOT NULL,
    username TEXT,
    effective_date TEXT NOT NULL,
    reason_category TEXT NOT NULL,
    note TEXT,
    status TEXT NOT NULL,
    recorded_by TEXT,
    recorded_by_user TEXT,
    recorded_at TEXT,
    confirmed_by TEXT,
    confirmed_at TEXT,
    executed_at TEXT,
    deactivated INTEGER DEFAULT 0,
    rescinded_by TEXT,
    rescinded_at TEXT,
    rescind_reason TEXT,
    correction_of INTEGER,
    corrected_by_id INTEGER,
    PRIMARY KEY (id)
);

CREATE TABLE import_mappings (
    signature TEXT NOT NULL,
    domain TEXT,
    mapping TEXT,
    updated TEXT,
    uses INTEGER DEFAULT 0,
    PRIMARY KEY (signature)
);

CREATE TABLE live_roster (
    run_id TEXT,
    agent_id TEXT,
    day INTEGER,
    shift TEXT,
    queue TEXT,
    alt_queue TEXT,
    ext_before REAL,
    ext_after REAL,
    kind TEXT,
    saved_at TEXT
);

CREATE TABLE messages (
    id BIGSERIAL NOT NULL,
    run_id TEXT,
    thread TEXT,
    created TEXT,
    sim_time TEXT,
    sender TEXT,
    sender_role TEXT,
    body TEXT,
    read_by TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE notices (
    id BIGSERIAL NOT NULL,
    run_id TEXT,
    dedupe TEXT,
    created TEXT,
    sim_time TEXT,
    priority TEXT,
    title TEXT,
    body TEXT,
    sender TEXT,
    recipient TEXT,
    related_kind TEXT,
    related_ref TEXT,
    action_required INTEGER,
    due_t INTEGER,
    status TEXT,
    read_by TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE plan_versions (
    run_id TEXT,
    plan_id TEXT,
    name TEXT,
    status TEXT,
    source TEXT,
    created_by TEXT,
    created TEXT,
    shifts INTEGER,
    saved_at TEXT
);

CREATE TABLE ref_records (
    id BIGSERIAL NOT NULL,
    domain TEXT NOT NULL,
    rkey TEXT NOT NULL,
    version INTEGER NOT NULL,
    effective_from TEXT NOT NULL,
    data TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    change_id INTEGER,
    changed_by TEXT,
    source TEXT,
    created TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE reminders (
    id BIGSERIAL NOT NULL,
    run_id TEXT,
    agent_id TEXT,
    day INTEGER,
    shift TEXT,
    recipient TEXT,
    channel TEXT,
    body TEXT,
    status TEXT,
    provider TEXT,
    provider_id TEXT,
    error TEXT,
    attempt INTEGER,
    created TEXT,
    sim_time TEXT,
    sent_by TEXT,
    token_hash TEXT,
    opens_t INTEGER,
    expires_t INTEGER,
    used_sim TEXT,
    used_via TEXT,
    late_min INTEGER,
    PRIMARY KEY (id)
);

CREATE TABLE scenario_workspace (
    id INTEGER NOT NULL,
    saved_at TEXT,
    blob BYTEA,
    version INTEGER DEFAULT 0,
    PRIMARY KEY (id)
);

CREATE TABLE schedule_changes (
    id BIGSERIAL NOT NULL,
    run_id TEXT,
    saved_at TEXT,
    sim_time TEXT,
    actor TEXT,
    event TEXT,
    summary TEXT,
    reason TEXT,
    n_changes INTEGER,
    details TEXT,
    snapshot TEXT,
    role TEXT DEFAULT ''::text,
    status TEXT DEFAULT ''::text,
    related TEXT DEFAULT ''::text,
    PRIMARY KEY (id)
);

CREATE TABLE schema_migrations (
    version INTEGER NOT NULL,
    name TEXT,
    applied TEXT,
    PRIMARY KEY (version)
);

CREATE TABLE whatif_runs (
    id BIGSERIAL NOT NULL,
    run_id TEXT,
    created TEXT,
    sim_time TEXT,
    actor TEXT,
    name TEXT,
    inputs TEXT,
    status TEXT,
    as_is TEXT,
    after TEXT,
    PRIMARY KEY (id)
);

CREATE TABLE workspace (
    id INTEGER NOT NULL,
    saved_at TEXT,
    blob BYTEA,
    version INTEGER DEFAULT 0,
    PRIMARY KEY (id)
);

-- trigger audit_no_change: BEFORE DELETE OR UPDATE ON audit_log -> refuses changes (append-only audit trail)
