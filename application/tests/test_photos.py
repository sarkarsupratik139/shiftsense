"""Employee profile photos: validation, storage, replace/remove, permissions, initials fallback, imports."""
import io

import pytest
from PIL import Image

import ui_kit
from shiftsense import auth, comms, dropfolder, photos, refdata, services, staffing
from shiftsense import workflow as wf
from shiftsense.services import Denied

PW = {u: r["demo_password"] for u, r in auth.config()["users"].items()}
U = lambda n: auth.authenticate(n, PW[n])      # noqa: E731


def img(fmt="JPEG", size=(300, 200), color=(40, 90, 140)):
    b = io.BytesIO()
    Image.new("RGB", size, color).save(b, fmt)
    return b.getvalue()


@pytest.fixture
def w(policy, data, req, optimized_plan, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_DB", str(tmp_path / "ph.db"))
    return wf.new_world(policy, data, req, optimized_plan["roster"], fc=staffing.forecast(policy, data["history"]))


def test_process_validates_crops_and_strips(tmp_path):
    p = photos.process(img("PNG", (400, 250)))
    out = Image.open(io.BytesIO(p["image"]))
    assert out.format == "JPEG" and out.size == (256, 256) and not out.info.get("exif")
    assert Image.open(io.BytesIO(p["thumb"])).size == (64, 64)
    for bad, why in ((b"", "empty"), (b"not an image at all", "not a readable image"),
                     (img("JPEG", (40, 40)), "too small"), (img("GIF"), "not accepted")):
        with pytest.raises(photos.PhotoError, match=why):
            photos.process(bad)


def test_size_limit(monkeypatch):
    monkeypatch.setenv("SHIFTSENSE_PHOTO_MAX_MB", "0.001")
    with pytest.raises(photos.PhotoError, match="larger than"):
        import os
        noisy = io.BytesIO()
        Image.frombytes("RGB", (300, 300), os.urandom(300 * 300 * 3)).save(noisy, "PNG")
        photos.process(noisy.getvalue())


def test_hr_upload_replace_remove_with_audit(w):
    with pytest.raises(Denied):
        services.set_employee_photo(w, U("tl1"), "A03", img())
    with pytest.raises(Denied):
        services.set_employee_photo(w, U("admin"), "A03", img())
    r1 = services.set_employee_photo(w, U("hr"), "A03", img(color=(10, 10, 10)))
    r2 = services.set_employee_photo(w, U("hr"), "A03", img(color=(200, 10, 10)))
    assert r1["sha256"] != r2["sha256"] and photos.info("A03")["id"] == r2["id"]
    assert services.remove_employee_photo(w, U("hr"), "A03")
    assert photos.info("A03") is None
    acts = [r["action"] for r in comms.audit_rows(w.run_id)]
    assert {"Profile photo added", "Profile photo replaced", "Profile photo removed"} <= set(acts)
    with pytest.raises(ValueError):
        services.set_employee_photo(w, U("hr"), "ZZ99", img())


def test_who_may_see_a_photo(w):
    services.set_employee_photo(w, U("hr"), "A01", img())
    team = dict(zip(w.data["agents"].agent_id, w.data["agents"].team))["A01"]
    tl_same = next(u for u, r in auth.config()["users"].items() if r["role"] == "team_lead" and r.get("team") == team)
    tl_other = next(u for u, r in auth.config()["users"].items() if r["role"] == "team_lead" and r.get("team") != team)
    assert "A01" in services.photo_uris(w, U("aarav"), ["A01"])        # own photo
    assert "A01" in services.photo_uris(w, U(tl_same), ["A01"])        # own team lead
    assert "A01" not in services.photo_uris(w, U(tl_other), ["A01"])   # other team lead: initials
    assert "A01" not in services.photo_uris(w, U("priya"), ["A01"])    # colleague agent: initials
    for who in ("ops", "analyst", "hr", "admin"):
        assert "A01" in services.photo_uris(w, U(who), ["A01"])
    assert services.photo_uris(w, U("hr"), ["A02"]) == {}              # no photo: initials


def test_avatar_fallbacks():
    assert "<img" in ui_kit.avatar("Rohit Verma", "data:image/jpeg;base64,AAAA")
    assert ">RV<" in ui_kit.avatar("Rohit Verma", None)
    assert ">RV<" in ui_kit.avatar("Rohit Verma", "https://evil.example/x.jpg")   # outside URLs never rendered
    assert "<svg" in ui_kit.avatar("", None) and "<svg" in ui_kit.avatar(None, None)


def test_import_photo_references_never_block_the_record(w):
    services.load_base_pack(w, U("analyst"))
    d = dropfolder.inbox() / "photos"
    d.mkdir(parents=True, exist_ok=True)
    (d / "A04.jpg").write_bytes(img())
    (d / "A05.jpg").write_bytes(b"garbage")
    emps = refdata.current("employees")
    rows = [{**emps["A04"], "photo_ref": "A04.jpg"}, {**emps["A05"], "photo_ref": "A05.jpg"},
            {**emps["A06"], "photo_ref": "https://cdn.example.com/a06.png"},
            {**emps["A07"], "photo_ref": "../../etc/passwd"}, {**emps["A08"], "photo_ref": "missing.png"}]
    out = services.submit_change(w, U("hr"), "employees", rows, reason="photos from HRIS")
    assert out["status"] in ("applied", "scheduled", "approved")
    cur = refdata.current("employees", "2099-01-01")
    assert (cur["A04"]["photo_ref"] or "").startswith("photo:") and photos.info("A04")
    for e in ("A05", "A06", "A07", "A08"):
        assert not cur[e].get("photo_ref") and photos.info(e) is None          # initials, record still imported
    assert photos.resolve_ref("https://x.example/a.png", dropfolder.inbox())[1].startswith("External photo links")
    assert "not a plain file name" in photos.resolve_ref("../a.jpg", dropfolder.inbox())[1]
