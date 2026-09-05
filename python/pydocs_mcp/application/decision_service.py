"""Decision read-side service + its dashboard value object (spec §D9/§D11).

``DecisionService`` is the real ``get_why`` backing (the ``DecisionNavigator``
Protocol). It composes the per-project :class:`DocsSearch` for semantic search
over ``origin="decision_record"`` chunks, then hydrates each hit back to its
structured :class:`DecisionRecord` via the ``metadata["decision_id"]`` backlink.
Three modes mirror the ``NullDecisionService`` shape so the composition-root swap
is one wiring branch; each ships as a ``why_*`` body-producer triple (markdown +
§3.6 items[] + extras) with a text-only façade for direct callers:

- ``why_search(query)`` / ``search(query)`` — semantic search → rank-ordered
  record hydration → render.
- ``why_targets(targets, *, query="")`` / ``for_targets(...)`` — §D11 path/qname
  target classification; governing decisions resolved through the GOVERNS
  reference graph (``find_governing``, resolver-backed §D18) with a
  parent-module fallback and an optional query-token filter; one card per target.
- ``why_dashboard()`` / ``dashboard()`` — governance rollup: counts, stalest
  active, awaiting review, ungoverned high-centrality modules (GOVERNS-edge
  anti-join, §D18).

``DecisionDashboard`` is the frozen view-model the ``dashboard()`` mode renders.
It lives next to the service (the renderer's consumer) so
``application/formatting.py`` can stay a pure rendering module and import it only
under ``TYPE_CHECKING``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydocs_mcp.application.formatting import (
    format_decision_dashboard,
    format_decision_records,
    pointer_token,
)
from pydocs_mcp.application.suggestions import (
    SEARCH_ZERO_HIT_SUGGESTION,
    log_suggestion_fired,
)
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    ChunkFilterField,
    ChunkOrigin,
    SearchQuery,
)
from pydocs_mcp.retrieval.config import SuggestionsConfig

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

# One rendered get_why body: ``(markdown, items, meta_extras)`` — the envelope
# body-producer triple (contract §2.1; ``application.envelope.BodyResult``).
WhyBody = tuple[str, tuple[dict[str, object], ...], dict[str, object]]


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
    Used so a PATH target reduces to the qname the GOVERNS-edge query keys on
    (``find_governing``), letting a file query surface the module's decisions.
    """
    stem = path
    for ext in _SOURCE_FILE_EXTENSIONS:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    return stem.replace("/", ".").replace("\\", ".").strip(".")


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
    # ADR 0007: ``search_zero_hit`` gates the zero-hit overview pointer here
    # exactly as it does in ToolRouter.search_codebase (one flag, both sites).
    suggestions: SuggestionsConfig = field(default_factory=SuggestionsConfig)

    async def search(self, query: str) -> str:
        """Semantic search over decision chunks → rank-ordered record cards.

        Delegates to :meth:`search_with_items` and drops the structured rows —
        one retrieval/hydration/render authority for both the get_why text
        surface and the ``search_codebase(kind="decision")`` items[] surface.
        """
        body, _items, _extras = await self.search_with_items(query)
        return body

    async def search_with_items(
        self, query: str
    ) -> tuple[str, tuple[dict[str, object], ...], dict[str, object]]:
        """Decision search in envelope body-producer shape (contract §3.2).

        Runs the chunk pipeline scoped to ``origin="decision_record"``, collects
        the ``decision_id`` backlinks in rank order, hydrates each to its
        structured record, and renders — returning one §3.2 ``kind="decision"``
        row per rendered record alongside the markdown body. Zero hits ⇒ an
        empty-state line plus the overview recovery pointer (spec §D1 empty
        contract) and no rows. Decision rows carry the record id with null
        path/span — locators stay in ``get_why`` (contract §3.6).
        """
        body, hydrated, scores = await self._search_hydrated(query)
        items = tuple(_decision_item(r, scores.get(r.id or -1, 0.0)) for r in hydrated)
        return body, items, self._zero_hit_extras(hydrated, tool="search_codebase")

    async def why_search(self, query: str) -> WhyBody:
        """``get_why`` query mode with §3.6 items — same retrieval/render run
        as :meth:`search_with_items` (one authority), different row shape:
        ``get_why`` rows carry the decision identity + evidence locators, not
        the §3.2 search-ranking fields."""
        body, hydrated, _scores = await self._search_hydrated(query)
        return body, _why_items(hydrated), self._zero_hit_extras(hydrated, tool="get_why")

    def _zero_hit_extras(
        self, hydrated: tuple[DecisionRecord, ...], *, tool: str
    ) -> dict[str, object]:
        """meta.suggestion mirror of the zero-hit pointer (§2.3, ADR 0007).

        ``tool`` names the surface consuming this run (``get_why`` vs
        ``search_codebase(kind="decision")``) so the fired-rule log line
        attributes the nudge to the tool the client actually called.
        """
        if hydrated or not self.suggestions.search_zero_hit:
            return {}
        log_suggestion_fired(tool, "search_zero_hit")
        return {"suggestion": SEARCH_ZERO_HIT_SUGGESTION}

    async def _search_hydrated(
        self, query: str
    ) -> tuple[str, tuple[DecisionRecord, ...], dict[int, float]]:
        """Shared retrieval/hydration/render for the two search surfaces —
        returns ``(body, rendered_records, chunk_scores)``; zero hits ⇒ the
        empty-state body with no records."""
        chunk_query = SearchQuery(
            terms=query,
            pre_filter={ChunkFilterField.ORIGIN.value: ChunkOrigin.DECISION_RECORD.value},
        )
        ranked = await self.docs.ranked(chunk_query)
        ordered_ids = _decision_ids_in_rank_order(ranked.items)
        # ADR 0007: the zero-hit overview pointer is flag-gated (search_zero_hit
        # off restores the bare pre-pointer body byte-for-byte).
        empty_body = "No decisions found."
        if self.suggestions.search_zero_hit:
            empty_body += f"\n{pointer_token('overview', '')}"
        if not ordered_ids:
            return empty_body, (), {}
        async with self.uow_factory() as uow:
            records = await uow.decisions.list_for_package(PROJECT_PACKAGE_NAME)
        by_id = {r.id: r for r in records if r.id is not None}
        hydrated = tuple(by_id[i] for i in ordered_ids[: self.default_limit] if i in by_id)
        if not hydrated:
            return empty_body, (), {}
        body = format_decision_records(hydrated, heading=f"Decisions matching {query!r}")
        return body, hydrated, _decision_scores(ranked.items)

    async def for_targets(self, targets: list[str], *, query: str = "") -> str:
        """Text-only façade over :meth:`why_targets` — one dispatch run, first
        element (same pattern as :meth:`search`)."""
        body, _items, _extras = await self.why_targets(targets, query=query)
        return body

    async def why_targets(self, targets: list[str], *, query: str = "") -> WhyBody:
        """Render one decision card per target (§D11 target mode, edge-backed §D18).

        Each target is classified (path / qname / both) and reduced to a qname;
        its governing decisions come from the resolver-backed GOVERNS graph
        (``find_governing(qname)``, exact) rather than an ``affected_qnames``
        substring scan. When a target has no inbound GOVERNS edge, the
        parent-module fallback walks up the qname. When ``query`` is non-empty,
        matched records are filtered to those sharing ≥1 normalized content token
        with it (§D11 both-set mode). items[] carry one §3.6 row per rendered
        record, deduped on ``decision_id`` (a record governing several targets
        renders per card but attributes once).
        """
        async with self.uow_factory() as uow:
            records = await uow.decisions.list_for_package(PROJECT_PACKAGE_NAME)
            by_key = {decision_key(r.title): r for r in records}
            # Resolve each target's governing decision keys through the GOVERNS
            # edges INSIDE the same UoW (one read scope), then map keys → records.
            matches = [await self._governing_records(uow, t, by_key) for t in targets]
        query_tokens = _title_tokens(query) if query else frozenset()
        visible = [
            _visible_records(matched, query_tokens, self.default_limit) for matched in matches
        ]
        cards = [
            _render_target_card(target, shown)
            for target, shown in zip(targets, visible, strict=True)
        ]
        surfaced = [record for shown in visible for record in shown]
        return "\n\n".join(cards), _why_items(surfaced), {}

    async def _governing_records(
        self,
        uow: UnitOfWork,
        target: str,
        by_key: Mapping[str, DecisionRecord],
    ) -> list[DecisionRecord]:
        """Records whose GOVERNS edge resolves to ``target`` (parent fallback).

        Reduces the target to a qname, asks the reference graph which decisions
        govern it (``find_governing``, resolver-backed), and maps the returned
        keys to records. On no inbound edge, walks the target's parent modules
        and returns the first governed parent's records (§D11 fallback).
        """
        classification = _classify_target(target)
        primary = _path_to_qname(target) if classification == "path" else target
        for qname in (primary, *_parent_modules(target, classification)):
            keys = await uow.references.find_governing(qname)
            found = [by_key[k] for k in keys if k in by_key]
            if found:
                found.sort(key=lambda r: r.id or 0)
                return found
        return []

    async def dashboard(self) -> str:
        """Text-only façade over :meth:`why_dashboard` — one run, first element."""
        body, _items, _extras = await self.why_dashboard()
        return body

    async def why_dashboard(self) -> WhyBody:
        """Governance rollup over all decisions (§D11 dashboard mode).

        One UoW read gathers records + centrality signals; counts by status and
        source, the stalest five active records, the five ``proposed`` records
        awaiting review, and the top-centrality module qnames with no inbound
        GOVERNS edge (the graph anti-join, §D18 — not an ``affected_qnames``
        scan). Centrality mirrors :class:`OverviewService`: pagerank, in-degree
        fallback — the shared §D6/§D11 degradation rule. items[] carry one §3.6
        row per record the rollup SURFACES (stalest, then awaiting review;
        deduped on ``decision_id``) so a harness can attribute the rollup.
        """
        async with self.uow_factory() as uow:
            records = await uow.decisions.list_for_package(PROJECT_PACKAGE_NAME)
            scores = await uow.node_scores.for_package(PROJECT_PACKAGE_NAME)
            degrees = await uow.references.degree_by_package(PROJECT_PACKAGE_NAME)
            governed = await uow.references.governed_qnames()
        summary = _build_dashboard(records, scores, degrees, governed)
        surfaced = (*summary.stalest, *summary.awaiting_review)
        return format_decision_dashboard(summary), _why_items(surfaced), {}


