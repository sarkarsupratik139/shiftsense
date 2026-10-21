"""In-app messaging (primary) and optional email (secondary): delivery honesty, duplicates, retries, verification,
permissions, attendance validation and history. SMS is retired."""
import pathlib
import re

import pytest

from shiftsense import accounts, attendance, auth, comms, mailer, services, staffing
from shiftsense import workflow as wf

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}
ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "m.db"))
    for k in ("SHIFTSENSE_EMAIL", "SMTP_HOST", "SHIFTSENSE_EMAIL_FROM"):
        monkeypatch.delenv(k, raising=False)
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def U(name):
    return auth.authenticate(name, PW[name])


def day_for(w, aid):
    return next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == aid) & (w.roster.day == x)).any())


class Fake:
    """Stands in for the SMTP relay; records what would have been sent."""
    def __init__(self, *results):
        self.sent, self.results = [], list(results) or [mailer.Result("sent", "Accepted by the mail relay.")]

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


@pytest.fixture
def smtp(monkeypatch):
    fake = Fake()
    monkeypatch.setattr(mailer, "transport", lambda: fake)
    return fake


def verified(w, username, addr):
    a = accounts.get(username)
    services.update_account(w, U("admin"), username, {"email": addr, "contact_pref": "email"}, a["version"])
    assert accounts.get(username)["email_verified"] is False
    assert accounts.mark_email_verified(username, addr)


# ---------------------------------------------------------------- in-app is primary and works without email
def test_reminder_works_without_email_configured(w):
    d = day_for(w, "A01")
    out = services.send_attendance_reminder(w, U("tl1"), "A01", d)
    assert out["status"] == "in_inbox" and out["notice_id"]
    assert out["email"]["status"] == "not_sent"                         # in-app preference
    n = comms.related_notice(w.run_id, "checkin", out["id"])
    assert n["recipient"] == {"agent": "A01"} and n["read_by"] == []    # in inbox, not opened
    view = services.reminder_view(w, attendance.log(w.run_id, {"A01"})[0])
    assert view == {"in_app": "In inbox", "opened": "Not opened yet", "checked_in": "—", "email": "Email not sent "
                    "(Recipient prefers in-app only.)"}
    services.open_reminder(w, U("aarav"), out["id"])
    assert services.reminder_view(w, attendance.log(w.run_id, {"A01"})[0])["opened"] == "Opened"
    assert ("A01", d) not in w.attendance                               # opened is not present
    w.now_t = out["opens_t"]
    services.check_in_self(w, U("aarav"), out["id"])
    assert w.attendance[("A01", d)] == "checkin"
    assert services.reminder_view(w, attendance.log(w.run_id, {"A01"})[0])["checked_in"] != "—"


def test_email_configured_but_unverified_address_gets_nothing(w, smtp):
    a = accounts.get("aarav")
    services.update_account(w, U("admin"), "aarav", {"email": "aarav.k@example.com", "contact_pref": "email"},
                            a["version"])
    out = services.send_attendance_reminder(w, U("tl1"), "A01", day_for(w, "A01"))
    assert out["email"]["status"] == "not_sent" and "not verified" in out["email"]["detail"] and smtp.sent == []


def test_email_preference_with_verified_address_and_not_configured(w):
    verified(w, "aarav", "aarav.k@example.com")
    out = services.send_attendance_reminder(w, U("tl1"), "A01", day_for(w, "A01"))
    assert out["status"] == "in_inbox" and out["email"]["status"] == "not_configured"


def test_email_sent_means_accepted_never_delivered(w, smtp):
    verified(w, "aarav", "aarav.k@example.com")
    out = services.send_attendance_reminder(w, U("tl1"), "A01", day_for(w, "A01"))
    assert out["email"]["status"] == "sent" and len(smtp.sent) == 1
    to, subject, body = smtp.sent[0]
    assert to == "aarav.k@example.com" and "Check in" in subject and "checkin=" not in body
    assert all(e["status"] != "delivered" for e in mailer.log(w.run_id))
    assert mailer.log(w.run_id)[0]["recipient"] == "a•••@example.com"   # stored masked


