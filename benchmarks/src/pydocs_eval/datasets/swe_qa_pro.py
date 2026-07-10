"""SWE-QA-Pro dataset loader (spec §D14, primary track).

``TIGER-Lab/SWE-QA-Pro-Bench`` (MIT): 260 QA over 26 Python repos, one full
40-hex ``commit_id`` per repo, near-regular ``(path.py: line N-M)`` answer
citations, and a ``qa_type`` {What/Where/How/Why} probe. Gold labels are
FILE-LEVEL pseudo-qrels (spec §D14): answer citations → resolved repo paths →
``GoldAnswer.file_set``. Rows whose answer cites no resolvable file are dropped
WITH a logged count (no-silent-caps rule).

Stdlib-only download (single ``data/test.jsonl`` behind the pinned HF revision)
so the fixture path never pulls the heavy ``datasets`` wheel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..corpus import materialize_corpus
from ..serialization import dataset_registry
from ._citations import extract_path_citations, resolve_bare_filenames
from ._download import check_content_length
from ._repo_cache import RepoCache, RepoCacheLike, read_checkout_files
from .base_dataset import EvalTask, GoldAnswer

log = logging.getLogger(__name__)

# WHY: pinned HF revision — the single ``data/test.jsonl`` behind this commit is
# the reproducible corpus. To bump: fetch the new jsonl, re-verify the citation
# hit-rate + qa_type distribution, then update this constant.
_PINNED_REVISION = "596892dac60b6f500f01a7dc2becb9f66593b7b7"
_RELEASE_URL = (
    "https://huggingface.co/datasets/TIGER-Lab/SWE-QA-Pro-Bench/resolve/{revision}/data/test.jsonl"
)


@dataset_registry.register("swe-qa-pro")
@dataclass
class SweQaProDataset:
    """SWE-QA-Pro (MIT, TIGER-Lab). File-level pseudo-qrels + qa_type probe."""

    name: str = "swe-qa-pro"
    revision: str = _PINNED_REVISION
    fixture_path: Path | None = None
    # WHY: injected so tests pass a no-git/no-network fake; production wiring
    # gets the real ``RepoCache`` by default (the cache clones each pin once).
    repo_cache: RepoCacheLike = field(default_factory=RepoCache)
    cache_dir: Path = field(
        default_factory=lambda: Path("~/.cache/pydocs-mcp/swe-qa-pro").expanduser(),
    )
    _rows_cache: list[dict[str, Any]] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    async def tasks(self) -> AsyncIterator[EvalTask]:
        if self._rows_cache is None:
            if self.fixture_path is not None:
                self._rows_cache = self._load_from_fixture()
            else:
                # WHY: urllib + JSON parse are sync + do network I/O; the runner
                # loop is async (CLAUDE.md §"Async Patterns") — offload so the
                # download doesn't block the event loop.
                self._rows_cache = await asyncio.to_thread(self._load_from_release)
        excluded = 0
        for i, row in enumerate(self._rows_cache):
            task = self._row_to_task(row, i)
            if task is None:
                excluded += 1
                continue
            yield task
        # No-silent-caps: an operator must see how many rows were dropped for
        # lacking a resolvable citation (the file-level qrel is undefined for them).
        log.info(
            "swe-qa-pro: excluded %d citation-free row(s) of %d", excluded, len(self._rows_cache)
        )

    def _load_from_fixture(self) -> list[dict[str, Any]]:
        assert self.fixture_path is not None
        return _read_jsonl(self.fixture_path)

    def _load_from_release(self) -> list[dict[str, Any]]:
        target = self.cache_dir / f"swe-qa-pro-{self.revision}.jsonl"
        if not target.exists():
            self._download_release_atomic(target)
        return _read_jsonl(target)

    def _download_release_atomic(self, target: Path) -> None:
        # WHY: a partial download must not masquerade as a good cache file —
        # fetch → check Content-Length → validate each line parses →
        # write .tmp → os.replace into place. Per-line JSON validation alone
        # cannot catch a body truncated exactly on a '\n' boundary (proxy
        # cut-off, dropped connection flushed mid-stream): every surviving
        # line still parses. The Content-Length comparison catches that case.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        url = _RELEASE_URL.format(revision=self.revision)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            payload = resp.read()
            check_content_length(resp, payload, url=url)
        for line in payload.decode().splitlines():
            if line.strip():
                json.loads(line)  # raises before the .tmp write if corrupt
        tmp.write_bytes(payload)
        tmp.replace(target)

    def _row_to_task(self, row: dict[str, Any], index: int) -> EvalTask | None:
        repo = row["repo"]
        commit_id = row["commit_id"]
        url = f"https://github.com/{repo}.git"
        citations = extract_path_citations(row.get("answer", ""))
        tree = self.repo_cache.file_tree(url, commit_id)
        resolved, dropped = resolve_bare_filenames(citations, tree)
        if dropped:
            log.info("swe-qa-pro: dropped ambiguous citation(s) %s in %s", dropped, repo)
        file_set = _distinct_paths(resolved)
        if not file_set:
            return None
        qa_type = row.get("qa_type", {})
        cluster = row.get("cluster", {})
        return EvalTask(
            task_id=f"swe_qa_pro/{repo}/{index}",
            query=row.get("question", ""),
            gold=GoldAnswer(file_set=file_set),
            # Default-arg closure captures this row's pin; the corpus is checked
            # out + copied lazily so a task that's never scored costs no clone.
            corpus_source=lambda u=url, c=commit_id: materialize_corpus(
                read_checkout_files(self.repo_cache.checkout(u, c))
            ),
            metadata={
                "repo": repo,
                # class_name is "How does it work" etc.; the leading token is the
                # What/Where/How/Why probe used for per-category reporting.
                "qa_type": qa_type.get("class_name", "").split(" ")[0],
                "sub_class": qa_type.get("sub_class_name", ""),
                "cluster": str(cluster.get("id", "")),
            },
        )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL file into a list of row dicts, skipping blank lines."""
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _distinct_paths(resolved: tuple[tuple[str, int, int], ...]) -> tuple[str, ...]:
    """Order-preserving distinct repo paths from resolved (path, start, end) triples."""
    seen: dict[str, None] = {}
    for path, _start, _end in resolved:
        seen.setdefault(path)
    return tuple(seen)
