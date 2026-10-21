"""Navigation model (pure, testable): which destinations each role sees, grouped for the sidebar.

Showing a link is not access control: every screen re-checks access (ui.page) and every action is checked on
the server (services). This only decides what is offered.
"""
from __future__ import annotations

# page key (config/users.yaml) -> (script, title, icon)
PAGES = {
    "me": ("views/agent.py", "My work", ":material/person:"),
    "team": ("views/team_lead.py", "My team", ":material/groups:"),
    "planner": ("views/planner.py", "Planning", ":material/insights:"),
    "ops": ("views/ops.py", "Operations", ":material/monitoring:"),
    "admin": ("views/admin.py", "Administration", ":material/admin_panel_settings:"),
    "demo": ("views/demo.py", "Demo & data", ":material/science:"),
    "data": ("views/data.py", "Data hub", ":material/database:"),
    "hr": ("views/hr.py", "People data", ":material/badge:"),
}
# Pages with several areas get one navigation item per area (same script; the link selects the area).
AREAS = {
    "me": ("me_area", [("Home", ":material/home:"), ("My shifts", ":material/calendar_month:"),
                       ("Inbox & requests", ":material/inbox:"), ("My history", ":material/history:")]),
    "team": ("tl_area", [("Overview", ":material/space_dashboard:"), ("Attendance", ":material/how_to_reg:"),
                         ("Team schedule", ":material/calendar_month:"), ("Requests", ":material/event_note:"),
                         ("Alerts & decisions", ":material/notification_important:"),
                         ("Team information", ":material/groups:")]),
    "ops": ("ops_area", [("Overview", ":material/space_dashboard:"), ("Coverage & risk", ":material/stacked_line_chart:"),
                         ("Alerts & escalations", ":material/notification_important:"),
                         ("Recovery", ":material/healing:"), ("Cost & staffing", ":material/payments:"),
                         ("Follow-ups, data & history", ":material/history:")]),
    "planner": ("pl_area", [("Forecast & staffing", ":material/query_stats:"),
                            ("Roster optimization", ":material/view_week:"),
                            ("What-if", ":material/compare_arrows:"), ("Review & publish", ":material/task_alt:"),
                            ("Benchmarks & history", ":material/history:")]),
    "demo": ("sys_area", [("Demo scenario", ":material/science:"), ("Clocks", ":material/schedule:")]),
    "data": ("data_area", [("Data overview", ":material/database:"), ("Add or update data", ":material/upload_file:"),
                           ("Data changes", ":material/rule:"), ("Situations", ":material/emergency:"),
                           ("Downloads", ":material/download:")]),
    "hr": ("hr_area", [("People overview", ":material/badge:"), ("Employees", ":material/person_search:"),
                       ("Leave & balances", ":material/account_balance_wallet:"),
                       ("Attendance", ":material/how_to_reg:"), ("Reviews & approvals", ":material/fact_check:"),
                       ("Restricted cases", ":material/lock:"), ("HR policies", ":material/policy:"),
                       ("Reports & exports", ":material/download:")]),
    "admin": ("ad_area", [("Overview", ":material/calendar_month:"), ("Accounts & access", ":material/manage_accounts:"),
                          ("Governance & decisions", ":material/policy:"), ("Audit & integrity", ":material/verified_user:"),
                          ("System status", ":material/monitor_heart:")]),
}
GROUP = {"me": "My work", "team": "Team", "ops": "Operations", "planner": "Planning", "demo": "Demo",
         "data": "Data", "hr": "People",
         "admin": "Oversight"}


def url_path(page: str, area: str) -> str:
    return (f"{page}-{area}".lower().replace(" & ", "-").replace("&", "").replace(",", "")
            .replace(" ", "-"))


def spec(allowed: list[str]) -> list[dict]:
    """One dict per destination, in order; the first is the home screen. Only the role's own pages."""
    out = []
    for k in allowed:
        script, title, icon = PAGES[k]
        if k in AREAS:
            key, areas = AREAS[k]
            for a, ic in areas:
                out.append({"group": GROUP[k], "page": k, "script": script, "title": a, "icon": ic,
                            "area_key": key, "area": a, "url": url_path(k, a)})
        else:
            out.append({"group": GROUP[k], "page": k, "script": script, "title": title, "icon": icon,
                        "area_key": None, "area": None, "url": None})
    if out:
        out[0]["default"] = True
    return out
