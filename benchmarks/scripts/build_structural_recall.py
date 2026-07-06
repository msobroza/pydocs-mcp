#!/usr/bin/env python
"""Generate the structural-recall fixture from RepoQA + the reference graph.

For each RepoQA repo: materialize the corpus, index it with pydocs-mcp (so the
reference graph is built), then for each needle ``N`` pick a 1-hop graph
NEIGHBOUR as the gold — a caller of N, a callee of N, or an overriding subclass
method — subject to two gates:

  * the needle ``N`` must itself be in the dense top-K (``--seed-top-k``), since
    ``graph_expand`` seeds from the dense top-S — a needle dense can't retrieve
    is unreachable by graph expansion, so such a task would be unfair; and
  * the gold neighbour must NOT be the dense top-1 (otherwise the task isn't
    "hard" — dense already nails it).

The query stays N's natural-language description. The result is exactly the
population dense ranks poorly but a 1-hop graph expansion from the dense hit
recovers (see ``datasets/structural_recall.py``).

Output: a static fixture JSON consumed by the ``repoqa-structural`` dataset.
This is an OFFLINE generator (it downloads RepoQA, indexes every repo, and runs
an embedder) — not part of the eval hot path.

Usage::

    PYTHONPATH=benchmarks/src python benchmarks/scripts/build_structural_recall.py \\
        --config benchmarks/configs/repoqa_dense_f2llm330m.yaml \\
        --out benchmarks/fixtures/structural_recall.json \\
        [--gpu] [--limit-repos N] [--seed-top-k 10]
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
        "--seed-top-k",
        type=int,
        default=10,
        help="The needle (seed) must appear in the dense top-K for the task to "
        "qualify — graph_expand seeds from the dense top-S, so a needle dense "
        "can't retrieve is unreachable by graph expansion. Also the gold must "
        "not be the dense top-1 (else the task isn't 'hard').",
    )
    return p.parse_args()


def _qname(chunk: Any) -> str | None:
    value = chunk.metadata.get("qualified_name")
    return value if value else None


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


def _neighbours_of(
    callers: list[Any], callees: list[Any], kinds_ok: set[str]
) -> list[tuple[str, str]]:
    """1-hop reference-graph neighbours of a seed as (qname, gold_kind).

    callers reference the seed (edge ``from_node_id``); callees are referenced
    by the seed (edge ``to_node_id``). INHERITS callers are overriding subclass
    methods. Unresolved callee targets (``to_node_id`` None) are skipped.
    """
    out: list[tuple[str, str]] = []
    for ref in callers:
        kind = str(ref.kind)
        if kind in kinds_ok and ref.from_node_id:
            out.append((ref.from_node_id, "override" if kind.endswith("inherits") else "caller"))
    for ref in callees:
        kind = str(ref.kind)
        if kind in kinds_ok and ref.to_node_id:
            out.append((ref.to_node_id, "override" if kind.endswith("inherits") else "callee"))
    return out


async def _build_rows_for_repo(
    needles: list[dict[str, Any]],
    *,
    repo: str,
    commit: str,
    content: dict[str, str],
    config: Any,
    seed_top_k: int,
) -> list[dict[str, Any]]:
    from benchmarks.eval.corpus import materialize_corpus
    from benchmarks.eval.systems.pydocs import PydocsMcpSystem
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    kinds_ok = {str(ReferenceKind.CALLS), str(ReferenceKind.INHERITS)}
    corpus_dir = materialize_corpus(content)
    system = PydocsMcpSystem(index_dependencies=False)
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
            # The needle (seed) must be in the dense top-S — graph_expand seeds
            # from the dense top-S, so a needle dense can't retrieve is
            # unreachable by graph expansion (an unfair task).
            top_qnames = [t.qualified_name for t in await system.search(query, limit=seed_top_k)]
            if seed_qname not in top_qnames:
                continue
            dense_top1 = top_qnames[0] if top_qnames else None

            async with uow_factory() as uow:
                callers = await uow.references.find_callers(target_node_id=seed_qname)
                callees = await uow.references.find_callees(from_node_id=seed_qname)

            # Gold = a 1-hop neighbour with a parseable chunk, not the seed and
            # not the dense top-1 (so the task is "hard"). Deterministic pick.
            seen: set[str] = set()
            cands: list[tuple[str, str, Any]] = []
            for nq, gold_kind in _neighbours_of(callers, callees, kinds_ok):
                if nq in seen or nq in (seed_qname, dense_top1):
                    continue
                seen.add(nq)
                chunk = by_qname.get(nq)
                if chunk is not None and _is_parseable(chunk.text):
                    cands.append((nq, gold_kind, chunk))
            if not cands:
                continue
            cands.sort(key=lambda c: c[0])
            nq, gold_kind, chunk = cands[0]
            rows.append(
                {
                    "query": query,
                    "gold_ast_body": chunk.text,
                    # NOTE: repo content is NOT stored here — it would duplicate
                    # each repo's full source per task (a ~20MB fixture). The
                    # dataset adapter reconstructs the corpus from the RepoQA
                    # release (already cached) by (repo, commit) at eval time.
                    "repo": repo,
                    "commit": commit,
                    "needle_path": needle["path"],
                    "seed_qname": seed_qname,
                    "gold_qname": nq,
                    "gold_kind": gold_kind,
                    "hop_distance": 1,
                }
            )
    finally:
        await system.teardown()
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
    for (repo, commit), repo_rows in repos:
        content = dict(repo_rows[0]["content"])
        # Each flattened row carries the needle under "needle" (the repo + content
        # are duplicated across a repo's rows). _build_rows_for_repo expects the
        # needle dicts (name / path / description / start_line / end_line).
        needles = [r["needle"] for r in repo_rows]
        try:
            rows = await _build_rows_for_repo(
                needles,
                repo=repo,
                commit=commit,
                content=content,
                config=config,
                seed_top_k=args.seed_top_k,
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