def _decision_scores(chunks: Sequence[Chunk]) -> dict[int, float]:
    """Best (first-seen, rank order) chunk relevance per ``decision_id``.

    Mirrors :func:`_decision_ids_in_rank_order`'s first-occurrence-wins rule so
    a record's item score is the relevance of the chunk that ranked it.
    """
    scores: dict[int, float] = {}
    for chunk in chunks:
        raw = chunk.metadata.get("decision_id")
        if isinstance(raw, int) and raw not in scores:
            scores[raw] = float(chunk.relevance or 0.0)
    return scores


def _decision_item(record: DecisionRecord, score: float) -> dict[str, object]:
    """One ``search_codebase`` §3.2 row for a decision record.

    ``qualified_name`` is the record's :func:`decision_key` — the stable
    normalized-title identity the GOVERNS graph keys on. Path/span are null by
    contract: decision locators live in ``get_why`` items (§3.6).
    """
    return {
        "kind": "decision",
        "id": str(record.id) if record.id is not None else "",
        "qualified_name": decision_key(record.title),
        "package": PROJECT_PACKAGE_NAME,
        "path": None,
        "start_line": None,
        "end_line": None,
        "score": score,
    }


def _why_item(record: DecisionRecord) -> dict[str, object]:
    """One ``get_why`` §3.6 row: decision identity + evidence locators.

    ``locators`` cite the record's verbatim evidence spans (``path:start-end``
    or a commit sha) — the fields ``search_codebase`` decision rows deliberately
    omit (contract §3.2 vs §3.6).
    """
    return {
        "decision_id": record.id,
        "title": record.title,
        "status": record.status,
        "locators": [evidence.locator for evidence in record.evidence],
        "affected_files": list(record.affected_files),
    }


