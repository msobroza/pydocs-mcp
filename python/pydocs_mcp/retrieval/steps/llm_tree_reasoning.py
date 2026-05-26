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

import json
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import Chunk, ChunkFilterField, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.prompts import render_prompt
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.storage.protocols import LlmClient, UnitOfWork

# WHY: single source of truth for every default — referenced from field
# defaults, to_dict omit-when-default, and from_dict YAML-fallback. The
# project-wide convention; see CLAUDE.md "Default values: single source
# of truth".
_DEFAULT_PROMPT_TEMPLATE = "tree_reasoning_pydocs_v1"
_DEFAULT_OUTPUT_SCRATCH_KEY = "tree.ranked"
_DEFAULT_REFERENCE_NEIGHBORS_LIMIT = 5
_DEFAULT_NAME = "llm_tree_reasoning"

# WHY: spec §"Scope: __project__ only" — dependencies stay in BM25 /
# dense paths; this step never reads dep trees. Hardcoded because the
# scoping decision IS the step's contract, not a tunable.
_PROJECT_PACKAGE = "__project__"


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
        default=_DEFAULT_REFERENCE_NEIGHBORS_LIMIT, kw_only=True,
    )
    output_scratch_key: str = field(
        default=_DEFAULT_OUTPUT_SCRATCH_KEY, kw_only=True,
    )
    name: str = field(default=_DEFAULT_NAME, kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        async with self.uow_factory() as uow:
            # DocumentTreeStore.load_all_in_package returns a
            # dict[str, DocumentNode] keyed by module qualified_name
            # (Protocol §12.2). Iterate over .values() so the rest of
            # the step works on the trees themselves, not the keys.
            trees_by_module = await uow.trees.load_all_in_package(
                _PROJECT_PACKAGE,
            )
            if not trees_by_module:
                return state
            trees = tuple(trees_by_module.values())

            tree_jsons = [_pageindex_with_qname(t) for t in trees]
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
                filter={ChunkFilterField.PACKAGE.value: _PROJECT_PACKAGE},
            )
            picked_set = set(picked)
            matched = tuple(
                c for c in all_chunks
                if c.metadata.get("qualified_name") in picked_set
            )
            if not matched:
                return state

            ranked = _score_by_position(matched, picked)
            new_scratch = dict(state.scratch)
            new_scratch[self.output_scratch_key] = ranked
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
        return out

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        context: BuildContext,
    ) -> "LlmTreeReasoningStep":
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
                "output_scratch_key", _DEFAULT_OUTPUT_SCRATCH_KEY,
            ),
            name=data.get("name", _DEFAULT_NAME),
        )


def _pageindex_with_qname(node: DocumentNode) -> dict[str, Any]:
    """Like :meth:`DocumentNode.to_pageindex_json` but adds ``qualified_name``.

    The shipped ``to_pageindex_json`` omits ``qualified_name`` (it's a
    first-class field on DocumentNode but the PageIndex shape predates
    that field's promotion). The LLM here needs symbol-stable IDs that
    map back onto chunk metadata — ``node_id`` is per-extraction
    auto-generated and not present in chunk metadata, but
    ``qualified_name`` IS persisted into ``chunk.metadata["qualified_name"]``
    by :func:`flatten_to_chunks`. Augmenting the serialization here keeps
    the step self-contained without changing the existing
    ``to_pageindex_json`` shape (which is consumed by LookupService and
    pinned by a contract test in
    ``tests/extraction/test_document_node_lookup_contract.py``).
    """
    d = node.to_pageindex_json()
    d["qualified_name"] = node.qualified_name
    d["nodes"] = [_pageindex_with_qname(child) for child in node.children]
    return d


def _collect_qnames(node: DocumentNode, acc: set[str]) -> None:
    acc.add(node.qualified_name)
    for child in node.children:
        _collect_qnames(child, acc)


def _parse_node_list(
    response: str, trees: tuple[DocumentNode, ...],
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
            f"LLM response 'node_list' must be a list; got "
            f"{type(node_list).__name__}",
        )
    known: set[str] = set()
    for tree in trees:
        _collect_qnames(tree, known)
    return tuple(qn for qn in node_list if isinstance(qn, str) and qn in known)


def _score_by_position(
    chunks: tuple[Chunk, ...], picked_qnames: tuple[str, ...],
) -> ChunkList:
    """Score each chunk by its position in the LLM's ``node_list``.

    Uses the project's :attr:`Chunk.relevance` field (not
    ``metadata["score"]``) to stay consistent with
    :class:`BM25ScorerStep` / :class:`RRFFusionStep` /
    :class:`WeightedScoreInterpolationStep` — those steps all read +
    write ``Chunk.relevance``, so downstream fusion sees a homogeneous
    score field across branches.
    """
    by_qname: dict[str, Chunk] = {
        c.metadata.get("qualified_name", ""): c for c in chunks
    }
    n = len(picked_qnames)
    scored: list[Chunk] = []
    for rank, qname in enumerate(picked_qnames):
        chunk = by_qname.get(qname)
        if chunk is None:
            continue
        scored.append(replace(chunk, relevance=1.0 - rank / n))
    return ChunkList(items=tuple(scored))


__all__ = ("LlmTreeReasoningStep",)
