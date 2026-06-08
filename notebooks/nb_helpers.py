"""Shared helpers for the pydocs-mcp method notebooks.

`make_searcher()` builds and runs the retrieval **pipeline in Python** — the
exact same `RetrieverPipeline` the CLI and MCP server use — instead of shelling
out to `pydocs-mcp search`. Building it ourselves lets us read the *ranked*
candidates (score + qualified_name) rather than the CLI's pre-rendered markdown.

Flow per method:
    config  = AppConfig.load(explicit_path=<method.yaml>)   # which method
    context = build_retrieval_context(db_path, config)       # embedder / vectors / llm
    pipeline = build_chunk_pipeline_from_config(config, ctx) # the RetrieverPipeline
    state   = await pipeline.run(SearchQuery(terms=q, ...))  # run it
    hits    = state.candidates.items                         # ranked chunks

`db_path` is the on-disk index the `pydocs-mcp index` CLI cell wrote for this
method (we only use the CLI for the heavyweight *indexing* wiring).

Notebooks only depend on `pydocs_mcp` + the committed `sample_repo/` +
`queries.json` — no benchmarks import is needed at notebook runtime.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.db import cache_path_for_project
from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.config import AppConfig, build_chunk_pipeline_from_config
from pydocs_mcp.retrieval.factories import build_retrieval_context

SAMPLE_REPO = Path("sample_repo")
QUERIES_JSON = Path("queries.json")


@dataclass(frozen=True)
class Hit:
    """One ranked retrieval result."""

    rank: int
    score: float | None
    qualified_name: str | None
    title: str | None
    text: str


def index_db_path(cache_dir: str | os.PathLike, repo: Path = SAMPLE_REPO) -> Path:
    """The SQLite index the `pydocs-mcp index ... --cache-dir <cache_dir>` cell wrote.

    Mirrors the CLI: the per-project ``<name>_<hash>.db`` slug under the chosen
    cache root (so each method's index is isolated and never clobbers another).
    """
    return Path(cache_dir) / cache_path_for_project(repo.resolve()).name


def make_searcher(config_path: str | os.PathLike | None, cache_dir: str | os.PathLike,
                  repo: Path = SAMPLE_REPO):
    """Build the Python retrieval pipeline for one method; return an async ``search`` fn.

    `config_path=None` selects the shipped default (BM25). Otherwise pass a
    method config from ``configs/``. `cache_dir` must match the index cell.
    """
    config = (
        AppConfig.load(explicit_path=Path(config_path))
        if config_path is not None
        else AppConfig.load()
    )
    db_path = index_db_path(cache_dir, repo)
    if not db_path.exists():
        raise FileNotFoundError(
            f"No index at {db_path} — run this notebook's `pydocs-mcp index ... "
            f"--cache-dir {cache_dir}` cell first."
        )
    context = build_retrieval_context(db_path, config)
    pipeline = build_chunk_pipeline_from_config(config, context)

    async def search(query: str, limit: int = 5) -> list[Hit]:
        # Run the pipeline directly and read the ranked candidates. The composite
        # `token_budget_formatter` sets state.result but KEEPS state.candidates,
        # so the ranked list with per-chunk scores is available either way.
        state = await pipeline.run(SearchQuery(terms=query, max_results=limit))
        items = state.candidates.items if state.candidates is not None else ()
        # The pipeline's own `limit` step caps the list (default 8); slice to the
        # caller's requested top-N for display.
        return [
            Hit(
                rank=i + 1,
                score=c.relevance,
                qualified_name=c.metadata.get("qualified_name"),
                title=c.metadata.get("title"),
                text=c.text,
            )
            for i, c in enumerate(items[:limit])
        ]

    return search


def load_queries(path: str | os.PathLike = QUERIES_JSON) -> list[dict]:
    """The static RepoQA example queries (NL description + gold function)."""
    return json.loads(Path(path).read_text())


def _is_gold(hit: Hit, gold_name: str) -> bool:
    qn_leaf = (hit.qualified_name or "").rsplit(".", 1)[-1]
    title = hit.title or ""
    return qn_leaf == gold_name or gold_name in title


def gold_rank(hits: list[Hit], gold_name: str) -> int | None:
    """1-indexed rank of the gold function in the hits, or None if absent."""
    return next((h.rank for h in hits if _is_gold(h, gold_name)), None)


def show_results(q: dict, hits: list[Hit], snippet_chars: int = 0) -> None:
    """Print the query, its gold answer, and the ranked python-pipeline hits."""
    nl = " ".join(q["query"].split())
    print("QUERY :", (nl[:150] + " …") if len(nl) > 150 else nl)
    print("GOLD  :", q["gold_name"], f'({q.get("needle_path", "")})')
    rk = gold_rank(hits, q["gold_name"])
    print(f"RESULT: gold found at rank {rk}" if rk else "RESULT: gold NOT in top results")
    for h in hits:
        mark = "  <-- gold" if _is_gold(h, q["gold_name"]) else ""
        score = f"{h.score:6.3f}" if h.score is not None else "   -  "
        print(f"   #{h.rank} [{score}] {h.qualified_name or h.title}{mark}")
        if snippet_chars:
            snip = " ".join(h.text.split())[:snippet_chars]
            print(f"        {snip}")
    print()


def load_dotenv(path: str | os.PathLike = "../.env") -> bool:
    """Load KEY=VALUE lines from a .env into os.environ (for the tree notebook's
    OPENAI_API_KEY). Returns True if the file was found. No external dependency."""
    p = Path(path)
    if not p.exists():
        return False
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    return True
