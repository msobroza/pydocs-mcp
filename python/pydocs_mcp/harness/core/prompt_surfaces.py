"""The optimizable-prompt-surface registry — core's map of trainable text.

Owner rule (2026-07-26): the freeze taxonomy needs a third status besides
"optimizable-active" (the core pool) and "frozen control" (harness
``freeze/`` pools): **dormant** — text that CAN be multitask-optimized (it
shapes retrieval quality, so it belongs to the trainable surface) but is
not exercised by the shipped default architecture today. The retrieval
tree-reasoning templates are the first case: the default docs pipeline
(``pipelines/chunk_search_graph.yaml``) carries no ``llm_tree_reasoning``
step, and ``config_search`` selects the *version* via the step's YAML
``prompt_template`` key without ever mutating the text.

The versioning/architecture-change story a dormant surface relies on:
template text is immutable per version (``_vN`` never edited — ship
``_vN+1``; every shipped version is byte-pinned by a freeze manifest until
a benchmarks artifact family takes ownership of the text), while WHICH
version runs — and whether the step runs at all — is pipeline YAML, i.e.
already inside the ``retrieval_config`` search space. Activating a dormant
surface (a new default pipeline, or a text-optimizing artifact family) is
a deliberate event: flip its status here and the conformance tests demand
the matching reality.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PromptSurfaceStatus = Literal["active", "dormant"]


@dataclass(frozen=True, slots=True)
class OptimizablePromptSurface:
    """One package of trainable prompt text and how it reaches a model.

    Example: ``OptimizablePromptSurface("pydocs_mcp.retrieval.prompts",
    ("tree_reasoning_pydocs_v1",), "dormant", activation="...")``.
    """

    package: str
    templates: tuple[str, ...]
    status: PromptSurfaceStatus
    # WHY prose, not code: activation is a pointer for humans deciding to
    # flip a status — the executable side lives in the conformance tests.
    activation: str


OPTIMIZABLE_PROMPT_SURFACES: tuple[OptimizablePromptSurface, ...] = (
    OptimizablePromptSurface(
        package="pydocs_mcp.harness.core.prompts",
        templates=("rewrite_v1", "system_v1"),
        status="active",
        activation=(
            "Live optimizable surface: seeds the benchmarks ask_prompt artifact "
            "(SYSTEM_PROMPT / REWRITE_PROMPT); byte-parity pinned by "
            "tests/harness/ask_your_docs/test_prompt_seed_parity.py."
        ),
    ),
    OptimizablePromptSurface(
        package="pydocs_mcp.retrieval.prompts",
        templates=("tree_reasoning_pageindex_v1", "tree_reasoning_pydocs_v1"),
        status="dormant",
        activation=(
            "Multitask-optimizable in principle, unused by the shipped default "
            "docs pipeline (chunk_search_graph.yaml has no llm_tree_reasoning "
            "step). Version selection is already YAML (the step's "
            "prompt_template key, retrieval_config search space); bytes are "
            "pinned by tests/fixtures/goldens/retrieval_prompt_freeze.json. "
            "Flip to active when a default pipeline adopts the step or a "
            "benchmarks artifact family starts optimizing the text."
        ),
    ),
)
