"""Pilot operations: scheduled jobs timing, health JSON and alert e-mail, backup age check."""
import json
from datetime import datetime

from shiftsense import health, jobs, mailer


def test_next_backup_is_tonight_or_tomorrow():
    assert jobs.next_backup(datetime(2026, 10, 20, 0, 10)) == datetime(2026, 10, 20, 1, 30)
    assert jobs.next_backup(datetime(2026, 10, 20, 1, 30)) == datetime(2026, 10, 21, 1, 30)


def test_health_json_and_backup_check(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "h.db"))
    code = health.main(["--json"])
    out = json.loads(capsys.readouterr().out)
    names = {c["name"] for c in out["checks"]}
    assert {"Latest backup", "Sign-in", "Password hashing", "Audit chain"} <= names
    assert code in (0, 1) and out["healthy"] == (code == 0)


def test_alert_email_only_for_essential_failures(monkeypatch):
    sent = []
    monkeypatch.setattr(mailer, "send", lambda *a, **k: sent.append(k) or {"status": "sent"})
    monkeypatch.setenv("SHIFTSENSE_ALERT_EMAIL", "oncall@example.test")
    assert health.alert([("Disk space", True, "ok", True), ("Email", False, "x", False)]) is None
    assert health.alert([("Audit chain", False, "broken at row 7", True)]) == "sent"
    assert sent[0]["address"] == "oncall@example.test" and "Audit chain" in sent[0]["body"]
    monkeypatch.delenv("SHIFTSENSE_ALERT_EMAIL")
    assert health.alert([("Audit chain", False, "broken", True)]) is None


def test_nightly_job_writes_verified_backup(tmp_path, monkeypatch, policy, data, req, optimized_plan):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "j.db"))
    monkeypatch.chdir(tmp_path)
    from shiftsense import store, workflow
    store.save(workflow.new_world(policy, data, req, optimized_plan["roster"]))
    jobs.nightly()
    log = (tmp_path / "data" / "jobs.log").read_text()
    assert "backup ok" in log and "drill" in log
