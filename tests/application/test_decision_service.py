"""DecisionService tests — the get_why read side (spec §D9/§D11).

Seeds an in-memory ``InMemoryDecisionStore`` via ``make_fake_uow_factory`` and a
fake ``DocsSearch`` that returns ranked ``decision_record`` chunks carrying the
``metadata["decision_id"]`` backlink. Asserts the three service modes:

- ``search(query)`` — semantic search → hydrate ranked chunks back to structured
  records → render (empty ⇒ empty-state line + overview pointer).
- ``for_targets(targets, *, query="")`` — §D11 path/qname classification, governing
  decisions resolved through the GOVERNS reference graph (``find_governing``, §D18),
  parent-module fallback, optional query token filter; one ``## Target `` card per target.
- ``dashboard()`` — counts by status/source, stalest active, awaiting review,
  ungoverned high-centrality modules (GOVERNS-edge anti-join, §D18).
"""

from __future__ import annotations

from pydocs_mcp.application.decision_service import DecisionService, _classify_target
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, Chunk, ChunkList
from pydocs_mcp.storage.decision_record import DecisionEvidence, DecisionRecord
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.node_score import NodeScore
from tests._fakes import (
    InMemoryDecisionStore,
    InMemoryNodeScoreStore,
    InMemoryReferenceStore,
    make_fake_uow_factory,
)

_PKG = PROJECT_PACKAGE_NAME


