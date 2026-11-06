"""``log_launch_event``: the launcher's structured JSON log lines, one named event per line."""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)


def log_launch_event(event: str, **fields: object) -> None:
    log.info(json.dumps({"event": event, **fields}, default=str))
