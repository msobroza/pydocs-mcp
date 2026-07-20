"""Append-only frozen-test touch log — R3 discipline (ADR 0013).

The Pro parquet is *read* for manifest/overlap computation only; ZERO rollouts touch any
frozen-test instance in Phase 3. This append-only ledger records every touch (config hash
+ justification per entry) so the first eventual rollout touch lands in a ledger rather
than a process invented on the spot. Phase 3 writes exactly one ``read_only_manifest``
entry (today's manifest/overlap read) and no ``rollout`` entry — asserted by a test.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# The two touch kinds. ``read_only_manifest`` = reading the parquet for pins/overlap (R3
# permits it); ``rollout`` = executing an agent against a frozen-test instance (forbidden
# this phase). A test asserts Phase 3 contains no ``rollout`` entry.
READ_ONLY_MANIFEST = "read_only_manifest"
ROLLOUT = "rollout"
_ACCESS_TYPES = (READ_ONLY_MANIFEST, ROLLOUT)


@dataclass(frozen=True, slots=True)
class TouchLogEntry:
    """One frozen-test access: what was touched, when, under which config, and why."""

    timestamp: str
    config_hash: str
    access_type: str
    justification: str
    instances_touched: int

    def __post_init__(self) -> None:
        if self.access_type not in _ACCESS_TYPES:
            raise ValueError(
                f"invalid access_type: got {self.access_type!r}, expected one of {_ACCESS_TYPES}"
            )

    def to_json(self) -> str:
        return json.dumps(
            {
                "timestamp": self.timestamp,
                "config_hash": self.config_hash,
                "access_type": self.access_type,
                "justification": self.justification,
                "instances_touched": self.instances_touched,
            },
            sort_keys=True,
        )


def config_hash(config: dict[str, object]) -> str:
    """Stable hex digest of a config dict (e.g. the pin metadata read for the access)."""
    return hashlib.sha256(json.dumps(config, sort_keys=True).encode("utf-8")).hexdigest()


def append_entry(log_path: Path, entry: TouchLogEntry) -> None:
    """Append one entry to the JSONL ledger (never rewrites existing lines)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(entry.to_json() + "\n")


def read_entries(log_path: Path) -> list[TouchLogEntry]:
    """Parse the JSONL ledger into entries (empty list if the file does not exist)."""
    if not log_path.exists():
        return []
    return [_parse(line) for line in log_path.read_text().splitlines() if line.strip()]


def _parse(line: str) -> TouchLogEntry:
    data = json.loads(line)
    return TouchLogEntry(
        timestamp=str(data["timestamp"]),
        config_hash=str(data["config_hash"]),
        access_type=str(data["access_type"]),
        justification=str(data["justification"]),
        instances_touched=int(data["instances_touched"]),
    )


def read_only_entry(config: dict[str, object], justification: str) -> TouchLogEntry:
    """Build the Phase 3 read-only entry (manifest/overlap access, zero instances run)."""
    return TouchLogEntry(
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        config_hash=config_hash(config),
        access_type=READ_ONLY_MANIFEST,
        justification=justification,
        instances_touched=0,
    )
