"""``embedding.query_prefix`` config contract.

The prefix is a literal, query-side-only instruction for instruction-tuned
embedders (Qwen3-Embedding's ``"Instruct: {task}\\nQuery:"``). It must never
change vector identity (``compute_pipeline_hash`` / ``ingestion_pipeline_hash``
— stored document vectors are untouched) but MUST change the query-cache
identity (``compute_query_identity_hash``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig, EmbeddingConfig

_QWEN_PREFIX = (
    "Instruct: Given a natural-language description of what a piece of code "
    "does, retrieve the code that implements it\nQuery:"
)
# Golden values computed on origin/main (2a5592a) before this feature: the
# conditional fold must keep every pre-existing query identity byte-identical.
_GOLDEN_DEFAULT_QUERY_IDENTITY = "8288822d3b605e02"
_GOLDEN_PROMPT_NAME_QUERY_IDENTITY = "a15f6e822c171404"


def _openai_qwen(**overrides: object) -> EmbeddingConfig:
    base: dict[str, object] = {
        "provider": "openai",
        "model_name": "qwen/qwen3-embedding-4b",
        "dim": 2560,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "send_dimensions": False,
    }
    return EmbeddingConfig(**{**base, **overrides})  # type: ignore[arg-type]


def _st_qwen(**overrides: object) -> EmbeddingConfig:
    base: dict[str, object] = {
        "provider": "sentence_transformers",
        "model_name": "Qwen/Qwen3-Embedding-0.6B",
        "dim": 1024,
    }
    return EmbeddingConfig(**{**base, **overrides})  # type: ignore[arg-type]


def test_query_prefix_defaults_to_none() -> None:
    assert EmbeddingConfig().query_prefix is None
    assert AppConfig.load().embedding.query_prefix is None


def test_real_newline_prefix_round_trips_byte_for_byte() -> None:
    cfg = _openai_qwen(query_prefix=_QWEN_PREFIX)
    assert cfg.query_prefix == _QWEN_PREFIX


def test_prefix_is_never_stripped() -> None:
    cfg = EmbeddingConfig(query_prefix="query: ")
    assert cfg.query_prefix == "query: "


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_blank_prefix_rejected_with_repr(blank: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        EmbeddingConfig(query_prefix=blank)
    msg = str(excinfo.value)
    assert repr(blank) in msg
    assert "omit the key" in msg


def test_template_placeholder_rejected() -> None:
    with pytest.raises(ValidationError, match="literal prefix, not a template"):
        EmbeddingConfig(query_prefix="Instruct: x\nQuery: {query}")


def test_unescaped_literal_backslash_n_rejected_with_yaml_and_shell_hints() -> None:
    with pytest.raises(ValidationError) as excinfo:
        EmbeddingConfig(query_prefix="Instruct: x\\nQuery:")
    msg = str(excinfo.value)
    assert "double-quoted" in msg
    assert "$'" in msg


def test_literal_backslash_n_alongside_real_newline_accepted() -> None:
    # A real newline proves escaping worked; an embedded literal "\n" (a
    # Windows path) is then genuine content, not the unescaped signature.
    prefix = "Instruct: search C:\\new\nQuery:"
    assert EmbeddingConfig(query_prefix=prefix).query_prefix == prefix


def test_prefix_and_prompt_name_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        _st_qwen(query_prefix=_QWEN_PREFIX, query_prompt_name="query")


@pytest.mark.parametrize(
    "make",
    [
        lambda **kw: EmbeddingConfig(**kw),
        _openai_qwen,
        _st_qwen,
    ],
    ids=["fastembed", "openai", "sentence_transformers"],
)
def test_pipeline_hash_ignores_prefix(make) -> None:
    assert make(query_prefix=_QWEN_PREFIX).compute_pipeline_hash() == make().compute_pipeline_hash()


def test_ingestion_pipeline_hash_ignores_prefix_from_yaml_overlay(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text('embedding:\n  query_prefix: "Instruct: find code\\nQuery:"\n')
    with_prefix = AppConfig.load(explicit_path=overlay)
    assert with_prefix.embedding.query_prefix == "Instruct: find code\nQuery:"
    assert with_prefix.ingestion_pipeline_hash == AppConfig.load().ingestion_pipeline_hash


def test_query_identity_folds_prefix() -> None:
    plain = _openai_qwen().compute_query_identity_hash()
    a = _openai_qwen(query_prefix=_QWEN_PREFIX).compute_query_identity_hash()
    b = _openai_qwen(query_prefix="query: ").compute_query_identity_hash()
    assert len({plain, a, b}) == 3


def test_query_identity_unchanged_for_pre_existing_configs() -> None:
    assert EmbeddingConfig().compute_query_identity_hash() == _GOLDEN_DEFAULT_QUERY_IDENTITY
    named = _st_qwen(query_prompt_name="query").compute_query_identity_hash()
    assert named == _GOLDEN_PROMPT_NAME_QUERY_IDENTITY


def test_unset_identity_is_the_pre_feature_formula() -> None:
    # Golden by construction, not just by recorded value: with no prefix the
    # hash input is exactly the pre-feature "{pipeline_hash}|query_prompt=".
    cfg = _openai_qwen()
    raw = f"{cfg.compute_pipeline_hash()}|query_prompt="
    assert cfg.compute_query_identity_hash() == hashlib.sha256(raw.encode()).hexdigest()[:16]


_MAKERS_BY_PROVIDER = {
    "fastembed": lambda **kw: EmbeddingConfig(**kw),
    "openai": _openai_qwen,
    "sentence_transformers": _st_qwen,
}


def test_every_provider_literal_is_covered() -> None:
    # A new provider must be added here, which forces a decision about
    # whether it applies query_prefix natively or through the wrapper.
    literal = get_args(EmbeddingConfig.model_fields["provider"].annotation)
    assert set(literal) == set(_MAKERS_BY_PROVIDER)


@pytest.mark.parametrize("provider", sorted(_MAKERS_BY_PROVIDER))
def test_prefix_accepted_for_every_provider(provider: str) -> None:
    cfg = _MAKERS_BY_PROVIDER[provider](query_prefix=_QWEN_PREFIX)
    assert cfg.query_prefix == _QWEN_PREFIX


def test_late_interaction_hash_unchanged_by_embedding_prefix(tmp_path: Path) -> None:
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text('embedding:\n  query_prefix: "Instruct: find code\\nQuery:"\n')
    with_prefix = AppConfig.load(explicit_path=overlay).late_interaction
    assert (
        with_prefix.compute_pipeline_hash()
        == AppConfig.load().late_interaction.compute_pipeline_hash()
    )


def test_env_var_with_real_newline_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDOCS_EMBEDDING__QUERY_PREFIX", "Instruct: find code\nQuery:")
    assert AppConfig.load().embedding.query_prefix == "Instruct: find code\nQuery:"


def test_env_var_with_literal_backslash_n_gets_shell_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDOCS_EMBEDDING__QUERY_PREFIX", "Instruct: find code\\nQuery:")
    with pytest.raises(ValidationError, match=r"\$'"):
        AppConfig.load()
