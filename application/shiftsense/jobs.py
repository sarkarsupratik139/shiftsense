"""Scheduled jobs for the pilot container (`python -m shiftsense.jobs`): a small loop, no cron needed.

* every night at 01:30 (server local time): verified backup keeping the newest 14, then a restore drill;
* every 15 minutes: health check with an alert email to SHIFTSENSE_ALERT_EMAIL when an essential check fails.
Each run is logged to data/jobs.log. A failing job never stops the loop.
"""
from __future__ import annotations

import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

BACKUP_AT = (1, 30)
HEALTH_EVERY = timedelta(minutes=15)


def _log(msg: str):
    p = Path("data/jobs.log")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")


def nightly():
    from . import backup
    try:
        path = backup.create(keep=14)
        _log(f"backup ok {Path(path).name}")
        r = backup.drill()
        _log(f"drill {'ok' if not r.get('problems') else 'PROBLEMS ' + str(r['problems'])}")
    except Exception as e:                                          # noqa: BLE001
        _log(f"backup FAILED {type(e).__name__}: {e}")
        traceback.print_exc()


def health():
    from . import health as h
    rows = h.run()
    failed = [r[0] for r in rows if not r[1] and r[3]]
    st = h.alert(rows)
    _log("health ok" if not failed else f"health FAIL {failed}" + (f" · alert {st}" if st else ""))


def next_backup(now: datetime) -> datetime:
    t = now.replace(hour=BACKUP_AT[0], minute=BACKUP_AT[1], second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def main():
    nb, nh = next_backup(datetime.now()), datetime.now()
    _log("jobs started")
    while True:
        now = datetime.now()
        if now >= nh:
            health()
            nh = now + HEALTH_EVERY
        if now >= nb:
            nightly()
            nb = next_backup(now)
        time.sleep(30)


if __name__ == "__main__":
    main()
