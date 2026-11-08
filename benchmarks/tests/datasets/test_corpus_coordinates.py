"""Every multi-location dataset names its corpus as ``repo`` + ``commit`` (step 7d).

A before/after plan groups tasks into per-corpus workspaces by exactly those two
metadata keys (``plan_task_workspaces``); a task without them searches the one
shared ``--workspace``, which holds none of these repositories. The loaders run
offline here: fixtures plus a named fake ``RepoCache``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after_corpora import plan_task_workspaces
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.bug_localization import LcaBugLocDataset, SweBenchVerifiedLocDataset
from pydocs_eval.datasets.crosscommitvuln import CrossCommitVulnDataset
from pydocs_eval.datasets.repo_coordinates import owner_name_of_repo_url
from pydocs_eval.datasets.repo_qa import SweQaQuestionDataset
from pydocs_eval.datasets.swe_qa import SweQaDataset

from ..campaign._corpora_fakes import INDEX_IDENTITY

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_CORPUS_DIR = _FIXTURES / "swe_qa_corpus"


@dataclass
class _FakeRepoCache:
    """Stand-in for ``RepoCache`` — no git, no network; every citation resolves."""

    corpus_dir: Path = field(default=_CORPUS_DIR)

    def checkout(self, url: str, sha: str) -> Path:
        return self.corpus_dir

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return ("lib/matplotlib/backends/backend_pgf.py",)


def _swe_qa_questions() -> SweQaQuestionDataset:
    source = SweQaDataset(
        fixture_path=_FIXTURES / "swe_qa_mini.jsonl",
        split="matplotlib",
        repo_cache=_FakeRepoCache(),
    )
    return SweQaQuestionDataset(source=source)


def _lca() -> LcaBugLocDataset:
    return LcaBugLocDataset(
        fixture_path=_FIXTURES / "lca_bug_loc_mini.jsonl", repo_cache=_FakeRepoCache()
    )


def _swe_bench() -> SweBenchVerifiedLocDataset:
    return SweBenchVerifiedLocDataset(
        fixture_path=_FIXTURES / "swe_bench_verified_loc_mini.jsonl", repo_cache=_FakeRepoCache()
    )


def _crosscommitvuln() -> CrossCommitVulnDataset:
    return CrossCommitVulnDataset(
        fixture_path=_FIXTURES / "crosscommitvuln" / "mini.jsonl", repo_cache=_FakeRepoCache()
    )


async def _tasks(tasks: AsyncIterator[EvalTask]) -> list[EvalTask]:
    return [task async for task in tasks]


async def test_a_swe_qa_question_names_its_pinned_repository_and_commit() -> None:
    tasks = await _tasks(_swe_qa_questions().tasks())

    assert tasks, "the fixture yields cited rows"
    assert {(t.metadata["repo"], t.metadata["commit"]) for t in tasks} == {
        ("matplotlib/matplotlib", "a5e1f60")
    }


async def test_an_lca_task_names_its_base_sha() -> None:
    first = (await _tasks(_lca().tasks()))[0]

    assert first.metadata["repo"] == "thealgorithms/python"
    assert first.metadata["commit"] == "dbfc220264e514cbc94320b6e4769acfaea85fad"


async def test_a_swe_bench_localization_task_names_its_base_commit() -> None:
    first = (await _tasks(_swe_bench().tasks()))[0]

    assert first.metadata["repo"] == "astropy/astropy"
    assert first.metadata["commit"] == "d16bfe05a744909de4b27f5875fe0d4ed41ce607"


async def test_a_crosscommitvuln_task_derives_its_repo_from_the_url_and_pins_the_prefix_sha() -> (
    None
):
    tasks = await _tasks(_crosscommitvuln().tasks())

    assert [(t.metadata["repo"], t.metadata["commit"]) for t in tasks] == [
        ("exampleorg/exampleproj", "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"),
        ("exampleorg/exampleproj2", "c1b2c3d4e5f60718293a4b5c6d7e8f9012345678"),
    ]


async def test_the_crosscommitvuln_record_metadata_is_kept_beside_the_coordinates() -> None:
    first = (await _tasks(_crosscommitvuln().tasks()))[0]

    assert first.metadata["ecosystem"] == "PyPI"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://github.com/psf/black", "psf/black"),
        ("https://github.com/psf/black.git", "psf/black"),
        ("https://github.com/psf/black/", "psf/black"),
        ("https://github.com/psf/black.git/", "psf/black"),
    ],
)
def test_a_repository_url_names_owner_and_name(url: str, expected: str) -> None:
    assert owner_name_of_repo_url(url) == expected


@pytest.mark.parametrize("url", ["", "black", "https://github.com//"])
def test_a_url_without_owner_and_name_is_refused_with_the_value(url: str) -> None:
    with pytest.raises(ValueError) as caught:
        owner_name_of_repo_url(url)

    assert repr(url) in str(caught.value)
    assert "<owner>/<name>" in str(caught.value)


@pytest.mark.parametrize(
    "dataset", [_swe_qa_questions, _lca, _crosscommitvuln], ids=["swe-qa-questions", "lca", "ccv"]
)
async def test_every_task_gets_the_workspace_of_its_own_corpus(dataset, tmp_path: Path) -> None:
    tasks = await _tasks(dataset().tasks())

    workspaces = plan_task_workspaces(tasks, workspace=tmp_path, identity=INDEX_IDENTITY)

    assert workspaces.shared_task_ids == ()
    keys = {(t.metadata["repo"], t.metadata["commit"]) for t in tasks}
    assert len(workspaces.corpora) == len(keys)
    assert all(workspaces.workspace_for(t.task_id) != tmp_path for t in tasks)
