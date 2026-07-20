"""Resumable campaign queue — one JSONL ledger, last-write-wins state (ADR 0014).

The repo's fourth use of the resumable-JSONL-ledger idiom (after the trials
ledger, the trajectory ledger, and the agent-track run record). One work item is
a ``(cell, instance_id)`` pair; each state transition appends one line and
updates an in-memory index keyed by the pair, so the LATEST line wins. A crashed
campaign reloads the ledger and resumes: :meth:`CampaignLedger.pending` returns
only the items not yet terminal, so completed rollouts are never re-run (R6 — a
re-spend would break the cost ceiling's meaning).

**Completed = a terminal DONE line** (Phase 2's "trace present + metrics
computable" — the runner records DONE only after both hold, ADR 0014 item 4).
``EXCLUDED`` (R8, infra-failed twice) is also terminal — excluded from
aggregates but never retried. ``QUEUED`` / ``RUNNING`` / ``INFRA_RETRY`` are
non-terminal, so a mid-flight crash leaves the item pending and it is picked up
on resume.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

log = logging.getLogger(__name__)

LEDGER_FILENAME = "queue.jsonl"


class WorkState(StrEnum):
    """The lifecycle states of one ``(cell, instance)`` work item (ADR 0014).

    ``DONE`` and ``EXCLUDED`` are terminal (never re-dispatched); the rest are
    non-terminal, so a crash mid-``RUNNING`` leaves the item pending on resume.
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    INFRA_RETRY = "infra_retry"
    EXCLUDED = "excluded"


_TERMINAL_STATES = frozenset({WorkState.DONE, WorkState.EXCLUDED})


@dataclass(frozen=True, slots=True)
class WorkItem:
    """One unit of campaign work: a cell run over one instance."""

    cell: str
    instance_id: str

    @property
    def key(self) -> tuple[str, str]:
        return (self.cell, self.instance_id)


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    """One appended state transition. ``attempt`` counts dispatch tries (R8
    retry-once uses it); ``trajectory_id`` / ``cost_usd`` / ``detail`` are
    filled as they become known (``DONE`` carries the trajectory + cost)."""

    cell: str
    instance_id: str
    state: WorkState
    attempt: int = 0
    trajectory_id: str | None = None
    cost_usd: float = 0.0
    detail: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.cell, self.instance_id)

    def to_record(self) -> dict[str, object]:
        return {
            "cell": self.cell,
            "instance_id": self.instance_id,
            "state": self.state.value,
            "attempt": self.attempt,
            "trajectory_id": self.trajectory_id,
            "cost_usd": self.cost_usd,
            "detail": self.detail,
        }


@dataclass(slots=True)
class CampaignLedger:
    """Append-only JSONL queue with a ``(cell, instance)`` last-write-wins index.

    Load-on-init rebuilds the index from any existing file; a corrupt trailing
    line (killed mid-write) is skipped with a warning rather than aborting, so
    the transitions before it survive.
    """

    path: Path
    _index: dict[tuple[str, str], LedgerRecord] = field(default_factory=dict, init=False)
    # Running spend accumulated over EVERY appended line, not just the latest per
    # item: a retried infra rollout's cost still counts against the ceiling (R8),
    # so both the INFRA_RETRY line and the final EXCLUDED/DONE line contribute.
    _spend: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            record = self._parse_line(stripped)
            if record is not None:
                self._index[record.key] = record
                self._spend += record.cost_usd

    def _parse_line(self, line: str) -> LedgerRecord | None:
        try:
            raw = json.loads(line)
            return LedgerRecord(
                cell=raw["cell"],
                instance_id=raw["instance_id"],
                state=WorkState(raw["state"]),
                attempt=int(raw.get("attempt", 0)),
                trajectory_id=raw.get("trajectory_id"),
                cost_usd=float(raw.get("cost_usd", 0.0)),
                detail=str(raw.get("detail", "")),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            log.warning("campaign ledger: skipping corrupt line in %s: %s", self.path, exc)
            return None

    def record(self, record: LedgerRecord) -> LedgerRecord:
        """Append one transition line and update the resume index."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record.to_record()) + "\n")
        self._index[record.key] = record
        self._spend += record.cost_usd
        return record

    def latest(self, item: WorkItem) -> LedgerRecord | None:
        """The most recent transition for ``item``, or ``None`` if never seen."""
        return self._index.get(item.key)

    def is_completed(self, item: WorkItem) -> bool:
        """True iff ``item``'s latest state is terminal (``DONE`` or ``EXCLUDED``)."""
        record = self._index.get(item.key)
        return record is not None and record.state in _TERMINAL_STATES

    def attempt_count(self, item: WorkItem) -> int:
        """The recorded attempt number for ``item`` (0 if never dispatched)."""
        record = self._index.get(item.key)
        return record.attempt if record is not None else 0

    def pending(self, work: Sequence[WorkItem]) -> list[WorkItem]:
        """The subset of ``work`` not yet terminal — the resume set (order-preserving)."""
        return [item for item in work if not self.is_completed(item)]

    def total_spend(self) -> float:
        """Accumulated ``cost_usd`` over EVERY appended line — the spend to date (R6/R8).

        Summed across all attempts, not just the latest per item, so a retried
        infra rollout's cost counts against the ceiling (ADR 0016 R8). Each
        rollout attempt records its cost exactly once (on its DONE / EXCLUDED /
        INFRA_RETRY line; QUEUED / RUNNING lines carry 0), so no double-count.
        """
        return self._spend


def build_work(cells: Iterable[str], instances: Sequence[str]) -> list[WorkItem]:
    """The full campaign work list: every cell crossed with every instance.

    Deterministic order (cells outer, instances inner) so resume and dispatch
    walk the same sequence across runs.
    """
    return [WorkItem(cell=cell, instance_id=iid) for cell in cells for iid in instances]
