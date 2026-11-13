"""child_expand's displacement guard as a property (#393).

The guard: children are appended only below the last original candidate and
only into the slots the limit step leaves free. So after the limit, every
original the limit keeps without the step is kept at the same rank with it,
and the limit drops no extra row — a short listing is never marked partial
by appended children. A seeded sweep of random worlds (candidate lists of any
kind and length, caps with and without the query's own, any knobs) pins it.
"""

from __future__ import annotations

import random

from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.steps.limit import LimitStep, rows_dropped_by_limit
from tests.retrieval.steps._child_expand_builders import (
    MOD,
    chunk_row,
    class_node,
    module_node,
    ranked_state,
    step_over,
    tree_node,
)

_SEEDS = range(300)


def _all_nodes(root: DocumentNode) -> list[DocumentNode]:
    nodes = [root]
    for child in root.children:
        nodes.extend(_all_nodes(child))
    return nodes


def _random_world(rng: random.Random) -> tuple[DocumentNode, list[Chunk], list[Chunk]]:
    """A module of classes / functions, a chunk row per node, and a ranked
    sample of those nodes as the original candidates (any kind, any count)."""
    classes = [
        class_node(f"{MOD}.C{c}", *(f"m{i}" for i in range(rng.randint(0, 6))))
        for c in range(rng.randint(0, 4))
    ]
    functions = [tree_node(f"{MOD}.f{i}", NodeKind.FUNCTION) for i in range(rng.randint(0, 3))]
    tree = module_node(*classes, *functions)
    nodes = _all_nodes(tree)
    rows = [chunk_row(n.qualified_name) for n in nodes]
    picked = rng.sample(nodes, rng.randint(0, len(nodes)))
    scores = sorted((rng.uniform(-0.2, 1.0) for _ in picked), reverse=True)
    ranked = [chunk_row(n.qualified_name, s) for n, s in zip(picked, scores, strict=True)]
    return tree, rows, ranked


async def test_no_original_the_limit_keeps_is_ever_displaced() -> None:
    expanded_cases = 0
    for seed in _SEEDS:
        rng = random.Random(seed)
        tree, rows, ranked = _random_world(rng)
        step = await step_over([tree], rows, top_s=rng.randint(1, 4), per_parent=rng.randint(1, 6))
        state = ranked_state(ranked, cap=rng.choice([None, *range(1, 13)]))

        expanded = await step.run(state)
        limited = await LimitStep().run(expanded)
        baseline = await LimitStep().run(state)

        assert isinstance(expanded.candidates, ChunkList)
        assert expanded.candidates.items[: len(ranked)] == tuple(ranked), f"seed {seed}"
        kept = baseline.candidates.items
        assert limited.candidates.items[: len(kept)] == kept, f"seed {seed}"
        assert rows_dropped_by_limit(limited) == rows_dropped_by_limit(baseline), f"seed {seed}"
        expanded_cases += len(expanded.candidates.items) > len(ranked)
    # Not vacuous: the sweep must actually append children in many worlds.
    assert expanded_cases >= 30
