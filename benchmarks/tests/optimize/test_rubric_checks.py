"""Scored deterministic checks — the 0-1 generalization of the boolean gates.

A ``Check`` is one free, per-sample 0-1 measurement that MAY also gate. Two
fields carry the policy, named to match ``coding-agent-playbook``'s
``Metric``/``Check``/``Rubric`` model so the two engines stay legible side by
side:

* ``required`` — a failed required check trips ``fail_fast`` and spares the judge;
* ``fail`` — the 0-1 cutoff below which the check counts as failed (``None``
  means it never fails, i.e. it scores but never blocks).

Multi-task: ``applies_to`` restricts a check to some task types (a CVE check is
meaningless for a swe-qa-pro row) and ``weight_by_type`` re-weights per type. The
composite renormalizes over APPLICABLE weights so a task scored on five checks
stays comparable to one scored on three.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize.rubric.checks import (
    Check,
    check_registry,
    deterministic_checks,
    evaluate_check,
    score_checks,
    validate_checks,
)
from pydocs_eval.optimize.rubric.model import GateCheck
from tests.optimize._trajectories import make_trajectory


def _task(
    *,
    task_id: str = "ccv/cve-2099-0001",
    file_set: tuple[str, ...] = (),
    extra: dict[str, object] | None = None,
) -> EvalTask:
    return EvalTask(
        task_id=task_id,
        query="where is the flaw?",
        gold=GoldAnswer(file_set=file_set, extra=dict(extra or {})),
        corpus_source=lambda: None,  # type: ignore[arg-type]  # checks never touch the corpus
    )


# --------------------------------------------------------------------------- #
# The new scored primitives
# --------------------------------------------------------------------------- #


def test_gold_recall_scores_the_found_fraction() -> None:
    task = _task(file_set=("a.py", "b.py", "c.py", "d.py"))
    trajectory = make_trajectory(answer="the flaw spans a.py and b.py")

    outcome = evaluate_check(
        Check(name="files", kind="gold_recall", params={"keys": ["file_set"]}, fail=None),
        task,
        trajectory,
    )
    assert outcome.score == 0.5


def test_gold_recall_full_and_empty() -> None:
    task = _task(file_set=("a.py", "b.py"))
    full = evaluate_check(
        Check(name="f", kind="gold_recall", params={"keys": ["file_set"]}, fail=None),
        task,
        make_trajectory(answer="a.py and b.py"),
    )
    none = evaluate_check(
        Check(name="f", kind="gold_recall", params={"keys": ["file_set"]}, fail=None),
        task,
        make_trajectory(answer="nothing relevant here"),
    )
    assert (full.score, none.score) == (1.0, 0.0)


def test_gold_recall_with_no_candidates_scores_one() -> None:
    """Vacuous pass, mirroring the boolean siblings' convention."""
    outcome = evaluate_check(
        Check(name="f", kind="gold_recall", params={}, fail=None), _task(), make_trajectory()
    )
    assert outcome.score == 1.0


def test_cve_id_exact_requires_the_id_verbatim() -> None:
    task = _task(extra={"cve_id": "CVE-2025-10283"})
    hit = evaluate_check(
        Check(name="c", kind="cve_id_exact"), task, make_trajectory(answer="it is CVE-2025-10283")
    )
    miss = evaluate_check(
        Check(name="c", kind="cve_id_exact"), task, make_trajectory(answer="it is CVE-2025-99999")
    )
    assert (hit.score, miss.score) == (1.0, 0.0)


# --------------------------------------------------------------------------- #
# Legacy boolean gates resolve through the check path unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("kind", "params", "trajectory", "expected"),
    [
        ("min_answer_chars", {"n": 40}, make_trajectory(answer="x" * 100), 1.0),
        ("min_answer_chars", {"n": 40}, make_trajectory(answer="short"), 0.0),
        ("max_turns", {"n": 12}, make_trajectory(turns=3), 1.0),
        ("max_turns", {"n": 2}, make_trajectory(turns=3), 0.0),
    ],
)
def test_legacy_gate_kinds_score_one_or_zero(kind, params, trajectory, expected) -> None:
    """Every registered boolean gate is usable as a check with no porting."""
    outcome = evaluate_check(Check(name=kind, kind=kind, params=params), _task(), trajectory)
    assert outcome.score == expected


