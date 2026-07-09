"""StructuralRecallDataset — fixture loading + EvalTask shape.

The fixture is generated offline by ``scripts/build_structural_recall.py``;
these tests use a tiny inline fixture (no network, no indexing) to pin the
adapter contract: gold is the NEIGHBOUR body, the query stays the needle
description, and the graph relationship is carried in metadata.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydocs_eval.datasets.base_dataset import Dataset
from pydocs_eval.datasets.structural_recall import StructuralRecallDataset
from pydocs_eval.serialization import dataset_registry

_FIXTURE = {
    "python": [
        {
            "query": "Return the factorial of n.",
            "gold_ast_body": "def caller():\n    return factorial(5)",
            "content": {"pkg/mod.py": "def factorial(n):\n    return 1\n"},
            "repo": "demo/repo",
            "commit": "0123456789abcdef",
            "needle_path": "pkg/mod.py",
            "seed_qname": "pkg.mod.factorial",
            "gold_qname": "pkg.mod.caller",
            "gold_kind": "caller",
            "hop_distance": 1,
        }
    ]
}


def _write_fixture(tmp_path: Path) -> Path:
    path = tmp_path / "structural_recall.json"
    path.write_text(json.dumps(_FIXTURE), encoding="utf-8")
    return path


def test_registered_in_dataset_registry() -> None:
    assert "repoqa-structural" in dataset_registry.names()


def test_satisfies_dataset_protocol() -> None:
    assert isinstance(StructuralRecallDataset(), Dataset)


async def test_yields_one_task_with_neighbour_gold(tmp_path: Path) -> None:
    ds = StructuralRecallDataset(fixture_path=_write_fixture(tmp_path))
    tasks = [t async for t in ds.tasks()]
    assert len(tasks) == 1
    task = tasks[0]
    # Gold is the NEIGHBOUR body, query is the original needle description.
    assert task.gold.ast_body == "def caller():\n    return factorial(5)"
    assert task.query == "Return the factorial of n."


async def test_metadata_carries_graph_relationship(tmp_path: Path) -> None:
    ds = StructuralRecallDataset(fixture_path=_write_fixture(tmp_path))
    tasks = [t async for t in ds.tasks()]
    task = tasks[0]
    assert task.metadata["seed_qname"] == "pkg.mod.factorial"
    assert task.metadata["gold_qname"] == "pkg.mod.caller"
    assert task.metadata["gold_kind"] == "caller"
    assert task.metadata["hop_distance"] == "1"
    assert task.metadata["language"] == "python"


async def test_corpus_source_materializes_content(tmp_path: Path) -> None:
    ds = StructuralRecallDataset(fixture_path=_write_fixture(tmp_path))
    tasks = [t async for t in ds.tasks()]
    corpus_dir = tasks[0].corpus_source()
    assert (corpus_dir / "pkg" / "mod.py").exists()


# ── pipeline config wiring ────────────────────────────────────────────────

_CONFIGS = Path(__file__).resolve().parents[2] / "configs"


def test_exp_dense_graph_pipeline_inserts_graph_expand_before_topk() -> None:
    cfg = yaml.safe_load((_CONFIGS / "pipelines" / "exp_dense_graph.yaml").read_text())
    types = [s["type"] for s in cfg["steps"]]
    assert "graph_expand" in types
    assert "rrf_fusion" not in types and "bm25_scorer" not in types  # embedding-centric
    # No dense_scorer in a pure-dense pipeline: dense_fetcher's ANN index score
    # IS the turbovec relevance (dense_scorer is a POST-FUSION re-ranker only).
    assert "dense_scorer" not in types
    assert types.index("dense_fetcher") < types.index("graph_expand") < types.index("top_k_filter")


@pytest.mark.parametrize("cfg_name", ["repoqa_dense_graph_f2llm330m", "repoqa_dense_f2llm330m"])
def test_sweep_configs_point_at_pipelines(cfg_name: str) -> None:
    cfg = yaml.safe_load((_CONFIGS / f"{cfg_name}.yaml").read_text())
    entry = cfg["pipelines"]["chunk"][0]
    assert entry["pipeline_path"].startswith("pipelines/")
    assert cfg["embedding"]["model_name"]  # an explicit embedder is pinned
