"""LlmTreeReasoningStep — PageIndex-style vectorless RAG.

Reads ``__project__`` ``DocumentNode`` trees, serializes them via
:meth:`DocumentNode.to_pageindex_json` (augmented with ``qualified_name``
so the LLM can return symbol-stable identifiers), renders a Jinja2
prompt, sends to a configured :class:`LlmClient`, parses
``{"thinking", "node_list": [...]}``, fetches matching chunks via
``uow.chunks``, scores by the LLM's pick order, and writes a
:class:`ChunkList` to ``state.scratch[output_scratch_key]``.

Scope: ``__project__`` only — dependencies stay in the BM25 / dense
retrieval branches. Composes via ``state.scratch[output_scratch_key]``
(default ``"tree.ranked"``), so downstream fusion steps
(:class:`RRFFusionStep`, :class:`WeightedScoreInterpolationStep`) can
fuse this branch with hybrid branches by name.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.strategies.chunkers._shared import (
    _collapse_ws,
    _header_from_text,
)
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    Chunk,
    ChunkFilterField,
    ChunkList,
)
from pydocs_mcp.retrieval.llm_clients.model_budget import derive_max_tree_words
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.prompts import render_prompt
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.storage.protocols import LlmClient, UnitOfWork

log = logging.getLogger(__name__)

# WHY: single source of truth for every default — referenced from field
# defaults, to_dict omit-when-default, and from_dict YAML-fallback. The
# project-wide convention; see CLAUDE.md "Default values: single source
# of truth".
_DEFAULT_PROMPT_TEMPLATE = "tree_reasoning_pydocs_v1"
_DEFAULT_OUTPUT_SCRATCH_KEY = "tree.ranked"
_DEFAULT_REFERENCE_NEIGHBORS_LIMIT = 5
_DEFAULT_NAME = "llm_tree_reasoning"
# WHY: bound the serialized tree sent to the LLM, measured in whitespace-
# separated WORDS, so a large repo's project tree can't overflow the model
# context window. When over budget, _fit_trees_to_budget drops per-node doc
# excerpts first, then prunes nodes — graceful degradation instead of a 400
# context_length_exceeded. The budget DEFAULTS to None = "derive from the
# configured LLM's context window" (model_budget.derive_max_tree_words), so it
# auto-scales across models; an explicit int in YAML overrides it.
# Docstring excerpt depth fed to the LLM per node. "sections" = first line +
# Args/Returns/Raises blocks (best discriminator-per-token); "full" = whole
# docstring (bounded); "off" = no doc field. YAML-tunable per deployment.
_DEFAULT_DOC_EXCERPT = "sections"
_DEFAULT_DOC_EXCERPT_MAX_CHARS = 240
_DOC_EXCERPT_MODES = ("sections", "full", "off")
# Cap on the per-node enriched title (decorators + signature) so a giant
# multi-line signature can't dominate the prompt. The header scanner + its
# scan-limit live in the shared chunker utils (``_header_from_text`` in
# extraction/strategies/chunkers/_shared.py).
_TITLE_MAX_CHARS = 200
# Section markers the "sections" doc excerpt recognizes (Google + NumPy
# headers, matched case-insensitively).
_DOC_SECTION_HEADERS = frozenset(
    {
        "args",
        "arguments",
        "parameters",
        "params",
        "returns",
        "return",
        "yields",
        "yield",
        "raises",
        "raise",
    }
)
# Sphinx / reST field-list prefixes (one field per line) the excerpt keeps.
_SPHINX_FIELD_PREFIXES = (
    ":param",
    ":parameter",
    ":returns",
    ":return",
    ":rtype",
    ":raises",
    ":raise",
    ":yields",
    ":yield",
    ":type",
)


@step_registry.register("llm_tree_reasoning")
@dataclass(frozen=True, slots=True)
class LlmTreeReasoningStep(RetrieverStep):
    """Vectorless RAG: LLM walks the project tree and picks chunks.

    See module docstring. Strict-gate ``from_dict`` mirrors
    :class:`LoadExistingChunkHashesStage` — ``context.llm_client`` and
    ``context.uow_factory`` must be non-None at YAML-build time, or a
    :class:`ValueError` is raised at construction (not at first
    ``run()``) so misconfigured pipelines fail at startup.
    """

    llm_client: LlmClient = field(kw_only=True)
    uow_factory: Callable[[], UnitOfWork] = field(kw_only=True)
    prompt_template: str = field(default=_DEFAULT_PROMPT_TEMPLATE, kw_only=True)
    include_references: bool = field(default=False, kw_only=True)
    reference_neighbors_limit: int = field(
        default=_DEFAULT_REFERENCE_NEIGHBORS_LIMIT,
        kw_only=True,
    )
    output_scratch_key: str = field(
        default=_DEFAULT_OUTPUT_SCRATCH_KEY,
        kw_only=True,
    )
    name: str = field(default=_DEFAULT_NAME, kw_only=True)
    # Cap on the serialized tree (words) handed to the LLM; prevents context
    # overflow on large repos. None (default) = auto-derive from the LLM's
    # context window at run time; an explicit int overrides. See the WHY above.
    max_tree_words: int | None = field(default=None, kw_only=True)
    # Docstring excerpt depth per node ("sections" | "full" | "off") and its
    # char cap. Enriches the LLM-visible tree with the author's own words
    # beyond the 140-char summary first line. See _DEFAULT_DOC_EXCERPT.
    doc_excerpt: str = field(default=_DEFAULT_DOC_EXCERPT, kw_only=True)
    doc_excerpt_max_chars: int = field(
        default=_DEFAULT_DOC_EXCERPT_MAX_CHARS,
        kw_only=True,
    )

    async def run(self, state: RetrieverState) -> RetrieverState:
        async with self.uow_factory() as uow:
            # DocumentTreeStore.load_all_in_package returns a
            # dict[str, DocumentNode] keyed by module qualified_name
            # (Protocol §12.2). Iterate over .values() so the rest of
            # the step works on the trees themselves, not the keys.
            trees_by_module = await uow.trees.load_all_in_package(
                PROJECT_PACKAGE_NAME,
            )
            if not trees_by_module:
                return state
            trees = tuple(trees_by_module.values())

            doc_truncations: list[int] = []
            tree_jsons = [
                _pageindex_with_qname(
                    t,
                    doc_mode=self.doc_excerpt,
                    doc_max_chars=self.doc_excerpt_max_chars,
                    _truncations=doc_truncations,
                )
                for t in trees
            ]
            if doc_truncations:
                log.warning(
                    "llm_tree_reasoning: %d docstring excerpt(s) hit the "
                    "doc_excerpt_max_chars=%d cap and were truncated. Raise "
                    "doc_excerpt_max_chars (or set doc_excerpt: off) if you want "
                    "different per-node docstring coverage.",
                    len(doc_truncations),
                    self.doc_excerpt_max_chars,
                )
            # Budget defaults to the model's context window (auto-scales across
            # LLMs); an explicit max_tree_words overrides. model_name is
            # guaranteed by the LlmClient Protocol.
            effective_budget = (
                self.max_tree_words
                if self.max_tree_words is not None
                else derive_max_tree_words(self.llm_client.model_name)
            )
            tree_jsons, reduction = _fit_trees_to_budget(tree_jsons, effective_budget)
            if reduction == "docs":
                log.warning(
                    "llm_tree_reasoning: project tree exceeded the %d-word budget "
                    "(model=%s); dropped per-node doc excerpts to fit while keeping "
                    "all nodes. Raise max_tree_words or use a larger-context model "
                    "for full docstring coverage.",
                    effective_budget,
                    self.llm_client.model_name,
                )
            elif reduction == "nodes":
                log.warning(
                    "llm_tree_reasoning: project tree exceeded the %d-word budget "
                    "(model=%s) even without doc excerpts; pruned deepest/excess "
                    "nodes to fit. Large repo — raise max_tree_words or use a "
                    "larger-context model for full tree coverage.",
                    effective_budget,
                    self.llm_client.model_name,
                )
            prompt = render_prompt(
                self.prompt_template,
                query=state.query.terms,
                trees=tree_jsons,
            )
            response = await self.llm_client.chat(
                [{"role": "user", "content": prompt}],
                response_format="json_object",
                temperature=0.0,
            )

            picked = _parse_node_list(response, trees)
            if not picked:
                return state

            # WHY: fetch by package only (the InMemoryChunkStore and the
            # real SqliteChunkRepository both support
            # filter={"package": ...}) then filter by qualified_name
            # client-side. The {"in": [...]} operator on metadata keys
            # isn't part of the ChunkStore contract — fetching one page
            # of project chunks and filtering in Python keeps the step
            # independent of repository-specific filter syntax.
            all_chunks = await uow.chunks.list(
                filter={ChunkFilterField.PACKAGE.value: PROJECT_PACKAGE_NAME},
            )
            picked_set = set(picked)
            matched = tuple(c for c in all_chunks if c.metadata.get("qualified_name") in picked_set)
            if not matched:
                return state

            ranked = _score_by_position(matched, picked)
            new_scratch = dict(state.scratch)
            new_scratch[self.output_scratch_key] = ranked

            if self.include_references:
                # WHY filter direction: ``to_name`` (the picked qnames) —
                # surfaces CALLERS of the picked nodes, which is the more
                # useful direction for "what calls this" queries.
                # NodeReference.from_node_id is a DocumentNode node_id,
                # NOT a qualified_name; filtering on the from-side would
                # need an extra qname→node_id translation step (and the
                # node_id isn't persisted into chunk.metadata at all).
                # ``to_name`` IS a dotted qname (it's the reference target
                # the resolver matched against), so it's the natural join
                # key here.
                #
                # WHY find_by_name per qname: the ReferenceStore Protocol
                # surface only exposes find_by_name / find_callers /
                # find_callees — no generic list({"to_name": {"in": [...]}})
                # operator. Iterating with one call per picked qname keeps
                # this step pure against the Protocol (same shape as
                # InMemoryReferenceStore and SqliteReferenceStore).
                #
                # Performance: asyncio.gather fans the per-qname lookups
                # out concurrently. Note — when running through the
                # SqliteUnitOfWork, the underlying find_by_name calls
                # serialize on the UoW's held-connection asyncio.Lock,
                # so the wall-clock win here comes from overlapping
                # asyncio.to_thread dispatch overhead rather than from
                # parallel SQLite queries. For non-UoW callers (e.g.
                # in-memory test stores, or a future Postgres adapter
                # with multiple connections), the queries can truly run
                # in parallel. Dedup + per-target neighbors cap still
                # apply downstream so observable output is unchanged.
                caller_lists = await asyncio.gather(
                    *(uow.references.find_by_name(qname) for qname in picked),
                )
                surfaced: list = []
                seen: set[tuple[str, str, str, str]] = set()
                for callers in caller_lists:
                    # Apply per-node neighbors cap before deduping so the
                    # bound is per-target, not global.
                    for ref in callers[: self.reference_neighbors_limit]:
                        key = (
                            ref.from_package,
                            ref.from_node_id,
                            ref.to_name,
                            str(ref.kind),
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        surfaced.append(ref)
                # WHY: this scratch key is reserved for future formatters/steps
                # that want to surface reference-graph context alongside picked
                # chunks. No shipped step consumes it today; the field is opt-in
                # (default include_references=False) and adding a consumer is
                # one-step extension work per EXTENSIONS.md.
                new_scratch[f"{self.output_scratch_key}.refs"] = tuple(surfaced)

            return replace(state, scratch=new_scratch)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "llm_tree_reasoning"}
        if self.prompt_template != _DEFAULT_PROMPT_TEMPLATE:
            out["prompt_template"] = self.prompt_template
        if self.include_references:
            out["include_references"] = True
        if self.reference_neighbors_limit != _DEFAULT_REFERENCE_NEIGHBORS_LIMIT:
            out["reference_neighbors_limit"] = self.reference_neighbors_limit
        if self.output_scratch_key != _DEFAULT_OUTPUT_SCRATCH_KEY:
            out["output_scratch_key"] = self.output_scratch_key
        if self.name != _DEFAULT_NAME:
            out["name"] = self.name
        if self.max_tree_words is not None:
            out["max_tree_words"] = self.max_tree_words
        if self.doc_excerpt != _DEFAULT_DOC_EXCERPT:
            out["doc_excerpt"] = self.doc_excerpt
        if self.doc_excerpt_max_chars != _DEFAULT_DOC_EXCERPT_MAX_CHARS:
            out["doc_excerpt_max_chars"] = self.doc_excerpt_max_chars
        return out

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        context: BuildContext,
    ) -> LlmTreeReasoningStep:
        # WHY: strict-gate at YAML-build time, not at first run() — a
        # missing llm_client / uow_factory is a wiring bug in the
        # composition root (server.py / __main__.py), not user input.
        # Surfacing it at startup gives an immediate, contextual error
        # rather than the cryptic "NoneType has no attribute chat" the
        # pipeline would raise on its first query.
        if context.llm_client is None:
            raise ValueError(
                "LlmTreeReasoningStep requires BuildContext.llm_client. "
                "Production wiring in __main__.py / server.py sets this "
                "via build_llm_client(config.llm); tests must pass it "
                "explicitly.",
            )
        if context.uow_factory is None:
            raise ValueError(
                "LlmTreeReasoningStep requires BuildContext.uow_factory.",
            )
        doc_excerpt = data.get("doc_excerpt", _DEFAULT_DOC_EXCERPT)
        if doc_excerpt not in _DOC_EXCERPT_MODES:
            raise ValueError(
                f"doc_excerpt must be one of {_DOC_EXCERPT_MODES}; got {doc_excerpt!r}",
            )
        doc_excerpt_max_chars = data.get(
            "doc_excerpt_max_chars",
            _DEFAULT_DOC_EXCERPT_MAX_CHARS,
        )
        # A non-positive cap would silently under-cap (negative slice drops the
        # tail instead of bounding) — fail fast at YAML-build time.
        if not isinstance(doc_excerpt_max_chars, int) or doc_excerpt_max_chars < 1:
            raise ValueError(
                f"doc_excerpt_max_chars must be a positive int; got {doc_excerpt_max_chars!r}",
            )
        # None (or absent) = auto-derive the budget from the LLM context window;
        # an explicit value must be a positive int.
        max_tree_words = data.get("max_tree_words")
        if max_tree_words is not None and (
            not isinstance(max_tree_words, int) or max_tree_words < 1
        ):
            raise ValueError(
                f"max_tree_words must be a positive int or null (auto); got {max_tree_words!r}",
            )
        return cls(
            llm_client=context.llm_client,
            uow_factory=context.uow_factory,
            prompt_template=data.get("prompt_template", _DEFAULT_PROMPT_TEMPLATE),
            include_references=data.get("include_references", False),
            reference_neighbors_limit=data.get(
                "reference_neighbors_limit",
                _DEFAULT_REFERENCE_NEIGHBORS_LIMIT,
            ),
            output_scratch_key=data.get(
                "output_scratch_key",
                _DEFAULT_OUTPUT_SCRATCH_KEY,
            ),
            name=data.get("name", _DEFAULT_NAME),
            max_tree_words=max_tree_words,
            doc_excerpt=doc_excerpt,
            doc_excerpt_max_chars=doc_excerpt_max_chars,
        )


def _enriched_title(node: DocumentNode) -> str:
    """Decorators + real signature for code nodes; the plain title otherwise.

    Falls back to ``node.title`` when the derived header doesn't look like a
    signature (e.g. synthetic nodes whose ``text`` isn't real source), so
    only genuine ``def`` / ``class`` headers replace the bare ``def foo()``
    title. The header scanner (``_header_from_text``) and whitespace collapse
    (``_collapse_ws``) are shared with the chunker (see
    ``extraction/strategies/chunkers/_shared.py``). Bounded by
    ``_TITLE_MAX_CHARS``. Decorators are NOT in ``node.text`` (Python 3.11
    ``lineno`` points at ``def`` / ``class``), so they're prepended separately.
    """
    decorators = node.extra_metadata.get("decorators") or ()
    if node.kind in (NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.CLASS):
        header = _header_from_text(node.text, max_chars=_TITLE_MAX_CHARS)
        if not header.startswith(("def ", "async def ", "class ")):
            header = node.title
    else:
        header = node.title
    if decorators:
        header = f"{' '.join(str(d) for d in decorators)} {header}".strip()
    return header[:_TITLE_MAX_CHARS]


def _doc_sections(text: str) -> str:
    """First line + parameter / return / raise blocks (Google/NumPy/Sphinx)."""
    lines = text.splitlines()
    kept: list[str] = []
    first = lines[0].strip()
    if first:
        kept.append(first)
    in_section = False
    i = 1
    while i < len(lines):
        stripped = lines[i].strip()
        head_word = stripped.rstrip(":").strip().lower()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        next_is_underline = set(nxt) == {"-"} and len(nxt) >= 3
        is_underline = set(stripped) == {"-"} and len(stripped) >= 3
        is_google_header = head_word in _DOC_SECTION_HEADERS and stripped.endswith(":")
        is_numpy_header = head_word in _DOC_SECTION_HEADERS and next_is_underline
        is_sphinx = any(stripped.lower().startswith(p) for p in _SPHINX_FIELD_PREFIXES)
        if is_underline:
            # Dashes belong to the header on the preceding line. A RECOGNIZED
            # NumPy header already toggled capture via is_numpy_header; an
            # UNRECOGNIZED one (Notes / Examples / See Also / a bare rule) must
            # NOT turn capture on, or its low-signal body leaks in. So just
            # skip the underline either way — never toggle, never append.
            i += 1
            continue
        if is_google_header or is_numpy_header:
            in_section = True
            kept.append(stripped)
        elif is_sphinx:
            kept.append(stripped)
            in_section = False
        elif in_section:
            if not stripped:
                in_section = False
            else:
                kept.append(stripped)
        i += 1
    return " ".join(kept)


def _doc_excerpt(docstring: str, mode: str, max_chars: int) -> str:
    """Bounded docstring excerpt for the LLM-visible node.

    ``"off"`` → empty. ``"full"`` → the whole docstring, whitespace-
    collapsed. ``"sections"`` (and any unknown mode) → the first line plus
    the Args/Parameters/Returns/Yields/Raises blocks (Google + NumPy headers
    and Sphinx ``:param:``-style field lists) — the author's own words about
    inputs/outputs beyond the 140-char summary. Always capped at
    ``max_chars``.
    """
    excerpt, _ = _doc_excerpt_with_flag(docstring, mode, max_chars)
    return excerpt


def _doc_excerpt_with_flag(docstring: str, mode: str, max_chars: int) -> tuple[str, bool]:
    """Like :func:`_doc_excerpt`, but also report whether the cap truncated it.

    The boolean lets the renderer surface one aggregated warning per query
    when emitted excerpts were cut — mirroring the ``max_tree_words``
    over-budget warning — instead of silently dropping docstring content.
    """
    if not docstring or mode == "off":
        return "", False
    text = docstring.strip()
    if not text:
        return "", False
    # Clamp so a non-positive cap can't become a tail-dropping negative slice;
    # the "always bounded (0 -> '')" contract holds for any caller.
    cap = max(0, max_chars)
    full = _collapse_ws(text) if mode == "full" else _collapse_ws(_doc_sections(text))
    return full[:cap], len(full) > cap


def _pageindex_with_qname(
    node: DocumentNode,
    *,
    doc_mode: str = _DEFAULT_DOC_EXCERPT,
    doc_max_chars: int = _DEFAULT_DOC_EXCERPT_MAX_CHARS,
    _truncations: list[int] | None = None,
) -> dict[str, Any]:
    """Build the LLM-visible tree shape — only fields the prompt asks for.

    The shipped :meth:`DocumentNode.to_pageindex_json` emits ``node_id``,
    ``source_path``, ``start_index``, ``end_index`` (because LookupService
    needs that shape and a contract test in
    ``tests/extraction/test_document_node_lookup_contract.py`` pins it),
    but the LLM here MUST NOT see ``node_id``: the prompts ask for
    ``qualified_name`` (a stable symbol path), while ``node_id`` is a
    per-extraction auto-generated content-hash identifier that doesn't
    exist in chunk metadata. If the LLM saw ``node_id`` it would be an
    attractive nuisance — the model would naturally pick the shorter,
    flatter-looking string, and downstream :func:`_parse_node_list` would
    silently drop every pick (because it filters against the
    ``qualified_name`` set, the only field that joins back to
    ``chunk.metadata["qualified_name"]`` via :func:`flatten_to_chunks`).

    So this helper deliberately bypasses ``to_pageindex_json`` and builds a
    tight shape: ``qualified_name`` (the join key), an enriched ``title``
    (decorators + real signature via :func:`_enriched_title`), ``kind``,
    ``summary``, an optional bounded ``doc`` excerpt, and recursive
    ``nodes``. ``doc`` is omitted when empty or identical to ``summary``
    (summary is already the docstring's first line — duplicating it would
    just burn tokens). Source-line spans are dropped — the LLM doesn't pick
    on byte offsets, and omitting them keeps the prompt budget tight.
    """
    out: dict[str, Any] = {
        "qualified_name": node.qualified_name,
        "title": _enriched_title(node),
        "kind": node.kind.value,
        "summary": node.summary,
    }
    docstring = str(node.extra_metadata.get("docstring", "") or "")
    excerpt, truncated = _doc_excerpt_with_flag(docstring, doc_mode, doc_max_chars)
    # Omit doc when it adds nothing beyond summary: empty, exactly summary, or
    # merely a (possibly longer) cut of the docstring's first line — summary
    # already carries that line, so a duplicate just burns prompt budget. A
    # richer excerpt (first line + Args/Returns/Raises) is longer than the
    # first line, so it survives this check and is kept.
    first_line = _collapse_ws(docstring.strip().split("\n", 1)[0])
    if (
        excerpt
        and excerpt != node.summary
        and not (first_line and excerpt == first_line[: len(excerpt)])
    ):
        out["doc"] = excerpt
        # Record truncation only for an EMITTED doc, so the aggregated warning
        # reflects real dropped content (not excerpts that get omitted anyway).
        if truncated and _truncations is not None:
            _truncations.append(1)
    out["nodes"] = [
        _pageindex_with_qname(
            child,
            doc_mode=doc_mode,
            doc_max_chars=doc_max_chars,
            _truncations=_truncations,
        )
        for child in node.children
    ]
    return out


def _total_nodes(tree_jsons: list[dict[str, Any]]) -> int:
    """Count every node across the pageindex forest (roots + descendants)."""
    return sum(1 + _total_nodes(n["nodes"]) for n in tree_jsons)


def _word_count(tree_jsons: list[dict[str, Any]]) -> int:
    """Whitespace-separated word count of the serialized pageindex forest."""
    return len(json.dumps(tree_jsons).split())


def _prune_to_node_budget(tree_jsons: list[dict[str, Any]], max_nodes: int) -> list[dict[str, Any]]:
    """Keep the first ``max_nodes`` nodes in BREADTH-FIRST order across the forest.

    BFS keeps the shallow structure the LLM picks from (modules, then top-level
    classes/functions, then methods) and drops the deepest/excess nodes. A kept
    node's ancestors are always kept (BFS dequeues a parent before enqueueing its
    children), so no orphans — children are simply filtered to the kept set.
    """
    from collections import deque

    kept: set[int] = set()
    queue: deque[dict[str, Any]] = deque(tree_jsons)
    while queue and len(kept) < max_nodes:
        node = queue.popleft()
        kept.add(id(node))
        queue.extend(node["nodes"])

    def rebuild(node: dict[str, Any]) -> dict[str, Any]:
        rebuilt: dict[str, Any] = {
            "qualified_name": node["qualified_name"],
            "title": node["title"],
            "kind": node["kind"],
            "summary": node["summary"],
        }
        # Preserve the optional enriched doc excerpt through pruning.
        if "doc" in node:
            rebuilt["doc"] = node["doc"]
        rebuilt["nodes"] = [rebuild(c) for c in node["nodes"] if id(c) in kept]
        return rebuilt

    return [rebuild(n) for n in tree_jsons if id(n) in kept]


def _strip_docs(tree_jsons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deep-copy the forest with every optional ``doc`` excerpt removed.

    Node coverage (qualified_name / title / summary) is preserved — only the
    most-optional enrichment is dropped. This is the first budget lever, so a
    too-large tree loses docstring excerpts before it loses whole nodes.
    """

    def strip(node: dict[str, Any]) -> dict[str, Any]:
        out = {k: v for k, v in node.items() if k != "doc"}
        out["nodes"] = [strip(c) for c in node["nodes"]]
        return out

    return [strip(n) for n in tree_jsons]


