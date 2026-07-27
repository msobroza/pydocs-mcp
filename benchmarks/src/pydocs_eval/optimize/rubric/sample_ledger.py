"""Sample-level rubric ledger — per-sample JSONL resume sidecar (spec §3.4.5).

A separate append-only sidecar next to the trials ledger (never an agent-track
ledger-line extension — that line shape is a stable resume contract for the
paired track). One line per sample, keyed ``(fingerprint, split, task_id,
objective_hash, arm_hash)`` — the fourth component is why resume is safe under
a *configurable* rubric (the same candidate under a different objective never
falsely resumes) and the fifth is why it is safe under ``arms:`` (two arms of
one run share a candidate, a split, a task AND an objective while measuring
different things — the shipped arm pair differs only in ``tool_names``).
Contract mirrors ``TrialsLedger``: append-only, ``lookup`` makes already-scored
samples free on rerun, corrupt lines are skipped with a warning,
``total_spend()`` sums ``cost_usd``.

Because a different arm now re-runs and APPENDS instead of resuming, ONE file
legitimately holds several arms' rows — the ``_ArmLedger`` precedent from the
external track. ``total_spend()`` therefore reports the whole file, which is
the run-level pool the budget guard is meant to see, never a per-arm licence.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydocs_eval.optimize.rubric.model import SampleRubricRecord

log = logging.getLogger(__name__)

_Key = tuple[str, str, str, str, str]


@dataclass(slots=True)
class SampleRubricLedger:
    """Append-only JSONL ledger of ``SampleRubricRecord`` lines."""

    path: Path
    _index: dict[_Key, SampleRubricRecord] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self._load()

    def _load(self) -> None:
        """Rebuild the in-memory index from ``path`` (empty when the file is new)."""
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            record = self._parse_line(stripped)
            if record is not None:
                self._index[_key_of(record)] = record

    def _parse_line(self, line: str) -> SampleRubricRecord | None:
        """Decode one JSONL line; ``None`` (with a warning) on a corrupt line."""
        try:
            return SampleRubricRecord(**json.loads(line))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            log.warning("sample ledger: skipping corrupt line in %s: %s", self.path, exc)
            return None

    def record(self, record: SampleRubricRecord) -> SampleRubricRecord:
        """Append one sample record to the JSONL file and update the resume index."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_as_line(record)) + "\n")
        self._index[_key_of(record)] = record
        return record

    def lookup(
        self,
        *,
        fingerprint: str,
        split: str,
        task_id: str,
        objective_hash: str,
        arm_hash: str = "",
    ) -> SampleRubricRecord | None:
        """Return the recorded sample for the full five-part key, or ``None``.

        ``arm_hash`` defaults to the single implicit arm, which is exactly what
        a config without an ``arms:`` block runs and what every legacy line
        carries — so the pre-``arms:`` resume path is unchanged.
        """
        return self._index.get((fingerprint, split, task_id, objective_hash, arm_hash))

    def total_spend(self) -> float:
        """Sum ``cost_usd`` across every recorded sample — the sidecar's spend."""
        return sum(record.cost_usd for record in self._index.values())


def _key_of(record: SampleRubricRecord) -> _Key:
    return (
        record.fingerprint,
        record.split,
        record.task_id,
        record.objective_hash,
        record.arm_hash,
    )


def _as_line(record: SampleRubricRecord) -> dict[str, object]:
    """Flatten a record to the JSONL line shape (round-trips ``_parse_line``)."""
    payload = asdict(record)
    payload["gates"] = dict(record.gates)
    payload["criteria"] = dict(record.criteria)
    payload["tracked"] = dict(record.tracked)
    # WHY conditional (the ``TrialsLedger._as_record`` rule, applied here too):
    # a single-implicit-arm line keeps the exact pre-``arms:`` byte shape, so a
    # sidecar written by this version still parses under the previous reader —
    # which reconstructs with ``SampleRubricRecord(**line)`` and would reject
    # every line as corrupt on an unknown kwarg, re-paying the whole run.
    if not record.arm_hash:
        del payload["arm_hash"]
    return payload
