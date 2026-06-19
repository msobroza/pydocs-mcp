#!/usr/bin/env python
"""Generate the structural-recall fixture from RepoQA + the reference graph.

For each RepoQA repo: materialize the corpus, index it with pydocs-mcp (so the
reference graph is built), then for each needle ``N`` pick a graph NEIGHBOUR
(a caller of N, or an overriding subclass method) whose body is
embedding-dissimilar to N's description AND is not already the dense top-1 hit.
That neighbour becomes the gold for a task whose query stays N's description —
exactly the population that dense-only retrieval misses but a 1-hop graph
expansion recovers (see ``datasets/structural_recall.py``).

Output: a static fixture JSON consumed by the ``repoqa-structural`` dataset.
This is an OFFLINE generator (it downloads RepoQA, indexes every repo, and runs
an embedder) — not part of the eval hot path.

Usage::

    PYTHONPATH=benchmarks/src python benchmarks/scripts/build_structural_recall.py \\
        --config benchmarks/configs/repoqa_dense_f2llm330m.yaml \\
        --out benchmarks/fixtures/structural_recall.json \\
        [--gpu] [--limit-repos N] [--dissimilarity-threshold 0.5]
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import gc
import shutil
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build the structural-recall fixture.")
    p.add_argument(
        "--config", required=True, type=Path, help="AppConfig YAML (embedder must match eval)."
    )
    p.add_argument("--out", required=True, type=Path, help="Output fixture JSON path.")
    p.add_argument("--split", default="all", help="RepoQA split (all/dev/test/small_test).")
    p.add_argument("--gpu", action="store_true", help="Run the embedder on CUDA.")
    p.add_argument(
        "--limit-repos", type=int, default=None, help="Cap number of repos (smoke runs)."
    )
    p.add_argument(
        "--dissimilarity-threshold",
        type=float,
        default=0.5,
        help="Keep neighbours with cosine(query, neighbour) BELOW this (hard split).",
    )
    return p.parse_args()


def _qname(chunk: Any) -> str | None:
    value = chunk.metadata.get("qualified_name")
    return value if value else None


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _is_parseable(text: str) -> bool:
    try:
        ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    return True


def _resolve_seed_qname(needle: dict[str, Any], chunks: list[Any]) -> str | None:
    """Map a needle (name + path) to the indexed qualified_name of its symbol."""
    name = needle["name"]
    path = needle["path"]
    matches = [c for c in chunks if (_qname(c) or "").split(".")[-1] == name]
    if not matches:
        return None
    # Disambiguate by source file when the same symbol name occurs in many files.
    base = path.rsplit("/", 1)[-1]
    for c in matches:
        src = str(c.metadata.get("source_path", ""))
        if src.endswith(path) or src.rsplit("/", 1)[-1] == base:
            return _qname(c)
    return _qname(matches[0])


async def _build_rows_for_repo(
    needles: list[dict[str, Any]],
    *,
    repo: str,
    commit: str,
    content: dict[str, str],
    config: Any,
    threshold: float,
) -> list[dict[str, Any]]:
    from benchmarks.eval.corpus import materialize_corpus
    from benchmarks.eval.systems.pydocs import PydocsMcpSystem
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.extraction.strategies.embedders import build_embedder
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    corpus_dir = materialize_corpus(content)
    system = PydocsMcpSystem(index_dependencies=False)
    embedder = build_embedder(config.embedding)
    rows: list[dict[str, Any]] = []
    try:
        await system.index(corpus_dir, config)
        uow_factory = build_sqlite_uow_factory(system._db_path)
        async with uow_factory() as uow:
            all_chunks = list(await uow.chunks.list())
        by_qname = {q: c for c in all_chunks if (q := _qname(c))}

        for needle in needles:
            seed_qname = _resolve_seed_qname(needle, all_chunks)
            if seed_qname is None:
                continue
            query = needle["description"]
            # Dense top-1 for this query — used to require the gold is NOT the
            # obvious dense hit (otherwise the split isn't "hard").
            top = await system.search(query, limit=1)
            top_qname = top[0].qualified_name if top else None

            async with uow_factory() as uow:
                callers = await uow.references.find_callers(target_node_id=seed_qname)
            query_vec = await embedder.embed_query(query)

            best: tuple[float, dict[str, Any]] | None = None
            for ref in callers:
                if str(ref.kind) not in {str(ReferenceKind.CALLS), str(ReferenceKind.INHERITS)}:
                    continue
                nbr_qname = ref.from_node_id
                if not nbr_qname or nbr_qname in (seed_qname, top_qname):
                    continue
                chunk = by_qname.get(nbr_qname)
                if chunk is None or not _is_parseable(chunk.text):
                    continue
                (nbr_vec,) = await embedder.embed_chunks([chunk.text])
                cos = _cosine(np.asarray(query_vec), np.asarray(nbr_vec))
                if cos >= threshold:
                    continue  # too similar — dense would already find it
                gold_kind = "override" if str(ref.kind) == str(ReferenceKind.INHERITS) else "caller"
                candidate = {
                    "query": query,
                    "gold_ast_body": chunk.text,
                    "content": content,
                    "repo": repo,
                    "commit": commit,
                    "needle_path": needle["path"],
                    "seed_qname": seed_qname,
                    "gold_qname": nbr_qname,
                    "gold_kind": gold_kind,
                    "hop_distance": 1,
                }
                # Hardest survivor wins (lowest cosine); deterministic tiebreak.
                rank = (cos, nbr_qname)
                if best is None or rank < (best[0], best[1]["gold_qname"]):
                    best = (cos, candidate)
            if best is not None:
                rows.append(best[1])
    finally:
        await system.teardown()
        if hasattr(embedder, "close"):
            embedder.close()
        del embedder
        shutil.rmtree(corpus_dir, ignore_errors=True)
        gc.collect()
    return rows


async def _run(args: argparse.Namespace) -> int:
    import json

    from benchmarks.eval.datasets.repoqa import RepoQADataset
    from pydocs_mcp.retrieval.config import AppConfig

    config = AppConfig.load(explicit_path=args.config).with_device(gpu=args.gpu)
    dataset = RepoQADataset(split=args.split)
    raw = await asyncio.to_thread(dataset._load_from_release)

    by_repo: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in raw:
        by_repo[(row["repo"], row["commit_sha"])].append(row)
    repos = list(by_repo.items())
    if args.limit_repos is not None:
        repos = repos[: args.limit_repos]

    out_rows: list[dict[str, Any]] = []
    skipped_repos = 0
    for (repo, commit), needles in repos:
        content = dict(needles[0]["content"])
        try:
            rows = await _build_rows_for_repo(
                needles,
                repo=repo,
                commit=commit,
                content=content,
                config=config,
                threshold=args.dissimilarity_threshold,
            )
        except Exception as exc:
            skipped_repos += 1
            print(f"[skip] {repo}: {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        out_rows.extend(rows)
        print(f"[ok] {repo}: {len(rows)} structural-recall tasks", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"python": out_rows}, indent=2), encoding="utf-8")
    kinds = defaultdict(int)
    for r in out_rows:
        kinds[r["gold_kind"]] += 1
    print(
        f"\nWrote {len(out_rows)} tasks to {args.out} "
        f"(kinds={dict(kinds)}, repos_skipped={skipped_repos})",
        file=sys.stderr,
    )
    return 0 if out_rows else 1


def main() -> int:
    return asyncio.run(_run(_parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
