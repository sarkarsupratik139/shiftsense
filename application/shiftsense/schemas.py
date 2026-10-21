"""What each kind of reference data looks like: fields, mandatory flags, accepted header names and value types.

One definition is shared by the smart converter, the forms, the checks, the templates and the exports, so a
field means the same thing everywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    name: str                      # canonical key
    label: str                     # what people see
    kind: str                      # text | int | float | bool | date | datetime | duration_sec | percent | list |
                                   # queue | queues | team | contract | shift | shifts | weekday | sla | hours | choice
    required: bool = False
    synonyms: tuple = ()
    choices: tuple = ()
    default: object = None
    help: str = ""


@dataclass(frozen=True)
class Domain:
    name: str
    label: str
    key: tuple                     # field names that identify a record
    fields: tuple
    approval: str | None = None    # role that must approve (unless that role submitted it)
    partial_ok: bool = True        # good rows may be applied while bad rows are held back
    owner_roles: tuple = ("analyst",)
    description: str = ""

    def field(self, name: str) -> Field:
        return next(f for f in self.fields if f.name == name)

    @property
    def required(self) -> list[str]:
        return [f.name for f in self.fields if f.required]


F = Field

EMPLOYEES = Domain(
    "employees", "Employee details", ("employee_id",), (
        F("employee_id", "Employee ID", "text", True, ("emp id", "employee code", "emp code", "agent id", "agent no",
                                                     "agent number", "staff id", "id", "employee no", "emp no", "ecode")),
        F("name", "Full name", "text", True, ("employee name", "agent name", "full name", "name of employee", "emp name")),
        F("team", "Team", "team", True, ("team name", "tl", "team lead", "supervisor team", "group", "pod")),
        F("primary", "Primary queue", "queue", True, ("primary queue", "main queue", "queue", "process", "lob",
                                                      "proc/lang", "skill", "primary skill")),
        F("contract", "Contract type", "contract", True, ("contract", "contract type", "employment type", "emp type",
                                                         "agreement")),
        F("secondary", "Other trained queues", "queues", False, ("secondary", "secondary queues", "other queues",
                                                                "cross skills", "additional skills", "other skills")),
        F("skill_levels", "Skill level per queue", "text", False, ("proficiency", "skill level", "level")),
        F("night_ok", "Night shifts allowed", "bool", False, ("night", "nights", "night ok", "can work nights",
                                                           "night shift ok", "nights ok"), default=True),
        F("pref_shift", "Preferred shift", "shift", False, ("preferred shift", "shift preference", "pref shift"),
          default="Day"),
        F("pref_off", "Preferred day off", "weekday", False, ("preferred day off", "weekly off", "week off", "off day",
                                                            "day off"), default=6),
        F("start_date", "Start date", "date", False, ("joining date", "doj", "date of joining", "start", "join date")),
        F("end_date", "Last working day", "date", False, ("lwd", "last working day", "exit date", "end date",
                                                        "relieving date")),
        F("status", "Status", "choice", False, ("employment status", "state"),
          choices=("Active", "On notice", "Long leave", "Exited"), default="Active"),
        F("photo_ref", "Profile photo", "text", False, ("photo", "profile photo", "profile image", "image",
                                                       "picture", "photo file", "photo url", "avatar")),
    ), approval=None, partial_ok=True, owner_roles=("analyst",),
    description="Everything the planner needs to know about a person.")

CONTRACTS = Domain(
    "contracts", "Employment contracts", ("name",), (
        F("name", "Contract name", "text", True, ("contract", "contract name", "contract type", "type")),
        F("contracted_hours_per_week", "Contracted paid hours per week", "float", True,
          ("contracted hours", "hours per week", "weekly hours", "paid hours")),
        F("contracted_shifts_per_week", "Contracted shifts per week", "int", True,
          ("contracted shifts", "shifts per week", "days per week")),
        F("max_weekly_hours", "Maximum paid hours per week", "float", False, ("max hours", "max weekly hours")),
        F("max_shifts_per_week", "Maximum shifts per week", "int", False, ("max shifts", "max days")),
        F("night_allowed", "Night shifts allowed", "bool", False, ("nights", "night allowed"), default=True),
        F("max_nights_per_week", "Maximum night shifts per week", "int", False, ("max nights",)),
        F("allowed_shifts", "Allowed shift types", "shifts", False, ("shifts allowed", "allowed shifts", "shift types")),
        F("min_rest_hours", "Minimum rest between shifts (h)", "float", False, ("rest", "min rest", "rest hours", "minimum rest (h)", "minimum rest")),
        F("max_ot_days_per_horizon", "Overtime days per fortnight", "int", False, ("ot days", "overtime days")),
        F("max_extension_hours", "Maximum extension (h)", "float", False, ("extension", "max extension")),
        F("hourly_rate", "Hourly rate (₹)", "float", False, ("rate", "hourly rate", "pay rate")),
        F("ot_multiplier", "Overtime multiplier", "float", False, ("ot multiplier", "overtime rate")),
    ), approval="ops_head", partial_ok=False, owner_roles=("analyst",),
    description="Working-time terms per contract type. Never looser than the company-wide rules.")

SERVICE_LEVELS = Domain(
    "service_levels", "Client contracts and service levels", ("queue",), (
        F("client", "Client / line of business", "text", True, ("client", "customer", "account", "lob", "line of business")),
        F("queue", "Queue", "queue_new", True, ("queue", "process", "skill", "proc/lang")),
        F("sla", "Service level", "sla", True, ("sla", "service level", "target", "sl target")),
        F("hours", "Opening hours", "hours", True, ("hours", "opening hours", "operating hours", "timing", "open hours")),
        F("aht_sec", "Average handle time", "duration_sec", True, ("aht", "handle time", "average handle time",
                                                                 "avg handle time")),
        F("priority", "Priority", "choice", False, ("priority", "importance", "sensitivity"),
          choices=("High", "Medium", "Low"), default="Medium"),
        F("penalty", "Penalty per missed half-hour (₹)", "float", False, ("penalty", "sla penalty")),
        F("volume_pct", "Contracted volume change %", "percent_change", False, ("volume change", "forecast override",
                                                                              "volume %")),
        F("effective_to", "Effective until", "date", False, ("until", "end", "valid to", "effective to")),
    ), approval="ops_head", partial_ok=False, owner_roles=("ops_head", "analyst"),
    description="The promise to each client, which drives how many people each queue needs.")

RULES = Domain(
    "rules", "Rules and policies", ("rule",), (
        F("rule", "Rule", "rule", True, ("rule", "setting", "parameter", "policy")),
        F("value", "New value", "float", True, ("value", "new value", "limit")),
    ), approval="ops_head", partial_ok=False, owner_roles=("analyst", "admin"),
    description="Company-wide limits and settings, versioned by effective date.")

LEAVE = Domain(
    "leave", "Leave and availability", ("employee_id", "date"), (
        F("employee_id", "Employee ID", "text", True, EMPLOYEES.fields[0].synonyms),
        F("date", "Date", "date", True, ("date", "leave date", "from", "from date", "start date", "start", "leave from", "leave start")),
        F("date_to", "Until (inclusive)", "date", False, ("to", "to date", "till", "until", "end date", "end")),
        F("type", "Leave type", "choice", False, ("leave type", "type", "category"),
          choices=("Annual", "Sick", "Unpaid", "Training", "Other"), default="Annual"),
        F("status", "Status", "choice", False, ("status", "approval"), choices=("Approved", "Requested"),
          default="Approved"),
        F("note", "Note", "text", False, ("note", "remarks", "comment", "reason")),
    ), approval=None, partial_ok=True, owner_roles=("analyst",),
    description="Approved leave and other planned absence.")

VOLUMES = Domain(
    "volumes", "Call volumes", ("date", "queue", "time"), (
        F("date", "Date", "date", True, ("date", "day", "call date")),
        F("queue", "Queue", "queue", True, ("queue", "skill", "split", "vdn", "process")),
        F("time", "Time slot", "time", False, ("time", "interval", "slot", "half hour", "hour", "start time", "interval start", "time slot")),
        F("calls", "Calls offered", "int", True, ("calls", "offered", "calls offered", "volume", "contacts")),
        F("answered", "Calls answered", "int", False, ("answered", "handled", "calls answered")),
        F("answered_in_target", "Answered within target", "int", False, ("answered within target", "within sl",
                                                                         "answered in sl", "in sla", "within target")),
    ), approval=None, partial_ok=True, owner_roles=("analyst",),
    description="Actual calls per queue; drives the forecast and makes forecast accuracy measurable.")

SITUATION_TYPES = {
    # type: (label, mandatory fields beyond type/title/start, optional fields, submitter roles, needs approval)
    "holiday": ("Public holiday", ("start",), ("end", "queues", "volume_pct", "closed"), ("analyst", "ops_head")),
    "planned_outage": ("Planned outage or maintenance", ("start", "queues"), ("end", "capacity_lost_pct", "aht_pct"),
                       ("ops_head", "analyst")),
    "unplanned_outage": ("Unplanned outage", ("start", "queues"), ("end", "capacity_lost_pct", "aht_pct"), ("ops_head",)),
    "protest": ("Protest, strike or transport disruption", ("start", "where"), ("end", "absence_pct", "agents"),
                ("ops_head", "team_lead")),
    "disaster": ("Disaster or emergency", ("start", "where"), ("end", "agents", "continuity_sla_pct"), ("ops_head",)),
    "campaign": ("Campaign or promotion", ("start", "end", "queues", "volume_pct"), (), ("analyst",)),
    "training": ("Training or team meeting", ("start", "end", "where"), ("agents",), ("team_lead", "analyst")),
    "rule_exception": ("Temporary rule exception", ("start", "end", "rule_key", "rule_value"), (),
                       ("ops_head", "analyst")),
    "other": ("Other", ("start", "where_or_queues"), ("end", "volume_pct", "capacity_lost_pct", "agents"),
              ("ops_head", "analyst")),
}

SITUATIONS = Domain(
    "situations", "Special conditions and events", ("id",), (
        F("id", "Reference", "text", True, ("id", "ref", "reference")),
        F("type", "Situation", "choice", True, ("type", "situation", "event type"), choices=tuple(SITUATION_TYPES)),
        F("title", "Title", "text", True, ("title", "name", "event", "description short")),
        F("start", "Starts", "datetime", True, ("start", "from", "begins", "start time", "date")),
        F("end", "Ends", "datetime", False, ("end", "to", "until", "end time")),
        F("queues", "Queues affected", "queues", False, ("queues", "queue", "process")),
        F("teams", "Teams affected", "list", False, ("teams", "team", "where", "location", "site", "area")),
        F("agents", "Agents affected", "list", False, ("agents", "employees", "people")),
        F("volume_pct", "Volume change %", "percent_change", False, ("volume", "volume change", "calls change")),
        F("closed", "Queues closed", "bool", False, ("closed", "shut")),
        F("capacity_lost_pct", "Capacity lost %", "percent", False, ("capacity lost", "capacity loss")),
        F("aht_pct", "Handle time change %", "percent_change", False, ("aht change", "handle time change")),
        F("absence_pct", "Expected absence %", "percent", False, ("absence", "expected absence")),
        F("continuity_sla_pct", "Continuity service target %", "percent", False, ("continuity", "relaxed target")),
        F("status", "Status", "choice", False, ("status",), choices=("Active", "Closed"), default="Active"),
        F("review_hours", "Remind Ops to update it every (h)", "int", False, ("review every", "review hours")),
        F("rule_key", "Rule (temporary exception)", "rule", False, ("rule",)),
        F("rule_value", "Temporary value", "float", False, ("temporary value", "exception value")),
        F("description", "Description", "text", False, ("description", "details", "notes")),
    ), approval=None, partial_ok=False, owner_roles=("ops_head", "analyst", "team_lead"),
    description="Holidays, outages, protests, disasters, campaigns, training and other one-off situations.")

ATTENDANCE = Domain(
    "attendance", "Attendance feed", ("employee_id", "date"), (
        F("employee_id", "Employee ID", "text", True, EMPLOYEES.fields[0].synonyms + ("badge id", "user id")),
        F("date", "Date", "date", True, ("date", "work date", "shift date", "day")),
        F("login", "Login time", "time", True, ("login", "log in", "in time", "clock in", "first login", "punch in",
                                               "sign in")),
        F("logout", "Logout time", "time", False, ("logout", "log out", "out time", "clock out", "last logout",
                                                  "punch out", "sign out")),
        F("source", "Source", "text", False, ("source", "system", "device"), default="feed"),
    ), approval=None, partial_ok=True, owner_roles=("analyst",),
    description="Login and logout times from badge or telephony systems. A missing login never becomes a no-show.")

DOMAINS = {d.name: d for d in (EMPLOYEES, CONTRACTS, SERVICE_LEVELS, RULES, LEAVE, VOLUMES, SITUATIONS, ATTENDANCE)}

RULE_LABELS = {
    "min_rest_hours": ("Minimum rest between shifts", "h"),
    "max_weekly_hours": ("Maximum paid hours per week", "h"),
    "contracted_shifts_per_week": ("Default contracted shifts per week", "shifts"),
    "max_shifts_per_week": ("Maximum shifts per week", "shifts"),
    "max_nights_per_week": ("Maximum night shifts per week", "nights"),
    "max_ot_days_per_horizon": ("Overtime days per fortnight", "days"),
    "max_daily_hours": ("Maximum daily span", "h"),
    "max_extension_hours": ("Maximum extension", "h"),
    "staffing.shrinkage": ("Shrinkage", "share 0–1"),
    "staffing.max_occupancy": ("Maximum occupancy", "share 0–1"),
    "requests.green_buffer": ("Spare staff needed for instant approval", "people"),
    "recovery.tl_overdue_hours": ("Team lead deadline before Ops is told", "h"),
    "recovery.data_alert_days": ("Days after today that get alerts after a data change (0 = today only)", "days"),
    "recovery.situation_review_hours": ("Default reminder to update an open-ended situation", "h"),
    "costs.redeploy_cost": ("Redeployment cost", "₹"),
    "costs.swap_cost": ("Swap cost", "₹"),
    "costs.hourly_rate": ("Default hourly rate", "₹"),
    "costs.ot_multiplier": ("Default overtime multiplier", "×"),
    "governance.approval_gap": ("Approval-rate gap that flags a review", "share 0–1"),
    "governance.min_requests": ("Minimum decided requests before comparing", "requests"),
    "governance.ot_repeat": ("Repeated overtime that flags a review", "times"),
    "governance.swap_pair_repeat": ("Repeated swaps that flag a review", "times"),
    "governance.overrides": ("Team-lead overrides that flag a review", "times"),
}
GOVERNANCE_RULES = {k for k in RULE_LABELS if k.startswith("governance.")}
