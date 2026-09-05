"""Trials ledger — (fingerprint, split, objective_hash, arm_hash) resume + spend (spec §D5).

A paid optimize run is manual, bounded, and interruptible: the ledger is the
crash-safe record that lets a rerun skip candidates it already scored. Every
``record`` appends one JSON line AND updates an in-memory index keyed by
``(fingerprint, split, objective_hash, arm_hash)`` — the fitness (paired-agent)
and the orchestrator consult ``lookup`` before spending, so an already-scored
candidate returns its recorded score instead of paying for it twice.

The split is part of the key so a train score never masks a holdout score for
the same artifact: the same candidate is scored on both sides at different
prices, and each must resume independently. ``arm_hash`` is there for the same
reason one level up (run-contract design §6): every arm of a run scores the
same candidate on the same splits under the same objective, so without it the
second arm would resume the first arm's score for free.

ONE file per run, shared by every arm — deliberately, because
``total_spend()`` is what the orchestrator's budget guard reads. Fragmenting
the ledger per arm would silently multiply ``budget.max_usd`` by the number of
arms; the budget is a run-level pool, never a per-arm licence.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One recorded fitness evaluation (spec §D5).

    Mirrors ``FitnessReport`` minus ``n_samples`` plus the ``(fingerprint,
    split, objective_hash, arm_hash)`` key: the tuple that pins WHICH candidate
    on WHICH split under WHICH objective in WHICH arm produced ``score`` at
    ``cost_usd``. ``objective_hash`` is ``None`` for fitnesses with a fixed
    in-code objective — legacy lines (written before the field existed) parse
    as ``None`` and keep resuming those fitnesses byte-for-byte (spec §3.6).
    ``arm_hash`` follows the same rule with ``""`` (the single implicit arm)
    as its legacy value.
    """

    fingerprint: str
    split: str
    score: float
    components: Mapping[str, float]
    cost_usd: float
    objective_hash: str | None = None
    arm_hash: str = ""


@dataclass(slots=True)
class TrialsLedger:
    """Append-only JSONL ledger with a ``(fingerprint, split)`` resume index.

    Load-on-init reads any existing file line-wise; a corrupt line is skipped
    with a ``log.warning`` rather than aborting the whole run — a half-written
    trailing line from a killed process must not lose the scores before it.
    """

    path: Path
    _index: dict[tuple[str, str], LedgerEntry] = field(default_factory=dict, init=False)

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
            entry = self._parse_line(stripped)
            if entry is not None:
                self._index[_key_of(entry)] = entry

    def _parse_line(self, line: str) -> LedgerEntry | None:
        """Decode one JSONL line to a ``LedgerEntry``; ``None`` on a corrupt line."""
        try:
            record = json.loads(line)
            return LedgerEntry(
                fingerprint=record["fingerprint"],
                split=record["split"],
                score=record["score"],
                components=record["components"],
                cost_usd=record["cost_usd"],
                # Legacy lines predate the field; .get keeps them resumable
                # for fitnesses whose objective_hash() is None (spec §3.6).
                objective_hash=record.get("objective_hash"),
                arm_hash=record.get("arm_hash", ""),
            )
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            log.warning("trials ledger: skipping corrupt line in %s: %s", self.path, exc)
            return None

    def record(
        self,
        *,
        fingerprint: str,
        split: str,
        score: float,
        components: Mapping[str, float],
        cost_usd: float,
        objective_hash: str | None = None,
        arm_hash: str = "",
    ) -> LedgerEntry:
        """Append one entry to the JSONL file and update the resume index."""
        entry = LedgerEntry(
            fingerprint=fingerprint,
            split=split,
            score=score,
            components=components,
            cost_usd=cost_usd,
            objective_hash=objective_hash,
            arm_hash=arm_hash,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_as_record(entry)) + "\n")
        self._index[_key_of(entry)] = entry
        return entry

    def lookup(
        self,
        *,
        fingerprint: str,
        split: str,
        objective_hash: str | None = None,
        arm_hash: str = "",
    ) -> LedgerEntry | None:
        """Return the entry for the full four-part key, or ``None``.

        The hash must match exactly: a stored ``None`` (legacy or hashless
        fitness) only answers a ``None`` request, and a hashed line only its
        own hash — the same candidate under a different objective never
        falsely resumes (spec AC-12). ``arm_hash`` behaves the same with ``""``
        as the single-implicit-arm value every pre-``arms:`` line carries.
        """
        return self._index.get((fingerprint, split, objective_hash, arm_hash))

    def total_spend(self) -> float:
        """Sum ``cost_usd`` across every recorded entry — the run's spend to date."""
        return sum(entry.cost_usd for entry in self._index.values())


def _key_of(entry: LedgerEntry) -> tuple[str, str, str | None, str]:
    return (entry.fingerprint, entry.split, entry.objective_hash, entry.arm_hash)


def _as_record(entry: LedgerEntry) -> dict[str, object]:
    """Flatten a ``LedgerEntry`` to the JSONL line shape (round-trips ``_parse_line``)."""
    record: dict[str, object] = {
        "fingerprint": entry.fingerprint,
        "split": entry.split,
        "score": entry.score,
        "components": dict(entry.components),
        "cost_usd": entry.cost_usd,
    }
    # WHY conditional: hashless / single-implicit-arm lines keep the exact
    # legacy byte shape, so a ledger written by this version replays under the
    # previous reader too.
    if entry.objective_hash is not None:
        record["objective_hash"] = entry.objective_hash
    if entry.arm_hash:
        record["arm_hash"] = entry.arm_hash
    return record
