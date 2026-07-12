"""Query-embedding cache config surface + identity hashes (AC-13/14/15).

The cache key needs its own identity hash because ``compute_pipeline_hash``
deliberately excludes ``query_prompt_name`` (it shapes query vectors, never
stored document vectors). The dual also holds: the ``query_cache`` tunables
must never perturb ``compute_pipeline_hash`` — toggling the cache must not
force a reindex.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import (
    AppConfig,
    EmbeddingConfig,
    LateInteractionConfig,
    QueryCacheConfig,
)


def _cfg(**overrides) -> EmbeddingConfig:
    return EmbeddingConfig(model_name="my-custom-model", dim=512, **overrides)


# ── AC-13: query identity folds query_prompt_name ─────────────────────────


def test_query_identity_hash_folds_prompt_name() -> None:
    plain = _cfg(query_prompt_name=None)
    prompted = _cfg(query_prompt_name="query")

    # Document-vector identity is untouched by the query prompt...
    assert plain.compute_pipeline_hash() == prompted.compute_pipeline_hash()
    # ...but query-vector identity MUST split, or a prompt change would
    # serve stale cached query vectors.
    assert plain.compute_query_identity_hash() != prompted.compute_query_identity_hash()


def test_query_identity_hash_deterministic_and_model_sensitive() -> None:
    assert _cfg().compute_query_identity_hash() == _cfg().compute_query_identity_hash()
    other_model = EmbeddingConfig(model_name="other-model", dim=512)
    assert _cfg().compute_query_identity_hash() != other_model.compute_query_identity_hash()


# ── AC-14: cache tunables never perturb pipeline hash ─────────────────────


def test_pipeline_hash_stable_across_query_cache_settings() -> None:
    default_cache = _cfg()
    tuned_cache = _cfg(query_cache=QueryCacheConfig(enabled=False, max_entries=7, ttl_seconds=1.5))

    # A cache setting changes no stored document vector: flipping it must
    # not invalidate existing .db/.tq sidecars...
    assert default_cache.compute_pipeline_hash() == tuned_cache.compute_pipeline_hash()
    # ...nor split the query-vector identity (same model → same vectors).
    assert default_cache.compute_query_identity_hash() == tuned_cache.compute_query_identity_hash()


# ── AC-15: config surface — defaults, overlay, env, extra=forbid ──────────


def test_query_cache_defaults() -> None:
    cache = AppConfig.load().embedding.query_cache
    assert cache.enabled is True
    assert cache.max_entries == 512
    assert cache.ttl_seconds == 0.0


def test_query_cache_yaml_overlay_overrides(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("embedding:\n  query_cache:\n    enabled: false\n    max_entries: 64\n")
    cache = AppConfig.load(explicit_path=overlay).embedding.query_cache
    assert cache.enabled is False
    assert cache.max_entries == 64


def test_query_cache_env_override(monkeypatch) -> None:
    monkeypatch.setenv("PYDOCS_EMBEDDING__QUERY_CACHE__MAX_ENTRIES", "2048")
    assert AppConfig.load().embedding.query_cache.max_entries == 2048


def test_query_cache_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        _cfg(query_cache={"enabled": True, "bogus": 1})


def test_query_cache_rejects_invalid_bounds() -> None:
    with pytest.raises(ValidationError):
        QueryCacheConfig(max_entries=0)
    with pytest.raises(ValidationError):
        QueryCacheConfig(ttl_seconds=-1.0)


# ── Late-interaction twin: its own query_cache block ───────────────────────


def test_late_interaction_query_cache_defaults_are_li_sized() -> None:
    cache = LateInteractionConfig().query_cache
    assert cache.enabled is True
    # Per-token matrices are ~30-60× larger per entry than pooled vectors,
    # so the LI block defaults to a smaller LRU than embedding.query_cache.
    assert cache.max_entries == 128
    assert cache.ttl_seconds == 0.0


def test_late_interaction_pipeline_hash_stable_across_query_cache_settings() -> None:
    plain = LateInteractionConfig()
    tuned = LateInteractionConfig(query_cache=QueryCacheConfig(enabled=False, max_entries=3))
    assert plain.compute_pipeline_hash() == tuned.compute_pipeline_hash()


def test_late_interaction_query_cache_env_override(monkeypatch) -> None:
    monkeypatch.setenv("PYDOCS_LATE_INTERACTION__QUERY_CACHE__MAX_ENTRIES", "16")
    assert AppConfig.load().late_interaction.query_cache.max_entries == 16
