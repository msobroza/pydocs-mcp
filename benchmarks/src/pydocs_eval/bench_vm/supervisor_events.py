"""The supervisor's log: one JSON object per line, with named fields, on stderr."""

from __future__ import annotations

import json
import sys


def log_supervisor_event(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, default=str), file=sys.stderr, flush=True)
