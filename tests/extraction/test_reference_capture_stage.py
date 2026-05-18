"""ReferenceCaptureStage runs over Python files, populates state.references."""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages import ReferenceCaptureStage
from pydocs_mcp.extraction.reference_kind import ReferenceKind


@pytest.mark.asyncio
async def test_capture_stage_emits_refs_for_python_files():
    """The stage walks state.file_contents (.py only) and fills
    state.references with unresolved tuples."""
    stage = ReferenceCaptureStage()
    state = IngestionState(
        target=Path("."),
        target_kind=TargetKind.PROJECT,
        package_name="pkg",
        root=Path("."),
        file_contents=(
            (
                "pkg/mod.py",
                "from helpers import compute as do_it\n"
                "def runner():\n"
                "    return do_it(42)\n",
            ),
        ),
    )
    new_state = await stage.run(state)
    # Expect at least one IMPORTS edge (from-import) + one CALLS edge.
    kinds = {r.kind for r in new_state.references}
    assert ReferenceKind.IMPORTS in kinds
    assert ReferenceKind.CALLS in kinds


@pytest.mark.asyncio
async def test_capture_stage_skips_non_python_files():
    """Markdown / notebook files don't go through the Python capture path."""
    stage = ReferenceCaptureStage()
    state = IngestionState(
        target=Path("."),
        target_kind=TargetKind.PROJECT,
        package_name="pkg",
        root=Path("."),
        file_contents=(
            ("README.md", "# A doc\nWith `pkg.func` text\n"),
            ("nb.ipynb", "{}"),
        ),
    )
    new_state = await stage.run(state)
    assert new_state.references == ()


@pytest.mark.asyncio
async def test_capture_stage_continues_on_per_file_error():
    """Spec §7.1 + AC #27 — one broken file does not abort the whole stage."""
    stage = ReferenceCaptureStage()
    state = IngestionState(
        target=Path("."),
        target_kind=TargetKind.PROJECT,
        package_name="pkg",
        root=Path("."),
        file_contents=(
            ("pkg/bad.py", "def broken( syntax error\n"),
            (
                "pkg/good.py",
                "def fn(): return helper()\n",
            ),
        ),
    )
    new_state = await stage.run(state)
    # The good file's CALLS edge survives despite the broken sibling.
    assert any(r.to_name == "helper" for r in new_state.references)
