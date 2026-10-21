"""Sign-in security: password storage, password policy, lockout, idle timeout, single sign-on mapping.

* Passwords are stored as ``pbkdf2_sha256$<iterations>$<random salt>$<hash>`` (OWASP: 600,000 iterations for
  PBKDF2-SHA256). Older hashes (fixed salt, 100,000 iterations) still verify and are upgraded at the next sign-in.
* Password policy: at least 12 characters, at least 3 of 4 character kinds, not containing the username or name,
  not a common password. Checked when a password is set, never retro-actively.
* Lockout: 5 wrong passwords in a row lock the account for 15 minutes (configurable). Every sign-in attempt is
  written to the append-only audit trail (username only; never the password).
* Idle timeout: a session with no activity for 30 minutes (configurable) must sign in again.
* Single sign-on (OIDC, Streamlit's built-in st.login): the identity provider proves who the person is; ShiftSense
  only lets them in if an ACTIVE account has that exact email address. Roles and teams always come from the
  ShiftSense account, never from the identity provider. No account is created automatically.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

DEFAULT_ITERATIONS = 600_000
LEGACY_ITERATIONS = 100_000

COMMON = {"password", "password1", "password123", "123456789012", "qwertyuiop", "welcome123", "letmein123",
          "shiftsense", "shiftsense123", "changeme", "changeme123", "admin12345", "iloveyou123", "p@ssw0rd",
          "p@ssword123", "qwerty123456", "abc123456789", "welcome@123", "password@123", "india@123"}


def iterations() -> int:
    return int(os.environ.get("SHIFTSENSE_PBKDF2_ITERATIONS", DEFAULT_ITERATIONS))


def max_failures() -> int:
    return int(os.environ.get("SHIFTSENSE_LOCKOUT_ATTEMPTS", 5))


def lockout_minutes() -> int:
    return int(os.environ.get("SHIFTSENSE_LOCKOUT_MINUTES", 15))


def idle_minutes() -> int:
    return int(os.environ.get("SHIFTSENSE_IDLE_MINUTES", 30))


def auth_mode() -> str:
    """'password' (default), 'sso' (single sign-on only) or 'both'."""
    m = os.environ.get("SHIFTSENSE_AUTH", "password").strip().lower()
    return m if m in ("password", "sso", "both") else "password"


# ------------------------------------------------------------------ hashing
def legacy_hash(username: str, password: str) -> str:
    salt = f"shiftsense:{username}".encode()
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, LEGACY_ITERATIONS).hex()


def make_hash(password: str, n: int | None = None) -> str:
    n = n or iterations()
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), n).hex()
    return f"pbkdf2_sha256${n}${salt}${h}"


def verify(username: str, password: str, stored: str | None) -> tuple[bool, bool]:
    """(matches, should_upgrade). Constant-time comparison; legacy hashes still verify."""
    if not stored:
        make_hash(password or "x", 1000)                      # similar work, no early exit
        return False, False
    if stored.startswith("pbkdf2_sha256$"):
        _, n, salt, h = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", (password or "").encode(), bytes.fromhex(salt), int(n)).hex()
        ok = hmac.compare_digest(got, h)
        return ok, ok and int(n) < iterations()
    ok = hmac.compare_digest(legacy_hash(username, password or ""), stored)
    return ok, ok


# ------------------------------------------------------------------ policy
def problems(password: str, username: str = "", name: str = "") -> list[str]:
    p = password or ""
    out = []
    if len(p) < 12:
        out.append("at least 12 characters")
    kinds = sum([any(c.islower() for c in p), any(c.isupper() for c in p), any(c.isdigit() for c in p),
                 any(not c.isalnum() for c in p)])
    if kinds < 3:
        out.append("at least 3 of: lower-case, upper-case, digit, symbol")
    low = p.lower()
    if username and len(username) >= 3 and username.lower() in low:
        out.append("must not contain the username")
    for part in (name or "").lower().split():
        if len(part) >= 4 and part in low:
            out.append("must not contain your name")
            break
    if low in COMMON or low.rstrip("!@#$%0123456789") in COMMON:
        out.append("too common")
    return out


def check(password: str, username: str = "", name: str = ""):
    bad = problems(password, username, name)
    if bad:
        raise ValueError("Password rules: " + "; ".join(bad) + ".")


# ------------------------------------------------------------------ lockout
def locked_until(rec: dict) -> datetime | None:
    v = rec.get("locked_until")
    if not v:
        return None
    t = datetime.fromisoformat(v)
    return t if t > datetime.now() else None


def after_failure(failed: int) -> tuple[int, str | None]:
    """New failure count and lock time (ISO) after one more wrong password."""
    failed += 1
    if failed >= max_failures():
        return 0, (datetime.now() + timedelta(minutes=lockout_minutes())).isoformat(timespec="seconds")
    return failed, None


# ------------------------------------------------------------------ idle timeout
def idle_expired(last_seen: str | None, now: datetime | None = None) -> bool:
    if not last_seen:
        return False
    now = now or datetime.now()
    return now - datetime.fromisoformat(last_seen) > timedelta(minutes=idle_minutes())


# ------------------------------------------------------------------ single sign-on
def sso_account(email: str | None, accounts_list: list[dict]) -> dict | None:
    """The single ACTIVE account whose email matches the identity provider's email (case-insensitive).
    None when there is no match or the match is ambiguous: never guess, never create."""
    e = (email or "").strip().lower()
    if not e or "@" not in e:
        return None
    hits = [a for a in accounts_list if a["active"] and (a.get("email") or "").strip().lower() == e]
    return hits[0] if len(hits) == 1 else None