def test_email_failure_is_recorded_and_retry_is_bounded_and_deduplicated(w, monkeypatch):
    fake = Fake(mailer.Result("failed", "Mail relay error: TimeoutError"))
    monkeypatch.setattr(mailer, "transport", lambda: fake)
    a = accounts.get("aarav")
    acct = {**a, "email": "aarav.k@example.com", "email_verified": True, "contact_pref": "email"}
    key = f"{w.run_id}|reminder|1|aarav"
    r1 = mailer.send(w.run_id, key, kind="reminder", ref="1", account=acct, subject="s", body="b")
    assert r1["status"] == "failed" and "TimeoutError" in r1["detail"]
    fake.results = [mailer.Result("sent", "ok")]
    r2 = mailer.send(w.run_id, key, kind="reminder", ref="1", account=acct, subject="s", body="b")   # retry
    assert r2["status"] == "sent" and r2["attempts"] == 2
    r3 = mailer.send(w.run_id, key, kind="reminder", ref="1", account=acct, subject="s", body="b")   # repeat
    assert r3["duplicate"] and len(fake.sent) == 2 and len(mailer.log(w.run_id)) == 1
    fake.results = [mailer.Result("failed", "x")]
    k2 = f"{w.run_id}|reminder|2|aarav"
    for _ in range(mailer.MAX_ATTEMPTS + 2):
        mailer.send(w.run_id, k2, kind="reminder", ref="2", account=acct, subject="s", body="b")
    assert mailer.log(w.run_id, ref="2")[0]["attempts"] == mailer.MAX_ATTEMPTS


def test_request_decision_and_escalation_emails(w, smtp):
    verified(w, "aarav", "aarav.k@example.com")
    verified(w, "ops", "ops.desk@example.com")
    rd = next(x for x in range(w.first_day, 14) if ((w.roster.agent_id == "A01") & (w.roster.day == x)).any()
              and wf.leave_check(w, "A01", x)["status"] != "Green")
    r = services.submit_leave(w, U("aarav"), rd)
    services.decide_request(w, U("tl1"), r["id"], False, "peak day")
    assert [s[0] for s in smtp.sent] == ["aarav.k@example.com"] and "rejected" in smtp.sent[0][1].lower()
    t = dict(zip(w.data["agents"].agent_id, w.data["agents"].team))
    a = next(x for x in w.roster[w.roster.day == w.first_day].agent_id if t[x] == "TL-1" and x not in ("A01", "A06"))
    inc = services.report_absence(w, U("tl1"), [a], w.first_day)
    services.decide_incident(w, U("tl1"), inc.id, "Escalate", "no one free")
    assert smtp.sent[-1][0] == "ops.desk@example.com" and "Escalation" in smtp.sent[-1][1]


def test_scenario_never_emails(w, smtp):
    verified(w, "aarav", "aarav.k@example.com")
    sc = services.load_demo_story(w, U("analyst"))
    out = services.send_attendance_reminder(sc, U("tl1"), "A01", day_for(sc, "A01"))
    assert out["email"]["status"] == "not_sent" and smtp.sent == []


# ---------------------------------------------------------------- verification
def test_email_verification_code_flow(w, smtp):
    a = accounts.get("sneha")
    services.update_account(w, U("admin"), "sneha", {"email": "sneha.g@example.com", "contact_pref": "email"},
                            a["version"])
    services.request_email_code(w, U("sneha"))
    code = re.search(r"\b(\d{6})\b", smtp.sent[-1][2]).group(1)
    with pytest.raises(ValueError, match="not correct"):
        services.confirm_email_code(w, U("sneha"), "000000" if code != "000000" else "111111")
    assert services.confirm_email_code(w, U("sneha"), code)
    assert accounts.get("sneha")["email_verified"] is True
    a = accounts.get("sneha")                                         # a changed address must be verified again
    services.update_account(w, U("admin"), "sneha", {"email": "sneha.new@example.com"}, a["version"])
    assert accounts.get("sneha")["email_verified"] is False
    assert code not in str(comms.audit_rows(w.run_id)) and code not in str(mailer.log(w.run_id))


