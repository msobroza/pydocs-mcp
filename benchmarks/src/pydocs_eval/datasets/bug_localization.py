"""The ``bug_loc`` framing — file-level bug localization over two corpora.

Task (arXiv:2607.11046, "Retrieval-Oriented Code Representations in Agentic Bug
Localization"): given a bug or issue report plus a repository snapshot, name the
file(s) that must change. Gold is a set of repo-relative paths and nothing else
— no symbol, no body — which is why both loaders leave ``GoldAnswer.ast_body``
at ``None``: ``metrics/_relevance.py`` dispatches on it, and setting it would
score these corpora as if their gold were a function's source.

Two registered datasets mint under the one product-enumerated task name, the
same one-name/many-corpora shape ``repo_qa`` established:

- ``swe-bench-verified-loc`` — SWE-bench Verified's 500 Python instances. Gold
  is derived from the ``patch`` diff (the paper's convention: patch-touched
  files, minus tests).
- ``lca-bug-loc`` — Long Code Arena's bug-localization ``test`` split, PYTHON
  slice only (50 instances). Gold is the record's own ``changed_files`` list,
  minus tests.

**Java / Kotlin are deliberately OUT of v1.** Long Code Arena also publishes a
50-instance Java slice and a 50-instance Kotlin slice (the paper's 150 is the
three ``test`` splits together). ``.java`` and ``.kt`` are absent from the
product's ``extraction/config.ALLOWED_EXTENSIONS`` ceiling, so those snapshots
cannot be indexed at all; admitting them requires registering chunkers and
amending the allowlist, which is an owner-gated ADR 0021 T1 product event, not
a dataset edit. Adding the two configs here once that lands is a two-line
change (a second pin + a second registration).

**Corpus materialization: real, per instance, never faked.** Both consumers
(``sweep`` and the agent-track orchestrator) call ``task.corpus_source()``
unconditionally and hand the result straight to an indexer, and bug
localization uses a DIFFERENT repository per instance — so the one shipped
metadata-only precedent (DS-1000's ``/dev/null`` plus a whole-sweep
``--corpus-dir`` override) cannot apply. Each task therefore checks its pinned
commit out through the shared :class:`RepoCache` and materializes a
history-less copy, exactly as ``swe-qa-pro`` and ``crosscommitvuln`` do. Both
corpora are repos on GitHub pinned by a full 40-hex SHA, so one acquisition
path serves both; Long Code Arena additionally mirrors its repos as ZIP
archives inside its HF dataset repo, which is the natural airgap source if the
prewarm path is ever wanted here (deferred — the ``RepoCache`` bundle seam
already covers offline clones).

**Costs an operator must plan for**, stated rather than discovered mid-run:
``RepoCache.checkout`` keeps one worktree per (repo, sha) forever, and
``swe-bench-verified-loc`` is the first ~500-pin consumer (12 base clones, up
to 500 worktrees of repos the size of django / sympy / matplotlib). Use
``--max-tasks`` on a small disk.

Licensing: Long Code Arena declares ``apache-2.0`` on its dataset card.
SWE-bench Verified declares NO license on its card; the SWE-bench project code
is MIT (arXiv:2310.06770) — that is a statement about the code, not the data,
so it is reported as-is rather than asserted of the dataset. Corpora are
redistributed-by-download in both cases: cloned into the cache, never committed.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..registries import dataset_registry
from ._bug_loc_gold import (
    BugLocGoldError,
    non_test_paths,
    paths_from_changed_files,
    paths_from_unified_diff,
)
from ._parquet import ParquetPin, download_parquet, read_parquet_rows
from ._repo_cache import RepoCache, RepoCacheLike, read_checkout_files
from .base_dataset import EvalTask, GoldAnswer
from .corpus import materialize_corpus
from .task_ids import mint_framed_task_id

log = logging.getLogger(__name__)

# The product-enumerated framing both datasets below mint under
# (``skill_artifact.TASK_NAMES``).
BUG_LOC_TASK_NAME = "bug_loc"

# Corpus scope. WHY wider than the ``.py``-only default every other repo-backed
# loader uses: gold here is "every non-test file the fix patch touched", and
# real fix patches routinely touch ``.rst`` docs, ``setup.cfg``, ``.toml`` and
# ``.json`` fixtures. A gold file missing from the materialized corpus can never
# be retrieved, so scoring it would be a guaranteed miss that measures the
# corpus builder rather than the retriever. This mirrors the product's DEFAULT
# indexable set (``extraction/config.DiscoveryScopeConfig.include_extensions``),
# which is what a real deployment would index; the mirroring is pinned by
# ``tests/datasets/test_bug_localization.py`` rather than imported, because this
# package must stay importable without the product installed.
#
# Measured coverage on the pinned revisions: this set holds 623/623 gold paths
# on swe-bench-verified-loc (622 ``.py`` + 1 ``.cfg``) and 114/114 on
# lca-bug-loc — so no gold file is currently outside the materialized corpus.
# If a pin bump ever admits one (``.pyx`` / ``.c`` / ``.h`` are the plausible
# extensions, and astropy / scikit-learn / matplotlib do ship them), that row
# becomes a guaranteed miss measuring the corpus builder rather than the
# retriever, and this set is where to widen.
CORPUS_GLOBS: tuple[str, ...] = (
    "*.py",
    "*.md",
    "*.ipynb",
    "*.toml",
    "*.yaml",
    "*.yml",
    "*.cfg",
    "*.ini",
    "*.rst",
    "*.txt",
    "*.json",
)

# WHY pinned HF revisions: the parquet behind a content-addressed commit is the
# reproducible corpus. To bump either, re-read the schema, re-verify the row
# count, then update BOTH the revision and ``expected_rows`` — never point at
# ``main``. ``expected_rows`` is enforced by ``read_parquet_rows``, so a pin
# edit that silently changes the slice fails at load rather than drifting.
SWE_BENCH_VERIFIED_INSTANCES = 500
SWE_BENCH_VERIFIED_PIN = ParquetPin(
    dataset_id="princeton-nlp/SWE-bench_Verified",
    revision="c104f840cc67f8b6eec6f759ebc8b2693d585d4a",
    files=("data/test-00000-of-00001.parquet",),
    expected_rows=SWE_BENCH_VERIFIED_INSTANCES,
)

# The Python slice is a physically SEPARATE parquet (the HF config name is the
# directory), so the language filter is the file path — there is no row-level
# filter to forget. Do NOT filter on ``repo_language`` instead: it reports the
# repository's GitHub-dominant language and takes 8 distinct values across this
# dataset's 3 configs (measured on the pinned py slice: Python 44, Go 3,
# JavaScript 2, Shell 1), so it does not discriminate the slice. The three
# configs' shards are IDENTICALLY NAMED under ``py/``, ``java/`` and ``kt/``,
# which is why ``expected_rows`` matters here even though all three hold 50 —
# see the loader-side language assertion in the tests.
LCA_BUG_LOC_INSTANCES = 50
LCA_BUG_LOC_PIN = ParquetPin(
    dataset_id="JetBrains-Research/lca-bug-localization",
    revision="f2bb26a1c3320ccc1d7b02d2630077ff327a80f6",
    files=("py/test-00000-of-00001.parquet",),
    expected_rows=LCA_BUG_LOC_INSTANCES,
)

# Exactly the columns each loader consumes. Naming them is not only a memory
# win: SWE-bench Verified's ``test_patch`` names the gold file's test module and
# ``hints_text`` carries PR-review commentary, so both are direct answer leaks —
# never reading them is a stronger guarantee than dropping them later.
_SWE_BENCH_COLUMNS = [
    "instance_id",
    "repo",
    "base_commit",
    "patch",
    "problem_statement",
    "version",
    "difficulty",
]
_LCA_COLUMNS = [
    "text_id",
    "repo_owner",
    "repo_name",
    "issue_title",
    "issue_body",
    "base_sha",
    "changed_files",
    "repo_language",
    "repo_license",
]

_GITHUB_URL = "https://github.com/{owner}/{name}.git"


@dataset_registry.register("swe-bench-verified-loc")
@dataclass
class SweBenchVerifiedLocDataset:
    """SWE-bench Verified as file-level bug localization (no license on card)."""

    name: str = "swe-bench-verified-loc"
    revision: str = SWE_BENCH_VERIFIED_PIN.revision
    fixture_path: Path | None = None
    # WHY: injected so tests pass a no-git/no-network fake; production wiring
    # gets the real ``RepoCache`` by default (it clones each repo once).
    repo_cache: RepoCacheLike = field(default_factory=RepoCache)
    cache_dir: Path | None = None
    _rows_cache: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)

    async def tasks(self) -> AsyncIterator[EvalTask]:
        rows = await self._rows()
        yielded, dropped = 0, 0
        for row in rows:
            try:
                task = self._row_to_task(row)
            except BugLocGoldError as exc:
                dropped += 1
                log.info("swe-bench-verified-loc: dropping %r — %s", row.get("instance_id"), exc)
                continue
            yielded += 1
            yield task
        _log_yield(self.name, yielded=yielded, dropped=dropped)

    async def _rows(self) -> list[dict[str, Any]]:
        if self._rows_cache is None:
            self._rows_cache = await _load_rows(
                fixture_path=self.fixture_path,
                pin=SWE_BENCH_VERIFIED_PIN,
                columns=_SWE_BENCH_COLUMNS,
                cache_dir=self.cache_dir,
            )
        return self._rows_cache

    def _row_to_task(self, row: dict[str, Any]) -> EvalTask:
        instance_id = str(row["instance_id"])
        repo = str(row["repo"])
        base_commit = str(row["base_commit"])
        file_set = _require_gold(
            non_test_paths(paths_from_unified_diff(str(row.get("patch") or ""))),
            record_id=instance_id,
            source="the patch's non-test files",
        )
        owner, _, repo_name = repo.partition("/")
        return EvalTask(
            task_id=_framed_id(self.name, instance_id),
            record_id=instance_id,
            # The issue text alone. ``hints_text`` (PR-review commentary) is not
            # part of the paper's input and is never read — see _SWE_BENCH_COLUMNS.
            query=str(row.get("problem_statement") or ""),
            gold=GoldAnswer(file_set=file_set),
            corpus_source=_corpus_source(
                self.repo_cache, _GITHUB_URL.format(owner=owner, name=repo_name), base_commit
            ),
            metadata={
                "repo": repo,
                "version": str(row.get("version") or ""),
                # 4 classes ("15 min - 1 hour" etc.) — a ready-made per-category
                # report breakout, the way swe-qa-pro's qa_type is.
                "difficulty": str(row.get("difficulty") or ""),
                "gold_file_count": str(len(file_set)),
            },
        )


@dataset_registry.register("lca-bug-loc")
@dataclass
class LcaBugLocDataset:
    """Long Code Arena bug localization, Python ``test`` slice (Apache-2.0)."""

    name: str = "lca-bug-loc"
    revision: str = LCA_BUG_LOC_PIN.revision
    fixture_path: Path | None = None
    repo_cache: RepoCacheLike = field(default_factory=RepoCache)
    cache_dir: Path | None = None
    _rows_cache: list[dict[str, Any]] | None = field(default=None, init=False, repr=False)

    async def tasks(self) -> AsyncIterator[EvalTask]:
        rows = await self._rows()
        yielded, dropped = 0, 0
        for row in rows:
            try:
                task = self._row_to_task(row)
            except BugLocGoldError as exc:
                dropped += 1
                log.info("lca-bug-loc: dropping %r — %s", row.get("text_id"), exc)
                continue
            yielded += 1
            yield task
        _log_yield(self.name, yielded=yielded, dropped=dropped)

    async def _rows(self) -> list[dict[str, Any]]:
        if self._rows_cache is None:
            self._rows_cache = await _load_rows(
                fixture_path=self.fixture_path,
                pin=LCA_BUG_LOC_PIN,
                columns=_LCA_COLUMNS,
                cache_dir=self.cache_dir,
            )
        return self._rows_cache

    def _row_to_task(self, row: dict[str, Any]) -> EvalTask:
        # ``text_id`` ("thealgorithms/python/295/289") carries provenance and is
        # what the record-keyed split and paired clustering key on. It contains
        # ``/`` — legal, because ``mint_framed_task_id`` puts record_id LAST and
        # the parse is vocabulary-anchored on the middle segment.
        text_id = str(row["text_id"])
        owner, repo_name = str(row["repo_owner"]), str(row["repo_name"])
        file_set = _require_gold(
            non_test_paths(paths_from_changed_files(row.get("changed_files"))),
            record_id=text_id,
            source="changed_files minus tests",
        )
        return EvalTask(
            task_id=_framed_id(self.name, text_id),
            record_id=text_id,
            # Title + body. A framing choice, stated because it is one: the
            # title carries real signal (it is usually the symptom in one line)
            # and the upstream baselines concatenate both halves too.
            query=_issue_text(row),
            gold=GoldAnswer(file_set=file_set),
            corpus_source=_corpus_source(
                self.repo_cache,
                _GITHUB_URL.format(owner=owner, name=repo_name),
                str(row["base_sha"]),
            ),
            metadata={
                "repo": f"{owner}/{repo_name}",
                "repo_language": str(row.get("repo_language") or ""),
                "repo_license": str(row.get("repo_license") or ""),
                "gold_file_count": str(len(file_set)),
            },
        )


def _issue_text(row: dict[str, Any]) -> str:
    """``"<issue_title>\\n\\n<issue_body>"``, tolerating either half being empty."""
    halves = [str(row.get(key) or "").strip() for key in ("issue_title", "issue_body")]
    return "\n\n".join(half for half in halves if half)


def _require_gold(file_set: tuple[str, ...], *, record_id: str, source: str) -> tuple[str, ...]:
    """``file_set`` unchanged, or a :class:`BugLocGoldError` when it is empty.

    An empty gold is never yielded: every deterministic gold check would pass
    vacuously on it and every retrieval metric would normalize by zero, so the
    row is dropped loudly (and counted) rather than scored.
    """
    if not file_set:
        raise BugLocGoldError(
            f"record {record_id!r} has an empty gold file set after {source}; expected at "
            "least one non-test repo-relative path (a vacuous gold scores 1.0 on every "
            "deterministic check)"
        )
    return file_set


def _corpus_source(cache: RepoCacheLike, url: str, sha: str) -> Callable[[], Path]:
    """A zero-arg corpus factory for one pinned (repo, sha).

    Bound eagerly so each task captures ITS pin, and lazy inside so a task that
    is never scored costs no clone. History-less by construction: the files are
    read out of the checkout and re-written into a fresh runner-owned dir, so no
    ``.git`` (and therefore no commit signal) reaches the model.
    """
    return lambda: materialize_corpus(
        read_checkout_files(cache.checkout(url, sha), globs=CORPUS_GLOBS)
    )


async def _load_rows(
    *,
    fixture_path: Path | None,
    pin: ParquetPin,
    columns: list[str],
    cache_dir: Path | None,
) -> list[dict[str, Any]]:
    """Fixture JSONL when injected, else the pinned parquet release.

    The fixture branch is what keeps the offline test suite free of both the
    network and the parquet wheels; the release branch runs in a worker thread
    because the download and the parquet read are blocking.
    """
    if fixture_path is not None:
        return _read_jsonl(fixture_path)
    return await asyncio.to_thread(_read_release, pin, columns, cache_dir)


def _read_release(
    pin: ParquetPin, columns: list[str], cache_dir: Path | None
) -> list[dict[str, Any]]:
    return read_parquet_rows(download_parquet(pin, cache_dir), columns, pin)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Parse a JSONL fixture into row dicts, skipping blank lines."""
    with path.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _log_yield(dataset: str, *, yielded: int, dropped: int) -> None:
    """No-silent-caps: the operator sees both the kept and the dropped counts.

    A TOTAL wipeout (every record dropped) logs at ERROR rather than INFO: at
    the default WARNING level an INFO line is invisible, so an upstream schema
    change would otherwise present as a sweep that quietly scored an empty
    corpus. Partial drops stay INFO — they are the expected, counted case.
    """
    if dropped and not yielded:
        log.error(
            "%s: yielded NO tasks — all %d record(s) had underivable gold; the release "
            "schema this loader reads has probably changed",
            dataset,
            dropped,
        )
        return
    log.info(
        "%s: yielded %d task(s), dropped %d record(s) with underivable gold",
        dataset,
        yielded,
        dropped,
    )


def _framed_id(dataset: str, record_id: str) -> str:
    """The three-part id for one bug_loc row — one call site per dataset."""
    return mint_framed_task_id(dataset=dataset, task_name=BUG_LOC_TASK_NAME, record_id=record_id)


__all__ = [
    "BUG_LOC_TASK_NAME",
    "CORPUS_GLOBS",
    "LCA_BUG_LOC_INSTANCES",
    "LCA_BUG_LOC_PIN",
    "SWE_BENCH_VERIFIED_INSTANCES",
    "SWE_BENCH_VERIFIED_PIN",
    "LcaBugLocDataset",
    "SweBenchVerifiedLocDataset",
]
