"""harness/core/prompt_surfaces — the optimizable-surface registry is honest.

Each declared surface must match repo reality: its templates exist, an
``ACTIVE`` surface is the core pool (byte-pinned by seed-parity, outside
every freeze manifest), and an ``INACTIVE`` surface stays freeze-pinned AND
genuinely unused by the shipped default docs pipeline — so promoting the
tree-reasoning step into the default forces a deliberate status flip here.
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

from pydocs_mcp.harness.core.prompt_freeze import frozen_prompt_digests
from pydocs_mcp.harness.core.prompt_surfaces import (
    ACTIVE_REWRITE_PROMPT_TEMPLATE,
    ACTIVE_SYSTEM_PROMPT_TEMPLATE,
    OPTIMIZABLE_PROMPT_SURFACES,
    PromptSurfaceStatus,
)
from pydocs_mcp.harness.core.prompts import core_prompt_names, render_core_prompt

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDENS = _REPO_ROOT / "tests/fixtures/goldens"
_DEFAULT_DOCS_PIPELINE = _REPO_ROOT / "python/pydocs_mcp/pipelines/chunk_search_graph.yaml"


def _surface(status: PromptSurfaceStatus) -> tuple[str, tuple[str, ...]]:
    (entry,) = [s for s in OPTIMIZABLE_PROMPT_SURFACES if s.status == status]
    return entry.package, entry.templates


def test_every_declared_template_exists_in_its_package() -> None:
    for surface in OPTIMIZABLE_PROMPT_SURFACES:
        pkg = resources.files(surface.package)
        for name in surface.templates:
            assert pkg.joinpath(f"{name}.j2").is_file(), (surface.package, name)


def test_active_surface_names_the_shipped_template_versions() -> None:
    # The record IS the single source of the active names: the render sites
    # read these two constants, so activating a new version is one flip here.
    package, templates = _surface(PromptSurfaceStatus.ACTIVE)
    assert package == "pydocs_mcp.harness.core.prompts"
    assert templates == (ACTIVE_REWRITE_PROMPT_TEMPLATE, ACTIVE_SYSTEM_PROMPT_TEMPLATE)
    assert set(templates) <= set(core_prompt_names())


def test_retired_versions_stay_in_the_pool_and_are_not_served() -> None:
    # Owner rule: never edit a shipped _vN — ship _vN+1 and leave the old file
    # on disk, renderable by name, serving nobody.
    _, templates = _surface(PromptSurfaceStatus.ACTIVE)
    retired = [name for name in core_prompt_names() if name not in templates]
    assert retired == ["system_v1"]
    assert render_core_prompt("system_v1") != render_core_prompt(ACTIVE_SYSTEM_PROMPT_TEMPLATE)


def test_inactive_surface_is_freeze_pinned() -> None:
    # Until an artifact family owns the text, the freeze manifest is the
    # control that keeps an inactive optimizable surface byte-stable.
    package, templates = _surface(PromptSurfaceStatus.INACTIVE)
    manifest = json.loads((_GOLDENS / "retrieval_prompt_freeze.json").read_text(encoding="utf-8"))
    live = frozen_prompt_digests(package)
    for name in templates:
        assert name in manifest and name in live


def test_inactive_tree_surface_matches_default_pipeline_reality() -> None:
    # INACTIVE is a factual claim: the shipped default docs pipeline does
    # not run the tree-reasoning step. Adopting it there is the deliberate
    # activation event — this test then demands the status flip.
    assert "llm_tree_reasoning" not in _DEFAULT_DOCS_PIPELINE.read_text(encoding="utf-8")
