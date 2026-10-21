from shiftsense import auth


def test_valid_login_returns_user_without_hash():
    u = auth.authenticate("tl1", "tl1@123")
    assert u and u["role"] == "team_lead" and u["team"] == "TL-1"
    assert "password_hash" not in u


def test_wrong_password_and_unknown_user_rejected():
    assert auth.authenticate("tl1", "wrong") is None
    assert auth.authenticate("nobody", "tl1@123") is None
    assert auth.authenticate("", "") is None


def test_username_is_case_insensitive():
    assert auth.authenticate("  Analyst ", "analyst@123")["role"] == "analyst"


def test_hashes_match_config():
    for name, rec in auth.config()["users"].items():
        assert rec["password_hash"] == auth.hash_password(name, rec["demo_password"])


def test_each_role_has_exactly_its_own_screen():
    expect = {"admin": ("admin@123", ["admin"]), "analyst": ("analyst@123", ["planner", "data", "demo"]),
              "tl1": ("tl1@123", ["team"]), "ops": ("ops@123", ["ops"]), "aarav": ("agent@123", ["me"]),
              "demo": ("demo@123", ["planner", "data", "demo"])}
    for user, (pw, pages) in expect.items():
        assert auth.role(auth.authenticate(user, pw))["pages"] == pages
    assert not auth.can_view(None, "planner")


def test_permissions_by_role():
    tl = auth.authenticate("tl1", "tl1@123")
    ops = auth.authenticate("ops", "ops@123")
    agent = auth.authenticate("aarav", "agent@123")
    admin = auth.authenticate("admin", "admin@123")
    demo = auth.authenticate("demo", "demo@123")
    assert auth.can(tl, "decide_fix") and auth.can(tl, "approve_requests") and not auth.can(tl, "view_audit")
    assert auth.can(ops, "ops_decide") and not auth.can(ops, "decide_fix")
    assert auth.can(agent, "request_leave") and not auth.can(agent, "approve_requests")
    # admin is oversight only: no operational or configuration changes, no reset
    assert auth.can(admin, "governance") and auth.can(admin, "view_audit")
    for p in ("manage_demo", "run_clock", "import", "decide_fix", "approve_requests", "replan", "ops_decide",
              "request_leave"):
        assert not auth.can(admin, p)
    assert demo["role"] == "analyst"                                  # retired operator account migrated
    assert auth.can(demo, "manage_demo") and not auth.can(demo, "governance")


def test_exactly_five_business_roles():
    roles = auth.config()["roles"]
    assert set(roles) == {"admin", "agent", "team_lead", "ops_head", "analyst", "hr"}
    assert [roles[r]["label"] for r in ("admin", "agent", "team_lead", "ops_head", "analyst")] == \
        ["Admin", "Agent", "Team Lead", "Ops", "Analyst"]
    assert {u["role"] for u in auth.config()["users"].values()} <= set(roles)


def test_request_visibility_is_team_scoped():
    reqs = [{"agent_id": "A01"}, {"agent_id": "A02"}, {"agent_id": "A06"}]
    team = {"A01": "TL-1", "A02": "TL-2", "A06": "TL-1"}
    tl1 = auth.authenticate("tl1", "tl1@123")
    tl2 = auth.authenticate("tl2", "tl2@123")
    ops = auth.authenticate("ops", "ops@123")
    aarav = auth.authenticate("aarav", "agent@123")
    assert [r["agent_id"] for r in auth.visible_requests(tl1, reqs, team)] == ["A01", "A06"]
    assert [r["agent_id"] for r in auth.visible_requests(tl2, reqs, team)] == ["A02"]
    assert len(auth.visible_requests(ops, reqs, team)) == 3
    assert [r["agent_id"] for r in auth.visible_requests(aarav, reqs, team)] == ["A01"]
