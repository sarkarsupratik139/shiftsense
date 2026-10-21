"""HR-system feed of opening balances: flexible headers, nothing guessed, unverified until a second HR user."""
import pytest

from shiftsense import auth, dropfolder, hr, services, staffing
from shiftsense import workflow as wf

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}
U = lambda n: auth.authenticate(n, PW[n])      # noqa: E731

FILE = ("Emp ID,Leave Type,Days Left,As of,Source\n"
        "A03,Annual,12,01/10/2026,HRIS Oct\n"
        "A04,SL,4.5,2026-10-01,HRIS Oct\n"
        "A05,CL,,01/10/2026,HRIS Oct\n"            # balance missing -> held back
        "Z99,Annual,3,01/10/2026,HRIS Oct\n"       # unknown employee -> held back
        "A06,LWP,10,01/10/2026,HRIS Oct\n"         # unpaid: not tracked
        "A07,bonus,2,01/10/2026,HRIS Oct\n").encode()


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "feed.db"))
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def test_import_reads_flexible_headers_and_holds_bad_rows(w):
    res = services.import_leave_balances(w, U("hr"), FILE, "hris.csv")
    assert len(res["entered"]) == 2
    rows = {p[0]: p[1] for p in res["problems"]}
    assert "balance missing" in rows[4] and "unknown employee" in rows[5] and "unknown leave type" in rows[7]
    assert res["skipped"] == [("A06", "unpaid leave is not tracked")]
    assert hr.balance("A03", "annual")["state"] == "unverified"
    assert hr.balance("A04", "sick")["state"] == "unverified"
    r = services.verify_leave_balances(w, U("hr"), res["entered"])
    assert r["verified"] == [] and len(r["refused"]) == 2                 # not by the importer
    r = services.verify_leave_balances(w, U("hrlead"), res["entered"])
    assert len(r["verified"]) == 2
    assert hr.balance("A03", "annual")["available"] == 12 and hr.balance("A04", "sick")["available"] == 4.5


def test_import_needs_hr_and_mandatory_columns(w):
    with pytest.raises(services.Denied):
        services.import_leave_balances(w, U("tl1"), FILE, "hris.csv")
    res = services.import_leave_balances(w, U("hr"), b"Emp ID,Days\nA03,4\n", "x.csv")
    assert res["entered"] == [] and "Missing column" in res["problems"][0][1]


def test_drop_folder_feed(w):
    d = dropfolder.inbox() / "leave_balances"
    d.mkdir(parents=True, exist_ok=True)
    (d / "nightly.csv").write_bytes(FILE)
    res = [r for r in dropfolder.process(w, U("hr")) if r["domain"] == "leave_balances"]
    assert res[0]["status"] == "entered (unverified)" and res[0]["rows"] == 2
    assert not (d / "nightly.csv").exists()                              # moved to processed/, never deleted
    assert any(p.name.endswith("nightly.csv") for p in (dropfolder.inbox() / "processed").iterdir())
