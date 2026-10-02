"""What the reference writer is shown per task: ground truth only, never an agent answer.

A repoqa-qa task shows the question, the gold path and symbol, and the needle's
body with a few lines around it; a chat record shows the question and every
gold site's path, symbol and span text.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.example_needle_chat import ExampleNeedleChatDataset, gold_sites_of
from pydocs_eval.datasets.repo_qa import RepoQaQuestionDataset
from pydocs_eval.datasets.repoqa import RepoQADataset
from pydocs_eval.judge.needle_citation import NeedleSite
from pydocs_eval.judge.reference_sources import (
    ReferenceShape,
    chat_reference_source,
    corpus_file_reader,
    repoqa_reference_source,
)

# In the release's own line convention (0-based start_line), so the spans read true.
_REPOQA_FIXTURE = Path(__file__).parents[1] / "fixtures" / "repoqa_release_lines.json"


async def _first(tasks: AsyncIterator[EvalTask]) -> EvalTask:
    async for task in tasks:
        return task
    raise AssertionError("the dataset yielded no task")


async def _repoqa_task() -> EvalTask:
    dataset = RepoQaQuestionDataset(source=RepoQADataset(fixture_path=_REPOQA_FIXTURE))
    return await _first(dataset.tasks())


async def _chat_task() -> EvalTask:
    return await _first(ExampleNeedleChatDataset(split="dev").tasks())


async def test_a_repoqa_source_shows_the_needle_with_the_lines_around_it() -> None:
    task = await _repoqa_task()

    with corpus_file_reader(task) as read_file:
        source = repoqa_reference_source(task, read_file, context_lines=1)
        file_lines = read_file("helpers.py").splitlines()

    (code,) = source.code
    assert (source.task_id, source.question, source.shape) == (
        task.task_id,
        task.query,
        ReferenceShape.REPOQA,
    )
    assert code.site == NeedleSite("helpers.py", "factorial")
    assert (code.start, code.end, code.first_line) == (6, 7, 5)
    assert code.lines == tuple(file_lines[4:8])
    assert code.lines[0] == "@functools.cache", "the decorator shows as context"


async def test_the_shown_window_stops_at_the_file_edges() -> None:
    task = await _repoqa_task()

    with corpus_file_reader(task) as read_file:
        (code,) = repoqa_reference_source(task, read_file, context_lines=50).code
        total = len(read_file("helpers.py").splitlines())

    assert (code.first_line, code.first_line + len(code.lines) - 1) == (1, total)


async def test_a_repoqa_task_without_its_needle_span_is_refused_by_name() -> None:
    task = await _repoqa_task()
    metadata = {k: v for k, v in task.metadata.items() if k != "needle_first_line"}
    bare = EvalTask(task.task_id, task.query, task.gold, task.corpus_source, metadata)

    with pytest.raises(ValueError, match="needle_first_line"):
        repoqa_reference_source(bare, lambda path: "", context_lines=1)


async def test_a_chat_source_shows_every_gold_site_and_only_its_span() -> None:
    task = await _chat_task()
    sites = gold_sites_of(task)
    files = {site.path: "\n".join(f"line {n}" for n in range(1, 400)) for site in sites}

    source = chat_reference_source(task, files.__getitem__)

    assert (source.question, source.shape) == (task.query, ReferenceShape.CHAT)
    assert [code.site for code in source.code] == [
        NeedleSite(site.path, site.symbol) for site in sites
    ]
    for code, site in zip(source.code, sites, strict=True):
        assert code.first_line == site.start
        assert code.lines == tuple(f"line {n}" for n in range(site.start, site.end + 1))


async def test_the_corpus_is_removed_once_read() -> None:
    task = await _repoqa_task()

    with corpus_file_reader(task) as read_file:
        read_file("helpers.py")
        root = read_file.root

    assert not root.exists()
