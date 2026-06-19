"""Structural-recall dataset — RepoQA needles re-targeted to graph neighbours.

RepoQA ``small_test`` is saturated (dense retrieval already scores ~1.0), so it
cannot show whether reference-graph expansion helps. This dataset is hard *by
construction*: each task keeps the original needle's natural-language
description as the query, but the GOLD answer is a graph NEIGHBOUR of the
needle (a caller, or an overriding subclass method) that is embedding-dissimilar
to the query — i.e. the answer a dense retriever misses but a 1-hop graph
expansion from the dense hit recovers.

The query set is generated offline by ``benchmarks/scripts/build_structural_recall.py``
(which indexes each RepoQA repo, walks the reference graph, and applies the
gates) into a static fixture JSON. The fixture is a THIN OVERLAY on RepoQA: it
does NOT store repo source (that would duplicate each repo's full tree per task,
a ~20MB blob). Instead this adapter reconstructs each task's corpus from the
RepoQA release (already cached locally) by ``(repo, commit)`` — exactly the
content the generator indexed.

Fixture schema (``{"python": [row, ...]}``), one row per task::

    {
      "query":         <needle description>,
      "gold_ast_body": <neighbour function source, ast.parse-able>,
      "repo":          <repo id>,
      "commit":        <sha>,
      "needle_path":   <file of the original needle>,
      "seed_qname":    <needle qualified_name>,
      "gold_qname":    <neighbour qualified_name>,
      "gold_kind":     "caller" | "callee" | "override",
      "hop_distance":  <int>
    }

A row MAY also carry an inline ``content`` map (used by the unit test's tiny
fixture); when present it is used directly instead of the RepoQA lookup.

Relevance keys only on ``gold.ast_body`` (``metrics/_relevance.py``), so every
existing metric (recall@k, mrr) works unchanged — the lift is simply the
dense-only vs dense+graph column delta on this dataset.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
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
    _repo_content: dict[str, Mapping[str, str]] | None = field(default=None, init=False, repr=False)

    def _content_for(self, repo: str, commit: str) -> Mapping[str, str]:
        """Repo source for (repo, commit) from the cached RepoQA release.

        Lazily builds a ``{repo: content}`` map once. Keyed by repo (RepoQA's
        Python split has one entry per repo); falls back to repo-only if a
        commit-qualified key is absent.
        """
        if self._repo_content is None:
            from .repoqa import RepoQADataset

            ds = RepoQADataset(language=self.language)
            rows = ds._load_from_release()
            self._repo_content = {r["repo"]: r["content"] for r in rows}
        try:
            return self._repo_content[repo]
        except KeyError as exc:  # pragma: no cover - guards a malformed fixture
            raise KeyError(
                f"repoqa-structural: repo {repo!r} (commit {commit[:7]}) not found in the "
                f"RepoQA release; regenerate the fixture or update RepoQA cache."
            ) from exc

    async def tasks(self) -> AsyncIterator[EvalTask]:
        path = self.fixture_path or _DEFAULT_FIXTURE
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        for row in data.get(self.language, []):
            commit = str(row.get("commit", ""))
            # Thin fixture: reconstruct corpus from RepoQA. Inline ``content``
            # (the unit-test fixture) takes precedence when present.
            inline = row.get("content")
            content: Mapping[str, str] = (
                dict(inline) if inline is not None else self._content_for(row["repo"], commit)
            )
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
