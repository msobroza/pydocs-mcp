"""SWE-QA / SWE-QA-Pro dataset adapter tests — hermetic via ``fixture_path``,
a fixture corpus dir, and a fake ``RepoCache`` injected as a constructor
field (no network, no real git checkouts)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_eval.datasets.base_dataset import Dataset
from pydocs_eval.serialization import dataset_registry
from pydocs_eval.datasets.swe_qa import SweQaDataset
from pydocs_eval.datasets.swe_qa_pro import SweQaProDataset

_FIXTURES = Path(__file__).parent / "fixtures"
_PRO_FIXTURE = _FIXTURES / "swe_qa_pro_mini.jsonl"
_SWEQA_FIXTURE = _FIXTURES / "swe_qa_mini.jsonl"
_CORPUS_DIR = _FIXTURES / "swe_qa_corpus"

# Every path the fixtures cite, as the fake repo's tracked-file listing.
_CORPUS_TREE = (
    "src/qibo/models/variational.py",
    "src/pkg/mod.py",
    "lib/matplotlib/backends/backend_pgf.py",
)


@dataclass
class _FakeRepoCache:
    """Stand-in for ``RepoCache`` — no git, no network. ``file_tree`` returns
    a fixed listing; ``checkout`` returns the shared fixture corpus dir."""

    tree: tuple[str, ...] = _CORPUS_TREE
    corpus_dir: Path = field(default=_CORPUS_DIR)

    def checkout(self, url: str, sha: str) -> Path:
        return self.corpus_dir

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return self.tree


async def test_pro_yields_tasks_with_file_set_and_metadata() -> None:
    ds = SweQaProDataset(fixture_path=_PRO_FIXTURE, repo_cache=_FakeRepoCache())
    tasks = [t async for t in ds.tasks()]
    assert len(tasks) == 4  # 5 fixture rows, 1 citation-free -> excluded
    t0 = tasks[0]
    assert t0.gold.file_set and t0.metadata["qa_type"] in {"What", "Where", "How", "Why"}
    assert t0.metadata["repo"] and t0.task_id.startswith("swe_qa_pro/")


async def test_pro_excluded_rows_are_counted(caplog) -> None:
    ds = SweQaProDataset(fixture_path=_PRO_FIXTURE, repo_cache=_FakeRepoCache())
    with caplog.at_level(logging.INFO):
        _ = [t async for t in ds.tasks()]
    # The exclusion count is logged (no-silent-caps rule).
    assert any(
        "1" in rec.getMessage() and "exclud" in rec.getMessage().lower() for rec in caplog.records
    )


async def test_pro_corpus_source_materializes_checkout() -> None:
    ds = SweQaProDataset(fixture_path=_PRO_FIXTURE, repo_cache=_FakeRepoCache())
    tasks = [t async for t in ds.tasks()]
    corpus_dir = tasks[0].corpus_source()
    assert (corpus_dir / "src/qibo/models/variational.py").exists()


async def test_swe_qa_infers_repo_from_split_and_resolves_bare_names() -> None:
    ds = SweQaDataset(
        fixture_path=_SWEQA_FIXTURE,
        split="matplotlib",
        repo_cache=_FakeRepoCache(tree=("lib/matplotlib/backends/backend_pgf.py",)),
    )
    tasks = [t async for t in ds.tasks()]
    assert tasks[0].gold.file_set == ("lib/matplotlib/backends/backend_pgf.py",)
    assert tasks[0].metadata["repo"] == "matplotlib"


async def test_swe_qa_unpinned_repo_row_is_skipped_and_logged(caplog) -> None:
    # A repo absent from _REPO_PINS is a data error: the row must be skipped
    # (not built with a wrong URL + empty sha that dies inside git) AND the
    # exclusion counted with a log line naming the unpinned repo. fixture_path
    # tags every row with the requested split, so an unpinned split label
    # drives the unpinned-repo branch without tripping __post_init__.
    ds = SweQaDataset(
        fixture_path=_SWEQA_FIXTURE,
        split="notarealrepo",
        repo_cache=_FakeRepoCache(),
    )
    with caplog.at_level(logging.INFO):
        tasks = [t async for t in ds.tasks()]
    assert tasks == []
    assert any(
        "notarealrepo" in rec.getMessage() and "unpinned" in rec.getMessage().lower()
        for rec in caplog.records
    )


async def test_both_datasets_satisfy_protocol() -> None:
    pro = SweQaProDataset(fixture_path=_PRO_FIXTURE, repo_cache=_FakeRepoCache())
    swe = SweQaDataset(fixture_path=_SWEQA_FIXTURE, split="matplotlib", repo_cache=_FakeRepoCache())
    assert isinstance(pro, Dataset)
    assert isinstance(swe, Dataset)


def test_both_registered() -> None:
    pro = dataset_registry.build(
        "swe-qa-pro", fixture_path=_PRO_FIXTURE, repo_cache=_FakeRepoCache()
    )
    swe = dataset_registry.build(
        "swe-qa",
        fixture_path=_SWEQA_FIXTURE,
        split="matplotlib",
        repo_cache=_FakeRepoCache(),
    )
    assert isinstance(pro, SweQaProDataset)
    assert isinstance(swe, SweQaDataset)
