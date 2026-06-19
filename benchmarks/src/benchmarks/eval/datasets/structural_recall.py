"""Structural-recall dataset — RepoQA needles re-targeted to graph neighbours.

RepoQA ``small_test`` is saturated (dense retrieval already scores ~1.0), so it
cannot show whether reference-graph expansion helps. This dataset is hard *by
construction*: each task keeps the original needle's natural-language
description as the query, but the GOLD answer is a graph NEIGHBOUR of the
needle (a caller, or an overriding subclass method) that is embedding-dissimilar
to the query — i.e. the answer a dense retriever misses but a 1-hop graph
expansion from the dense hit recovers.

The query set is generated offline by ``benchmarks/scripts/build_structural_recall.py``
(which indexes each RepoQA repo, walks the reference graph, and applies a
dissimilarity gate) into a static fixture JSON. This adapter only loads that
fixture, so eval-time has no indexing/graph cost beyond the normal run.

Fixture schema (``{"python": [row, ...]}``), one row per task::

    {
      "query":         <needle description>,
      "gold_ast_body": <neighbour function source, ast.parse-able>,
      "content":       {<relative_path>: <source>, ...},   # repo snapshot
      "repo":          <repo id>,
      "commit":        <sha>,
      "needle_path":   <file of the original needle>,
      "seed_qname":    <needle qualified_name>,
      "gold_qname":    <neighbour qualified_name>,
      "gold_kind":     "caller" | "override",
      "hop_distance":  <int>
    }

Relevance keys only on ``gold.ast_body`` (``metrics/_relevance.py``), so every
existing metric (recall@k, mrr) works unchanged — the lift is simply the
dense-only vs dense+graph column delta on this dataset.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..corpus import materialize_corpus
from ..serialization import dataset_registry
from .base_dataset import EvalTask, GoldAnswer

# benchmarks/src/benchmarks/eval/datasets/structural_recall.py -> benchmarks/
_BENCHMARKS_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_FIXTURE = _BENCHMARKS_ROOT / "fixtures" / "structural_recall.json"


@dataset_registry.register("repoqa-structural")
@dataclass
class StructuralRecallDataset:
    """Graph-neighbour-gold variant of RepoQA (see module docstring)."""

    name: str = "repoqa-structural"
    revision: str = "v1"
    fixture_path: Path | None = None
    language: str = "python"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        path = self.fixture_path or _DEFAULT_FIXTURE
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in data.get(self.language, []):
            content: Mapping[str, str] = dict(row["content"])
            commit = str(row.get("commit", ""))
            yield EvalTask(
                task_id=f"{row['repo']}@{commit[:7]}/{row['needle_path']}::{row['gold_qname']}",
                query=row["query"],
                gold=GoldAnswer(ast_body=row["gold_ast_body"]),
                # Default-arg closure binds THIS row's content (the late-binding
                # trap that would otherwise share the loop's last value).
                corpus_source=lambda files=content: materialize_corpus(files),
                metadata={
                    "repo": str(row["repo"]),
                    "commit": commit,
                    "language": self.language,
                    "needle_path": str(row["needle_path"]),
                    "seed_qname": str(row["seed_qname"]),
                    "gold_qname": str(row["gold_qname"]),
                    "gold_kind": str(row["gold_kind"]),
                    "hop_distance": str(row.get("hop_distance", "")),
                },
            )


__all__ = ["StructuralRecallDataset"]
