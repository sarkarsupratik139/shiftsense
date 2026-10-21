"""Email over a real SMTP conversation: a small local SMTP server on a socket (no mocks of smtplib)."""
import socketserver
import threading

import pytest

from shiftsense import auth, mailer, services, staffing
from shiftsense import workflow as wf

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}


class _SMTP(socketserver.StreamRequestHandler):
    received: list = []
    refuse = False

    def _say(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self._say("220 localhost test SMTP")
        data, in_data, rcpt = [], False, []
        while True:
            line = self.rfile.readline().decode(errors="replace")
            if not line:
                return
            if in_data:
                if line.rstrip("\r\n") == ".":
                    in_data = False
                    _SMTP.received.append({"rcpt": rcpt[:], "data": "".join(data)})
                    self._say("250 OK queued")
                else:
                    data.append(line)
                continue
            cmd = line.strip().upper()
            if cmd.startswith(("EHLO", "HELO")):
                self._say("250 localhost")
            elif cmd.startswith("MAIL FROM"):
                self._say("250 OK")
            elif cmd.startswith("RCPT TO"):
                if _SMTP.refuse:
                    self._say("550 No such user")
                else:
                    rcpt.append(line.strip()[8:])
                    self._say("250 OK")
            elif cmd == "DATA":
                in_data = True
                self._say("354 End data with <CR><LF>.<CR><LF>")
            elif cmd == "QUIT":
                self._say("221 Bye")
                return
            else:
                self._say("250 OK")


@pytest.fixture
def relay(monkeypatch, tmp_path):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _SMTP)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _SMTP.received, _SMTP.refuse = [], False
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "mail.db"))
    for k, v in {"SHIFTSENSE_EMAIL": "smtp", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(srv.server_address[1]),
                 "SHIFTSENSE_EMAIL_FROM": "shiftsense@example.test", "SMTP_TLS": "off"}.items():
        monkeypatch.setenv(k, v)
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def w(policy, data, req, optimized_plan):
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def test_test_email_travels_over_smtp(relay, w):
    r = services.send_test_email(w, auth.authenticate("admin", PW["admin"]), "ops.lead@example.test")
    assert r["status"] == "sent" and "Message-ID" in r["detail"]
    got = _SMTP.received[-1]
    assert "ops.lead@example.test" in got["rcpt"][0]
    assert "Subject: ShiftSense: test email" in got["data"] and "Message-ID:" in got["data"]
    with pytest.raises(services.Denied):
        services.send_test_email(w, auth.authenticate("tl1", PW["tl1"]), "x@example.test")


def test_relay_refusal_is_recorded_as_failed(relay, w):
    _SMTP.refuse = True
    r = services.send_test_email(w, auth.authenticate("admin", PW["admin"]), "nobody@example.test")
    assert r["status"] == "failed" and "refused" in r["detail"]


def test_unreachable_relay_fails_cleanly(monkeypatch, tmp_path, w):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "m2.db"))
    for k, v in {"SHIFTSENSE_EMAIL": "smtp", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": "1",
                 "SHIFTSENSE_EMAIL_FROM": "s@example.test", "SMTP_TLS": "off"}.items():
        monkeypatch.setenv(k, v)
    r = services.send_test_email(w, auth.authenticate("admin", PW["admin"]), "a@example.test")
    assert r["status"] == "failed" and "Mail relay error" in r["detail"]