def test_unknown_kind_names_both_registries() -> None:
    with pytest.raises(KeyError, match="nope"):
        evaluate_check(Check(name="x", kind="nope"), _task(), make_trajectory())


# --------------------------------------------------------------------------- #
# required / fail — the gating policy
# --------------------------------------------------------------------------- #


def test_fail_cutoff_decides_passed() -> None:
    task = _task(file_set=("a.py", "b.py", "c.py", "d.py"))
    trajectory = make_trajectory(answer="a.py b.py c.py")  # recall 0.75

    lenient = evaluate_check(
        Check(name="f", kind="gold_recall", params={"keys": ["file_set"]}, fail=0.5),
        task,
        trajectory,
    )
    strict = evaluate_check(
        Check(name="f", kind="gold_recall", params={"keys": ["file_set"]}, fail=1.0),
        task,
        trajectory,
    )
    assert lenient.passed is True
    assert strict.passed is False


def test_fail_none_never_fails_and_never_blocks() -> None:
    task = _task(file_set=("a.py", "b.py"))
    outcome = evaluate_check(
        Check(name="f", kind="gold_recall", params={"keys": ["file_set"]}, fail=None),
        task,
        make_trajectory(answer="nothing"),
    )
    assert outcome.score == 0.0
    assert outcome.passed is True
    assert outcome.blocking is False


def test_only_required_failures_block() -> None:
    task = _task(file_set=("a.py", "b.py"))
    trajectory = make_trajectory(answer="nothing")
    common = {"kind": "gold_recall", "params": {"keys": ["file_set"]}, "fail": 1.0}

    assert evaluate_check(Check(name="r", required=True, **common), task, trajectory).blocking
    assert not evaluate_check(Check(name="o", required=False, **common), task, trajectory).blocking


def test_out_of_range_score_raises_naming_the_check_and_value() -> None:
    @check_registry.register("_bad_range")
    @dataclass(frozen=True, slots=True)
    class _Bad:
        def __call__(self, task, trajectory, params) -> float:
            return 1.5

    with pytest.raises(ValueError, match=r"1\.5"):
        evaluate_check(Check(name="bad", kind="_bad_range"), _task(), make_trajectory())


# --------------------------------------------------------------------------- #
# Multi-task: applicability + per-type weights
# --------------------------------------------------------------------------- #


def test_applies_to_restricts_the_check_to_its_task_types() -> None:
    check = Check(name="cve", kind="cve_id_exact", applies_to=("ccv",))
    assert check.applicable("ccv") is True
    assert check.applicable("sweqapro") is False


def test_empty_applies_to_means_every_task_type() -> None:
    assert Check(name="any", kind="min_answer_chars").applicable("whatever") is True


def test_weight_by_type_overrides_the_default_weight() -> None:
    check = Check(name="g", kind="gold_recall", weight=1.0, weight_by_type={"ccv": 2.0})
    assert check.weight_for("ccv") == 2.0
    assert check.weight_for("sweqapro") == 1.0


def test_non_applicable_checks_are_excluded_entirely() -> None:
    """A CVE check must neither score nor gate a swe-qa-pro row."""
    checks = (
        Check(name="len", kind="min_answer_chars", params={"n": 1}, weight=1.0),
        Check(name="cve", kind="cve_id_exact", applies_to=("ccv",), weight=9.0),
    )
    scoring = score_checks(checks, _task(task_id="sweqapro/x"), make_trajectory())

    assert "cve" not in scoring.outcomes
    assert scoring.blocked is False  # the absent CVE must not sink an unrelated task
    assert scoring.score == 1.0  # renormalized over the one applicable check


def test_composite_renormalizes_over_applicable_weights() -> None:
    """Two task types scored on different check sets stay comparable in [0, 1]."""
    checks = (
        Check(name="len", kind="min_answer_chars", params={"n": 1}, weight=1.0),
        Check(
            name="files",
            kind="gold_recall",
            params={"keys": ["file_set"]},
            applies_to=("ccv",),
            weight=3.0,
            fail=None,
        ),
    )
    task = _task(task_id="ccv/x", file_set=("a.py", "b.py"))
    scoring = score_checks(checks, task, make_trajectory(answer="a.py only"))

    # (1.0*1 + 0.5*3) / 4 == 0.625 — NOT divided by a global weight sum.
    assert scoring.score == pytest.approx(0.625)


