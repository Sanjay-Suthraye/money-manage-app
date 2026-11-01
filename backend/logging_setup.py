"""File logging, rotated, inside the project only.

Everything lands in settings.LOG_DIR (data/logs/, gitignored with the rest of
data/). Nothing is written outside the project folder, and nothing is left to the
supervisor's stdout redirection:

  * systemd (deploy/money-manager.service) configures no log destination, so
    stdout goes to the journal, whose rotation we don't control on a 1 GB box and
    which `journalctl --vacuum` erases.
  * Cost accounting has to survive months — "what did quick-add cost in August?"
    should be one jq line, not an archaeology project.
  * Rotation is not hypothetical here: the LaunchAgent once accumulated 380 KB of
    one repeated traceback into an unrotated file at a stale path.

Two logger trees, both file-only (propagate=False) so nothing is duplicated into
the launchd/journal stream:

  "copilot" -> copilot.jsonl   one JSON object per quick-add, for cost + hit-rate
  root      -> app.log         ordinary app/uvicorn logging
"""

import json
import logging
from logging.handlers import RotatingFileHandler
from typing import Any

from backend import settings

_configured = False


def _handler(filename: str, fmt: str) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        settings.LOG_DIR / filename,
        maxBytes=settings.LOG_MAX_BYTES,
        backupCount=settings.LOG_BACKUPS,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(fmt))
    return handler


def init_logging() -> None:
    """Idempotent: safe under uvicorn --reload, which imports the app twice."""
    global _configured
    if _configured:
        return
    settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

    # The record IS the JSON — no prefix, so the file is valid JSONL for jq.
    copilot = logging.getLogger("copilot")
    copilot.setLevel(logging.INFO)
    copilot.propagate = False
    copilot.handlers = [_handler("copilot.jsonl", "%(message)s")]

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Replace rather than append so a reload can't double every line.
    root.handlers = [_handler("app.log", "%(asctime)s %(levelname)s %(name)s %(message)s")]

    _configured = True


def log_event(event: str, **fields: Any) -> None:
    """Write one JSONL line to copilot.jsonl.

    sort_keys keeps the file diffable and stable across releases. Callers must not
    pass the user's raw text unless settings.COPILOT_LOG_TEXT is on — see
    backend/services/copilot.py, which owns that decision.
    """
    payload = {"event": event, **fields}
    logging.getLogger("copilot").info(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    )
