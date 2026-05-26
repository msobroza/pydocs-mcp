"""AC-12: Three opt-in preset YAMLs load + round-trip via CodeRetrieverPipeline.

The shipped default ``chunk_search.yaml`` stays untouched; these three
presets let a user opt into LLM tree reasoning by overlaying ``--config``
or ``PYDOCS_CONFIG_PATH``:

- ``chunk_search_with_tree_reasoning_parallel.yaml`` — hybrid (BM25 +
  dense + RRF) in parallel with LLM tree reasoning; outer RRF fuses
  both branches.
- ``chunk_search_with_tree_reasoning_after.yaml`` — hybrid first; the
  LLM tree-reasoning branch fires only on long queries via
  ``conditional`` + ``is_long_query`` predicate.
- ``tree_only.yaml`` — vectorless: LLM tree reasoning is the only
  retrieval signal.

Tests assert (1) the files ship in ``python/pydocs_mcp/pipelines/`` and
(2) ``CodeRetrieverPipeline.from_dict`` accepts each preset and the
result round-trips through ``to_dict`` with the right number of steps.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pydocs_mcp.retrieval.config import _shipped_pipelines_dir

PRESETS = (
    "chunk_search_with_tree_reasoning_parallel",
    "chunk_search_with_tree_reasoning_after",
    "tree_only",
)


@pytest.mark.parametrize("preset", PRESETS)
def test_preset_loads_from_pipelines_dir(preset: str) -> None:
    """File ships in the shipped pipelines directory."""
    path = _shipped_pipelines_dir() / f"{preset}.yaml"
    assert path.is_file(), f"missing preset {preset}.yaml at {path}"


@pytest.mark.parametrize("preset", PRESETS)
def test_preset_roundtrips_to_dict(preset: str, tmp_path: Path) -> None:
    """Load preset YAML, build the pipeline through CodeRetrieverPipeline.from_dict,
    call to_dict(), assert structural equality with the original (same name +
    same number of top-level steps).

    The new presets contain ``llm_tree_reasoning`` steps whose decoder requires
    both ``context.llm_client`` and ``context.uow_factory``; the production
    ``build_retrieval_context`` factory threads ``llm_client`` but not
    ``uow_factory``, so the test stitches one in via the same fake-UoW helper
    other LLM-step tests use.
    """
    from pydocs_mcp.retrieval.factories import build_retrieval_context
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
    from pydocs_mcp.retrieval.config import AppConfig
    from dataclasses import replace as dc_replace
    from tests._fakes import FakeLlmClient, MockEmbedder, make_fake_uow_factory

    yaml_path = _shipped_pipelines_dir() / f"{preset}.yaml"
    original = yaml.safe_load(yaml_path.read_text())

    base_ctx = build_retrieval_context(tmp_path / "x.db", AppConfig())
    ctx = dc_replace(
        base_ctx,
        llm_client=FakeLlmClient(responses={}),
        uow_factory=make_fake_uow_factory(),
        embedder=MockEmbedder(),
    )

    pipeline = CodeRetrieverPipeline.from_dict(original, ctx)
    rebuilt = pipeline.to_dict()

    # Structural equality — pipeline name + step count.
    assert rebuilt["name"] == original["name"]
    assert len(rebuilt["steps"]) == len(original["steps"]), (
        f"step count mismatch for {preset!r}: "
        f"original={len(original['steps'])} rebuilt={len(rebuilt['steps'])}"
    )
