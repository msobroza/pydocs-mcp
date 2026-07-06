"""AC-14: Two new benchmark system variants for tree reasoning."""

from __future__ import annotations

import pytest

from benchmarks.eval.systems.pydocs import (
    PydocsMcpSystem,
    PydocsTreeOnlySystem,
    PydocsTreeParallelSystem,
)
from pydocs_mcp.retrieval.config import AppConfig


def test_tree_only_system_uses_correct_config() -> None:
    sys = PydocsTreeOnlySystem()
    assert "tree_only" in str(sys._config_path)


def test_tree_parallel_system_uses_correct_config() -> None:
    sys = PydocsTreeParallelSystem()
    assert "chunk_search_with_tree_reasoning_parallel" in str(sys._config_path)


@pytest.mark.parametrize(
    "system_cls",
    [PydocsTreeOnlySystem, PydocsTreeParallelSystem],
)
def test_tree_preset_override_carries_runner_gpu_device(
    system_cls: type[PydocsMcpSystem],
) -> None:
    """The reloaded preset must inherit the runner's --gpu device.

    The runner applies ``.with_device`` to the config it threads into
    ``index``; ``_preset_override`` reloads the pinned preset YAML, which
    would otherwise discard that device stamp and silently embed on CPU.
    """
    incoming = AppConfig().with_device(gpu=True)
    assert incoming.embedding.device == "cuda"  # guard: precondition holds

    override = system_cls()._preset_override(incoming)

    assert override is not incoming  # the preset really was reloaded
    assert override.embedding.device == "cuda"
    assert override.late_interaction.device == "cuda"