def _record(
    *,
    id: int,
    title: str,
    status: str = "active",
    source: str = "commit_messages",
    confidence: float = 0.9,
    staleness_score: float = 0.1,
    affected_files: tuple[str, ...] = (),
    affected_qnames: tuple[str, ...] = ("pkg.mod",),
) -> DecisionRecord:
    return DecisionRecord(
        id=id,
        package=_PKG,
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


REC_SIDECAR = _record(id=1, title="Use SQLite sidecar")
REC_CACHE = _record(id=2, title="Use redis cache")


def _chunk_for(record: DecisionRecord) -> Chunk:
    """A ranked decision chunk carrying the ``decision_id`` backlink metadata."""
    return Chunk(
        text=f"## {record.title}\nbody\n",
        metadata={"origin": "decision_record", "decision_id": record.id},
    )


class _FakeDocs:
    """A ``DocsSearch`` stand-in whose ``ranked`` returns fixed decision chunks."""

    def __init__(self, hits: tuple[Chunk, ...]) -> None:
        self._hits = hits
        self.queries: list[object] = []

    async def ranked(self, query: object) -> ChunkList:
        self.queries.append(query)
        return ChunkList(items=self._hits)


def _service(
    *,
    records: tuple[DecisionRecord, ...] = (),
    docs: _FakeDocs | None = None,
    node_scores: InMemoryNodeScoreStore | None = None,
    references: InMemoryReferenceStore | None = None,
) -> DecisionService:
    store = InMemoryDecisionStore()
    for rec in records:
        store.by_id[rec.id or 0] = rec
    uow_factory = make_fake_uow_factory(
        decisions=store,
        node_scores=node_scores,
        references=references,
    )
    return DecisionService(uow_factory=uow_factory, docs=docs or _FakeDocs(hits=()))


# ── search ───────────────────────────────────────────────────────────────


async def test_search_hydrates_records_from_ranked_chunks() -> None:
    svc = _service(
        records=(REC_SIDECAR, REC_CACHE),
        docs=_FakeDocs(hits=(_chunk_for(REC_SIDECAR),)),
    )
    out = await svc.search("why sidecar")
    assert "Use SQLite sidecar" in out
    assert "Use redis cache" not in out


async def test_search_preserves_rank_order() -> None:
    svc = _service(
        records=(REC_SIDECAR, REC_CACHE),
        docs=_FakeDocs(hits=(_chunk_for(REC_CACHE), _chunk_for(REC_SIDECAR))),
    )
    out = await svc.search("why")
    assert out.index("Use redis cache") < out.index("Use SQLite sidecar")


async def test_search_no_hits_renders_empty_state_with_pointer() -> None:
    svc = _service(records=(REC_SIDECAR,), docs=_FakeDocs(hits=()))
    out = await svc.search("no such decision")
    assert "[[next:overview:]]" in out
    assert "Use SQLite sidecar" not in out


# ── for_targets ──────────────────────────────────────────────────────────


def test_target_classification_rule() -> None:
    assert _classify_target("a/b.py") == "path"
    assert _classify_target("pkg.mod") == "qname"
    assert _classify_target("README.md") == "path"
    assert _classify_target("single") == "both"


async def test_for_targets_matches_files_and_qname_prefixes() -> None:
    # Edge-backed (§D18): a PATH target reduces to its dotted qname form and
    # matches via the GOVERNS edge resolved to that qname; a qname target matches
    # directly. The GOVERNS edge's to_node_id is the resolver-backed qname.
    rec_file = _record(id=1, title="DB path decision")
    rec_qname = _record(id=2, title="Storage package decision")
    references = InMemoryReferenceStore()
    references.by_package[_PKG] = [
        # ``python/pydocs_mcp/db.py`` → ``python.pydocs_mcp.db`` (path→qname form).
        _governs_edge(key=decision_key("DB path decision"), qname="python.pydocs_mcp.db"),
        _governs_edge(key=decision_key("Storage package decision"), qname="pydocs_mcp.storage"),
    ]
    svc = _service(records=(rec_file, rec_qname), references=references)
    out = await svc.for_targets(["python/pydocs_mcp/db.py", "pydocs_mcp.storage"])
    assert out.count("## Target ") == 2  # one card per target, §D11
    assert "DB path decision" in out
    assert "Storage package decision" in out


async def test_for_targets_parent_module_fallback() -> None:
    # No GOVERNS edge resolves to ``pkg.mod.sub`` directly; the parent module
    # ``pkg.mod`` is governed, so the fallback surfaces the parent's decision.

    rec = _record(id=1, title="Parent module decision")
    references = InMemoryReferenceStore()
    references.by_package[_PKG] = [
        _governs_edge(key=decision_key("Parent module decision"), qname="pkg.mod"),
    ]
    svc = _service(records=(rec,), references=references)
    out = await svc.for_targets(["pkg.mod.sub"])
    assert "Parent module decision" in out


async def test_for_targets_with_query_filters_by_token_overlap() -> None:

    rec_hit = _record(id=1, title="Use SQLite sidecar")
    rec_miss = _record(id=2, title="Unrelated decision")
    references = InMemoryReferenceStore()
    references.by_package[_PKG] = [
        _governs_edge(key=decision_key("Use SQLite sidecar"), qname="pkg.mod"),
        _governs_edge(key=decision_key("Unrelated decision"), qname="pkg.mod"),
    ]
    svc = _service(records=(rec_hit, rec_miss), references=references)
    out = await svc.for_targets(["pkg/mod.py"], query="sidecar vectors")
    assert "Use SQLite sidecar" in out
    assert "Unrelated decision" not in out


# ── dashboard ────────────────────────────────────────────────────────────


async def test_dashboard_counts_and_ungoverned_modules() -> None:
    active_stale = _record(
        id=1,
        title="Stale active",
        status="active",
        staleness_score=0.9,
    )
    active_fresh = _record(
        id=2,
        title="Fresh active",
        status="active",
        staleness_score=0.1,
        source="adr_files",
    )
    proposed = _record(
        id=3,
        title="Awaiting review",
        status="proposed",
    )
    # Two central modules: ``pkg.covered`` (has an inbound GOVERNS edge) and
    # ``pkg.hot`` (no edge) — only the latter surfaces as ungoverned. Coverage is
    # the GOVERNS-edge anti-join (§D18), NOT an affected_qnames scan.
    node_scores = InMemoryNodeScoreStore()
    for qn, pr in (("pkg.hot", 0.9), ("pkg.covered", 0.5)):
        node_scores.by_key[(_PKG, qn)] = NodeScore(
            package=_PKG,
            qualified_name=qn,
            pagerank=pr,
            community=0,
        )
    references = InMemoryReferenceStore()
    references.by_package[_PKG] = [
        _governs_edge(key=decision_key("Stale active"), qname="pkg.covered"),
    ]
    svc = _service(
        records=(active_stale, active_fresh, proposed),
        node_scores=node_scores,
        references=references,
    )
    out = await svc.dashboard()
    assert "## By status" in out
    assert "active: 2" in out
    assert "proposed: 1" in out
    assert "## By source" in out
    assert "commit_messages: 2" in out  # two records from commit_messages
    assert "## Stalest active" in out
    # Stalest active ordering: the 0.9 record leads the 0.1 record.
    assert out.index("Stale active") < out.index("Fresh active")
    assert "## Awaiting review" in out
    assert "Awaiting review" in out
    assert "## Ungoverned high-centrality modules" in out
    assert "`pkg.hot`" in out
    assert "`pkg.covered`" not in out  # covered ⇒ not ungoverned


async def test_dashboard_ungoverned_falls_back_to_in_degree() -> None:
    # No node_scores rows → degrade to the reference in-degree proxy (§D6/§D11).
    references = InMemoryReferenceStore()
    references.by_package[_PKG] = [
        NodeReference(
            from_package=_PKG,
            from_node_id="pkg.caller",
            to_name="pkg.hot",
            to_node_id="pkg.hot",
            kind=ReferenceKind.CALLS,
        ),
    ]
    svc = _service(records=(), references=references)
    out = await svc.dashboard()
    assert "## Ungoverned high-centrality modules" in out
    assert "`pkg.hot`" in out


# ── edge-backed resolution (spec §D18) ────────────────────────────────────


def _governs_edge(*, key: str, qname: str) -> NodeReference:
    """A RESOLVED GOVERNS edge from ``decision:<key>`` to ``qname``."""
    return NodeReference(
        from_package=_PKG,
        from_node_id=f"decision:{key}",
        to_name=qname,
        to_node_id=qname,
        kind=ReferenceKind.GOVERNS,
    )


async def test_for_targets_resolves_via_governs_edges_not_string_scan() -> None:
    # The record's affected_qnames does NOT name the target — only the
    # resolver-backed GOVERNS edge does. The edge-backed path must surface it;
    # a live affected_qnames substring scan would miss it.

    rec = _record(id=1, title="Use SQLite sidecar", affected_qnames=("stale.provenance.only",))
    references = InMemoryReferenceStore()
    references.by_package[_PKG] = [
        _governs_edge(key=decision_key("Use SQLite sidecar"), qname="pkg.storage.sqlite"),
    ]
    svc = _service(records=(rec,), references=references)
    out = await svc.for_targets(["pkg.storage.sqlite"])
    assert "Use SQLite sidecar" in out


async def test_for_targets_no_governing_edge_renders_empty_card() -> None:
    rec = _record(id=1, title="Use SQLite sidecar", affected_qnames=("pkg.storage.sqlite",))
    references = InMemoryReferenceStore()  # no GOVERNS edges at all
    svc = _service(records=(rec,), references=references)
    out = await svc.for_targets(["pkg.storage.sqlite"])
    # Edge-backed: no inbound GOVERNS edge ⇒ the record does not surface even
    # though its affected_qnames names the target.
    assert "Use SQLite sidecar" not in out
    assert "## Target " in out  # the card frame still renders


async def test_dashboard_ungoverned_is_governs_edge_anti_join() -> None:
    # pkg.hot is central AND has an inbound GOVERNS edge ⇒ governed ⇒ NOT
    # ungoverned. pkg.cold is central with no GOVERNS edge ⇒ ungoverned. The
    # anti-join keys on edges, not on any record's affected_qnames.
    node_scores = InMemoryNodeScoreStore()
    for qn, pr in (("pkg.hot", 0.9), ("pkg.cold", 0.8)):
        node_scores.by_key[(_PKG, qn)] = NodeScore(
            package=_PKG, qualified_name=qn, pagerank=pr, community=0
        )
    references = InMemoryReferenceStore()
    references.by_package[_PKG] = [_governs_edge(key="hot-decision", qname="pkg.hot")]
    svc = _service(records=(), node_scores=node_scores, references=references)
    out = await svc.dashboard()
    assert "`pkg.cold`" in out
    assert "`pkg.hot`" not in out  # governed by an edge ⇒ excluded
