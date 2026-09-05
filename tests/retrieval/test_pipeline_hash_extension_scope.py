"""The effective extension scope folds UNCONDITIONALLY into the pipeline hash.

ADR 0021 7a: multilang-on vs -off deployments index different corpora, so the
chunk-cache identity must diverge the moment the effective ``include_extensions``
set changes — NOT gated on YAML bytes the way the late-interaction fold is.
Flipping scope re-embeds by design; an identical scope hashes stably.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Isolate each test from ambient ``PYDOCS_*`` env vars and a user file."""
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PYDOCS_LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./pydocs-mcp.yaml
    yield


def _config_with_extensions(tmp_path: Path, name: str, exts: list[str]) -> AppConfig:
    # Override BOTH discovery scopes: the effective scope is the UNION of
    # project + dependency, so narrowing only one leaves the union unchanged.
    overlay = tmp_path / name
    listing = ", ".join(repr(e) for e in exts)
    overlay.write_text(
        "extraction:\n"
        "  discovery:\n"
        "    project:\n"
        f"      include_extensions: [{listing}]\n"
        "    dependency:\n"
        f"      include_extensions: [{listing}]\n"
    )
    return AppConfig.load(explicit_path=overlay)


def test_narrowing_the_extension_scope_changes_the_pipeline_hash(tmp_path: Path) -> None:
    # A narrowed scope (a subset of the widened default) is a DIFFERENT corpus
    # identity — the fold guarantees it re-embeds rather than silently reusing a
    # differently-scoped index.
    base = AppConfig.load()
    narrowed = _config_with_extensions(tmp_path, "narrow.yaml", [".py", ".md"])
    assert base.ingestion_pipeline_hash != narrowed.ingestion_pipeline_hash


def test_same_extension_scope_hashes_stably(tmp_path: Path) -> None:
    one = _config_with_extensions(tmp_path, "a.yaml", [".py", ".md", ".rst"])
    two = _config_with_extensions(tmp_path, "b.yaml", [".rst", ".py", ".md"])
    # Order-independent (the fold sorts): the same SET is the same identity.
    assert one.ingestion_pipeline_hash == two.ingestion_pipeline_hash


def test_scope_fold_is_unconditional_not_gated_on_yaml(tmp_path: Path) -> None:
    # Unlike the multi-vector fold, the scope fold is NOT gated on the ingestion
    # YAML referencing any stage: a default single-vector pipeline still sees its
    # hash move when the scope changes. Two disjoint scopes MUST differ.
    text_only = _config_with_extensions(tmp_path, "text.yaml", [".py"])
    with_toml = _config_with_extensions(tmp_path, "toml.yaml", [".py", ".toml"])
    assert text_only.ingestion_pipeline_hash != with_toml.ingestion_pipeline_hash
