"""Deterministic gate predicates — free, per-sample boolean checks (spec §3.4.2)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize.rubric.gates import evaluate_gate, gate_registry
from pydocs_eval.optimize.rubric.model import GateCheck

_SHIPPED_KINDS = (
    "answer_regex",
    "gold_substring",
    "max_turns",
    "max_wall_seconds",
    "min_answer_chars",
    "used_indexed_tools",
)


@dataclass(frozen=True, slots=True)
class _Call:
    tool_name: str
    args_digest: str = ""


@dataclass(frozen=True, slots=True)
class _Transcript:
    answer: str = "x" * 100
    tool_calls: tuple[_Call, ...] = ()
    turns: int = 3
    wall_seconds: float = 10.0
    cost_usd: float = 0.0


def _task(*, file_set: tuple[str, ...] = (), extra: dict[str, object] | None = None) -> EvalTask:
    return EvalTask(
        task_id="t1",
        query="how does routing work?",
        gold=GoldAnswer(file_set=file_set, extra=dict(extra or {})),
        corpus_source=lambda: None,  # type: ignore[arg-type]  # gates never touch the corpus
    )


def _check(kind: str, params: dict[str, object] | None = None) -> GateCheck:
    return GateCheck(name=kind, kind=kind, params=dict(params or {}))


def test_registry_ships_exactly_the_six_kinds() -> None:
    assert gate_registry.names() == _SHIPPED_KINDS


def test_unknown_kind_fails_loud() -> None:
    with pytest.raises(KeyError, match="min_answer_chars"):
        evaluate_gate(_check("no_such_gate"), _task(), _Transcript())


class TestMinAnswerChars:
    def test_short_answer_fails_default_threshold(self) -> None:
        assert not evaluate_gate(_check("min_answer_chars"), _task(), _Transcript(answer="hi"))

    def test_long_answer_passes(self) -> None:
        assert evaluate_gate(_check("min_answer_chars"), _task(), _Transcript(answer="y" * 40))

    def test_threshold_override(self) -> None:
        check = _check("min_answer_chars", {"n": 5})
        assert evaluate_gate(check, _task(), _Transcript(answer="hello"))
        assert not evaluate_gate(check, _task(), _Transcript(answer="hey"))


class TestAnswerRegex:
    def test_pattern_present_passes(self) -> None:
        check = _check("answer_regex", {"pattern": r"APIRouter\b"})
        assert evaluate_gate(check, _task(), _Transcript(answer="uses APIRouter here"))

    def test_pattern_absent_fails(self) -> None:
        check = _check("answer_regex", {"pattern": r"APIRouter\b"})
        assert not evaluate_gate(check, _task(), _Transcript(answer="no match"))


class TestGoldSubstring:
    def test_gold_path_in_answer_passes(self) -> None:
        task = _task(file_set=("fastapi/routing.py",))
        transcript = _Transcript(answer="see fastapi/routing.py for the include logic")
        assert evaluate_gate(_check("gold_substring"), task, transcript)

    def test_no_gold_evidence_fails(self) -> None:
        task = _task(file_set=("fastapi/routing.py",))
        assert not evaluate_gate(_check("gold_substring"), task, _Transcript(answer="unrelated"))

    def test_gold_symbol_from_extra_counts(self) -> None:
        task = _task(extra={"symbol": "APIRouter.include_router"})
        transcript = _Transcript(answer="APIRouter.include_router mounts the child")
        assert evaluate_gate(_check("gold_substring"), task, transcript)

    def test_task_without_gold_substrings_passes_vacuously(self) -> None:
        assert evaluate_gate(_check("gold_substring"), _task(), _Transcript(answer="anything"))


class TestUsedIndexedTools:
    def test_one_indexed_call_passes_default(self) -> None:
        transcript = _Transcript(tool_calls=(_Call("search_codebase"),))
        assert evaluate_gate(_check("used_indexed_tools"), _task(), transcript)

    def test_non_indexed_tools_do_not_count(self) -> None:
        transcript = _Transcript(tool_calls=(_Call("Bash"), _Call("WebSearch")))
        assert not evaluate_gate(_check("used_indexed_tools"), _task(), transcript)

    def test_threshold_override(self) -> None:
        transcript = _Transcript(tool_calls=(_Call("get_symbol"),))
        assert not evaluate_gate(_check("used_indexed_tools", {"n": 2}), _task(), transcript)


class TestBounds:
    def test_max_turns_default(self) -> None:
        assert evaluate_gate(_check("max_turns"), _task(), _Transcript(turns=12))
        assert not evaluate_gate(_check("max_turns"), _task(), _Transcript(turns=13))

    def test_max_wall_seconds_default(self) -> None:
        assert evaluate_gate(_check("max_wall_seconds"), _task(), _Transcript(wall_seconds=300.0))
        assert not evaluate_gate(
            _check("max_wall_seconds"), _task(), _Transcript(wall_seconds=300.1)
        )
