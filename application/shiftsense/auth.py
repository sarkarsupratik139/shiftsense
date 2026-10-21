"""Demo authentication and role-based access control.

Accounts live in config/users.yaml with salted PBKDF2 password hashes. They are FAKE demo
credentials for the PoC; real deployments would delegate to the customer's SSO (out of scope).
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import os
from functools import lru_cache
from pathlib import Path

import yaml

USERS_PATH = Path(__file__).resolve().parent.parent / "config" / "users.yaml"
ITERATIONS = 100_000


def hash_password(username: str, password: str) -> str:
    """Legacy (fixed-salt) format, kept only to read the demo hashes in config/users.yaml. New passwords use
    security.make_hash (random salt, 600,000 iterations)."""
    salt = f"shiftsense:{username}".encode()
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS).hex()


@lru_cache(maxsize=2)
def _load(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def config(path: str | Path = USERS_PATH) -> dict:
    return copy.deepcopy(_load(str(path)))


FAIL = "Invalid username or password."


def _session_user(rec: dict, cfg: dict) -> dict:
    user = {k: v for k, v in rec.items() if k not in ("password_hash", "version", "created_at", "created_by",
                                                     "updated_at", "updated_by", "extra_permissions", "failed_logins",
                                                     "locked_until", "must_change", "password_changed_at",
                                                     "theme_pref")
            and v not in ("", None)}
    user["username"] = rec["username"]
    user["role_label"] = cfg["roles"][rec["role"]]["label"]
    return user


def _audit(username: str, what: str, status: str):
    from . import comms
    try:
        comms.audit("security", username or "(blank)", "sign-in", what, f"user {username or '(blank)'}", "", "",
                    "", status, "")
    except Exception:                                   # never block sign-in on an audit write problem
        pass


def sign_in(username: str, password: str, path: str | Path = USERS_PATH) -> tuple[dict | None, str]:
    """(user, message). Locks the account after repeated wrong passwords; every attempt is audited. The message
    never says whether the username exists."""
    from . import accounts, hr, security
    cfg = config(path)
    username = (username or "").strip().lower()
    if security.auth_mode() == "sso" and username not in {u.strip() for u in
                                                          os.environ.get("SHIFTSENSE_BREAKGLASS", "").split(",") if u}:
        return None, "Sign in with your company account (single sign-on)."
    rec = accounts.get(username, with_hash=True)
    if not rec or not rec["active"] or rec["role"] not in cfg["roles"]:
        security.verify(username, password, None)
        _audit(username, "Sign-in refused", "unknown or inactive account")
        return None, FAIL
    if security.locked_until(rec):
        _audit(username, "Sign-in refused", "locked")
        return None, f"Too many wrong passwords. Try again after {security.locked_until(rec):%H:%M}, or ask an " \
                     "administrator to unlock the account."
    ok, upgrade = security.verify(username, password, rec["password_hash"])
    if not ok:
        lock = accounts.record_failure(username)
        _audit(username, "Sign-in failed", "locked" if lock else "wrong password")
        return None, FAIL
    if hr.access_ended(username):                       # separation effective: no new sign-in either
        _audit(username, "Sign-in refused", "separation effective")
        return None, FAIL
    accounts.record_success(username, security.make_hash(password) if upgrade else None)
    _audit(username, "Signed in", "password")
    user = _session_user(rec, cfg)
    if rec.get("must_change"):
        user["must_change"] = True
    return user, ""


def authenticate(username: str, password: str, path: str | Path = USERS_PATH) -> dict | None:
    """Return the session user (without the hash) if the credentials match and the account is active."""
    return sign_in(username, password, path)[0]


def sso_sign_in(email: str | None, path: str | Path = USERS_PATH) -> tuple[dict | None, str]:
    """Single sign-on: the identity provider has proved the email; the ShiftSense account decides the rest."""
    from . import accounts, hr, security
    cfg = config(path)
    rec = security.sso_account(email, accounts.all_accounts())
    if rec is None or rec["role"] not in cfg["roles"] or hr.access_ended(rec["username"]):
        _audit((email or "").split("@")[0], "SSO sign-in refused", "no single active account with this email")
        return None, "Your company account is not linked to an active ShiftSense account. Ask an administrator."
    accounts.record_success(rec["username"])
    _audit(rec["username"], "Signed in", "single sign-on")
    return _session_user(rec, cfg), ""


def current(user: dict | None) -> dict | None:
    """The signed-in user re-checked against the accounts store: None if deactivated; the role is the
    stored one, so a role change or deactivation takes effect on the next action, not the next login."""
    from . import accounts
    if not user:
        return None
    rec = accounts.get(user.get("username", ""))
    if rec is None:
        return user if user.get("username") is None else None
    if not rec["active"]:
        return None
    from . import hr
    if hr.access_ended(rec["username"]):            # separation reached its effective time (business timezone)
        return None
    if (rec["role"] != user.get("role") or rec.get("team", "") != user.get("team", "")
            or (rec.get("agent_id") or None) != (user.get("agent_id") or None)):
        return {**user, "role": rec["role"], "team": rec.get("team") or None, "agent_id": rec.get("agent_id") or None}
    return user


def role(user: dict | None, path: str | Path = USERS_PATH) -> dict:
    if not user:
        return {"label": "Signed out", "pages": [], "permissions": []}
    return config(path)["roles"][user["role"]]


def can_view(user: dict | None, page: str) -> bool:
    return page in role(user)["pages"]


def can(user: dict | None, permission: str) -> bool:
    """Role permissions from config; extra permissions (Senior HR) are read from the STORED account every time,
    never from the session, and only count for the role they belong to."""
    if not user:
        return False
    if permission in role(user)["permissions"]:
        return True
    from . import accounts
    if permission in accounts.EXTRA_PERMISSIONS:
        rec = accounts.get(user.get("username", ""))
        return bool(rec and rec["active"] and rec["role"] == accounts.EXTRA_PERMISSIONS[permission] == user.get("role")
                    and permission in rec["extra_permissions"])
    return False


def visible_requests(user: dict | None, requests: list[dict], agent_team: dict[str, str]) -> list[dict]:
    """Team leads see only their team's requests; agents only their own; ops/admin all."""
    if not user:
        return []
    if can(user, "all_teams"):
        return requests
    if user["role"] == "team_lead":
        return [r for r in requests if agent_team.get(r["agent_id"]) == user.get("team")]
    if user["role"] == "agent":
        return [r for r in requests if r["agent_id"] == user.get("agent_id")]
    return []
