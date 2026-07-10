"""Template-method hooks on PydocsMcpSystem (index scaffolding shared with
the oracle + tree-preset variants).

Pins the CRITICAL ordering constraint: a preset-pinned variant must apply
``_preset_override`` BEFORE ``_bench_cache.make_key`` — otherwise the tree
variants and the base system can share one cache key and silently reuse
each other's indexed DBs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydocs_eval import _bench_cache
from pydocs_eval.systems.pydocs import (
    PydocsMcpSystem,
    PydocsTreeOnlySystem,
    PydocsTreeParallelSystem,
)
from pydocs_mcp.retrieval.config import AppConfig


class _Sentinel(Exception):
    """Aborts index() right after the cache key is computed."""


async def test_preset_override_applies_before_cache_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A runner config with a NON-default embedder: the base system must key
    # the cache on it, while the tree variant must key on its reloaded
    # preset (default embedder) — so the two keys MUST differ. With the
    # override mis-ordered after make_key, both would key on the runner
    # config and collide.
    overlay = tmp_path / "custom_embed.yaml"
    overlay.write_text("embedding:\n  model_name: BAAI/bge-base-en-v1.5\n  dim: 768\n")
    config = AppConfig.load(explicit_path=overlay)

    captured: list[str] = []
    real_make_key = _bench_cache.make_key

    def recording_make_key(corpus_dir: Path, cfg: AppConfig) -> str:
        captured.append(real_make_key(corpus_dir, cfg))
        raise _Sentinel

    monkeypatch.setattr(_bench_cache, "make_key", recording_make_key)
    monkeypatch.setattr(_bench_cache, "is_enabled", lambda: True)

    for system in (PydocsMcpSystem(), PydocsTreeOnlySystem()):
        with pytest.raises(_Sentinel):
            await system.index(tmp_path, config)

    assert len(captured) == 2
    assert captured[0] != captured[1]


def test_tree_variants_inherit_index_unmodified() -> None:
    # The byte-identical 8-line index() overrides are gone — each variant
    # declares only its name + pinned preset path.
    assert "index" not in PydocsTreeOnlySystem.__dict__
    assert "index" not in PydocsTreeParallelSystem.__dict__


def test_base_system_preset_override_is_identity() -> None:
    config = AppConfig.load()
    assert PydocsMcpSystem()._preset_override(config) is config