def _fit_trees_to_budget(
    tree_jsons: list[dict[str, Any]], max_words: int
) -> tuple[list[dict[str, Any]], str]:
    """Reduce the LLM-visible tree to <= ``max_words`` words, content-first.

    Returns ``(trees, reduction)`` where ``reduction`` is:

    - ``""`` — fit as-is, no reduction.
    - ``"docs"`` — dropped the per-node ``doc`` excerpts; **every node is
      preserved**. Node coverage drives recall, so the optional docstring
      content is sacrificed first.
    - ``"nodes"`` — still over budget even without docs, so deepest/excess
      nodes were pruned (BFS halving) — graceful degradation instead of a 400
      context_length_exceeded.
    """
    if _word_count(tree_jsons) <= max_words:
        return tree_jsons, ""
    stripped = _strip_docs(tree_jsons)
    if _word_count(stripped) <= max_words:
        return stripped, "docs"
    budget = _total_nodes(stripped)
    pruned = stripped
    while budget > 1:
        budget //= 2
        pruned = _prune_to_node_budget(stripped, budget)
        if _word_count(pruned) <= max_words:
            return pruned, "nodes"
    return _prune_to_node_budget(stripped, 1), "nodes"


def _collect_qnames(node: DocumentNode, acc: set[str]) -> None:
    acc.add(node.qualified_name)
    for child in node.children:
        _collect_qnames(child, acc)


