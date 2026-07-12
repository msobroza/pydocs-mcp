"""The ask_architecture structured artifact — validate + enumerate_space (AC-5)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pydocs_eval.optimize.artifacts.ask_architecture import AskArchitectureArtifact
from pydocs_eval.optimize.registries import artifact_registry


@pytest.fixture
def pipelines_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "pipelines"
    directory.mkdir()
    for stem in ("exp_hybrid_rrf_k60", "exp_dense_graph"):
        (directory / f"{stem}.yaml").write_text("search: {}\n", encoding="utf-8")
    return directory


def _artifact(pipelines_dir: Path, **overrides: object) -> AskArchitectureArtifact:
    fields: dict[str, object] = {
        "architecture": "text_react",
        "rewrite_enabled": True,
        "scope_pin": True,
        "retrieval_config": "exp_hybrid_rrf_k60",
        "max_agent_turns": 12,
        "pipelines_dir": pipelines_dir,
    }
    fields.update(overrides)
    return AskArchitectureArtifact(**fields)  # type: ignore[arg-type]


def test_registered() -> None:
    assert "ask_architecture" in artifact_registry.names()


def test_canonical_render_is_sorted_yaml(pipelines_dir: Path) -> None:
    rendered = _artifact(pipelines_dir).render()
    keys = list(yaml.safe_load(rendered))
    assert keys == sorted(keys)


def test_valid_cell_passes(pipelines_dir: Path) -> None:
    assert _artifact(pipelines_dir).validate() == ()


def test_unknown_architecture_fails(pipelines_dir: Path) -> None:
    violations = _artifact(pipelines_dir, architecture="plan_act").validate()
    assert any("plan_act" in v for v in violations)


def test_missing_pipeline_stem_fails(pipelines_dir: Path) -> None:
    violations = _artifact(pipelines_dir, retrieval_config="exp_nope").validate()
    assert any("exp_nope" in v for v in violations)


def test_out_of_range_turns_fail(pipelines_dir: Path) -> None:
    assert any(
        "max_agent_turns" in v for v in _artifact(pipelines_dir, max_agent_turns=0).validate()
    )
    assert any(
        "max_agent_turns" in v for v in _artifact(pipelines_dir, max_agent_turns=41).validate()
    )


def test_unknown_key_in_candidate_yaml_fails(pipelines_dir: Path) -> None:
    doc = yaml.safe_dump({"architecture": "text_react", "mystery_knob": 3})
    violations = _artifact(pipelines_dir).with_content(doc).validate()
    assert any("mystery_knob" in v for v in violations)


def test_unparseable_candidate_never_raises(pipelines_dir: Path) -> None:
    violations = _artifact(pipelines_dir).with_content(":( not yaml [").validate()
    assert violations != ()


_DIMS = {
    "architecture": ("text_react",),
    "rewrite_enabled": (True, False),
    "scope_pin": (True,),
    "retrieval_config": ("exp_hybrid_rrf_k60", "exp_dense_graph"),
    "max_agent_turns": (8, 12),
}


class TestEnumerateSpace:
    def test_yields_exactly_the_cross_product(self, pipelines_dir: Path) -> None:
        cells = AskArchitectureArtifact.enumerate_space(_DIMS, pipelines_dir=pipelines_dir)
        assert len(cells) == 1 * 2 * 1 * 2 * 2
        assert len({c.fingerprint for c in cells}) == len(cells)

    def test_deterministic_order(self, pipelines_dir: Path) -> None:
        first = AskArchitectureArtifact.enumerate_space(_DIMS, pipelines_dir=pipelines_dir)
        second = AskArchitectureArtifact.enumerate_space(_DIMS, pipelines_dir=pipelines_dir)
        assert [c.fingerprint for c in first] == [c.fingerprint for c in second]

    def test_fingerprints_stable_across_dim_key_order(self, pipelines_dir: Path) -> None:
        # AC-5: canonical render makes the fingerprint independent of the
        # YAML author's key order.
        reordered = dict(reversed(list(_DIMS.items())))
        first = AskArchitectureArtifact.enumerate_space(_DIMS, pipelines_dir=pipelines_dir)
        second = AskArchitectureArtifact.enumerate_space(reordered, pipelines_dir=pipelines_dir)
        assert {c.fingerprint for c in first} == {c.fingerprint for c in second}

    def test_unknown_dimension_fails_loud(self, pipelines_dir: Path) -> None:
        with pytest.raises(KeyError, match="mystery"):
            AskArchitectureArtifact.enumerate_space(
                {**_DIMS, "mystery": [1]}, pipelines_dir=pipelines_dir
            )

    def test_every_cell_passes_validate(self, pipelines_dir: Path) -> None:
        for cell in AskArchitectureArtifact.enumerate_space(_DIMS, pipelines_dir=pipelines_dir):
            assert cell.validate() == ()
