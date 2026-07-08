"""EmbeddingConfig.compute_pipeline_hash + AppConfig.compute_ingestion_pipeline_hash.

Per spec Decision 4 + AC-12. pipeline_hash captures embedder identity +
raw ingestion YAML bytes. Any change to embedder model/dim/bit_width OR
any edit to the YAML invalidates the hash → diff-merge sees all chunks
as added → full re-embed via the existing path.
"""

from pathlib import Path

from pydocs_mcp.retrieval.config import AppConfig, EmbeddingConfig


def test_compute_pipeline_hash_deterministic() -> None:
    """Same EmbeddingConfig fields → same hash."""
    cfg1 = EmbeddingConfig(
        provider="fastembed",
        model_name="m",
        dim=8,
        batch_size=16,
        bit_width=4,
    )
    cfg2 = EmbeddingConfig(
        provider="fastembed",
        model_name="m",
        dim=8,
        batch_size=16,
        bit_width=4,
    )
    assert cfg1.compute_pipeline_hash() == cfg2.compute_pipeline_hash()
    assert len(cfg1.compute_pipeline_hash()) == 64  # SHA-256 hex


def test_compute_pipeline_hash_excludes_batch_size() -> None:
    """batch_size affects throughput, not vector identity → not in hash."""
    cfg1 = EmbeddingConfig(
        provider="fastembed",
        model_name="m",
        dim=8,
        batch_size=16,
        bit_width=4,
    )
    cfg2 = EmbeddingConfig(
        provider="fastembed",
        model_name="m",
        dim=8,
        batch_size=64,
        bit_width=4,
    )
    assert cfg1.compute_pipeline_hash() == cfg2.compute_pipeline_hash()


def test_compute_pipeline_hash_changes_on_model_swap() -> None:
    """Different model_name → different hash."""
    cfg1 = EmbeddingConfig(
        provider="fastembed",
        model_name="model-A",
        dim=8,
        batch_size=16,
        bit_width=4,
    )
    cfg2 = EmbeddingConfig(
        provider="fastembed",
        model_name="model-B",
        dim=8,
        batch_size=16,
        bit_width=4,
    )
    assert cfg1.compute_pipeline_hash() != cfg2.compute_pipeline_hash()


def test_compute_pipeline_hash_changes_on_dim_change() -> None:
    cfg1 = EmbeddingConfig(
        provider="fastembed",
        model_name="m",
        dim=8,
        batch_size=16,
        bit_width=4,
    )
    cfg2 = EmbeddingConfig(
        provider="fastembed",
        model_name="m",
        dim=16,
        batch_size=16,
        bit_width=4,
    )
    assert cfg1.compute_pipeline_hash() != cfg2.compute_pipeline_hash()


def test_compute_ingestion_pipeline_hash_changes_on_yaml_edit(tmp_path: Path) -> None:
    """Editing the ingestion YAML (even a comment) invalidates the hash."""
    yaml_path = tmp_path / "ingestion.yaml"
    yaml_path.write_text("name: ingestion\nstages:\n  - {type: flatten}\n")
    # explicit_path makes tmp_path the user-config dir, so the controlled
    # ingestion YAML below sits inside the pipeline_path allowlist (shipped
    # pipelines dir OR user-config dir). The file must exist: AppConfig.load
    # rejects a missing explicit path (a typo'd --config must fail loud, not
    # silently fall back to shipped defaults).
    user_config_path = tmp_path / "pydocs-mcp.yaml"
    user_config_path.write_text("")
    cfg_a = AppConfig.load(explicit_path=user_config_path)
    cfg_a.extraction.ingestion.pipeline_path = yaml_path
    hash_a = cfg_a.compute_ingestion_pipeline_hash()

    # Edit YAML: comment-only change is enough (raw-bytes hash is conservative)
    yaml_path.write_text("# new comment\nname: ingestion\nstages:\n  - {type: flatten}\n")
    cfg_b = AppConfig.load(explicit_path=user_config_path)
    cfg_b.extraction.ingestion.pipeline_path = yaml_path
    hash_b = cfg_b.compute_ingestion_pipeline_hash()

    assert hash_a != hash_b


