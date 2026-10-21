"""The data layer against a real PostgreSQL server. Runs only when SHIFTSENSE_TEST_PG_URL points to a database
it may wipe (its public schema is dropped and recreated); otherwise skipped."""
import os

import pytest

PG = os.environ.get("SHIFTSENSE_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG, reason="no test PostgreSQL server (set SHIFTSENSE_TEST_PG_URL)")


@pytest.fixture
def pg(monkeypatch, tmp_path):
    import psycopg
    with psycopg.connect(PG, autocommit=True) as c:
        c.execute("DROP SCHEMA public CASCADE")
        c.execute("CREATE SCHEMA public")
    monkeypatch.setenv("SHIFTSENSE_DATABASE_URL", PG)
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "ws.db"))
    from shiftsense import db, refdata, store
    for cache in (refdata._ready, store._ready, db._setup_done):     # the schema was just wiped
        cache.clear()
    yield
    for cache in (refdata._ready, store._ready, db._setup_done):
        cache.clear()


def test_full_data_flow_on_postgres(pg, policy, data, req, optimized_plan):
    from shiftsense import auth, db, dataflow, dropfolder, exports, refdata, services
    from shiftsense import workflow as wf
    from shiftsense.constraints import check
    pw = {u: r["demo_password"] for u, r in auth.config()["users"].items()}
    U = lambda n: auth.authenticate(n, pw[n])
    assert db.dialect() == "postgres" and "@" in db.describe() and "•••" in db.describe()
    w0 = wf.new_world(policy, data, req, optimized_plan["roster"])
    assert services.load_base_pack(w0, U("analyst"))["employees"] == 50
    with db.connect() as con:                                   # really in PostgreSQL
        assert con.execute("SELECT COUNT(*) FROM ref_records WHERE domain='employees'").fetchone()[0] == 50
        assert {r[0] for r in con.execute("SELECT version FROM schema_migrations").fetchall()} >= {101}
    w = services.rebuild_from_reference(w0, U("analyst"))
    assert check(w.policy, w.data["agents"], w.data["leave"], w.roster) == []
    a = w.roster[w.roster.day == w.today + 1].iloc[0]
    emp = refdata.current("employees")[a.agent_id]
    other = next(q for q in w.policy["queues"] if q != a.queue)
    out = services.submit_change(w, U("hr"), "employees", [{**emp, "primary": other, "secondary": []}])
    assert out["status"] == "applied" and out["outcome"]["alerts"]
    assert [h["version"] for h in refdata.history("employees", a.agent_id)] == [1, 2]
    c = refdata.current("contracts")["Full-time 40h"]
    out = services.submit_change(w, U("hr"), "contracts", [{**c, "max_nights_per_week": 3}], reason="pg test")
    assert out["status"] == "pending"
    assert services.decide_change(w, U("ops"), out["id"], True, "ok")["status"] == "applied"
    assert dataflow.get_set(out["id"])["decided_by"].startswith("Anil")
    services.remember_mapping("sig1", "leave", {"Emp": "employee_id"})
    services.remember_mapping("sig1", "leave", {"Emp ID": "employee_id"})   # replace, no duplicate key error
    assert services.saved_mapping("sig1") == {"Emp ID": "employee_id"}
    d = dropfolder.inbox() / "leave"
    d.mkdir(parents=True, exist_ok=True)
    (d / "x.csv").write_text("Emp ID,From\nA03,15/10/2026\n")
    staged = dropfolder.process(w, U("hr"), stage=True)
    assert staged[0]["status"] == "queued" and services.apply_queued(w)
    assert exports.change_log(services.data_changes(w, U("admin")))[:2] == b"PK"


def test_postgres_backup_restore_drill_and_migration(pg, tmp_path, policy, data, req, optimized_plan, monkeypatch):
    from shiftsense import auth, backup, comms, db, services, store
    from shiftsense import workflow as wf
    pw = {u: r["demo_password"] for u, r in auth.config()["users"].items()}
    U = lambda n: auth.authenticate(n, pw[n])
    w = wf.new_world(policy, data, req, optimized_plan["roster"])
    store.save(w)
    services.post_message(w, U("aarav"), "agent:A01", "before backup")
    b = backup.create(tmp_path / "bk", keep=2)
    assert b.suffix == ".zip" and backup.verify(b) == []
    r = backup.drill(b)
    assert r["problems"] == [] and r["audit"] == "verified"
    with db.connect() as con:                                   # the drill's temporary schema is gone
        assert not con.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name LIKE 'drill_%'").fetchone()
    services.post_message(w, U("aarav"), "agent:A01", "after backup")
    backup.restore(b)
    msgs = [m["body"] for m in comms.thread(w.run_id, "agent:A01")]
    assert "before backup" in msgs and "after backup" not in msgs and comms.verify_audit() == (True, None)
    for _ in range(3):
        backup.create(tmp_path / "bk", keep=2)
    assert len(list((tmp_path / "bk").glob("shiftsense-pg-*.zip"))) == 2

    # SQLite -> PostgreSQL: every row and id carried, phone values not carried, audit chain intact
    from shiftsense import migrate_to_pg
    lite = tmp_path / "old.db"
    monkeypatch.delenv("SHIFTSENSE_DATABASE_URL")
    monkeypatch.setenv("SHIFTSENSE_DB", str(lite))
    w2 = wf.new_world(policy, data, req, optimized_plan["roster"])
    store.save(w2)
    services.post_message(w2, U("aarav"), "agent:A01", "from sqlite")
    with db.connect() as con:
        con.execute("UPDATE accounts SET phone='+15555550101', sms_consent=1 WHERE username='aarav'")
    monkeypatch.setenv("SHIFTSENSE_DATABASE_URL", PG)
    monkeypatch.setenv("SHIFTSENSE_PG_SCHEMA", "migr_test")
    monkeypatch.delenv("SHIFTSENSE_PG_SCHEMA_FROM_DB", raising=False)
    try:
        rep = migrate_to_pg.migrate(lite, replace=True)
        assert rep["audit"] == "verified" and not rep["mismatch"]
        assert store.load().run_id == w2.run_id
        assert any(m["body"] == "from sqlite" for m in comms.thread(w2.run_id, "agent:A01"))
        with db.connect() as con:
            assert con.execute("SELECT phone, sms_consent FROM accounts WHERE username='aarav'").fetchone() == ("", 0)
        with pytest.raises(RuntimeError, match="already holds data"):
            migrate_to_pg.migrate(lite)
    finally:
        import psycopg
        with psycopg.connect(PG, autocommit=True) as c:
            c.execute("DROP SCHEMA IF EXISTS migr_test CASCADE")
