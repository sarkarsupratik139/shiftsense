"""Employee profile photos: an optional employee-data field with secure storage.

* Storage: a separate table (never inside the versioned employee record). The employee record carries only a
  reference ``photo_ref = "photo:<id>"``. Images are small, re-encoded copies: 256 px and 64 px squares, JPEG,
  metadata (EXIF, location) removed.
* Validation on the server: real image content (decoded by Pillow, not trusted from the file name or browser type),
  JPEG / PNG / WebP only, size limit (SHIFTSENSE_PHOTO_MAX_MB, default 5), minimum 64 x 64 px, decompression-bomb
  guard. Anything else is refused with a clear message.
* No external fetching: a URL in an import is never downloaded; the person shows initials until a file is supplied.
* Removing a photo marks it removed (kept for the audit trail, no automatic deletion: retention is not decided);
  the employee record and history are untouched.
* No face recognition or any analysis of the image; photos are never sent to an AI service.
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
from datetime import datetime
from functools import lru_cache

from . import db

db.migration(301, "employee photos", """
CREATE TABLE IF NOT EXISTS employee_photos (
    id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL, status TEXT NOT NULL, sha256 TEXT NOT NULL,
    image BLOB NOT NULL, thumb BLOB NOT NULL, source TEXT, uploaded_by TEXT, uploaded_at TEXT, removed_by TEXT,
    removed_at TEXT);
CREATE INDEX IF NOT EXISTS ix_photo_emp ON employee_photos (employee_id, status);
""")

FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
SIZES = {"image": 256, "thumb": 64}
_ready: dict = {}


class PhotoError(ValueError):
    pass


def ensure():
    if not _ready.get(db.key()):
        db.migrate()
        _ready[db.key()] = True


def max_bytes() -> int:
    return int(float(os.environ.get("SHIFTSENSE_PHOTO_MAX_MB", "5")) * 1024 * 1024)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def process(data: bytes) -> dict:
    """Validate an uploaded file and produce the stored copies. Raises PhotoError with a person-readable reason."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not data:
        raise PhotoError("The file is empty.")
    if len(data) > max_bytes():
        raise PhotoError(f"The file is larger than {max_bytes() / (1024 * 1024):g} MB.")
    Image.MAX_IMAGE_PIXELS = 40_000_000                         # decompression-bomb guard
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()                                      # structural check of the whole file
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError) as e:
        raise PhotoError("This file is not a readable image (use JPEG, PNG or WebP).") from e
    if fmt not in FORMATS:
        raise PhotoError(f"{fmt or 'This'} images are not accepted. Use JPEG, PNG or WebP.")
    if min(img.size) < 64:
        raise PhotoError("The image is too small; use at least 64 × 64 pixels.")
    img = ImageOps.exif_transpose(img).convert("RGB")           # respect camera rotation, then drop all metadata
    side = min(img.size)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    square = img.crop((left, top, left + side, top + side))     # centred square crop
    out = {}
    for name, px in SIZES.items():
        buf = io.BytesIO()
        square.resize((px, px), Image.LANCZOS).save(buf, "JPEG", quality=85, optimize=True)
        out[name] = buf.getvalue()
    out["sha256"] = hashlib.sha256(out["image"]).hexdigest()
    return out


def save(employee_id: str, data: bytes, by: str, source: str = "upload") -> dict:
    """Store a new photo; the previous one (if any) is marked replaced. Returns the new record (no image bytes)."""
    ensure()
    p = process(data)
    with db.connect() as con:
        con.execute("UPDATE employee_photos SET status='replaced', removed_by=?, removed_at=? WHERE employee_id=? "
                    "AND status='active'", (by, _now(), employee_id))
        cur = con.execute("INSERT INTO employee_photos (employee_id, status, sha256, image, thumb, source, "
                          "uploaded_by, uploaded_at) VALUES (?,?,?,?,?,?,?,?)",
                          (employee_id, "active", p["sha256"], p["image"], p["thumb"], source, by, _now()))
        pid = cur.lastrowid
    return {"id": pid, "employee_id": employee_id, "sha256": p["sha256"], "ref": f"photo:{pid}"}


def remove(employee_id: str, by: str) -> bool:
    ensure()
    with db.connect() as con:
        cur = con.execute("UPDATE employee_photos SET status='removed', removed_by=?, removed_at=? WHERE "
                          "employee_id=? AND status='active'", (by, _now(), employee_id))
        return cur.rowcount > 0


def info(employee_id: str) -> dict | None:
    """The active photo's metadata (no bytes), or None."""
    ensure()
    with db.connect() as con:
        r = con.execute("SELECT id, sha256, source, uploaded_by, uploaded_at FROM employee_photos WHERE "
                        "employee_id=? AND status='active' ORDER BY id DESC LIMIT 1", (employee_id,)).fetchone()
    return dict(zip(["id", "sha256", "source", "uploaded_by", "uploaded_at"], r)) if r else None


def active_map() -> dict[str, str]:
    """employee_id -> sha256 of the active photo (one query for a whole directory)."""
    ensure()
    with db.connect() as con:
        rows = con.execute("SELECT employee_id, sha256 FROM employee_photos WHERE status='active'").fetchall()
    return {r[0]: r[1] for r in rows}


@lru_cache(maxsize=2048)
def _uri(sha: str, size: str, key: str) -> str | None:
    col = "thumb" if size == "thumb" else "image"
    with db.connect() as con:
        r = con.execute(f"SELECT {col} FROM employee_photos WHERE sha256=? ORDER BY id DESC LIMIT 1", (sha,)).fetchone()
    if not r or not r[0]:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(bytes(r[0])).decode()


def data_uri(employee_id: str, size: str = "thumb", shas: dict | None = None) -> str | None:
    """A data: URI for display, or None (the caller shows initials). Cached by content hash."""
    sha = (shas if shas is not None else active_map()).get(employee_id)
    if not sha:
        return None
    try:
        return _uri(sha, size, db.key())
    except Exception:                                             # a broken record never breaks the page
        return None


# ------------------------------------------------------------------ imports
PHOTO_DIR = "photos"


def resolve_ref(value: str, inbox_dir) -> tuple[str | None, str | None]:
    """An import's photo reference -> (path of a file in the drop folder's photos/ sub-folder, warning).
    URLs are never fetched. A missing or unsafe name gives a warning; the employee shows initials."""
    v = (value or "").strip()
    if not v:
        return None, None
    low = v.lower()
    if low.startswith(("http://", "https://", "ftp://", "//")):
        return None, ("External photo links are not downloaded. Put the file in the drop folder's photos/ folder "
                      "or upload it on the employee's profile; initials are shown until then.")
    name = v
    if "/" in v or "\\" in v or name in (".", "..") or name.startswith("."):
        return None, f"Photo reference '{v}' is not a plain file name; initials are shown."
    path = inbox_dir / PHOTO_DIR / name
    if not path.is_file():
        return None, f"Photo file '{name}' was not found in the photos/ folder; initials are shown."
    return str(path), None