def _parse_node_list(
    response: str,
    trees: tuple[DocumentNode, ...],
) -> tuple[str, ...]:
    """Parse LLM response; return qualified_names that survive validation.

    Hallucinated IDs (the LLM returns a string not in the tree) are
    silently dropped — well-known LLM behavior; the step degrades
    gracefully to fewer chunks rather than crashing. A malformed
    ``node_list`` (non-list) raises ValueError so a broken prompt /
    LLM-format regression surfaces immediately.
    """
    data = json.loads(response)
    node_list = data.get("node_list", [])
    if not isinstance(node_list, list):
        raise ValueError(
            f"LLM response 'node_list' must be a list; got {type(node_list).__name__}",
        )
    known: set[str] = set()
    for tree in trees:
        _collect_qnames(tree, known)
    return tuple(qn for qn in node_list if isinstance(qn, str) and qn in known)


def _score_by_position(
    chunks: tuple[Chunk, ...],
    picked_qnames: tuple[str, ...],
) -> ChunkList:
    """Score each chunk by its position in the LLM's ``node_list``.

    Uses the project's :attr:`Chunk.relevance` field (not
    ``metadata["score"]``) to stay consistent with
    :class:`BM25ScorerStep` / :class:`RRFFusionStep` /
    :class:`WeightedScoreInterpolationStep` — those steps all read +
    write ``Chunk.relevance``, so downstream fusion sees a homogeneous
    score field across branches.
    """
    by_qname: dict[str, Chunk] = {c.metadata.get("qualified_name", ""): c for c in chunks}
    n = len(picked_qnames)
    scored: list[Chunk] = []
    for rank, qname in enumerate(picked_qnames):
        chunk = by_qname.get(qname)
        if chunk is None:
            continue
        scored.append(replace(chunk, relevance=1.0 - rank / n))
    return ChunkList(items=tuple(scored))


__all__ = ("LlmTreeReasoningStep",)
