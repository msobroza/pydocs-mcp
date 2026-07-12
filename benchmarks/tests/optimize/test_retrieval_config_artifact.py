"""The retrieval_config structured YAML artifact — overlay bytes + key firewall (AC-6 half)."""

from __future__ import annotations

from pathlib import Path

import yaml

from pydocs_eval.optimize.artifacts.retrieval_config import RetrievalConfigArtifact
from pydocs_eval.optimize.registries import artifact_registry


def test_registered() -> None:
    assert "retrieval_config" in artifact_registry.names()


def test_render_is_the_literal_seed_bytes(tmp_path: Path) -> None:
    seed = tmp_path / "exp_hybrid_rrf_k60.yaml"
    seed.write_text("search:\n  default_limit: 10\n", encoding="utf-8")
    art = RetrievalConfigArtifact(seed_path=seed)
    assert art.render() == "search:\n  default_limit: 10\n"


def test_with_content_overrides_the_seed(tmp_path: Path) -> None:
    seed = tmp_path / "seed.yaml"
    seed.write_text("search: {}\n", encoding="utf-8")
    art = RetrievalConfigArtifact(seed_path=seed).with_content("output: {}\n")
    assert art.render() == "output: {}\n"


def test_known_appconfig_sections_pass() -> None:
    doc = yaml.safe_dump({"search": {"default_limit": 10}, "output": {}})
    assert RetrievalConfigArtifact().with_content(doc).validate() == ()


def test_unknown_top_level_key_fails() -> None:
    violations = RetrievalConfigArtifact().with_content("mystery_section: {}\n").validate()
    assert any("mystery_section" in v for v in violations)


def test_known_sections_come_from_the_product_model() -> None:
    # Never a hard-coded list: the violation message names AppConfig fields.
    violations = RetrievalConfigArtifact().with_content("nope: {}\n").validate()
    assert any("search" in v for v in violations)


def test_non_mapping_yaml_fails_without_raising() -> None:
    assert RetrievalConfigArtifact().with_content("- just\n- a list\n").validate() != ()
    assert RetrievalConfigArtifact().with_content(":( not yaml [").validate() != ()


def test_empty_unseeded_artifact_fails() -> None:
    assert RetrievalConfigArtifact().validate() != ()


def test_fingerprint_tracks_content(tmp_path: Path) -> None:
    a = RetrievalConfigArtifact().with_content("search: {}\n")
    b = RetrievalConfigArtifact().with_content("output: {}\n")
    assert a.fingerprint != b.fingerprint and len(a.fingerprint) == 64
