"""Pin the file-set branch of ``is_relevant`` — the citation-derived gold
label path (spec §5, SWE-QA track).

When gold carries neither an ``ast_body`` (RepoQA) nor a resolved chunk-id
set (DS-1000) but DOES carry a ``file_set``, relevance is a suffix match:
the retrieved item's ``source_path`` ends with a gold repo-relative path on
a ``/`` path-segment boundary. Corpus dirs are materialized tmp copies, so
the ``source_path`` carries a tmp prefix the repo-relative gold path must
tolerate.

Hermetic: no ``pydocs_mcp`` import.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.metrics._relevance import is_relevant
from pydocs_eval.systems.base_system import RetrievedItem


def _task(gold: GoldAnswer) -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=gold,
        corpus_source=lambda: Path(),
    )


def test_retrieved_item_relevant_when_source_path_in_file_set() -> None:
    task = _task(gold=GoldAnswer(file_set=("src/pkg/mod.py",)))
    hit = RetrievedItem(rank=1, text="...", source_path="/tmp/corpus123/src/pkg/mod.py")
    assert is_relevant(hit, task) is True


def test_suffix_match_tolerates_materialized_corpus_prefix() -> None:
    # corpus dirs are tmp copies; source_path carries the tmp prefix — the
    # repo-relative gold path must match by suffix on path-segment boundary.
    task = _task(gold=GoldAnswer(file_set=("pkg/mod.py",)))
    assert is_relevant(RetrievedItem(rank=1, text="", source_path="/x/y/pkg/mod.py"), task)
    assert not is_relevant(RetrievedItem(rank=1, text="", source_path="/x/y/otherpkg/mod.py"), task)


def test_exact_source_path_equals_gold_path() -> None:
    # No prefix at all: source_path == gold path is a hit (the ``sp == g``
    # arm of the predicate).
    task = _task(gold=GoldAnswer(file_set=("pkg/mod.py",)))
    assert is_relevant(RetrievedItem(rank=1, text="", source_path="pkg/mod.py"), task)


def test_multiple_gold_files_any_match() -> None:
    task = _task(gold=GoldAnswer(file_set=("a/one.py", "b/two.py")))
    assert is_relevant(RetrievedItem(rank=1, text="", source_path="/c/b/two.py"), task)


def test_no_gold_file_matches_returns_false() -> None:
    task = _task(gold=GoldAnswer(file_set=("a/one.py",)))
    assert not is_relevant(RetrievedItem(rank=1, text="", source_path="/c/b/two.py"), task)


def test_existing_ast_and_chunk_id_paths_unchanged() -> None:
    # regression: RepoQA (ast_body) and DS-1000 (resolved_chunk_ids) dispatch
    # first — a file_set alongside them must NOT hijack relevance.
    body = "def f(): return 1"
    ast_task = _task(gold=GoldAnswer(ast_body=body, file_set=("never/match.py",)))
    assert is_relevant(RetrievedItem(rank=1, text=body, source_path="/x/other.py"), ast_task)

    resolved_task = _task(
        gold=GoldAnswer(
            file_set=("never/match.py",),
            extra={"resolved_chunk_ids": frozenset({"chunk:7"})},
        )
    )
    hit = RetrievedItem(rank=1, text="", source_path="/x/never/match.py", chunk_id=7)
    assert is_relevant(hit, resolved_task) is True
    miss = RetrievedItem(rank=1, text="", source_path="/x/never/match.py", chunk_id=9)
    # Resolved branch owns dispatch (ast_body is None but resolved set present
    # via non-empty file_set path NOT reached): chunk_id 9 not in set -> miss,
    # even though the source_path suffix-matches the file_set.
    assert is_relevant(miss, resolved_task) is False