def _why_items(records: Sequence[DecisionRecord]) -> tuple[dict[str, object], ...]:
    """§3.6 rows for ``records``, first-seen-deduped on ``decision_id``.

    Unpersisted records (``id is None``) are skipped — the contract types
    ``decision_id`` as ``int`` and every read path hydrates from SQLite, so a
    None id here would be a fixture artifact, not attributable evidence.
    """
    seen: set[int] = set()
    rows: list[dict[str, object]] = []
    for record in records:
        if record.id is None or record.id in seen:
            continue
        seen.add(record.id)
        rows.append(_why_item(record))
    return tuple(rows)


def _visible_records(
    matched: Sequence[DecisionRecord],
    query_tokens: frozenset[str],
    limit: int,
) -> tuple[DecisionRecord, ...]:
    """The records one target card actually renders: query-token filter (§D11
    both-set mode) then the default-limit slice. Split from the renderer so
    ``why_targets`` can attribute exactly the rendered set in items[]."""
    if query_tokens:
        matched = [r for r in matched if _matches_query(r, query_tokens)]
    return tuple(matched[:limit])


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


def _render_target_card(target: str, visible: Sequence[DecisionRecord]) -> str:
    """Render the ``## Target `` card for one target (helper for ``why_targets``).

    ``visible`` is the already-filtered/sliced record set (``_visible_records``
    — the same set items[] attribute). ``format_decision_records`` is the single
    render authority — it emits an H1 (``# {heading}``) doc, so the target card
    promotes that to the ``## Target `` H2 the §D11 target mode wants by
    prefixing one ``#``. Record blocks never start a line with ``#`` (they use
    bold titles + ``-`` bullets), so promoting the leading heading is safe and
    touches only the first line.
    """
    body = format_decision_records(tuple(visible), heading=f"Target {target}")
    return "#" + body if body.startswith("# ") else body


