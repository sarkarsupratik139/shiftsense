"""Application log (data/shiftsense.log, rotating). Records failures and refused actions for diagnosis.

Never logged: passwords, tokens, phone numbers, API keys or message bodies. Users appear by username only.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG = None


def log_path() -> Path:
    from .store import db_path
    return Path(os.environ.get("SHIFTSENSE_LOG", db_path().parent / "shiftsense.log"))


def get() -> logging.Logger:
    global _LOG
    if _LOG is None:
        _LOG = logging.getLogger("shiftsense")
        _LOG.setLevel(logging.INFO)
        _LOG.propagate = False
        try:
            path = log_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            h = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            _LOG.addHandler(h)
        except OSError:                       # read-only disk: keep running, log to stderr instead
            _LOG.addHandler(logging.StreamHandler())
    return _LOG
