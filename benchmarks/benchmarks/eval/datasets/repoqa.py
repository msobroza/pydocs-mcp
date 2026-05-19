"""RepoQA-SNF dataset loader (spec §4.3, §4.8).

Two code paths, chosen at construction time:

1. **Fixture path** (offline) — when ``fixture_path`` points at a local
   JSON file, ``tasks()`` reads it and yields ``EvalTask`` instances
   without touching the network. Tests and CI smoke runs use this.
2. **HuggingFace path** — when ``fixture_path`` is ``None``, ``tasks()``
   lazy-imports ``datasets``, calls ``load_dataset("evalplus/repoqa",
   revision=PINNED_REVISION, cache_dir=...)``, filters to
   ``language == "python"``, and yields one task per row.

The lazy-import semantics differ deliberately from the MLflow tracker:
MLflow has no fallback, so the import fires in ``__post_init__``. RepoQA
*does* have a fallback (``fixture_path``), so the import is deferred to
``_load_from_hf`` — construction stays offline-safe.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..corpus import materialize_corpus
from ..protocols import EvalTask, GoldAnswer
from ..serialization import dataset_registry

# WHY: pinned revision is filled in by Task 9 (CI + baseline) once the
# first real benchmark run captures the commit hash from HuggingFace.
# Tests bypass this via ``fixture_path`` so the placeholder never reaches
# the wire during development.
_PINNED_REVISION = "<TODO_PIN_AT_FIRST_RUN>"

# WHY: install command is duplicated verbatim in the error message so
# users can copy-paste it from any traceback without scrolling around
# for context. Single source of truth lives here.
_INSTALL_MSG = "uv pip install -e benchmarks[repoqa]"


def _require_datasets() -> Any:
    try:
        import datasets  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ImportError(
            f"RepoQADataset requires the optional [repoqa] extra. "
            f"Install with: {_INSTALL_MSG}"
        ) from exc
    return datasets


def _row_to_task(row: Mapping[str, Any]) -> EvalTask:
    """Translate one fixture / HF row into an ``EvalTask``.

    The closure over ``files`` (default-arg trick) freezes the mapping at
    construction time so subsequent iterator advances don't smuggle the
    next row's files into earlier tasks' corpus_source closures.
    """
    files: Mapping[str, str] = dict(row["files"])
    # Field names below are placeholders matched by the fixture JSON. The real
    # HuggingFace evalplus/repoqa schema may use different keys (e.g.
    # nl_description vs description, function_body vs needle_function_body) —
    # Task 9 pins _PINNED_REVISION and confirms / adjusts these column names
    # against the actual dataset rows.
    return EvalTask(
        task_id=str(row["task_id"]),
        query=str(row["description"]),
        gold=GoldAnswer(ast_body=str(row["needle_function_body"])),
        corpus_source=lambda files=files: materialize_corpus(files),
        metadata={
            "repo": str(row.get("repo", "")),
            "language": str(row.get("language", "python")),
        },
    )


@dataset_registry.register("repoqa")
@dataclass
class RepoQADataset:
    """Loader for RepoQA-SNF (Apache-2.0, EvalPlus, arXiv 2406.06025).

    Default behavior loads from HuggingFace; tests and air-gapped CI
    smoke runs override ``fixture_path`` to read a local JSON copy.
    """

    name: str = "repoqa"
    revision: str = _PINNED_REVISION
    fixture_path: Path | None = None
    cache_dir: Path = field(
        default_factory=lambda: Path("~/.cache/pydocs-mcp/repoqa").expanduser()
    )

    # WHY: declared as ``def`` returning ``AsyncIterator`` (Protocol shape)
    # but implemented as an ``async def`` generator. Python wraps the
    # call site automatically so callers do ``async for t in ds.tasks():``
    # without an extra await.
    async def tasks(self) -> AsyncIterator[EvalTask]:
        rows = (
            self._load_from_fixture()
            if self.fixture_path is not None
            else self._load_from_hf()
        )
        for row in rows:
            yield _row_to_task(row)

    def _load_from_fixture(self) -> list[Mapping[str, Any]]:
        # WHY: the fixture path is the offline / test-only escape hatch.
        # Reading sync inside the async generator is fine — it's a one-shot
        # JSON load before the yield loop, not per-task I/O.
        assert self.fixture_path is not None  # narrowed by caller
        with self.fixture_path.open() as fh:
            data = json.load(fh)
        return [row for row in data if row.get("language", "python") == "python"]

    def _load_from_hf(self) -> list[Mapping[str, Any]]:
        datasets = _require_datasets()
        # WHY: ``cache_dir`` is created lazily by ``load_dataset`` itself
        # — no need to ``mkdir`` here. The pinned ``revision`` makes the
        # eval reproducible across CI runs even if EvalPlus pushes new
        # data to ``main``.
        ds = datasets.load_dataset(
            "evalplus/repoqa",
            revision=self.revision,
            cache_dir=str(self.cache_dir),
        )
        # HF datasets ship a DatasetDict; the eval split is named ``test``
        # for RepoQA-SNF. Filter language=python per spec §4.8.
        split = ds["test"] if "test" in ds else next(iter(ds.values()))
        return [row for row in split if row.get("language") == "python"]


def _download() -> None:
    """Eager download — used by CI to pre-fill the HF cache.

    ``python -m benchmarks.benchmarks.eval.datasets.repoqa --download``
    triggers this. The CI workflow keys its cache on
    ``_PINNED_REVISION`` so the artifact is reused across PRs (spec §4.12,
    AC11).
    """
    dataset = RepoQADataset()
    rows = dataset._load_from_hf()
    # CI logs read this line to confirm the cache step succeeded.
    print(f"Downloaded {len(rows)} Python tasks to {dataset.cache_dir}")


if __name__ == "__main__":  # pragma: no cover -- CLI entry, not unit-tested
    import argparse

    parser = argparse.ArgumentParser(description="RepoQA dataset cache helper")
    parser.add_argument(
        "--download",
        action="store_true",
        help="Pre-fill the HuggingFace cache for the pinned revision.",
    )
    args = parser.parse_args()
    if args.download:
        _download()
