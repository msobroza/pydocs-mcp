"""Cell definitions: the 6-cell screening grid + bare-arm suggestion inertness."""

from __future__ import annotations

import pytest

from pydocs_eval.agent_track._types import ArmConfig
from pydocs_eval.campaign.cells import CellConfig, screening_cells


def test_screening_grid_is_six_cells_not_full_factorial() -> None:
    cells = screening_cells()
    assert len(cells) == 6  # bare×inj{2} + indexed×sugg×inj{4}, ADR 0016 §Stage 1


def test_screening_grid_crosses_suggestions_only_with_indexed() -> None:
    cells = screening_cells()
    bare = [c for c in cells if not c.arm.mcp]
    indexed = [c for c in cells if c.arm.mcp]
    assert len(bare) == 2  # only injection varies in the bare arm
    assert len(indexed) == 4  # suggestions × injection
    assert all(c.suggestion_overlay is None for c in bare)


def test_anchor_contrast_cells_present() -> None:
    names = {c.name for c in screening_cells()}
    assert "indexed_sugg-on_inj-off" in names  # anchor treatment
    assert "bare_inj-off" in names  # anchor control


def test_bare_cell_rejects_suggestion_overlay() -> None:
    with pytest.raises(ValueError, match="inert without the MCP server"):
        CellConfig(name="bad", arm=ArmConfig(name="bare", mcp=False), suggestion_overlay="off")


def test_cell_to_dict_is_hashable_shape() -> None:
    cell = next(c for c in screening_cells() if c.arm.mcp and c.injection)
    doc = cell.to_dict()
    assert doc["injection"] is True
    assert doc["arm"]["mcp"] is True
    assert set(doc) == {"name", "arm", "suggestion_overlay", "injection"}
