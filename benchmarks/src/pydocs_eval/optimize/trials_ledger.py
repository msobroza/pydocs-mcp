"""Trials ledger — (fingerprint, split) resume + spend accounting (spec §D5).

A paid optimize run is manual, bounded, and interruptible: the ledger is the
crash-safe record that lets a rerun skip candidates it already scored. Every
``record`` appends one JSON line AND updates an in-memory index keyed by
``(fingerprint, split)`` — the fitness (paired-agent) and the orchestrator
consult ``lookup`` before spending, so an already-scored candidate returns its
recorded score instead of paying for it twice.

The split is part of the key so a train score never masks a holdout score for
the same artifact: the same candidate is scored on both sides at different
prices, and each must resume independently.
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
    split)`` key: the tuple that pins WHICH candidate on WHICH split produced
    ``score`` at ``cost_usd``.
    """

    fingerprint: str
    split: str
    score: float
    components: Mapping[str, float]
    cost_usd: float


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
                self._index[(entry.fingerprint, entry.split)] = entry

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
    ) -> LedgerEntry:
        """Append one entry to the JSONL file and update the resume index."""
        entry = LedgerEntry(
            fingerprint=fingerprint,
            split=split,
            score=score,
            components=components,
            cost_usd=cost_usd,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_as_record(entry)) + "\n")
        self._index[(fingerprint, split)] = entry
        return entry

    def lookup(self, *, fingerprint: str, split: str) -> LedgerEntry | None:
        """Return the recorded entry for ``(fingerprint, split)`` or ``None``."""
        return self._index.get((fingerprint, split))

    def total_spend(self) -> float:
        """Sum ``cost_usd`` across every recorded entry — the run's spend to date."""
        return sum(entry.cost_usd for entry in self._index.values())


def _as_record(entry: LedgerEntry) -> dict[str, object]:
    """Flatten a ``LedgerEntry`` to the JSONL line shape (round-trips ``_parse_line``)."""
    return {
        "fingerprint": entry.fingerprint,
        "split": entry.split,
        "score": entry.score,
        "components": dict(entry.components),
        "cost_usd": entry.cost_usd,
    }
