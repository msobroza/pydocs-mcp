"""AC-14: Two new benchmark system variants for tree reasoning."""

from __future__ import annotations

from pathlib import Path

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
def test_tree_preset_index_carries_runner_gpu_device(
    system_cls: type[PydocsMcpSystem],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reloaded preset override must inherit the runner's --gpu device.

    The runner applies ``.with_device`` to the config it threads into
    ``index``. The tree-preset variants reload their own preset YAML, which
    would otherwise discard that device stamp and silently embed on CPU.
    """
    captured: dict[str, AppConfig] = {}

    async def _capture(self, corpus_dir: Path, config: AppConfig) -> None:
        captured["config"] = config

    monkeypatch.setattr(PydocsMcpSystem, "index", _capture)

    incoming = AppConfig().with_device(gpu=True)
    assert incoming.embedding.device == "cuda"  # guard: precondition holds

    import asyncio

    asyncio.run(system_cls().index(Path("corpus"), incoming))

    assert captured["config"].embedding.device == "cuda"
    assert captured["config"].late_interaction.device == "cuda"
