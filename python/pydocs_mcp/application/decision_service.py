"""Decision read-side service + its dashboard value object (spec §D9/§D11).

``DecisionService`` is the real ``get_why`` backing (the ``DecisionNavigator``
Protocol). It composes the per-project :class:`DocsSearch` for semantic search
over ``origin="decision_record"`` chunks, then hydrates each hit back to its
structured :class:`DecisionRecord` via the ``metadata["decision_id"]`` backlink.
Three modes mirror the ``NullDecisionService`` shape so the composition-root swap
is one wiring branch:

- ``search(query)`` — semantic search → rank-ordered record hydration → render.
- ``for_targets(targets, *, query="")`` — §D11 path/qname target classification,
  file-suffix / dotted-prefix matching, parent-module fallback, optional query
  token filter; one card per target.
- ``dashboard()`` — governance rollup: counts, stalest active, awaiting review,
  ungoverned high-centrality modules.

``DecisionDashboard`` is the frozen view-model the ``dashboard()`` mode renders.
It lives next to the service (the renderer's consumer) so
``application/formatting.py`` can stay a pure rendering module and import it only
under ``TYPE_CHECKING``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.application.formatting import (
    format_decision_dashboard,
    format_decision_records,
    pointer_token,
)
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    ChunkFilterField,
    ChunkOrigin,
    SearchQuery,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydocs_mcp.application.docs_search import DocsSearch
    from pydocs_mcp.models import Chunk
    from pydocs_mcp.storage.decision_record import DecisionRecord
    from pydocs_mcp.storage.node_score import NodeScore
    from pydocs_mcp.storage.protocols import UnitOfWork

# Single source of truth for the decision-read default limit — the YAML-canonical
# value lives in ``DecisionsOutputConfig.default_limit`` (Field(10)); this mirrors
# it so a service constructed without an explicit limit behaves like the shipped
# default (the composition root threads ``cfg.decisions.output.default_limit``).
_DEFAULT_LIMIT = 10

# Top-N caps for the governance dashboard lists (spec §D11). Stalest-active and
# awaiting-review lists show five each; ungoverned modules show five. Single
# source so the slice widths never drift from the renderer's expectations.
_DASHBOARD_LIST_LIMIT = 5

# Known source-file extensions that force a dotted-name target to classify as a
# PATH rather than a qname (``README.md`` has a dot but is a file, not a symbol).
# The set is deliberately small — the classification only needs to disambiguate
# the common "looks dotted but is really a file" case (spec §D11).
_SOURCE_FILE_EXTENSIONS = (".py", ".pyi", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".cfg")

# Lifecycle state that marks a decision as awaiting human review (spec §D9).
_PROPOSED_STATUS = "proposed"
_ACTIVE_STATUS = "active"


def _classify_target(target: str) -> str:
    """Classify a ``get_why`` target as ``"path"`` / ``"qname"`` / ``"both"`` (§D11).

    Rule (verbatim from spec §D11, mirrored in the CLI ``why`` help by Task 5):

    - contains ``/`` OR ends with a known source-file extension → ``"path"``
      (``a/b.py`` and ``README.md`` are files even though the latter looks dotted).
    - otherwise dotted → ``"qname"`` (``pkg.mod``).
    - bare single token → ``"both"`` (try file and qname matching, union).
    """
    lowered = target.lower()
    if "/" in target or lowered.endswith(_SOURCE_FILE_EXTENSIONS):
        return "path"
    if "." in target:
        return "qname"
    return "both"


def _path_to_qname(path: str) -> str:
    """Best-effort dotted qname for a source path (``pkg/mod.py`` → ``pkg.mod``).

    Strips a trailing source-file extension and turns path separators into dots.
    Used so a PATH target can also match ``affected_qnames`` (a decision that
    names the module, not the file, still surfaces for a file query).
    """
    stem = path
    for ext in _SOURCE_FILE_EXTENSIONS:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem.replace("/", ".").replace("\\", ".").strip(".")


def _segments_suffix_match(target: str, candidate: str, sep: str) -> bool:
    """True when ``target`` is a segment-boundary suffix of ``candidate`` (or vice
    versa) under separator ``sep``.

    Segment-boundary means the shorter string aligns to a full-segment boundary
    of the longer — ``storage`` matches ``pydocs_mcp.storage`` but ``mod`` does
    NOT match ``submod`` (a substring-but-not-segment hit).
    """
    short, long = sorted((target, candidate), key=len)
    if short == long:
        return True
    short_segs = short.split(sep)
    long_segs = long.split(sep)
    if len(short_segs) > len(long_segs):
        return False
    # Prefix alignment (``pkg`` for ``pkg.mod``) OR suffix alignment
    # (``mod.py`` for ``a/b/mod.py`` once separators unify).
    return long_segs[: len(short_segs)] == short_segs or long_segs[-len(short_segs) :] == short_segs


def _record_matches_target(record: DecisionRecord, target: str, classification: str) -> int:
    """Return the length of the longest matching key, or 0 when no match.

    A non-zero return doubles as the specificity score — a longer matched key
    (``pydocs_mcp.storage.sqlite`` beats ``pydocs_mcp``) ranks the record higher
    for the "most-specific first" ordering (spec §D11).
    """
    best = 0
    if classification in ("path", "both"):
        for f in record.affected_files:
            if _segments_suffix_match(target, f, "/"):
                best = max(best, len(f))
    qname_target = _path_to_qname(target) if classification == "path" else target
    if classification in ("qname", "both", "path"):
        for q in record.affected_qnames:
            if _segments_suffix_match(qname_target, q, "."):
                best = max(best, len(q))
    return best


def _parent_modules(target: str, classification: str) -> Iterable[str]:
    """Yield successive parent-module qnames of ``target`` (spec §D11 fallback).

    For ``pkg.mod.sub`` yields ``pkg.mod`` then ``pkg``. A path target is first
    reduced to its qname form so the walk is dotted-segment based either way.
    Empty when the target has no parent (single segment).
    """
    qname = _path_to_qname(target) if classification == "path" else target
    parts = qname.split(".")
    for depth in range(len(parts) - 1, 0, -1):
        yield ".".join(parts[:depth])


def _title_tokens(title: str) -> frozenset[str]:
    """Normalized content tokens of ``title`` via the shared decision tokenizer.

    Reuses the PUBLIC ``decision_key`` (single source of truth): the key is the
    normalized tokens joined by ``-``, and ``_PUNCT_RE`` guarantees no token
    itself contains ``-``, so ``.split("-")`` losslessly recovers the tuple.
    """
    key = decision_key(title)
    return frozenset(tok for tok in key.split("-") if tok)


def _matches_query(record: DecisionRecord, query_tokens: frozenset[str]) -> bool:
    """True when the record shares ≥1 normalized content token with the query."""
    return bool(_title_tokens(record.title) & query_tokens)


@dataclass(frozen=True, slots=True)
class DecisionDashboard:
    """Governance view-model for ``get_why`` dashboard mode (spec §D11).

    Fields are already sliced/ranked by the service — the renderer only lays
    them out. ``stalest`` / ``awaiting_review`` are capped at 5 by the service;
    ``ungoverned_modules`` are the top-centrality module qnames with no
    decision coverage (up to 5).
    """

    by_status: Mapping[str, int]
    by_source: Mapping[str, int]
    stalest: tuple[DecisionRecord, ...]
    awaiting_review: tuple[DecisionRecord, ...]
    ungoverned_modules: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DecisionService:
    """Real ``get_why`` backing over mined decisions (spec §D9/§D11).

    Composes :class:`DocsSearch` for semantic ranking and the decision store for
    hydration. Reads only — no ``commit()`` on any path (CLAUDE.md read contract).
    """

    uow_factory: Callable[[], UnitOfWork]
    docs: DocsSearch  # semantic search over decision chunks
    default_limit: int = _DEFAULT_LIMIT  # wired from decisions.output

    async def search(self, query: str) -> str:
        """Semantic search over decision chunks → rank-ordered record cards.

        Runs the chunk pipeline scoped to ``origin="decision_record"``, collects
        the ``decision_id`` backlinks in rank order, hydrates each to its
        structured record, and renders. Zero hits ⇒ an empty-state line plus the
        overview recovery pointer (spec §D1 empty contract).
        """
        chunk_query = SearchQuery(
            terms=query,
            pre_filter={ChunkFilterField.ORIGIN.value: ChunkOrigin.DECISION_RECORD.value},
        )
        ranked = await self.docs.ranked(chunk_query)
        ordered_ids = _decision_ids_in_rank_order(ranked.items)
        if not ordered_ids:
            return f"No decisions found.\n{pointer_token('overview', '')}"
        async with self.uow_factory() as uow:
            records = await uow.decisions.list_for_package(PROJECT_PACKAGE_NAME)
        by_id = {r.id: r for r in records if r.id is not None}
        hydrated = tuple(by_id[i] for i in ordered_ids[: self.default_limit] if i in by_id)
        if not hydrated:
            return f"No decisions found.\n{pointer_token('overview', '')}"
        return format_decision_records(hydrated, heading=f"Decisions matching {query!r}")

    async def for_targets(self, targets: list[str], *, query: str = "") -> str:
        """Render one decision card per target (§D11 target mode).

        Each target is classified (path / qname / both), matched against
        ``affected_files`` (segment-boundary suffix) and ``affected_qnames``
        (dotted prefix/suffix), most-specific first. When nothing matches, the
        parent-module fallback walks up the qname. When ``query`` is non-empty,
        matched records are filtered to those sharing ≥1 normalized content token
        with it (§D11 both-set mode).
        """
        async with self.uow_factory() as uow:
            records = await uow.decisions.list_for_package(PROJECT_PACKAGE_NAME)
        query_tokens = _title_tokens(query) if query else frozenset()
        cards = [
            _render_target_card(target, records, query_tokens, self.default_limit)
            for target in targets
        ]
        return "\n\n".join(cards)

    async def dashboard(self) -> str:
        """Governance rollup over all decisions (§D11 dashboard mode).

        One UoW read gathers records + centrality signals; counts by status and
        source, the stalest five active records, the five ``proposed`` records
        awaiting review, and the top-centrality module qnames with zero decision
        coverage (centrality mirrors :class:`OverviewService`: pagerank, in-degree
        fallback — the shared §D6/§D11 degradation rule).
        """
        async with self.uow_factory() as uow:
            records = await uow.decisions.list_for_package(PROJECT_PACKAGE_NAME)
            scores = await uow.node_scores.for_package(PROJECT_PACKAGE_NAME)
            degrees = await uow.references.degree_by_package(PROJECT_PACKAGE_NAME)
        summary = _build_dashboard(records, scores, degrees)
        return format_decision_dashboard(summary)


def _decision_ids_in_rank_order(chunks: Sequence[Chunk]) -> tuple[int, ...]:
    """Collect ``metadata["decision_id"]`` off ranked chunks, de-duped, rank order.

    A decision chunk carries an ``int`` backlink to its source record (stamped by
    ``IndexingService._stamp_decision_ids``). Chunks without a valid id are
    skipped; the first occurrence of each id wins (rank order preserved).
    """
    ordered: list[int] = []
    seen: set[int] = set()
    for chunk in chunks:
        raw = chunk.metadata.get("decision_id")
        if isinstance(raw, int) and raw not in seen:
            seen.add(raw)
            ordered.append(raw)
    return tuple(ordered)


def _render_target_card(
    target: str,
    records: Sequence[DecisionRecord],
    query_tokens: frozenset[str],
    limit: int,
) -> str:
    """Render the ``## Target `` card for one target (helper for ``for_targets``).

    ``format_decision_records`` is the single render authority — it emits an H1
    (``# {heading}``) doc, so the target card promotes that to the ``## Target ``
    H2 the §D11 target mode wants by prefixing one ``#``. Record blocks never
    start a line with ``#`` (they use bold titles + ``-`` bullets), so promoting
    the leading heading is safe and touches only the first line.
    """
    classification = _classify_target(target)
    matched = _match_records_for_target(target, classification, records)
    if query_tokens:
        matched = [r for r in matched if _matches_query(r, query_tokens)]
    body = format_decision_records(tuple(matched[:limit]), heading=f"Target {target}")
    return "#" + body if body.startswith("# ") else body


def _match_records_for_target(
    target: str,
    classification: str,
    records: Sequence[DecisionRecord],
) -> list[DecisionRecord]:
    """Records affecting ``target``, most-specific first; parent-module fallback.

    Direct matches (a record whose ``affected_files`` / ``affected_qnames`` cover
    the target) rank by longest matched key. When there is no direct match, walk
    the target's parent modules and return the first parent's matches (§D11).
    """
    scored = [
        (score, r) for r in records if (score := _record_matches_target(r, target, classification))
    ]
    if scored:
        scored.sort(key=lambda sr: (-sr[0], sr[1].id or 0))
        return [r for _, r in scored]
    for parent in _parent_modules(target, classification):
        parent_matches = _match_records_for_target(parent, "qname", records)
        if parent_matches:
            return parent_matches
    return []


def _build_dashboard(
    records: Sequence[DecisionRecord],
    scores: Sequence[NodeScore],
    degrees: Mapping[str, tuple[int, int]],
) -> DecisionDashboard:
    """Assemble the governance :class:`DecisionDashboard` (pure — no I/O)."""
    by_status = _count_by(records, key=lambda r: r.status)
    by_source = _count_by(records, key=lambda r: r.source)
    active = [r for r in records if r.status == _ACTIVE_STATUS]
    active.sort(key=lambda r: (-r.staleness_score, r.id or 0))
    proposed = [r for r in records if r.status == _PROPOSED_STATUS]
    proposed.sort(key=lambda r: (-r.staleness_score, r.id or 0))
    covered = _covered_qnames(records)
    ungoverned = _ungoverned_modules(scores, degrees, covered)
    return DecisionDashboard(
        by_status=by_status,
        by_source=by_source,
        stalest=tuple(active[:_DASHBOARD_LIST_LIMIT]),
        awaiting_review=tuple(proposed[:_DASHBOARD_LIST_LIMIT]),
        ungoverned_modules=ungoverned,
    )


def _count_by(
    records: Sequence[DecisionRecord],
    *,
    key: Callable[[DecisionRecord], str],
) -> dict[str, int]:
    """Tally ``records`` by the ``key`` projection (status or source)."""
    counts: dict[str, int] = {}
    for record in records:
        bucket = key(record)
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def _covered_qnames(records: Sequence[DecisionRecord]) -> frozenset[str]:
    """All module qnames any decision affects (a module with coverage is governed)."""
    return frozenset(q for r in records for q in r.affected_qnames)


def _ungoverned_modules(
    scores: Sequence[NodeScore],
    degrees: Mapping[str, tuple[int, int]],
    covered: frozenset[str],
) -> tuple[str, ...]:
    """Top-centrality module qnames with zero decision coverage (§D11).

    Centrality source mirrors :class:`OverviewService`: pagerank when node scores
    exist, else the reference in-degree proxy — the shared §D6/§D11 degradation
    rule. Modules already covered by a decision are excluded; the result is the
    top ``_DASHBOARD_LIST_LIMIT`` uncovered qnames by descending centrality.
    """
    if scores:
        ranking = [(s.pagerank, s.qualified_name) for s in scores]
    else:
        ranking = [(float(in_deg), qname) for qname, (in_deg, _out) in degrees.items()]
    uncovered = [(rank, qname) for rank, qname in ranking if qname not in covered]
    uncovered.sort(key=lambda rq: (-rq[0], rq[1]))
    return tuple(qname for _rank, qname in uncovered[:_DASHBOARD_LIST_LIMIT])
