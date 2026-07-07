"""DecisionRecord value objects — one row of the ``decision_records`` table.

Immutable mined-architectural-decision records (spec §D8-§D10): a title +
status + primary source, a tuple of verbatim :class:`DecisionEvidence` spans
(nothing paraphrased at capture, §D8), the files / qnames the decision affects,
a staleness score, a supersession link, and a verification tier (§D12). Consumed
by the decision-capture pipeline and the ``get_why`` surface, keyed on ``id``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# Single source of truth for the decision lifecycle states (spec §D9).
_VALID_STATUSES = frozenset({"active", "proposed", "rejected", "superseded", "deprecated"})


@dataclass(frozen=True, slots=True)
class DecisionEvidence:
    """One verbatim evidence span (spec §D8) — nothing paraphrased at capture."""

    source: str  # source kind that produced it
    locator: str  # "path:start-end" or commit sha
    text: str  # verbatim span


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One ``decision_records`` row. Identity = ``id`` (None until inserted)."""

    id: int | None
    package: str
    title: str
    status: str
    source: str  # primary (highest-confidence) source kind
    confidence: float
    evidence: tuple[DecisionEvidence, ...]
    affected_files: tuple[str, ...]
    affected_qnames: tuple[str, ...]
    staleness_score: float
    superseded_by: int | None
    verification: str  # verbatim | verified | unverified (§D12)
    structured: Mapping[str, object] | None
    created_at: float
    updated_at: float

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"status {self.status!r} not in {sorted(_VALID_STATUSES)}")
