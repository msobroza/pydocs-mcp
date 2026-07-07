"""Source contract + shared value objects for decision mining (spec §D8).

A :class:`DecisionSource` reads a :class:`CaptureContext` (already-extracted
trees + project root + config, never the DB or network) and emits pre-merge
:class:`RawDecision` records. The merge/reconciliation engine (a later slice)
collapses these across sources into ``decision_records`` rows. Every source is
registered on :data:`decision_source_registry` so the capture stage can look
them up by the ``decision_capture.sources`` config list.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydocs_mcp.retrieval.serialization import ComponentRegistry
from pydocs_mcp.storage.decision_record import DecisionEvidence

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode
    from pydocs_mcp.retrieval.config.models import DecisionCaptureConfig

# The six inline decision markers (spec §D8). ``# NOTE:`` and friends are
# deliberately absent — only markers that signal an architectural CHOICE mine a
# decision. Group 1 = marker keyword (drives status), group 2 = payload text.
_MARKER_RE = re.compile(r"#\s*(WHY|DECISION|TRADEOFF|RATIONALE|REJECTED|WORKAROUND):\s*(.+)")

# ADR ``Status:`` header → decision lifecycle state. Unknown values fall back to
# "proposed" (a draft ADR with a novel status word shouldn't silently vanish).
_ADR_STATUS_MAP = {
    "accepted": "active",
    "proposed": "proposed",
    "draft": "proposed",
    "superseded": "superseded",
    "deprecated": "deprecated",
    "rejected": "rejected",
}


@dataclass(frozen=True, slots=True)
class RawDecision:
    """One pre-merge mined decision; the reconciliation engine merges these."""

    title: str
    status: str
    source: str
    confidence: float
    evidence: tuple[DecisionEvidence, ...]
    affected_files: tuple[str, ...]
    affected_qnames: tuple[str, ...]
    evidence_date: float | None = None  # ADR Date: / commit author date; None → capture time


@dataclass(frozen=True, slots=True)
class CaptureContext:
    """Everything a source may read; sources never touch the DB or network."""

    project_root: Path
    trees: tuple[DocumentNode, ...]
    config: DecisionCaptureConfig
    git_log_text: str = ""  # a later slice fills this; "" = no git history


@runtime_checkable
class DecisionSource(Protocol):
    """A deterministic decision-mining source (spec §D8).

    ``name`` is the registry key that ``decision_capture.sources`` selects on.
    ``mine`` runs concurrently with per-source failure isolation, so an
    implementation must not raise for merely-empty inputs — it returns ``()``.
    """

    name: str

    async def mine(self, ctx: CaptureContext) -> tuple[RawDecision, ...]: ...


# Reuses the retrieval ``ComponentRegistry`` decorator (same ``.register`` /
# ``.names`` contract as ``stage_registry``); sources are looked up by name, not
# YAML-decoded, so only ``register`` / ``names`` / ``get`` are exercised here.
decision_source_registry: ComponentRegistry[DecisionSource] = ComponentRegistry()
"""Decorator-populated registry for ``@decision_source_registry.register('name')``.

Populated by side-effect import of ``extraction.decisions.sources`` — each
concrete :class:`DecisionSource` carries the decorator at module scope.
"""


__all__ = [
    "_ADR_STATUS_MAP",
    "_MARKER_RE",
    "CaptureContext",
    "DecisionEvidence",
    "DecisionSource",
    "RawDecision",
    "decision_source_registry",
]
