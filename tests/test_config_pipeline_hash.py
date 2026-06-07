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
    # pipelines dir OR user-config dir). The file need not exist — load()'s
    # explicit override is honored regardless.
    cfg_a = AppConfig.load(explicit_path=tmp_path / "pydocs-mcp.yaml")
    cfg_a.extraction.ingestion.pipeline_path = yaml_path
    hash_a = cfg_a.compute_ingestion_pipeline_hash()

    # Edit YAML: comment-only change is enough (raw-bytes hash is conservative)
    yaml_path.write_text("# new comment\nname: ingestion\nstages:\n  - {type: flatten}\n")
    cfg_b = AppConfig.load(explicit_path=tmp_path / "pydocs-mcp.yaml")
    cfg_b.extraction.ingestion.pipeline_path = yaml_path
    hash_b = cfg_b.compute_ingestion_pipeline_hash()

    assert hash_a != hash_b


def test_compute_ingestion_pipeline_hash_stable_when_yaml_unchanged(tmp_path: Path) -> None:
    yaml_path = tmp_path / "ingestion.yaml"
    yaml_path.write_text("stages:\n  - {type: flatten}\n")
    cfg = AppConfig.load(explicit_path=tmp_path / "pydocs-mcp.yaml")
    cfg.extraction.ingestion.pipeline_path = yaml_path
    h1 = cfg.compute_ingestion_pipeline_hash()
    h2 = cfg.compute_ingestion_pipeline_hash()
    assert h1 == h2
