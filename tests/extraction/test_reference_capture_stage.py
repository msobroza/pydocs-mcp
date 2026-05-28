"""ReferenceCaptureStage runs over Python files, populates state.refs."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.pipeline.stages import reference_capture as stages_mod
from pydocs_mcp.extraction.pipeline.ingestion import (
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages import ReferenceCaptureStage
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.retrieval.config import ReferenceCaptureConfig


def _state(file_contents: tuple[tuple[str, str], ...]) -> IngestionState:
    """Helper — build an IngestionState with a populated FileBundle."""
    return IngestionState(
        files=FileBundle(
            target=Path(),
            target_kind=TargetKind.PROJECT,
            package_name="pkg",
            root=Path(),
            file_contents=file_contents,
        ),
    )


@pytest.mark.asyncio
async def test_capture_stage_emits_refs_for_python_files():
    """The stage walks state.files.file_contents (.py only) and fills
    state.refs.references with unresolved tuples."""
    stage = ReferenceCaptureStage()
    state = _state((
        (
            "pkg/mod.py",
            "from helpers import compute as do_it\n"
            "def runner():\n"
            "    return do_it(42)\n",
        ),
    ))
    new_state = await stage.run(state)
    # Expect at least one IMPORTS edge (from-import) + one CALLS edge.
    kinds = {r.kind for r in new_state.refs.references}
    assert ReferenceKind.IMPORTS in kinds
    assert ReferenceKind.CALLS in kinds


@pytest.mark.asyncio
async def test_capture_stage_skips_non_python_files():
    """Markdown / notebook files don't go through the Python capture path."""
    stage = ReferenceCaptureStage()
    state = _state((
        ("README.md", "# A doc\nWith `pkg.func` text\n"),
        ("nb.ipynb", "{}"),
    ))
    new_state = await stage.run(state)
    assert new_state.refs.references == ()


@pytest.mark.asyncio
async def test_capture_stage_continues_on_per_file_error():
    """Spec §7.1 + AC #27 — one broken file does not abort the whole stage."""
    stage = ReferenceCaptureStage()
    state = _state((
        ("pkg/bad.py", "def broken( syntax error\n"),
        (
            "pkg/good.py",
            "def fn(): return helper()\n",
        ),
    ))
    new_state = await stage.run(state)
    # The good file's CALLS edge survives despite the broken sibling.
    assert any(r.to_name == "helper" for r in new_state.refs.references)


@pytest.mark.asyncio
async def test_capture_stage_no_ops_when_capture_disabled(monkeypatch):
    """``capture.enabled=False`` short-circuits the stage — no refs, no aliases.

    Wires the module-level ``_CAPTURE_CONFIG`` via monkeypatch (mirrors how
    ``configure_from_app_config`` will install it at server / CLI startup).
    """
    monkeypatch.setattr(
        stages_mod,
        "_CAPTURE_CONFIG",
        ReferenceCaptureConfig(enabled=False),
    )
    stage = ReferenceCaptureStage()
    state = _state((
        (
            "pkg/mod.py",
            "from helpers import compute as do_it\n"
            "def runner():\n"
            "    return do_it(42)\n",
        ),
    ))
    new_state = await stage.run(state)
    assert new_state.refs.references == ()
    assert new_state.refs.reference_aliases == {}


@pytest.mark.asyncio
async def test_capture_stage_kinds_filter_drops_imports_but_keeps_aliases(
    monkeypatch,
):
    """``kinds=["calls"]`` drops IMPORTS rows but the alias table is preserved.

    ``capture_imports`` must always run so ``collector.aliases`` stays
    populated — the resolver consumes it to disambiguate aliased calls.
    Filtering happens AFTER capture, by dropping IMPORTS rows from
    ``collector.refs`` while leaving ``collector.aliases`` intact.
    """
    monkeypatch.setattr(
        stages_mod,
        "_CAPTURE_CONFIG",
        ReferenceCaptureConfig(enabled=True, kinds=["calls"]),
    )
    stage = ReferenceCaptureStage()
    state = _state((
        (
            "pkg/mod.py",
            "from helpers import compute as do_it\n"
            "class Base: pass\n"
            "class Child(Base):\n"
            "    def fn(self):\n"
            "        return do_it(42)\n",
        ),
    ))
    new_state = await stage.run(state)
    kinds = {r.kind for r in new_state.refs.references}
    # IMPORTS and INHERITS rows filtered out; CALLS kept.
    assert ReferenceKind.IMPORTS not in kinds
    assert ReferenceKind.INHERITS not in kinds
    assert ReferenceKind.CALLS in kinds
    # The alias table survives the filter — resolver needs it later.
    assert new_state.refs.reference_aliases.get("pkg.mod", {}).get("do_it") == (
        "helpers.compute"
    )


def test_get_capture_config_returns_safe_default():
    """``_get_capture_config()`` returns the module-level default — a
    ``ReferenceCaptureConfig`` with ``enabled=True`` and the three AST kinds.

    The default exists so the stage works the moment it's instantiated, before
    ``configure_from_app_config`` installs an overlay (e.g. unit tests that
    construct the stage without going through the YAML path).
    """
    cfg = stages_mod._get_capture_config()
    assert isinstance(cfg, ReferenceCaptureConfig)
    assert cfg.enabled is True
    assert set(cfg.kinds) == {"calls", "imports", "inherits"}


@pytest.mark.asyncio
async def test_capture_stage_emits_mentions_for_markdown_when_kinds_include_mentions(
    monkeypatch,
):
    """Markdown files emit MENTIONS edges when ``mentions`` is in ``kinds``.

    Sub-PR #5c — the stage gains a ``.md`` branch in ``_capture_all``,
    gated on ``"mentions" in allowed``. The shipped default omits
    ``mentions`` (regex over markdown is lower-precision than AST capture),
    so this test overlays a config with mentions enabled.
    """
    monkeypatch.setattr(
        stages_mod,
        "_CAPTURE_CONFIG",
        ReferenceCaptureConfig(
            enabled=True,
            kinds=["calls", "imports", "inherits", "mentions"],
        ),
    )
    stage = ReferenceCaptureStage()
    state = _state((
        (
            "pkg/README.md",
            "# Docs\n"
            "See `pkg.helpers.compute` for the entry point.\n"
            "Also mentions `pkg.utils.runner`.\n",
        ),
    ))
    new_state = await stage.run(state)
    mentions = [
        r for r in new_state.refs.references if r.kind == ReferenceKind.MENTIONS
    ]
    names = {r.to_name for r in mentions}
    assert {"pkg.helpers.compute", "pkg.utils.runner"} <= names


@pytest.mark.asyncio
async def test_capture_stage_skips_mentions_when_not_in_kinds(monkeypatch):
    """Default config omits ``mentions`` from ``kinds`` — markdown files
    contribute zero MENTIONS edges (the markdown branch is gated)."""
    monkeypatch.setattr(
        stages_mod,
        "_CAPTURE_CONFIG",
        ReferenceCaptureConfig(
            enabled=True,
            kinds=["calls", "imports", "inherits"],
        ),
    )
    stage = ReferenceCaptureStage()
    state = _state((
        (
            "pkg/README.md",
            "See `pkg.helpers.compute` and `pkg.utils.runner`.\n",
        ),
    ))
    new_state = await stage.run(state)
    mentions = [
        r for r in new_state.refs.references if r.kind == ReferenceKind.MENTIONS
    ]
    assert mentions == []
