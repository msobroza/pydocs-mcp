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
    cache_dir: Path = field(
        default_factory=lambda: Path("~/.cache/pydocs-mcp/repoqa").expanduser(),
    )
    language: str = "python"
    _rows_cache: list[dict[str, Any]] | None = field(
        default=None, init=False, repr=False,
    )

    async def tasks(self) -> AsyncIterator[EvalTask]:
        if self._rows_cache is None:
            if self.fixture_path is not None:
                self._rows_cache = self._load_from_fixture()
            else:
                # WHY: urllib.urlopen + gzip.decompress are sync + CPU-bound.
                # The runner loop is async (CLAUDE.md §"Async Patterns") — offload
                # to a worker thread so the 12MB download doesn't block the event loop.
                self._rows_cache = await asyncio.to_thread(self._load_from_release)
        for row in self._rows_cache:
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
        # cache file. Pattern: write to .tmp, validate JSON parses, then
        # os.replace into place. If JSON validation fails we propagate the
        # decode error and the .tmp file gets garbage-collected by the OS.
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
            rows.append({
                "repo": repo_entry["repo"],
                "commit_sha": repo_entry["commit_sha"],
                "topic": repo_entry["topic"],
                "content": repo_entry["content"],
                "needle": needle,
            })
    return rows


def _row_to_task(row: dict[str, Any]) -> EvalTask:
    needle = row["needle"]
    content: Mapping[str, str] = dict(row["content"])
    needle_body = _extract_body(
        content[needle["path"]], needle["start_line"], needle["end_line"],
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