def test_verification_needs_configured_email(w):
    a = accounts.get("sneha")
    services.update_account(w, U("admin"), "sneha", {"email": "sneha.g@example.com"}, a["version"])
    with pytest.raises(ValueError, match="not configured"):
        services.request_email_code(w, U("sneha"))


# ---------------------------------------------------------------- permissions and attendance validation
def test_reminder_and_checkin_permissions(w):
    d6 = day_for(w, "A06")
    for who in ("aarav", "ops", "analyst", "admin", "tl2"):
        with pytest.raises(services.Denied):
            services.send_attendance_reminder(w, U(who), "A06", d6)
    out = services.send_attendance_reminder(w, U("tl1"), "A06", d6)
    w.now_t = out["opens_t"]
    with pytest.raises(ValueError):
        services.check_in_self(w, U("aarav"), out["id"])                # someone else's reminder
    for who in ("tl1", "ops", "admin", "analyst"):
        with pytest.raises(services.Denied):
            services.check_in_self(w, U(who), out["id"])
    with pytest.raises(services.Denied):                                # forged identity
        services.check_in_self(w, dict(U("aarav"), agent_id="A06"), out["id"])
    assert ("A06", d6) not in w.attendance


def test_checkin_refused_when_attendance_already_recorded(w):
    d = day_for(w, "A06")
    out = services.send_attendance_reminder(w, U("tl1"), "A06", d)
    w.now_t = max(out["opens_t"], d * 48 + 40)
    w.attendance[("A06", d)] = "confirmed"                              # e.g. team lead confirmed presence
    with pytest.raises(ValueError, match="already recorded"):
        services.check_in_self(w, U("sneha"), out["id"])


def test_resend_gives_one_checkin_per_shift(w):
    d = day_for(w, "A01")
    services.send_attendance_reminder(w, U("tl1"), "A01", d)
    second = services.send_attendance_reminder(w, U("tl1"), "A01", d, resend=True)
    assert [r["id"] for r in attendance.open_for(w.run_id, "A01", w.now_t)] == [second["id"]]


def test_messages_stay_private(w):
    services.post_message(w, U("aarav"), "agent:A01", "my private note")
    for who in ("tl2", "ops", "analyst", "admin", "sneha"):
        assert not services.can_access_thread(w, U(who), "agent:A01")
        assert not any("my private note" in e["Event"] for e in services.history(w, U(who)))
    assert any("my private note" in e["Event"] for e in services.history(w, U("tl1")))


def test_history_shows_reminder_states_and_emails_in_scope(w, smtp):
    verified(w, "aarav", "aarav.k@example.com")
    out = services.send_attendance_reminder(w, U("tl1"), "A01", day_for(w, "A01"))
    services.open_reminder(w, U("aarav"), out["id"])
    mine = services.history(w, U("aarav"))
    assert any(e["Event"] == "Reminder (in-app)" and "In inbox · opened" in e["After"] for e in mine)
    assert any(e["Type"] == "Email" and e["After"] == "Email accepted by relay" for e in mine)
    assert any(e["Type"] == "Email" for e in services.history(w, U("tl1")))
    assert not any(e["Type"] == "Email" for e in services.history(w, U("tl2")) + services.history(w, U("analyst")))
    assert "aarav.k@example.com" not in services.history_export(w, U("admin"))


# ---------------------------------------------------------------- SMS is gone
def test_no_active_workflow_depends_on_sms():
    code = "\n".join(p.read_text() for p in [*(ROOT / "shiftsense").glob("*.py"), *(ROOT / "views").glob("*.py"),
                                             ROOT / "app.py", ROOT / "ui_common.py"])
    for banned in ("twilio", "TWILIO_", "SHIFTSENSE_SMS_MODE", "check_in_with_token", "consented to sms", "mask_phone"):
        assert banned.lower() not in code.lower(), banned
    assert not (ROOT / "views" / "checkin.py").exists()
    assert "sms" not in accounts.CONTACT