def _build_dashboard(
    records: Sequence[DecisionRecord],
    scores: Sequence[NodeScore],
    degrees: Mapping[str, tuple[int, int]],
    governed: frozenset[str],
) -> DecisionDashboard:
    """Assemble the governance :class:`DecisionDashboard` (pure — no I/O).

    ``governed`` is the resolver-backed GOVERNS anti-join set (qnames with an
    inbound GOVERNS edge, §D18) — the ungoverned list is the top-centrality
    modules NOT in it.
    """
    by_status = _count_by(records, key=lambda r: r.status)
    by_source = _count_by(records, key=lambda r: r.source)
    active = [r for r in records if r.status == _ACTIVE_STATUS]
    active.sort(key=lambda r: (-r.staleness_score, r.id or 0))
    proposed = [r for r in records if r.status == _PROPOSED_STATUS]
    proposed.sort(key=lambda r: (-r.staleness_score, r.id or 0))
    ungoverned = _ungoverned_modules(scores, degrees, governed)
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


def _ungoverned_modules(
    scores: Sequence[NodeScore],
    degrees: Mapping[str, tuple[int, int]],
    governed: frozenset[str],
) -> tuple[str, ...]:
    """Top-centrality module qnames with no inbound GOVERNS edge (§D18 anti-join).

    Centrality source mirrors :class:`OverviewService`: pagerank when node scores
    exist, else the reference in-degree proxy — the shared §D6/§D11 degradation
    rule. Modules with an inbound GOVERNS edge (``governed``) are excluded; the
    result is the top ``_DASHBOARD_LIST_LIMIT`` ungoverned qnames by descending
    centrality.
    """
    if scores:
        ranking = [(s.pagerank, s.qualified_name) for s in scores]
    else:
        ranking = [(float(in_deg), qname) for qname, (in_deg, _out) in degrees.items()]
    uncovered = [(rank, qname) for rank, qname in ranking if qname not in governed]
    uncovered.sort(key=lambda rq: (-rq[0], rq[1]))
    return tuple(qname for _rank, qname in uncovered[:_DASHBOARD_LIST_LIMIT])
