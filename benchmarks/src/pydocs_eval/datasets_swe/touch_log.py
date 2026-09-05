"""Append-only frozen-test touch log — R3/R4 discipline (ADR 0013, ADR 0020).

The Pro parquet is *read* for manifest/overlap computation only; ZERO rollouts touch any
frozen-test instance in Phase 3. This append-only ledger records every touch (config hash
+ justification per entry) so the first eventual rollout touch lands in a ledger rather
than a process invented on the spot.

Phase-window scoping (ADR 0020 §R4 touch-log continuity): through the Phase 3 window the
log carries exactly one ``read_only_manifest`` entry and NO ``rollout`` entry. Phase 4's
authorized frozen-test sweeps (seed + one) write the first ``rollout`` entries — each
under an owner-authorized ``config_hash`` with a ``justification`` referencing the recorded
authorization. The blanket "zero rollout entries" assertion is therefore superseded by the
Phase 4 pin :func:`unauthorized_rollouts`: an authorized rollout is admitted while any
rollout under an unauthorized config hash (or a rollout missing its justification) still
fails.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# The two touch kinds. ``read_only_manifest`` = reading the parquet for pins/overlap (R3
# permits it); ``rollout`` = executing an agent against a frozen-test instance (forbidden
# in the Phase 3 window; admitted in Phase 4 ONLY under an owner-authorized config hash,
# per :func:`unauthorized_rollouts`).
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


def rollout_entry(
    config: dict[str, object], justification: str, instances_touched: int
) -> TouchLogEntry:
    """Build a Phase 4 authorized frozen-test rollout entry (ADR 0020 §R4).

    ``config`` is the frozen lockfile block whose ``config_hash`` binds the sweep to
    an authorized config; ``justification`` references the recorded owner
    authorization. An entry that lands here is not yet *authorized* — that is the
    reader's check (:func:`unauthorized_rollouts`) against the set of authorized
    config hashes, so a rollout under an unknown config never passes.
    """
    return TouchLogEntry(
        timestamp=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        config_hash=config_hash(config),
        access_type=ROLLOUT,
        justification=justification,
        instances_touched=instances_touched,
    )


def unauthorized_rollouts(
    entries: list[TouchLogEntry], authorized_config_hashes: frozenset[str]
) -> tuple[TouchLogEntry, ...]:
    """The Phase 4 pin: every ``rollout`` entry NOT under an authorized config (ADR 0020).

    Supersedes the Phase 3 "zero rollout entries" assertion. A ``rollout`` entry is
    authorized iff its ``config_hash`` is in ``authorized_config_hashes`` AND it carries
    a non-empty ``justification`` (a rollout with no recorded reason cannot be
    authorized). Returns the offending entries; an empty tuple means the log is clean.
    An empty authorized set reproduces the Phase-3-window rule (any rollout fails).
    """
    return tuple(
        entry
        for entry in entries
        if entry.access_type == ROLLOUT
        and (entry.config_hash not in authorized_config_hashes or not entry.justification.strip())
    )