def test_zero_weight_check_gates_without_scoring() -> None:
    """The pure-screen shape: contributes to blocking, contributes no score."""
    checks = (
        Check(name="screen", kind="min_answer_chars", params={"n": 1000}, weight=0.0),
        Check(
            name="files",
            kind="gold_recall",
            params={"keys": ["file_set"]},
            weight=1.0,
            fail=None,
        ),
    )
    task = _task(file_set=("a.py",))
    scoring = score_checks(checks, task, make_trajectory(answer="a.py"))

    assert scoring.blocked is True  # the screen failed
    assert scoring.score == 1.0  # but it did not drag the score down


# --------------------------------------------------------------------------- #
# Config validation — the judge-avoidance guarantee, asserted at load time
# --------------------------------------------------------------------------- #


def test_unknown_task_type_in_applies_to_is_rejected() -> None:
    checks = (Check(name="cve", kind="cve_id_exact", applies_to=("cvv",)),)  # typo
    with pytest.raises(ValueError, match="cvv"):
        validate_checks(checks, known_task_types=("ccv", "sweqapro"))


def test_unknown_task_type_in_weight_by_type_is_rejected() -> None:
    checks = (Check(name="g", kind="gold_recall", weight_by_type={"nope": 2.0}),)
    with pytest.raises(ValueError, match="nope"):
        validate_checks(checks, known_task_types=("ccv",))


def test_task_type_with_no_required_check_is_rejected() -> None:
    """Without a required applicable check, every sample of that type pays the judge."""
    checks = (
        Check(name="cve", kind="cve_id_exact", applies_to=("ccv",), required=True),
        Check(name="g", kind="gold_recall", applies_to=("sweqapro",), required=False, fail=None),
    )
    with pytest.raises(ValueError, match="sweqapro"):
        validate_checks(checks, known_task_types=("ccv", "sweqapro"))


def test_task_type_with_no_positive_weight_is_rejected() -> None:
    checks = (Check(name="s", kind="min_answer_chars", weight=0.0),)
    with pytest.raises(ValueError, match="weight"):
        validate_checks(checks, known_task_types=("ccv",))


def test_a_valid_multitask_config_passes() -> None:
    checks = (
        Check(name="len", kind="min_answer_chars", params={"n": 40}, weight=0.0),
        Check(name="cve", kind="cve_id_exact", applies_to=("ccv",), weight=3.0),
        Check(
            name="grounded",
            kind="gold_recall",
            weight=1.0,
            fail=None,
            required=False,
            weight_by_type={"ccv": 2.0},
        ),
    )
    validate_checks(checks, known_task_types=("ccv", "sweqapro"))


# --------------------------------------------------------------------------- #
# Review fixes: the judge-avoidance guarantee must actually hold
# --------------------------------------------------------------------------- #


def test_required_check_that_can_never_fail_is_rejected() -> None:
    """`required=True, fail=None` reads like a gate but can never block.

    `passed` is unconditionally True when `fail is None`, so validate_checks
    would have certified a config whose "required" check is a no-op — exactly
    the guarantee it exists to enforce.
    """
    checks = (Check(name="r", kind="gold_recall", required=True, fail=None),)
    with pytest.raises(ValueError, match="can never fail"):
        validate_checks(checks, known_task_types=("ccv",))


def test_required_check_with_a_zero_cutoff_is_rejected() -> None:
    """`score >= 0.0` holds for every 0-1 score, so this gate can never fire."""
    checks = (Check(name="r", kind="gold_recall", required=True, fail=0.0),)
    with pytest.raises(ValueError, match="can never fail"):
        validate_checks(checks, known_task_types=("ccv",))