def test_compute_ingestion_pipeline_hash_stable_when_yaml_unchanged(tmp_path: Path) -> None:
    yaml_path = tmp_path / "ingestion.yaml"
    yaml_path.write_text("stages:\n  - {type: flatten}\n")
    user_config_path = tmp_path / "pydocs-mcp.yaml"
    user_config_path.write_text("")
    cfg = AppConfig.load(explicit_path=user_config_path)
    cfg.extraction.ingestion.pipeline_path = yaml_path
    h1 = cfg.compute_ingestion_pipeline_hash()
    h2 = cfg.compute_ingestion_pipeline_hash()
    assert h1 == h2


def test_compute_ingestion_pipeline_hash_resolves_relative_pipelines_path() -> None:
    """A config-relative ``pipelines/<name>.yaml`` override resolves through the
    shipped pipelines dir (the branch the resolver fix targets), NOT cwd — so
    the hash is computed without crashing and is stable across reloads. This is
    the real motivation for routing the override through the shared resolver;
    the other tests exercise the absolute-path branch.
    """
    cfg_a = AppConfig.load()
    cfg_a.extraction.ingestion.pipeline_path = Path("pipelines/ingestion.yaml")
    h1 = cfg_a.compute_ingestion_pipeline_hash()

    cfg_b = AppConfig.load()
    cfg_b.extraction.ingestion.pipeline_path = Path("pipelines/ingestion.yaml")
    h2 = cfg_b.compute_ingestion_pipeline_hash()

    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex — proves it actually read + hashed bytes


# ── backend + model_file_name folding (ST openvino/onnx quantization) ──


def test_default_hash_unchanged_by_backend_fields() -> None:
    """Golden stability: the new fields at their defaults must NOT alter the
    hash — mirrors the late-interaction 'default install hash is stable'
    invariant, so shipping this feature re-embeds nobody."""
    import hashlib

    cfg = EmbeddingConfig(provider="fastembed", model_name="m", dim=8, bit_width=4)
    legacy_identity = "|".join(
        [
            cfg.provider,
            cfg.model_name,
            str(cfg.dim),
            str(cfg.bit_width),
            str(cfg.max_seq_length),
            str(cfg.normalize),
        ]
    )
    assert cfg.compute_pipeline_hash() == hashlib.sha256(legacy_identity.encode()).hexdigest()


def test_backend_changes_hash() -> None:
    base = EmbeddingConfig(provider="sentence_transformers", model_name="m", dim=8)
    ov = EmbeddingConfig(
        provider="sentence_transformers", model_name="m", dim=8, backend="openvino"
    )
    assert base.compute_pipeline_hash() != ov.compute_pipeline_hash()


def test_model_file_name_changes_hash() -> None:
    base = EmbeddingConfig(provider="sentence_transformers", model_name="m", dim=8)
    q = EmbeddingConfig(
        provider="sentence_transformers",
        model_name="m",
        dim=8,
        model_file_name="openvino/openvino_model_qint8_quantized.xml",
    )
    assert base.compute_pipeline_hash() != q.compute_pipeline_hash()
    q2 = EmbeddingConfig(
        provider="sentence_transformers",
        model_name="m",
        dim=8,
        model_file_name="onnx/model_qint8_avx512.onnx",
    )
    assert q.compute_pipeline_hash() != q2.compute_pipeline_hash()


def test_openvino_plus_cuda_rejected() -> None:
    import pytest

    with pytest.raises(ValueError, match="OpenVINO"):
        EmbeddingConfig(
            provider="sentence_transformers",
            model_name="m",
            dim=8,
            backend="openvino",
            device="cuda",
        )
