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

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    Chunk,
    ChunkFilterField,
    ChunkList,
)
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
# context window. When the tree is bigger, _fit_trees_to_budget prunes
# deepest/excess nodes to fit — graceful degradation instead of a 400
# context_length_exceeded. Tunable per deployment via the `max_tree_words` param.
# NOTE: words != tokens — a serialized-JSON word is ~1.5-3 model tokens, so size
# this to the model: a 128K-token model fits comfortably under ~40-80K words.
_DEFAULT_MAX_TREE_WORDS = 300_000


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
    # overflow on large repos. See _DEFAULT_MAX_TREE_WORDS.
    max_tree_words: int = field(default=_DEFAULT_MAX_TREE_WORDS, kw_only=True)

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

            tree_jsons = [_pageindex_with_qname(t) for t in trees]
            tree_jsons, truncated = _fit_trees_to_budget(tree_jsons, self.max_tree_words)
            if truncated:
                log.warning(
                    "llm_tree_reasoning: project tree exceeded max_tree_words=%d; "
                    "pruned deepest/excess nodes to fit the LLM context window. "
                    "Large repo — raise max_tree_words or use a larger-context "
                    "model for full tree coverage.",
                    self.max_tree_words,
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
        if self.max_tree_words != _DEFAULT_MAX_TREE_WORDS:
            out["max_tree_words"] = self.max_tree_words
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
            max_tree_words=data.get("max_tree_words", _DEFAULT_MAX_TREE_WORDS),
        )


def _pageindex_with_qname(node: DocumentNode) -> dict[str, Any]:
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

    So this helper deliberately bypasses ``to_pageindex_json`` and builds
    a tight shape: ``qualified_name`` (the join key), ``title``, ``kind``,
    ``summary``, and recursive ``nodes``. Source-line spans are dropped
    too — the LLM doesn't pick on byte offsets, and omitting them keeps
    the prompt token budget tight.
    """
    return {
        "qualified_name": node.qualified_name,
        "title": node.title,
        "kind": node.kind.value,
        "summary": node.summary,
        "nodes": [_pageindex_with_qname(child) for child in node.children],
    }


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
        return {
            "qualified_name": node["qualified_name"],
            "title": node["title"],
            "kind": node["kind"],
            "summary": node["summary"],
            "nodes": [rebuild(c) for c in node["nodes"] if id(c) in kept],
        }

    return [rebuild(n) for n in tree_jsons if id(n) in kept]


def _fit_trees_to_budget(
    tree_jsons: list[dict[str, Any]], max_words: int
) -> tuple[list[dict[str, Any]], bool]:
    """Prune the LLM-visible tree so its serialized JSON is <= ``max_words`` words.

    Returns ``(trees, truncated)``. Halving the node budget until it fits
    guarantees a bounded prompt for an arbitrarily large repo, so the tree step
    degrades gracefully instead of raising a 400 context_length_exceeded. The
    common (small-repo) case returns the tree unchanged after one size check.
    """
    if _word_count(tree_jsons) <= max_words:
        return tree_jsons, False
    budget = _total_nodes(tree_jsons)
    pruned = tree_jsons
    while budget > 1:
        budget //= 2
        pruned = _prune_to_node_budget(tree_jsons, budget)
        if _word_count(pruned) <= max_words:
            return pruned, True
    return _prune_to_node_budget(tree_jsons, 1), True


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
