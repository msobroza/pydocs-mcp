"""harness/core/prompt_surfaces — the optimizable-surface registry is honest.

Each declared surface must match repo reality: its templates exist, an
``active`` surface is the core pool (byte-pinned by seed-parity, outside
every freeze manifest), and a ``dormant`` surface stays freeze-pinned AND
genuinely unused by the shipped default docs pipeline — so promoting the
tree-reasoning step into the default forces a deliberate status flip here.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from pydocs_mcp.harness.core.prompt_freeze import frozen_prompt_digests
from pydocs_mcp.harness.core.prompt_surfaces import OPTIMIZABLE_PROMPT_SURFACES
from pydocs_mcp.harness.core.prompts import core_prompt_names

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDENS = _REPO_ROOT / "tests/fixtures/goldens"
_DEFAULT_DOCS_PIPELINE = _REPO_ROOT / "python/pydocs_mcp/pipelines/chunk_search_graph.yaml"


def _surface(status: str) -> tuple[str, tuple[str, ...]]:
    (entry,) = [s for s in OPTIMIZABLE_PROMPT_SURFACES if s.status == status]
    return entry.package, entry.templates


def test_every_declared_template_exists_in_its_package() -> None:
    for surface in OPTIMIZABLE_PROMPT_SURFACES:
        pkg = resources.files(surface.package)
        for name in surface.templates:
            assert pkg.joinpath(f"{name}.j2").is_file(), (surface.package, name)


def test_active_surface_is_exactly_the_core_pool() -> None:
    _, templates = _surface("active")
    assert templates == core_prompt_names()


def test_dormant_surface_is_freeze_pinned() -> None:
    # Until an artifact family owns the text, the freeze manifest is the
    # control that keeps a dormant optimizable surface byte-stable.
    package, templates = _surface("dormant")
    manifest = json.loads((_GOLDENS / "retrieval_prompt_freeze.json").read_text(encoding="utf-8"))
    live = frozen_prompt_digests(package)
    for name in templates:
        assert name in manifest and name in live


def test_dormant_tree_surface_matches_default_pipeline_reality() -> None:
    # "dormant" is a factual claim: the shipped default docs pipeline does
    # not run the tree-reasoning step. Adopting it there is the deliberate
    # activation event — this test then demands the status flip.
    assert "llm_tree_reasoning" not in _DEFAULT_DOCS_PIPELINE.read_text(encoding="utf-8")
