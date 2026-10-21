"""Sign-in security: password storage and policy, lockout, forced change, idle timeout, single sign-on mapping."""
from datetime import datetime, timedelta

import pytest
import streamlit  # noqa: F401

from shiftsense import accounts, auth, comms, security, services, staffing
from shiftsense import workflow as wf

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}
GOOD = "Blue-Harbour-42!"


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "sec.db"))
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def test_password_policy():
    assert security.problems(GOOD, "aarav", "Aarav Shah") == []
    assert "at least 12 characters" in security.problems("Ab1!", "x")
    assert any("3 of" in p for p in security.problems("alllowercaseletters", "x"))
    assert "must not contain the username" in security.problems("Aarav-Strong-2026", "aarav")
    assert "must not contain your name" in security.problems("Shahrukh-Strong-26".replace("Shahrukh", "Sharma"),
                                                              "zz", "Rohit Sharma")
    assert "too common" in security.problems("Password@123", "x")


def test_hash_has_random_salt_and_legacy_upgrades(w):
    a, b = security.make_hash(GOOD), security.make_hash(GOOD)
    assert a != b and a.startswith("pbkdf2_sha256$")
    assert security.verify("x", GOOD, a) == (True, False)
    assert security.verify("x", "wrong", a)[0] is False
    old = accounts.get("aarav", with_hash=True)["password_hash"]
    assert not old.startswith("pbkdf2_sha256$")                      # seeded legacy demo hash
    assert auth.authenticate("aarav", PW["aarav"])
    new = accounts.get("aarav", with_hash=True)["password_hash"]
    assert new.startswith("pbkdf2_sha256$")                          # upgraded at sign-in
    assert auth.authenticate("aarav", PW["aarav"])


def test_lockout_after_repeated_failures_and_admin_unlock(w):
    for _ in range(security.max_failures()):
        u, msg = auth.sign_in("aarav", "wrong")
        assert u is None and msg == auth.FAIL                        # never says whether the user exists
    u, msg = auth.sign_in("aarav", PW["aarav"])
    assert u is None and "Too many wrong passwords" in msg           # right password refused while locked
    with pytest.raises(services.Denied):
        services.unlock_account(w, auth.authenticate("tl1", PW["tl1"]), "aarav", "x")
    services.unlock_account(w, auth.authenticate("admin", PW["admin"]), "aarav", "called the agent")
    assert auth.authenticate("aarav", PW["aarav"])
    rows = [r for r in comms.audit_rows("security") if r["record"] == "user aarav"]
    assert any(r["action"] == "Sign-in failed" for r in rows) and any(r["action"] == "Signed in" for r in rows)
    assert all(PW["aarav"] not in str(r) and "wrong" not in r["after"] for r in rows)


def test_unknown_user_same_message(w):
    assert auth.sign_in("nobody", "x") == (None, auth.FAIL)


def test_admin_reset_forces_change_and_policy(w):
    admin = auth.authenticate("admin", PW["admin"])
    with pytest.raises(ValueError):
        services.reset_password(w, admin, "aarav", "short", "forgot")
    services.reset_password(w, admin, "aarav", "Temp-Reset-2026!", "forgot password")
    u = auth.authenticate("aarav", "Temp-Reset-2026!")
    assert u["must_change"]
    with pytest.raises(ValueError):
        services.change_own_password(w, u, "wrong", GOOD)
    services.change_own_password(w, u, "Temp-Reset-2026!", GOOD)
    u2 = auth.authenticate("aarav", GOOD)
    assert u2 and not u2.get("must_change")
    with pytest.raises(services.Denied):
        services.reset_password(w, auth.authenticate("hr", PW["hr"]), "aarav", GOOD, "x")


def test_new_accounts_start_with_temporary_password(w):
    admin = auth.authenticate("admin", PW["admin"])
    with pytest.raises(accounts.AccountError):
        services.create_account(w, admin, "newone", {"name": "New One", "employee_id": "E-990", "role": "analyst", "contact_pref": "in_app"},
                                "longpassword")
    services.create_account(w, admin, "newone", {"name": "New One", "employee_id": "E-990", "role": "analyst", "contact_pref": "in_app"},
                            "Temp-Pass-2026!")
    assert auth.authenticate("newone", "Temp-Pass-2026!")["must_change"]


def test_idle_timeout(monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_IDLE_MINUTES", "30")
    now = datetime(2026, 10, 20, 10, 0)
    assert not security.idle_expired((now - timedelta(minutes=29)).isoformat(), now)
    assert security.idle_expired((now - timedelta(minutes=31)).isoformat(), now)
    assert not security.idle_expired(None, now)


def test_sso_maps_only_to_one_active_account_by_email(w):
    accs = [{"username": "a", "active": True, "email": "Asha@Corp.com"},
            {"username": "b", "active": False, "email": "old@corp.com"},
            {"username": "c", "active": True, "email": "dup@corp.com"},
            {"username": "d", "active": True, "email": "dup@corp.com"}]
    assert security.sso_account("asha@corp.com", accs)["username"] == "a"
    assert security.sso_account("old@corp.com", accs) is None          # inactive
    assert security.sso_account("dup@corp.com", accs) is None          # ambiguous: never guess
    assert security.sso_account("", accs) is None
    u, msg = auth.sso_sign_in("stranger@corp.com")
    assert u is None and "not linked" in msg


def test_sso_only_mode_refuses_passwords_except_break_glass(w, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_AUTH", "sso")
    assert auth.authenticate("aarav", PW["aarav"]) is None
    monkeypatch.setenv("SHIFTSENSE_BREAKGLASS", "admin")
    assert auth.authenticate("admin", PW["admin"])
