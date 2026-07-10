"""RepoQA-SNF dataset loader (spec §5.1).

RepoQA is distributed as a single gzipped JSON file from
``evalplus/repoqa_release`` GitHub Releases. Stdlib-only — no
``datasets`` / ``huggingface-hub`` dependency.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import urllib.request
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..corpus import materialize_corpus
from ..serialization import dataset_registry
from ._split import _DEFAULT_SMALL_TEST_SIZE, stratified_split, validate_split
from .base_dataset import EvalTask, GoldAnswer

# WHY: the date-tagged GitHub release. To bump: download the new gz,
# run _flatten_needles + _row_to_task on it, verify _extract_body produces
# valid Python for 3 sample needles, then update this constant.
_PINNED_RELEASE_VERSION = "2024-06-23"
_RELEASE_URL = (
    "https://github.com/evalplus/repoqa_release/releases/download/"
    "{version}/repoqa-{version}.json.gz"
)


@dataset_registry.register("repoqa")
@dataclass
class RepoQADataset:
    """RepoQA-SNF (Apache-2.0, EvalPlus, arXiv 2406.06025)."""

    name: str = "repoqa"
    revision: str = _PINNED_RELEASE_VERSION
    fixture_path: Path | None = None
    # WHY: stratified-by-repo dev/test split so each slice keeps the
    # per-repo proportions of the full corpus; seeded so the partition is
    # reproducible across runs. Default ``"all"`` is the whole corpus (no
    # partition) — strict backward-compat for existing usage and the CI
    # fixture, which rely on getting every needle. The split is stratified
    # by ``repo`` (RepoQA's analogue of DS-1000's library) via the shared
    # ``_split.stratified_split`` helper, so the two datasets' partition
    # logic stays identical.
    split: str = "all"
    dev_fraction: float = 0.2
    split_seed: int = 0
    # Target size for BOTH small splits: ``small_test`` (fixed-size
    # stratified subsample of the held-out ``test`` tail) and ``small_dev``
    # (its mirror on the ``dev`` head — the burn-free iteration slice for
    # the expensive dense / hybrid / LLM-tree sweeps; see
    # benchmarks/README.md §"Sweep protocol"). Default from the shared
    # split helper (single source of truth).
    small_test_size: int = _DEFAULT_SMALL_TEST_SIZE
    cache_dir: Path = field(
        default_factory=lambda: Path("~/.cache/pydocs-mcp/repoqa").expanduser(),
    )
    language: str = "python"
    _rows_cache: list[dict[str, Any]] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        # A bad ``split`` is a caller bug — fail loud at construction rather
        # than silently yielding the wrong slice deep in the async loop.
        validate_split(self.split)

    async def tasks(self) -> AsyncIterator[EvalTask]:
        if self._rows_cache is None:
            if self.fixture_path is not None:
                self._rows_cache = self._load_from_fixture()
            else:
                # WHY: urllib.urlopen + gzip.decompress are sync + CPU-bound.
                # The runner loop is async (CLAUDE.md §"Async Patterns") — offload
                # to a worker thread so the 12MB download doesn't block the event loop.
                self._rows_cache = await asyncio.to_thread(self._load_from_release)
        # Partition the flattened needle rows by ``split``. Stratify by
        # ``repo`` so each slice keeps the corpus's per-repo proportions;
        # the shared helper owns the determinism contract (see
        # ``_split.stratified_split``). Default ``"all"`` returns the rows
        # unchanged — a strict no-op preserving the pre-split behavior. The
        # sort key (``<path>::<name>``) is a deterministic, position-
        # independent per-needle identity so the seeded shuffle is stable
        # across runs and load paths.
        rows = stratified_split(
            self._rows_cache,
            split=self.split,
            dev_fraction=self.dev_fraction,
            seed=self.split_seed,
            small_test_size=self.small_test_size,
            stratum_of=lambda r: r["repo"],
            sort_key=lambda r: f"{r['needle']['path']}::{r['needle']['name']}",
        )
        for row in rows:
            yield _row_to_task(row)

    def _load_from_fixture(self) -> list[dict[str, Any]]:
        assert self.fixture_path is not None
        with self.fixture_path.open() as fh:
            data = json.load(fh)
        return _flatten_needles(data.get(self.language, []))

    def _load_from_release(self) -> list[dict[str, Any]]:
        target = self.cache_dir / f"repoqa-{self.revision}.json"
        if not target.exists():
            self._download_release_atomic(target)
        data = json.loads(target.read_text())
        return _flatten_needles(data.get(self.language, []))

    def _download_release_atomic(self, target: Path) -> None:
        # WHY: a partial / corrupt download must NOT masquerade as a good
        # cache file. Decompress in memory → validate JSON parses → write
        # .tmp → os.replace into place. Because validation runs BEFORE the
        # .tmp write, a corrupt payload never lands on disk at all.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        url = _RELEASE_URL.format(version=self.revision)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            payload = gzip.decompress(resp.read())
        # Validate JSON shape before publishing. If this raises, the
        # decode error propagates and `target` is never created.
        json.loads(payload.decode())
        tmp.write_bytes(payload)
        tmp.replace(target)


def _flatten_needles(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per needle (NOT per repo). Each row carries the repo content
    once; corpus_source closures share it via the default-arg trick."""
    rows: list[dict[str, Any]] = []
    for repo_entry in repos:
        for needle in repo_entry["needles"]:
            rows.append(
                {
                    "repo": repo_entry["repo"],
                    "commit_sha": repo_entry["commit_sha"],
                    "topic": repo_entry["topic"],
                    "content": repo_entry["content"],
                    "needle": needle,
                }
            )
    return rows


def _row_to_task(row: dict[str, Any]) -> EvalTask:
    needle = row["needle"]
    content: Mapping[str, str] = dict(row["content"])
    needle_body = _extract_body(
        content[needle["path"]],
        needle["start_line"],
        needle["end_line"],
    )
    repo_id = f"{row['repo']}@{row['commit_sha'][:7]}"
    return EvalTask(
        task_id=f"{repo_id}/{needle['path']}::{needle['name']}",
        query=needle["description"],
        gold=GoldAnswer(ast_body=needle_body),
        corpus_source=lambda files=content: materialize_corpus(files),
        metadata={
            "repo": row["repo"],
            "commit": row["commit_sha"],
            "topic": row["topic"],
            "language": "python",
            "needle_name": needle["name"],
            "needle_path": needle["path"],
        },
    )


def _extract_body(source: str, start_line: int, end_line: int) -> str:
    """1-indexed inclusive line slice. ``splitlines()`` normalizes mixed
    line endings (\\n, \\r\\n, \\r) so the body is reconstructed with a
    canonical \\n separator — extraction is endpoint-stable regardless of
    the source's line-ending convention."""
    lines = source.splitlines()
    return "\n".join(lines[start_line - 1 : end_line])
