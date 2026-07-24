"""Deterministic gate predicates — free, per-sample boolean checks (spec §3.4.2)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize.rubric.gates import evaluate_gate, gate_registry
from pydocs_eval.optimize.rubric.model import GateCheck

_SHIPPED_KINDS = (
    "answer_regex",
    "gold_substring",
    "gold_substring_all",
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


def test_registry_ships_exactly_the_seven_kinds() -> None:
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


class TestGoldSubstringAll:
    """ALL-candidates exactness gate (design §6.5) — mirrors GoldSubstring."""

    _GOLD: ClassVar[dict[str, object]] = {
        "file_set": ("app/jobs.py", "app/sysutils.py"),
        "extra": {"cve_id": "CVE-2099-0001", "cwe_id_0": "CWE-78"},
    }

    def test_all_candidates_present_passes(self) -> None:
        task = _task(**self._GOLD)
        answer = (
            "CVE-2099-0001 (CWE-78): taint enters app/jobs.py and reaches "
            "the shell wrapper in app/sysutils.py"
        )
        assert evaluate_gate(_check("gold_substring_all"), task, _Transcript(answer=answer))

    def test_one_missing_candidate_fails(self) -> None:
        task = _task(**self._GOLD)
        answer = "CVE-2099-0001 (CWE-78): the flaw is in app/jobs.py"  # sysutils missing
        assert not evaluate_gate(_check("gold_substring_all"), task, _Transcript(answer=answer))

    def test_any_gate_would_pass_where_all_fails(self) -> None:
        # The exactness contrast with the shipped ANY gate, pinned side by side.
        task = _task(**self._GOLD)
        transcript = _Transcript(answer="see app/jobs.py")
        assert evaluate_gate(_check("gold_substring"), task, transcript)
        assert not evaluate_gate(_check("gold_substring_all"), task, transcript)

    def test_keys_param_restricts_candidates(self) -> None:
        task = _task(**self._GOLD)
        check = _check("gold_substring_all", {"keys": ["file_set", "cve_id"]})
        answer = "CVE-2099-0001: app/jobs.py and app/sysutils.py"  # no CWE cited
        assert evaluate_gate(check, task, _Transcript(answer=answer))
        assert not evaluate_gate(check, task, _Transcript(answer="app/jobs.py only"))

    def test_empty_candidates_pass_vacuously(self) -> None:
        assert evaluate_gate(_check("gold_substring_all"), _task(), _Transcript(answer="anything"))

    def test_non_list_keys_param_fails_loud(self) -> None:
        check = _check("gold_substring_all", {"keys": "file_set"})
        with pytest.raises(TypeError, match="file_set"):
            evaluate_gate(check, _task(**self._GOLD), _Transcript())


class TestUsedIndexedTools:
    def test_one_indexed_call_passes_default(self) -> None:
        transcript = _Transcript(tool_calls=(_Call("search_codebase"),))
        assert evaluate_gate(_check("used_indexed_tools"), _task(), transcript)

    def test_non_indexed_tools_do_not_count(self) -> None:
        transcript = _Transcript(tool_calls=(_Call("Bash"), _Call("WebSearch")))
        assert not evaluate_gate(_check("used_indexed_tools"), _task(), transcript)

    def test_filesystem_tools_count_as_indexed(self) -> None:
        # Contract §1: grep/glob/read_file joined the task-shaped surface in
        # 0.6.0 — a run that greps the indexed corpus IS grounded tool use.
        for name in ("grep", "glob", "read_file"):
            transcript = _Transcript(tool_calls=(_Call(name),))
            assert evaluate_gate(_check("used_indexed_tools"), _task(), transcript), name

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
