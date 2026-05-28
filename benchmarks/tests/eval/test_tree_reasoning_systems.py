"""AC-14: Two new benchmark system variants for tree reasoning."""

from __future__ import annotations

from benchmarks.eval.systems.pydocs import (
    PydocsTreeOnlySystem,
    PydocsTreeParallelSystem,
)


def test_tree_only_system_uses_correct_config() -> None:
    sys = PydocsTreeOnlySystem()
    assert "tree_only" in str(sys._config_path)


def test_tree_parallel_system_uses_correct_config() -> None:
    sys = PydocsTreeParallelSystem()
    assert "chunk_search_with_tree_reasoning_parallel" in str(sys._config_path)
