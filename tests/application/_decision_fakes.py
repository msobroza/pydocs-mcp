"""Shared builders for the DecisionService suites (get_why + kind="decision").

A :class:`DecisionService` over the in-memory stores of ``tests/_fakes.py`` plus
two ``DocsSearch`` stand-ins: :class:`FixedHitsDocs` returns the same ranked
chunks whatever the query asks for, and :class:`PreFilteringDocs` ranks like the
shipped ``decision_search`` preset — it keeps only the chunks whose package the
query's pre-filter admits, then caps the list at the preset's row count, which
is what lets one package's decisions crowd another's out of the ranked slots.
"""

from __future__ import annotations

import importlib.resources

import yaml

from pydocs_mcp.application.decision_service import DecisionService
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk, ChunkFilterField, ChunkList
from pydocs_mcp.retrieval.config import SuggestionsConfig
from pydocs_mcp.storage.decision_record import DecisionEvidence, DecisionRecord
from pydocs_mcp.storage.node_reference import NodeReference
from tests._fakes import (
    InMemoryDecisionStore,
    InMemoryNodeScoreStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)


def _decision_preset_row_limit() -> int:
    """``limit.max_results`` of the shipped ``pipelines/decision_search.yaml``,
    read from the preset so the fake follows it when the preset changes."""
    preset = importlib.resources.files("pydocs_mcp.pipelines").joinpath("decision_search.yaml")
    steps = yaml.safe_load(preset.read_text(encoding="utf-8"))["steps"]
    return next(int(step["params"]["max_results"]) for step in steps if step["type"] == "limit")


# The ranked slots every decision search shares.
DECISION_PRESET_ROWS = _decision_preset_row_limit()


def decision_record(
    *,
    id: int,
    title: str,
    status: str = "active",
    source: str = "commit_messages",
    confidence: float = 0.9,
    staleness_score: float = 0.1,
    affected_files: tuple[str, ...] = (),
    affected_qnames: tuple[str, ...] = ("pkg.mod",),
    package: str = PROJECT_PACKAGE_NAME,
) -> DecisionRecord:
    return DecisionRecord(
        id=id,
        package=package,
        title=title,
        status=status,
        source=source,
        confidence=confidence,
        evidence=(DecisionEvidence(source=source, locator="pkg/mod.py:1-2", text="verbatim span"),),
        affected_files=affected_files,
        affected_qnames=affected_qnames,
        staleness_score=staleness_score,
        superseded_by=None,
        verification="verbatim",
        structured=None,
        created_at=0.0,
        updated_at=0.0,
    )


def decision_chunk(record: DecisionRecord) -> Chunk:
    """A ranked decision chunk carrying the ``decision_id`` backlink metadata."""
    return Chunk(
        text=f"## {record.title}\nbody\n",
        metadata={
            "origin": "decision_record",
            "decision_id": record.id,
            "package": record.package,
        },
    )


def governs_edge(*, key: str, qname: str, package: str = PROJECT_PACKAGE_NAME) -> NodeReference:
    """A RESOLVED GOVERNS edge from ``package``'s ``decision:<key>`` to ``qname``."""
    return NodeReference(
        from_package=package,
        from_node_id=f"decision:{key}",
        to_name=qname,
        to_node_id=qname,
        kind=ReferenceKind.GOVERNS,
    )


class FixedHitsDocs:
    """A ``DocsSearch`` stand-in whose ``ranked`` returns fixed decision chunks."""

    def __init__(self, hits: tuple[Chunk, ...]) -> None:
        self._hits = hits
        self.queries: list[object] = []

    async def ranked(self, query: object) -> ChunkList:
        self.queries.append(query)
        return ChunkList(items=self._hits)


class PreFilteringDocs:
    """A ``DocsSearch`` stand-in that ranks like the decision preset.

    ``ranked_chunks`` is the full ranking over every package; a query keeps the
    chunks whose ``package`` its pre-filter admits (``eq`` value or
    ``{"in": [...]}``; no package key admits all), in that order, and gets at
    most :data:`DECISION_PRESET_ROWS` of them.
    """

    def __init__(self, ranked_chunks: tuple[Chunk, ...]) -> None:
        self._ranked = ranked_chunks

    async def ranked(self, query) -> ChunkList:
        wanted = (query.pre_filter or {}).get(ChunkFilterField.PACKAGE.value)
        admitted = [c for c in self._ranked if _admits(wanted, c.metadata.get("package"))]
        return ChunkList(items=tuple(admitted[:DECISION_PRESET_ROWS]))


def _admits(wanted: object, package: object) -> bool:
    if wanted is None:
        return True
    if isinstance(wanted, dict):
        return package in wanted["in"]
    return package == wanted


def make_decision_service(
    *,
    records: tuple[DecisionRecord, ...] = (),
    docs: FixedHitsDocs | PreFilteringDocs | None = None,
    node_scores: InMemoryNodeScoreStore | None = None,
    references: InMemoryReferenceStore | None = None,
    suggestions: SuggestionsConfig | None = None,
) -> DecisionService:
    store = InMemoryDecisionStore()
    for rec in records:
        store.by_id[rec.id or 0] = rec
    uow_factory = make_fake_uow_factory(
        decisions=store,
        node_scores=node_scores,
        references=references,
    )
    return DecisionService(
        uow_factory=uow_factory,
        docs=docs or FixedHitsDocs(hits=()),
        suggestions=suggestions or SuggestionsConfig(),
    )