def test_duplicate_check_names_are_rejected_at_load() -> None:
    """Mirrors the gate path, which rejects duplicate names in the config."""
    checks = (
        Check(name="grounded", kind="min_answer_chars"),
        Check(name="grounded", kind="max_turns"),
    )
    with pytest.raises(ValueError, match="grounded"):
        validate_checks(checks, known_task_types=("ccv",))


def test_score_checks_refuses_duplicate_names_instead_of_dropping_one() -> None:
    """Keying outcomes by name silently lost a check — including a required screen."""
    checks = (
        Check(name="grounded", kind="min_answer_chars", params={"n": 1000}, weight=0.0),
        Check(name="grounded", kind="min_answer_chars", params={"n": 1}, weight=1.0),
    )
    with pytest.raises(ValueError, match="grounded"):
        score_checks(checks, _task(), make_trajectory())


# --------------------------------------------------------------------------- #
# deterministic_checks — the ONE set a sample is measured against
# --------------------------------------------------------------------------- #


class TestDeterministicChecks:
    """Gates carry the composite alone; with checks present they become screens."""

    _GATES = (
        GateCheck(name="non_empty", kind="min_answer_chars", params={"n": 1}),
        GateCheck(name="grounded", kind="used_indexed_tools", params={"n": 1}),
    )

    def test_gates_alone_reproduce_the_boolean_pass_fraction(self) -> None:
        # The back-compat contract: weight 1.0 + fail 1.0 over booleans IS
        # ``fmean``, which is what keeps every unmigrated gates: config's
        # verdicts byte-identical.
        task = _task()
        trajectory = make_trajectory(answer="x" * 100)  # no tool calls -> grounded fails
        scoring = score_checks(deterministic_checks(self._GATES), task, trajectory)

        assert scoring.score == pytest.approx(fmean([1.0, 0.0]))
        assert scoring.blocked is True  # fail_fast still fires on a failed gate

    def test_checks_present_demote_every_gate_to_a_pure_screen(self) -> None:
        # The whole point of the apportionment: two screens must not dilute a
        # configured {0.75, 0.25} into {1, 1, 0.75, 0.25} / 3.
        checks = (
            Check(name="recall", kind="gold_recall", weight=0.75, required=False, fail=None),
            Check(name="len", kind="min_answer_chars", params={"n": 1}, weight=0.25, fail=None),
        )
        built = deterministic_checks(self._GATES, checks)

        assert [c.weight for c in built[:2]] == [0.0, 0.0]
        assert [c.required for c in built[:2]] == [True, True]
        assert built[2:] == checks

    def test_a_failed_screen_still_blocks_while_the_checks_score(self) -> None:
        checks = (Check(name="recall", kind="gold_recall", weight=1.0, required=False, fail=None),)
        task = _task(file_set=("a.py", "b.py"))
        scoring = score_checks(
            deterministic_checks(self._GATES, checks), task, make_trajectory(answer="a.py")
        )

        assert scoring.blocked is True  # ``grounded`` saw no server tool call
        assert scoring.score == 0.5  # and the screens contributed nothing to it

    def test_no_gates_and_no_checks_is_the_vacuous_one(self) -> None:
        assert deterministic_checks((), ()) == ()
        assert score_checks((), _task(), make_trajectory()).score == 1.0

    def test_a_task_type_no_check_applies_to_falls_back_to_its_screens(self) -> None:
        """A demoted screen must still measure when every scored check is filtered out.

        The gates fall to weight 0 as soon as ANY check is configured, so once
        ``applies_to`` excludes every scored check the applicable weights sum to
        zero. Renormalizing "over nothing" scored a vacuous 1.0 — a full
        deterministic layer for a sample that passed nothing, and with
        ``keep_deterministic_on_skip`` that lands straight on the verdict.
        """
        checks = (Check(name="cve", kind="cve_id_exact", applies_to=("ccv",), weight=1.0),)
        built = deterministic_checks(self._GATES, checks)
        trajectory = make_trajectory(answer="x" * 100)  # no tool calls -> grounded fails

        scoring = score_checks(built, _task(task_id="sweqapro/x"), trajectory)

        assert "cve" not in scoring.outcomes
        assert scoring.blocked is True
        assert scoring.score == pytest.approx(fmean([1.0, 0.0]))  # the screens, not a free 1.0
