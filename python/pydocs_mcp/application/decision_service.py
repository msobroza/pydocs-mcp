"""Decision read-side service (spec §D9/§D11).

``DecisionService`` is the real ``get_why`` backing (the ``DecisionNavigator``
Protocol). It composes the per-project :class:`DocsSearch` for semantic search
over ``origin="decision_record"`` chunks, then hydrates each hit back to its
structured :class:`DecisionRecord` via the ``metadata["decision_id"]`` backlink.
Three modes mirror the ``NullDecisionService`` shape so the composition-root swap
is one wiring branch; each ships as a ``why_*`` body-producer triple (markdown +
§3.6 items[] + extras) with a text-only façade for direct callers:

- ``why_search(query)`` / ``search(query)`` — semantic search → rank-ordered
  record hydration → render, over the project's decisions.
  ``search_with_items(query, *, scope, package)`` backs
  ``search_codebase(kind="decision")``: the request's ``scope`` / ``package``
  pick the packages searched, so a dependency's decisions (mined under
  ``decision_capture.include_deps``) answer only when asked (#346).
- ``why_targets(targets, *, query="")`` / ``for_targets(...)`` — §D11 path/qname
  target classification; governing decisions resolved through the GOVERNS
  reference graph (``find_governing``, resolver-backed §D18) with a
  parent-module fallback and an optional query-token filter; one card per
  target, in the package that mined each governing decision.
- ``why_dashboard()`` / ``dashboard()`` — governance rollup: counts, stalest
  active, awaiting review, ungoverned high-centrality modules (GOVERNS-edge
  anti-join, §D18).

The dashboard's view-model lives in ``application/decision_dashboard.py``; which
packages each mode covers lives in ``application/decision_corpus.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydocs_mcp.application.decision_corpus import (
    PROJECT_DECISION_CORPUS,
    RecordsByKeyPerPackage,
    decision_pre_filter_for_packages,
    records_for_governs_edges_to_qname,
)
from pydocs_mcp.application.decision_dashboard import build_decision_dashboard
from pydocs_mcp.application.formatting import (
    format_decision_dashboard,
    format_decision_records,
)
from pydocs_mcp.application.pointer_bundles import render_pointer_bundle
from pydocs_mcp.application.suggestions import (
    SEARCH_ZERO_HIT_SUGGESTION,
    log_suggestion_fired,
)
from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, SearchQuery, SearchScope
from pydocs_mcp.pointer_table import PointerTableConfig, ResponseKind
from pydocs_mcp.retrieval.config import SuggestionsConfig

if TYPE_CHECKING:
    from pydocs_mcp.application.docs_search import DocsSearch
    from pydocs_mcp.models import Chunk
    from pydocs_mcp.storage.decision_record import DecisionRecord
    from pydocs_mcp.storage.protocols import UnitOfWork

# Single source of truth for the decision-read default limit — the YAML-canonical
# value lives in ``DecisionsOutputConfig.default_limit`` (Field(10)); this mirrors
# it so a service constructed without an explicit limit behaves like the shipped
# default (the composition root threads ``cfg.decisions.output.default_limit``).
_DEFAULT_LIMIT = 10

# The body an empty decision search answers (single source): the zero-hit path
# below, and the router's landing-unit answer for kind="decision" (#315).
EMPTY_DECISIONS_MSG = "No decisions found."

# Known source-file extensions that force a dotted-name target to classify as a
# PATH rather than a qname (``README.md`` has a dot but is a file, not a symbol).
# The set is deliberately small — the classification only needs to disambiguate
# the common "looks dotted but is really a file" case (spec §D11).
_SOURCE_FILE_EXTENSIONS = (".py", ".pyi", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".cfg")

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
    # The deployment's pointer table — the rows a rendered decision card draws
    # its follow-ups from. Both decision surfaces (``get_why`` and
    # ``search_codebase(kind="decision")``) render through this one service, so
    # one table reaches both.
    pointers: PointerTableConfig = field(default_factory=PointerTableConfig)

    async def search(self, query: str) -> str:
        """Semantic search over decision chunks → rank-ordered record cards.

        Delegates to :meth:`search_with_items` and drops the structured rows —
        one retrieval/hydration/render authority for both the get_why text
        surface and the ``search_codebase(kind="decision")`` items[] surface.
        """
        body, _items, _extras = await self.search_with_items(query)
        return body

    async def search_with_items(
        self,
        query: str,
        *,
        scope: SearchScope = SearchScope.ALL,
        package: str = "",
        branch: str | None = None,
    ) -> tuple[str, tuple[dict[str, object], ...], dict[str, object]]:
        """Decision search in envelope body-producer shape (contract §3.2).

        Runs the chunk pipeline scoped to ``origin="decision_record"`` and to
        the packages ``scope`` / ``package`` select (:meth:`_selected_packages`
        — the request's frozen ``search_codebase`` selectors, not new MCP
        parameters), collects the ``decision_id`` backlinks in rank order,
        hydrates each to its structured record, and renders — returning one
        §3.2 ``kind="decision"`` row per rendered record alongside the markdown
        body. Zero hits ⇒ an empty-state line plus the overview recovery
        pointer (spec §D1 empty contract) and no rows. Decision rows carry the
        record id with null path/span — locators stay in ``get_why`` (§3.6).
        ``branch``: the branch read (§6.4, #313; ``None`` the served default).
        """
        packages = await self._selected_packages(scope, package, branch)
        body, hydrated, scores = await self._search_hydrated(query, packages, branch)
        items = tuple(_decision_item(r, scores.get(r.id or -1, 0.0)) for r in hydrated)
        return body, items, self._zero_hit_extras(hydrated, tool="search_codebase")

    async def why_search(self, query: str, *, branch: str | None = None) -> WhyBody:
        """``get_why`` query mode with §3.6 items — same retrieval/render run
        as :meth:`search_with_items` (one authority), different row shape:
        ``get_why`` rows carry the decision identity + evidence locators, not
        the §3.2 search-ranking fields. Always the project's decisions: get_why
        has no corpus selector, and its §3.6 rows carry no package (#346).
        ``branch`` selects the branch's decisions (spec §6.4, #313)."""
        corpus = PROJECT_DECISION_CORPUS
        body, hydrated, _scores = await self._search_hydrated(query, corpus, branch)
        return body, _why_items(hydrated), self._zero_hit_extras(hydrated, tool="get_why")

    async def _selected_packages(
        self, scope: SearchScope, package: str, branch: str | None
    ) -> tuple[str, ...]:
        """The packages one ``search_codebase(kind="decision")`` covers (#346).

        ``package`` wins when set. ``scope="deps"`` is every dependency with
        records (none ⇒ an empty corpus, never the project). ``scope="project"``
        and ``scope="all"`` — the default, which the server cannot tell from an
        explicit value — are the project, so dependency decisions answer only
        when a request asks for them and the default output stays unchanged.
        """
        if package:
            return (package,)
        if scope != SearchScope.DEPENDENCIES_ONLY:
            return PROJECT_DECISION_CORPUS
        async with self.uow_factory() as uow:
            listed = await uow.decisions.list_packages(branch=branch)
        return tuple(name for name in listed if name != PROJECT_PACKAGE_NAME)

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
        self, query: str, packages: tuple[str, ...], branch: str | None
    ) -> tuple[str, tuple[DecisionRecord, ...], dict[int, float]]:
        """Shared retrieval/hydration/render for the two search surfaces —
        returns ``(body, rendered_records, chunk_scores)``; zero hits (or an
        empty ``packages`` corpus) ⇒ the empty-state body with no records.

        ``packages`` rides the retrieval pre-filter, not a filter after it: the
        decision preset ranks a fixed number of rows, so an unscoped query let
        dependency decisions take every slot and leave the project none (#346).
        Hydration is by id across packages, and keeps only the requested
        packages — a guard, since both retrieval branches honour the pushdown.
        A named ``branch`` pins the query (#312) and the hydration (#313).
        """
        if not packages:
            return self._empty_state_body(), (), {}
        chunk_query = SearchQuery(
            terms=query,
            pre_filter=decision_pre_filter_for_packages(packages),
            branch=branch or "",
        )
        ranked = await self.docs.ranked(chunk_query)
        ordered_ids = _decision_ids_in_rank_order(ranked.items)[: self.default_limit]
        if not ordered_ids:
            return self._empty_state_body(), (), {}
        async with self.uow_factory() as uow:
            records = await uow.decisions.list_by_ids(ordered_ids, branch=branch)
        by_id = {r.id: r for r in records if r.id is not None and r.package in packages}
        hydrated = tuple(by_id[i] for i in ordered_ids if i in by_id)
        if not hydrated:
            return self._empty_state_body(), (), {}
        body = format_decision_records(
            hydrated, heading=f"Decisions matching {query!r}", pointers=self.pointers
        )
        return body, hydrated, _decision_scores(ranked.items)

    def _empty_state_body(self) -> str:
        """``No decisions found.`` plus the zero-hit overview pointer."""
        # ADR 0007: the zero-hit overview pointer is flag-gated (search_zero_hit
        # off restores the bare pre-pointer body byte-for-byte).
        empty_body = EMPTY_DECISIONS_MSG
        if self.suggestions.search_zero_hit:
            zero_hit = self.pointers.row_for(ResponseKind.ZERO_HIT)
            empty_body += f"\n{render_pointer_bundle(zero_hit, '')}"
        return empty_body

    async def for_targets(self, targets: list[str], *, query: str = "") -> str:
        """Text-only façade over :meth:`why_targets` — one dispatch run, first
        element (same pattern as :meth:`search`)."""
        body, _items, _extras = await self.why_targets(targets, query=query)
        return body

    async def why_targets(
        self, targets: list[str], *, query: str = "", branch: str | None = None
    ) -> WhyBody:
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

        The target is the corpus selector (#346): each GOVERNS edge names the
        package that mined its decision, so a dependency symbol surfaces that
        dependency's decisions and a project symbol the project's — even when a
        dependency decision carries the same title, hence the same key.
        ``branch`` selects the branch's edges and records (spec §6.4, #313).
        """
        async with self.uow_factory() as uow:
            # Resolve each target's governing decisions through the GOVERNS
            # edges INSIDE the same UoW (one read scope); each package's
            # records load once per call, on first use.
            cache: RecordsByKeyPerPackage = {}
            matches = [await self._governing_records(uow, t, cache, branch) for t in targets]
        query_tokens = _title_tokens(query) if query else frozenset()
        visible = [
            _visible_records(matched, query_tokens, self.default_limit) for matched in matches
        ]
        cards = [
            _render_target_card(target, shown, self.pointers)
            for target, shown in zip(targets, visible, strict=True)
        ]
        surfaced = [record for shown in visible for record in shown]
        return "\n\n".join(cards), _why_items(surfaced), {}

    async def _governing_records(
        self,
        uow: UnitOfWork,
        target: str,
        records_by_key_per_package: RecordsByKeyPerPackage,
        branch: str | None,
    ) -> list[DecisionRecord]:
        """Records whose GOVERNS edge resolves to ``target`` (parent fallback).

        Reduces the target to a qname, asks the reference graph which decisions
        govern it (``find_governing``, resolver-backed), and maps each returned
        ``(package, key)`` to that package's record. On no inbound edge, walks
        the target's parent modules and returns the first governed parent's
        records (§D11 fallback).
        """
        classification = _classify_target(target)
        primary = _path_to_qname(target) if classification == "path" else target
        for qname in (primary, *_parent_modules(target, classification)):
            found = await records_for_governs_edges_to_qname(
                uow, qname, records_by_key_per_package, branch
            )
            if found:
                found.sort(key=lambda r: r.id or 0)
                return found
        return []

    async def dashboard(self) -> str:
        """Text-only façade over :meth:`why_dashboard` — one run, first element."""
        body, _items, _extras = await self.why_dashboard()
        return body

    async def why_dashboard(self, *, branch: str | None = None) -> WhyBody:
        """Governance rollup over all decisions (§D11 dashboard mode).

        One UoW read gathers records + centrality signals; counts by status and
        source, the stalest five active records, the five ``proposed`` records
        awaiting review, and the top-centrality module qnames with no inbound
        GOVERNS edge (the graph anti-join, §D18 — not an ``affected_qnames``
        scan). Centrality mirrors :class:`OverviewService`: pagerank, in-degree
        fallback — the shared §D6/§D11 degradation rule. items[] carry one §3.6
        row per record the rollup SURFACES (stalest, then awaiting review;
        deduped on ``decision_id``) so a harness can attribute the rollup.
        Every read is ``branch``'s (spec §6.4, #313; ``None`` — the served default).
        """
        project = PROJECT_PACKAGE_NAME
        async with self.uow_factory() as uow:
            records = await uow.decisions.list_for_package(project, branch=branch)
            scores = await uow.node_scores.for_package(project, branch=branch)
            degrees = await uow.references.degree_by_package(project, branch=branch)
            governed = await uow.references.governed_qnames(branch=branch)
        summary = build_decision_dashboard(records, scores, degrees, governed)
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
    normalized-title identity the GOVERNS graph keys on. ``package`` is the
    record's own: a dependency's decision answers under its package (#346).
    Path/span are null by contract: decision locators live in ``get_why``
    items (§3.6).
    """
    return {
        "kind": "decision",
        "id": str(record.id) if record.id is not None else "",
        "qualified_name": decision_key(record.title),
        "package": record.package,
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


def _render_target_card(
    target: str, visible: Sequence[DecisionRecord], pointers: PointerTableConfig
) -> str:
    """Render the ``## Target `` card for one target (helper for ``why_targets``).

    ``visible`` is the already-filtered/sliced record set (``_visible_records``
    — the same set items[] attribute). ``format_decision_records`` is the single
    render authority — it emits an H1 (``# {heading}``) doc, so the target card
    promotes that to the ``## Target `` H2 the §D11 target mode wants by
    prefixing one ``#``. Record blocks never start a line with ``#`` (they use
    bold titles + ``-`` bullets), so promoting the leading heading is safe and
    touches only the first line.
    """
    body = format_decision_records(tuple(visible), heading=f"Target {target}", pointers=pointers)
    return "#" + body if body.startswith("# ") else body
