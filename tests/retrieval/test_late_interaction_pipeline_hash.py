"""LateInteractionConfig folds into ingestion_pipeline_hash when active.

Spec Decision G + Task 13: the LateInteractionConfig identity (model_name,
dim, document_length, ...) only changes vector identity when the active
ingestion YAML actually references the ``embed_chunks_multi_vector`` stage.
For a default install (single-vector ingestion), toggling
LateInteractionConfig MUST NOT change ``ingestion_pipeline_hash`` — the
"default install hash is stable" invariant.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig, LateInteractionConfig


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    """Isolate each test from ambient ``PYDOCS_*`` env vars and a user file.

    Mirrors the fixture in ``test_config.py`` / ``test_late_interaction_config.py``
    so ``AppConfig.load()`` resolves only the shipped baseline unless a test
    explicitly sets env or supplies an explicit_path.
    """
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.delenv("PYDOCS_LOG_LEVEL", raising=False)
    monkeypatch.chdir(tmp_path)
    # Empty user config in cwd so AppConfig.load() resolves tmp_path as the
    # user-config dir: the multi-vector tests below point pipeline_path at a
    # tmp YAML, which must sit inside the pipeline_path allowlist (shipped
    # pipelines dir OR user-config dir). An empty mapping adds no overrides, so
    # the shipped-baseline stability tests are unaffected.
    (tmp_path / "pydocs-mcp.yaml").write_text("{}\n")
    yield


def test_li_device_excluded_from_pipeline_hash() -> None:
    """Switching cpu<->cuda must NOT invalidate the LI index cache."""
    from pydocs_mcp.retrieval.config import LateInteractionConfig

    cpu = LateInteractionConfig(enabled=True, device="cpu")
    cuda = LateInteractionConfig(enabled=True, device="cuda")
    assert cpu.compute_pipeline_hash() == cuda.compute_pipeline_hash()


def test_default_ingestion_hash_unaffected_by_late_interaction_toggle() -> None:
    """Default install: shipped ingestion YAML has no ``embed_chunks_multi_vector``.

    Toggling LateInteractionConfig (enabled True/False, swapping model_name)
    MUST NOT change ``ingestion_pipeline_hash``. This is the
    "default install hash is stable" invariant — multi-vector identity only
    folds in when the active YAML actually consumes it.
    """
    a = AppConfig.load()
    h_baseline = a.ingestion_pipeline_hash

    b = AppConfig.load()
    object.__setattr__(
        b, "late_interaction", LateInteractionConfig(enabled=True, model_name="some-model")
    )
    assert b.ingestion_pipeline_hash == h_baseline

    c = AppConfig.load()
    object.__setattr__(
        c, "late_interaction", LateInteractionConfig(enabled=True, model_name="other-model")
    )
    assert c.ingestion_pipeline_hash == h_baseline


def test_multi_vector_ingestion_pipeline_hash_changes_on_model(tmp_path: Path) -> None:
    """When the YAML references ``embed_chunks_multi_vector``, swapping the
    LateInteractionConfig.model_name MUST change the hash.
    """
    yaml_text = (
        "steps:\n"
        "  - { name: discover, type: file_discovery, params: {} }\n"
        "  - { name: embed, type: embed_chunks_multi_vector, params: {} }\n"
    )
    pipe = tmp_path / "ing.yaml"
    pipe.write_text(yaml_text)

    a = AppConfig.load()
    a.extraction.ingestion.pipeline_path = pipe
    object.__setattr__(a, "late_interaction", LateInteractionConfig(enabled=True, model_name="m1"))
    h1 = a.ingestion_pipeline_hash

    b = AppConfig.load()
    b.extraction.ingestion.pipeline_path = pipe
    object.__setattr__(b, "late_interaction", LateInteractionConfig(enabled=True, model_name="m2"))
    h2 = b.ingestion_pipeline_hash

    assert h1 != h2


def test_multi_vector_ingestion_pipeline_hash_stable_when_unchanged(tmp_path: Path) -> None:
    """Idempotency: same YAML + same LateInteractionConfig → same hash."""
    yaml_text = "steps:\n  - { name: embed, type: embed_chunks_multi_vector, params: {} }\n"
    pipe = tmp_path / "ing.yaml"
    pipe.write_text(yaml_text)

    a = AppConfig.load()
    a.extraction.ingestion.pipeline_path = pipe
    object.__setattr__(a, "late_interaction", LateInteractionConfig(enabled=True, model_name="m1"))

    b = AppConfig.load()
    b.extraction.ingestion.pipeline_path = pipe
    object.__setattr__(b, "late_interaction", LateInteractionConfig(enabled=True, model_name="m1"))

    assert a.ingestion_pipeline_hash == b.ingestion_pipeline_hash


def test_multi_vector_ingestion_hash_differs_from_single_vector(tmp_path: Path) -> None:
    """A YAML *with* multi_vector + late_interaction folded MUST differ from
    the same YAML *without* the multi_vector stage. Documents that the fold
    is gated on the YAML referencing the stage.
    """
    multi_yaml = tmp_path / "multi.yaml"
    multi_yaml.write_text(
        "steps:\n  - { name: embed, type: embed_chunks_multi_vector, params: {} }\n"
    )
    single_yaml = tmp_path / "single.yaml"
    single_yaml.write_text("steps:\n  - { name: embed, type: embed_chunks, params: {} }\n")

    a = AppConfig.load()
    a.extraction.ingestion.pipeline_path = multi_yaml
    object.__setattr__(a, "late_interaction", LateInteractionConfig(enabled=True, model_name="m1"))

    b = AppConfig.load()
    b.extraction.ingestion.pipeline_path = single_yaml
    object.__setattr__(b, "late_interaction", LateInteractionConfig(enabled=True, model_name="m1"))

    # Different YAML bytes → different hash, but additionally the multi-vector
    # branch folds late_interaction identity in.
    assert a.ingestion_pipeline_hash != b.ingestion_pipeline_hash
