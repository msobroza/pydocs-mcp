"""SWE-QA dataset loader (spec §D14, secondary track).

``swe-qa/SWE-QA-Benchmark`` (Apache-2.0): 720 QA over 15 Python repos. The HF
release columns are ``question`` + ``answer`` ONLY — the repo is inferred from
the split name, and there are NO commit pins in the data. Pins live in the
companion GitHub repo (see ``_REPO_PINS``). Citations are noisier than
SWE-QA-Pro's (~8% bare filenames needing unique-basename resolution), so gold is
FILE-LEVEL pseudo-qrels (spec §D14): answer → resolved repo paths →
``GoldAnswer.file_set``; citation-free rows drop WITH a logged count.
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
from ._repo_cache import RepoCache, RepoCacheLike, read_checkout_files
from .base_dataset import EvalTask, GoldAnswer

log = logging.getLogger(__name__)

# WHY: pinned HF revision of the ``question``/``answer`` release.
_PINNED_REVISION = "07e206aa29fdad0cf3f1d532ff077f9705387348"

# The whole-corpus split (all 15 per-repo splits concatenated).
_DEFAULT_SPLIT = "default"

# Split name → (github clone url, short commit SHA).
#
# WHY these live here and not in the data: the SWE-QA HF release carries NO
# commit column. Pins were transcribed from
# ``github.com/peng-weihan/SWE-QA-Bench`` ``repo_commit.txt`` (fetched
# 2026-07-06). CAVEAT: it is UNVERIFIED that the 720-row HF release was built
# against exactly these commits, so we keep gold labels FILE-LEVEL only (safe
# under line drift) — never line-level (that would be false precision).
_REPO_PINS: dict[str, tuple[str, str]] = {
    "astropy": ("https://github.com/astropy/astropy.git", "0a041d3"),
    "django": ("https://github.com/django/django.git", "14fc2e9"),
    "flask": ("https://github.com/pallets/flask.git", "85c5d93"),
    "matplotlib": ("https://github.com/matplotlib/matplotlib.git", "a5e1f60"),
    "pylint": ("https://github.com/pylint-dev/pylint.git", "44740e5"),
    "pytest": ("https://github.com/pytest-dev/pytest.git", "5989efe"),
    "requests": ("https://github.com/psf/requests.git", "46e939b"),
    "scikit_learn": ("https://github.com/scikit-learn/scikit-learn.git", "adb1ae7"),
    "sphinx": ("https://github.com/sphinx-doc/sphinx.git", "6c9e320"),
    "sqlfluff": ("https://github.com/sqlfluff/sqlfluff.git", "db9801b"),
    "sympy": ("https://github.com/sympy/sympy.git", "3c817ed"),
    "xarray": ("https://github.com/pydata/xarray.git", "40119bf"),
    "conan": ("https://github.com/conan-io/conan.git", "52f43d9"),
    "reflex": ("https://github.com/reflex-dev/reflex.git", "fe0f946"),
    "streamlink": ("https://github.com/streamlink/streamlink.git", "ab1f365"),
}

# HF stores each per-repo split file with a hyphenated name; the split key is
# underscored (``scikit_learn`` split ↔ ``scikit-learn.jsonl``).
_RELEASE_URL = (
    "https://huggingface.co/datasets/swe-qa/SWE-QA-Benchmark/resolve/{revision}/data/{repo}.jsonl"
)


@dataset_registry.register("swe-qa")
@dataclass
class SweQaDataset:
    """SWE-QA (Apache-2.0). Split-inferred repo + companion-repo commit pins."""

    name: str = "swe-qa"
    revision: str = _PINNED_REVISION
    # ``default`` iterates all 15 repos; a repo name selects one per-repo split.
    split: str = _DEFAULT_SPLIT
    fixture_path: Path | None = None
    repo_cache: RepoCacheLike = field(default_factory=RepoCache)
    cache_dir: Path = field(
        default_factory=lambda: Path("~/.cache/pydocs-mcp/swe-qa").expanduser(),
    )
    _rows_cache: list[dict[str, Any]] | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        # A bad split is a caller bug — fail loud at construction rather than
        # silently yielding nothing deep in the async loop. The fixture path is
        # exempt (tests drive one repo through an arbitrary split label).
        if (
            self.fixture_path is None
            and self.split != _DEFAULT_SPLIT
            and self.split not in _REPO_PINS
        ):
            raise ValueError(
                f"unknown split {self.split!r}; expected {_DEFAULT_SPLIT!r} or one of {sorted(_REPO_PINS)}"
            )

    async def tasks(self) -> AsyncIterator[EvalTask]:
        if self._rows_cache is None:
            if self.fixture_path is not None:
                self._rows_cache = self._load_from_fixture()
            else:
                self._rows_cache = await asyncio.to_thread(self._load_from_release)
        excluded = 0
        unpinned: dict[str, int] = {}
        for i, row in enumerate(self._rows_cache):
            repo = row["repo"]
            # A repo absent from the pins is a data error, not a citation-free
            # row: building a task would hand RepoCache.checkout a guessed URL
            # + EMPTY sha and die inside git with a confusing error. Skip it and
            # surface the offending repo in its own log line (§D14 no-silent-caps).
            if repo not in _REPO_PINS:
                unpinned[repo] = unpinned.get(repo, 0) + 1
                continue
            task = self._row_to_task(row, i)
            if task is None:
                excluded += 1
                continue
            yield task
        log.info("swe-qa: excluded %d citation-free row(s) of %d", excluded, len(self._rows_cache))
        if unpinned:
            log.info(
                "swe-qa: skipped %d row(s) for unpinned repo(s) %s (absent from _REPO_PINS)",
                sum(unpinned.values()),
                sorted(unpinned),
            )

    def _load_from_fixture(self) -> list[dict[str, Any]]:
        # Fixture is a single per-repo jsonl; tag every row with the requested
        # split so ``_row_to_task`` resolves one repo (mirrors the release path).
        assert self.fixture_path is not None
        repo = self.split if self.split != _DEFAULT_SPLIT else "matplotlib"
        return [{**row, "repo": repo} for row in _read_jsonl(self.fixture_path)]

    def _load_from_release(self) -> list[dict[str, Any]]:
        repos = list(_REPO_PINS) if self.split == _DEFAULT_SPLIT else [self.split]
        rows: list[dict[str, Any]] = []
        for repo in repos:
            for row in self._load_repo_split(repo):
                rows.append({**row, "repo": repo})
        return rows

    def _load_repo_split(self, repo: str) -> list[dict[str, Any]]:
        # HF file names are hyphenated; the pin key is underscored.
        filename = repo.replace("_", "-")
        target = self.cache_dir / f"swe-qa-{self.revision}-{filename}.jsonl"
        if not target.exists():
            self._download_split_atomic(filename, target)
        return _read_jsonl(target)

    def _download_split_atomic(self, filename: str, target: Path) -> None:
        # Atomic write: fetch → validate each line parses → .tmp → os.replace.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        url = _RELEASE_URL.format(revision=self.revision, repo=filename)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            payload = resp.read()
        for line in payload.decode().splitlines():
            if line.strip():
                json.loads(line)
        tmp.write_bytes(payload)
        tmp.replace(target)

    def _row_to_task(self, row: dict[str, Any], index: int) -> EvalTask | None:
        # PRECONDITION: caller (``tasks``) has already filtered unpinned repos,
        # so ``repo`` is guaranteed present in ``_REPO_PINS`` — no guessed-URL /
        # empty-sha fallback that would die inside git.
        repo = row["repo"]
        url, sha = _REPO_PINS[repo]
        citations = extract_path_citations(row.get("answer", ""))
        tree = self.repo_cache.file_tree(url, sha)
        resolved, dropped = resolve_bare_filenames(citations, tree)
        if dropped:
            log.info("swe-qa: dropped ambiguous citation(s) %s in %s", dropped, repo)
        file_set = _distinct_paths(resolved)
        if not file_set:
            return None
        return EvalTask(
            task_id=f"swe_qa/{repo}/{index}",
            query=row.get("question", ""),
            gold=GoldAnswer(file_set=file_set),
            corpus_source=lambda u=url, s=sha: materialize_corpus(
                read_checkout_files(self.repo_cache.checkout(u, s))
            ),
            metadata={"repo": repo},
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
