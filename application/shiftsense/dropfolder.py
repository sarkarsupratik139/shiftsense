"""Drop folder: HR and telephony exports placed in an agreed folder are checked and applied like an upload.

    data/inbox/employees/   data/inbox/contracts/   data/inbox/leave/   data/inbox/volumes/ ...
    python -m shiftsense.dropfolder run --as <username>      (on the server, e.g. from a nightly scheduler)

From the app ("Process drop folder") files are applied at once. A server run only STAGES them as queued change
sets: the running app applies queued sets on its next refresh (the workspace is only ever written by the app, so
a night-time run can never be overwritten by, or overwrite, what people are doing on screen).

Each file goes through the same converter, checks and change engine as an upload, with the permissions of the
named, active account (HR for people data, Analyst for volumes). A file with usable rows is submitted; held-back
rows are written next to it as a rows-to-fix file. Processed files move to inbox/processed/, unusable ones to
inbox/failed/. Nothing is deleted. Every run is audited.
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from .schemas import DOMAINS

SUFFIXES = (".csv", ".tsv", ".txt", ".xlsx", ".xls", ".xlsm")


def inbox() -> Path:
    from .store import db_path
    return db_path().parent / "inbox"


FEEDS = ("leave_balances",)          # HR-system feeds handled outside the reference-data engine


def waiting() -> list[tuple[str, Path]]:
    out = []
    for dom in (*DOMAINS, *FEEDS):
        d = inbox() / dom
        if d.exists():
            out += [(dom, f) for f in sorted(d.iterdir()) if f.is_file() and f.suffix.lower() in SUFFIXES]
    return out


def _move(f: Path, where: str) -> Path:
    dest = inbox() / where / f"{datetime.now():%Y%m%d-%H%M%S}-{f.parent.name}-{f.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(f), dest)
    return dest


def process(w, user, *, effective_from: str | None = None, stage: bool = False) -> list[dict]:
    """Process every waiting file the user may submit. Returns one result per file. stage=True queues instead
    of applying (server runs)."""
    from . import convert, services
    results = []
    for dom, f in waiting():
        r = {"file": f"{dom}/{f.name}", "domain": dom}
        if dom == "leave_balances":                # entered as unverified HR records; the workspace is not touched
            try:
                out = services.import_leave_balances(w, user, f.read_bytes(), f.name)
            except services.Denied as e:
                r.update(status="skipped", detail=str(e))
                results.append(r)
                continue
            r.update(status="entered (unverified)" if out["entered"] else "failed", rows=len(out["entered"]),
                     held=len(out["problems"]), detail="; ".join(f"row {i}: {m}" for i, m in out["problems"][:5]),
                     moved_to=_move(f, "processed" if out["entered"] else "failed").name)
            results.append(r)
            continue
        try:
            res, _ = services.convert_data(w, user, dom, data=f.read_bytes(), filename=f.name)
        except services.Denied as e:
            r.update(status="skipped", detail=str(e))              # someone else's kind of data: left in place
            results.append(r)
            continue
        if res.held:
            fix = inbox() / "failed" / f"{datetime.now():%Y%m%d-%H%M%S}-rows-to-fix-{dom}-{f.stem}.xlsx"
            fix.parent.mkdir(parents=True, exist_ok=True)
            fix.write_bytes(convert.rows_to_fix(res))
            r["rows_to_fix"] = fix.name
        blocked = not res.rows or (res.errors and not DOMAINS[dom].partial_ok)
        if blocked:
            r.update(status="failed", detail=(res.errors[0].message if res.errors else "no usable rows"),
                     moved_to=_move(f, "failed").name)
            results.append(r)
            continue
        if stage:
            cid = services.queue_change(w, user, dom, res.rows, source=f"drop folder: {f.name}",
                                        converted_by=res.converted_by, effective_from=effective_from)
            r.update(status="queued", change=cid, rows=len(res.rows), held=len(res.held),
                     moved_to=_move(f, "processed").name)
            results.append(r)
            continue
        try:
            out = services.submit_change(w, user, dom, res.rows, effective_from=effective_from,
                                         reason=f"drop folder file {f.name}", source=f"drop folder: {f.name}",
                                         converted_by=res.converted_by,
                                         issues=[{"row": i.row, "field": i.field, "level": i.level, "message": i.message}
                                                 for i in res.issues])
            r.update(status=out["status"], change=out["id"], rows=len(res.rows), held=len(res.held),
                     moved_to=_move(f, "processed").name)
        except ValueError as e:
            r.update(status="failed", detail=str(e), moved_to=_move(f, "failed" if "Nothing to change" not in str(e)
                                                                    else "processed").name)
        results.append(r)
    return results


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] != "run" or argv[1] != "--as":
        print(__doc__)
        return 2
    from . import accounts, auth, services, store, workflow
    from .config import load_policy
    acc = accounts.get(argv[2])
    if not acc or not acc["active"]:
        print("No active account with that username.")
        return 1
    user = {**{k: v for k, v in acc.items() if v not in ("", None)}, "username": argv[2],
            "role_label": auth.config()["roles"][acc["role"]]["label"]}
    w = store.load()
    if w is None:
        print("No live workspace yet.")
        return 1
    w = workflow.upgrade(w)                              # read only: nothing in the workspace is written here
    results = process(w, user, stage=True)
    for r in results:
        print(r)
    services._audit(w, user, "Drop folder staged (server run)", "inbox", "", f"{len(results)} file(s)", "",
                    "queued for the app")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
