"""harness/core/prompt_namespace — the generalized per-architecture resolver.

Exercised against the real ask-your-docs prompt package: resolution is
three-tier (architecture's own dir → the harness-local ``shared/`` pool →
the cross-harness core pool), and the core pool carries ONLY prompts
plausibly shared across harnesses/tasks (owner rule 2026-07-26) — a
harness's feature machinery stays in its own ``shared/``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("jinja2")

from pydocs_mcp.harness.core.prompt_namespace import HarnessPromptNamespace
from pydocs_mcp.harness.core.prompts import core_prompt_names

_AYD_PACKAGE = "pydocs_mcp.harness.ask_your_docs.prompts"


def test_own_namespace_wins_then_pools() -> None:
    ns = HarnessPromptNamespace(_AYD_PACKAGE, "inline")
    assert ns.resolve_source("system_suffix_v1") == "inline"
    assert ns.resolve_source("system_v1") == "core"
    assert "Image handling:" in ns.render("system_suffix_v1")


def test_harness_local_shared_pool_is_the_middle_tier() -> None:
    # vision/reinspect are ask-your-docs feature machinery, not retriever
    # guidance — they live in the harness's shared/ pool, not in core.
    ns = HarnessPromptNamespace(_AYD_PACKAGE, "no_such_architecture")
    assert ns.resolve_source("vision_extraction_v1") == "shared"
    assert ns.render("vision_extraction_v1", question="q")
    assert ns.resolve_source("system_v1") == "core"
    assert ns.render("rewrite_v1", history="H", question="Q")


def test_core_pool_carries_only_cross_harness_prompts() -> None:
    # The owner rule as an executable pin: only the retriever-driving,
    # optimizer-seeded guidance surface is shareable across harnesses.
    assert core_prompt_names() == ("rewrite_v1", "system_v1")


def test_unknown_template_raises_listing_all_three_locations() -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        HarnessPromptNamespace(_AYD_PACKAGE, "inline").render("nope_v1")
    msg = str(excinfo.value)
    assert "inline" in msg and "shared" in msg and "harness/core/prompts" in msg


def test_names_are_own_union_both_pools() -> None:
    names = HarnessPromptNamespace(_AYD_PACKAGE, "inline").names()
    assert "system_suffix_v1" in names  # own
    assert "vision_extraction_v1" in names  # harness shared/ pool
    assert "system_v1" in names  # core pool
