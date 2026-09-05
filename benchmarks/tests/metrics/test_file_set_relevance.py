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


def test_empty_injected_resolved_set_does_not_hijack_the_file_set_branch() -> None:
    # Regression (2026-07-28): ``sweep_support._resolve_and_inject`` injects
    # ``resolved_chunk_ids`` for EVERY system exposing a gold resolver, and the
    # shipped resolvers return an empty frozenset when the gold carries no
    # ``doc_contents`` — which is every file-set corpus. Dispatching on key
    # PRESENCE therefore routed those tasks into an always-empty membership
    # test and scored the whole retrieval track a flat 0.0. Dispatch is on
    # truthiness now, so the file_set branch is reached.
    task = _task(
        gold=GoldAnswer(
            file_set=("src/pkg/mod.py",),
            extra={"resolved_chunk_ids": frozenset()},
        )
    )
    hit = RetrievedItem(rank=1, text="", source_path="/tmp/c/src/pkg/mod.py", chunk_id=3)
    assert is_relevant(hit, task) is True


def test_ground_truth_count_follows_the_same_dispatch() -> None:
    # The AP / IDCG denominator must agree with the predicate, or a found gold
    # can be normalized by a count that ignores it.
    from pydocs_eval.metrics._relevance import ground_truth_count

    assert ground_truth_count(_task(GoldAnswer(ast_body="def f(): ..."))) == 1
    assert ground_truth_count(_task(GoldAnswer(file_set=("a.py", "b.py")))) == 2
    resolved = _task(GoldAnswer(extra={"resolved_chunk_ids": frozenset({"chunk:1", "chunk:2"})}))
    assert ground_truth_count(resolved) == 2
    # Empty injected set + a file_set: counted as the FILE set, matching the
    # branch ``is_relevant`` now takes.
    empty_injection = _task(
        GoldAnswer(file_set=("a.py",), extra={"resolved_chunk_ids": frozenset()})
    )
    assert ground_truth_count(empty_injection) == 1
