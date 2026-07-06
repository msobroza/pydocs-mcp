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
from typing import Any, ClassVar

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    Chunk,
    ChunkFilterField,
    ChunkList,
)
from pydocs_mcp.retrieval.llm_clients.model_budget import derive_max_tree_tokens
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.prompts import render_prompt
from pydocs_mcp.retrieval.protocols import LlmClient
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    step_registry,
    step_to_yaml_dict,
    yaml_kwargs,
)
from pydocs_mcp.retrieval.tree_prompt.doc_excerpt import (
    DEFAULT_DOC_EXCERPT,
    DEFAULT_DOC_EXCERPT_MAX_CHARS,
    DOC_EXCERPT_MODES,
)
from pydocs_mcp.retrieval.tree_prompt.pageindex_serializer import pageindex_with_qname
from pydocs_mcp.retrieval.tree_prompt.tree_budget_fitter import fit_trees_to_budget
from pydocs_mcp.storage.protocols import UnitOfWork

log = logging.getLogger(__name__)

# WHY: single source of truth for every default — referenced from field
# defaults, to_dict omit-when-default, and from_dict YAML-fallback. The
# project-wide convention; see CLAUDE.md "Default values: single source
# of truth".
_DEFAULT_PROMPT_TEMPLATE = "tree_reasoning_pydocs_v1"
_DEFAULT_OUTPUT_SCRATCH_KEY = "tree.ranked"
_DEFAULT_REFERENCE_NEIGHBORS_LIMIT = 5
_DEFAULT_NAME = "llm_tree_reasoning"
# WHY: bound the serialized tree sent to the LLM, measured in REAL tiktoken
# TOKENS, so a large repo's project tree can't overflow the model context
# window. (Words badly under-count code — a 50K-word tree is ~170K tokens — so
# a word budget let prompts blow past 128K; tokens make the bound exact.) When
# over budget, fit_trees_to_budget drops per-node doc excerpts first, then
# prunes nodes — graceful degradation instead of a 400 context_length_exceeded.
# The budget DEFAULTS to None = "derive from the configured LLM's context
# window" (model_budget.derive_max_tree_tokens); an explicit int in YAML
# overrides it.


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
    # Cap on the serialized tree (tiktoken tokens) handed to the LLM; prevents
    # context overflow on large repos. None (default) = auto-derive from the
    # LLM's context window at run time; an explicit int overrides. See the WHY.
    max_tree_tokens: int | None = field(default=None, kw_only=True)
    # Docstring excerpt depth per node ("sections" | "full" | "off") and its
    # char cap. Enriches the LLM-visible tree with the author's own words
    # beyond the 140-char summary first line. See tree_prompt.doc_excerpt.DEFAULT_DOC_EXCERPT.
    doc_excerpt: str = field(default=DEFAULT_DOC_EXCERPT, kw_only=True)
    doc_excerpt_max_chars: int = field(
        default=DEFAULT_DOC_EXCERPT_MAX_CHARS,
        kw_only=True,
    )
    # Two-stage rerank mode. When True, the step restricts the LLM-visible tree
    # to the qualified_names of the INCOMING state.candidates (a prior BM25/dense
    # stage) and writes its ranked picks back to state.candidates — so the tree
    # reranks that candidate subset instead of walking the whole project tree,
    # and produces the pipeline's final ranking directly (no fusion step needed).
    rerank_candidates: bool = field(default=False, kw_only=True)
    _YAML_KEYS: ClassVar[tuple[str, ...]] = (
        "prompt_template",
        "include_references",
        "reference_neighbors_limit",
        "output_scratch_key",
        "name",
        "max_tree_tokens",
        "doc_excerpt",
        "doc_excerpt_max_chars",
        "rerank_candidates",
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

            # Two-stage rerank: restrict the LLM-visible tree to the incoming
            # candidates (a prior BM25/dense stage). Empty result = nothing
            # usable to rerank, so skip the LLM call entirely.
            if self.rerank_candidates:
                trees = _scope_trees_to_candidates(trees, state)
            if not trees:
                return state

            doc_truncations: list[int] = []
            tree_jsons = [
                pageindex_with_qname(
                    t,
                    doc_mode=self.doc_excerpt,
                    doc_max_chars=self.doc_excerpt_max_chars,
                    _truncations=doc_truncations,
                )
                for t in trees
            ]
            # Budget (tokens) defaults to a fraction of the model's context
            # window (auto-scales across LLMs); an explicit max_tree_tokens
            # overrides. model_name is guaranteed by the LlmClient Protocol and
            # selects the tiktoken encoding the pruner counts with.
            model_name = self.llm_client.model_name
            effective_budget = (
                self.max_tree_tokens
                if self.max_tree_tokens is not None
                else derive_max_tree_tokens(model_name)
            )
            tree_jsons, reduction = fit_trees_to_budget(tree_jsons, effective_budget, model_name)
            _log_reductions(
                doc_truncations,
                self.doc_excerpt_max_chars,
                reduction,
                effective_budget,
                model_name,
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
                if self.rerank_candidates:
                    # A silent passthrough once masqueraded as a real rerank
                    # run for weeks (PAGEINDEX_DIVS.md F6.4) — degradation to
                    # the stage-1 ranking must be observable.
                    log.warning(
                        "llm_tree_reasoning: rerank produced no valid picks "
                        "(empty or fully-hallucinated node_list); passing "
                        "stage-1 candidates through unchanged.",
                    )
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
                if self.rerank_candidates:
                    log.warning(
                        "llm_tree_reasoning: no picked qualified_name matched "
                        "a project chunk; passing stage-1 candidates through "
                        "unchanged.",
                    )
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

            # In rerank mode the tree's picks lead the pipeline output, and
            # the unpicked stage-1 remainder is backfilled after them — an
            # LLM omission must not drop a candidate stage 1 already
            # surfaced (PAGEINDEX_DIVS.md F6.1). The scratch key keeps the
            # picks-only list so fusion consumers read the genuine LLM
            # ranking.
            return replace(
                state,
                candidates=(
                    _backfill_unpicked(ranked, state.candidates)
                    if self.rerank_candidates
                    else state.candidates
                ),
                scratch=new_scratch,
            )

    def to_dict(self) -> dict[str, Any]:
        return step_to_yaml_dict(self, type_name="llm_tree_reasoning", keys=self._YAML_KEYS)

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
        kwargs = yaml_kwargs(data, cls, cls._YAML_KEYS)
        if kwargs["doc_excerpt"] not in DOC_EXCERPT_MODES:
            raise ValueError(
                f"doc_excerpt must be one of {DOC_EXCERPT_MODES}; got {kwargs['doc_excerpt']!r}",
            )
        doc_excerpt_max_chars = kwargs["doc_excerpt_max_chars"]
        # A non-positive cap would silently under-cap (negative slice drops the
        # tail instead of bounding) — fail fast at YAML-build time.
        if not isinstance(doc_excerpt_max_chars, int) or doc_excerpt_max_chars < 1:
            raise ValueError(
                f"doc_excerpt_max_chars must be a positive int; got {doc_excerpt_max_chars!r}",
            )
        # Migration aid: the budget is now token-based; reject the old word param.
        if "max_tree_words" in data:
            raise ValueError(
                "max_tree_words was renamed to max_tree_tokens (the budget is now "
                "measured in real tiktoken tokens, not words). Update your YAML.",
            )
        # None (or absent) = auto-derive the budget from the LLM context window;
        # an explicit value must be a positive int.
        max_tree_tokens = kwargs["max_tree_tokens"]
        if max_tree_tokens is not None and (
            not isinstance(max_tree_tokens, int) or max_tree_tokens < 1
        ):
            raise ValueError(
                f"max_tree_tokens must be a positive int or null (auto); got {max_tree_tokens!r}",
            )
        return cls(
            llm_client=context.llm_client,
            uow_factory=context.uow_factory,
            **kwargs,
        )


def _candidate_qnames(state: RetrieverState) -> set[str]:
    """Qualified names carried by the incoming candidates (a prior retrieval
    stage's output). Empty when there are no candidates / none carry a qname."""
    candidates = state.candidates
    if candidates is None:
        return set()
    return {qn for c in candidates.items if (qn := c.metadata.get("qualified_name"))}


def _filter_tree_to_qnames(node: DocumentNode, allowed: set[str]) -> DocumentNode | None:
    """Prune a tree to nodes whose qualified_name is in ``allowed``, keeping
    ancestor scaffolding so the LLM still sees structure. Returns None when
    neither the node nor any descendant survives."""
    kept = tuple(
        child for c in node.children if (child := _filter_tree_to_qnames(c, allowed)) is not None
    )
    if node.qualified_name in allowed or kept:
        return replace(node, children=kept)
    return None


def _scope_trees_to_candidates(
    trees: tuple[DocumentNode, ...],
    state: RetrieverState,
) -> tuple[DocumentNode, ...]:
    """Restrict the project trees to the incoming candidates' qualified_names.
    Empty result signals 'nothing to rerank' (the caller passes state through)."""
    allowed = _candidate_qnames(state)
    if not allowed:
        return ()
    return tuple(
        pruned for t in trees if (pruned := _filter_tree_to_qnames(t, allowed)) is not None
    )


def _log_reductions(
    doc_truncations: list[int],
    doc_max_chars: int,
    reduction: str,
    budget: int,
    model_name: str,
) -> None:
    """Emit budget warnings: doc-excerpt truncation, then the reduction mode
    (``"docs"`` = dropped doc excerpts, ``"nodes"`` = pruned nodes) chosen to
    fit the LLM context window. Pulled out of ``run()`` to keep it simple."""
    if doc_truncations:
        log.warning(
            "llm_tree_reasoning: %d docstring excerpt(s) hit the "
            "doc_excerpt_max_chars=%d cap and were truncated. Raise "
            "doc_excerpt_max_chars (or set doc_excerpt: off) if you want "
            "different per-node docstring coverage.",
            len(doc_truncations),
            doc_max_chars,
        )
    if reduction == "docs":
        log.warning(
            "llm_tree_reasoning: project tree exceeded the %d-token budget "
            "(model=%s); dropped per-node doc excerpts to fit while keeping "
            "all nodes. Raise max_tree_tokens or use a larger-context model "
            "for full docstring coverage.",
            budget,
            model_name,
        )
    elif reduction == "nodes":
        log.warning(
            "llm_tree_reasoning: project tree exceeded the %d-token budget "
            "(model=%s) even without doc excerpts; pruned deepest/excess "
            "nodes to fit. Large repo — raise max_tree_tokens or use a "
            "larger-context model for full tree coverage.",
            budget,
            model_name,
        )


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


def _chunk_key(chunk: Chunk) -> str:
    """Dedupe identity for pick/backfill: ``qualified_name`` when present
    (unique per tree node), else ``content_hash`` (always populated by
    ``Chunk.__post_init__``)."""
    return str(chunk.metadata.get("qualified_name") or chunk.content_hash)


def _backfill_unpicked(ranked: ChunkList, incoming: ChunkList | None) -> ChunkList:
    """Append stage-1 candidates the LLM did not pick after the picks.

    WHY: rerank mode used to hard-replace ``state.candidates`` with the
    picks, so one LLM omission dropped a gold the stage-1 retriever had
    already surfaced — the only measured regression vs the pure stage-1
    ranking (PAGEINDEX_DIVS.md F6.1). Appending the unpicked remainder in
    stage-1 order is strictly non-negative for recall@k. Relevance is
    re-imputed positionally (``1 - i/n``) over the combined list so the
    score field stays strictly decreasing across the pick/backfill
    boundary for downstream limit / formatting steps.
    """
    if incoming is None:
        return ranked
    picked_keys = {_chunk_key(c) for c in ranked.items}
    tail = tuple(c for c in incoming.items if _chunk_key(c) not in picked_keys)
    if not tail:
        return ranked
    combined = tuple(ranked.items) + tail
    n = len(combined)
    return ChunkList(
        items=tuple(replace(c, relevance=1.0 - i / n) for i, c in enumerate(combined)),
    )


__all__ = ("LlmTreeReasoningStep",)
