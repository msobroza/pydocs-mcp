"""The ``scoring:`` arm cell key — one objective, many observed metrics (design §6).

Three surfaces are pinned here: the closed ``ObjectiveKind`` vocabulary, the
strict ``ArmScoring`` shape (unknown key / unknown objective / unresolvable
tracked name each named by their offending value), and the pure-observation
role — a tracked metric is measured per sample and can never move a verdict.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.optimize.arm_scoring import (
    ArmScoring,
    ObjectiveKind,
    _known_metric_kinds,
    _observable_kinds,
    observation_checks,
    observe_tracked_metrics,
    resolve_rubric_section,
)
from pydocs_eval.optimize.rubric.checks import required_params_for, score_checks
from tests.optimize._trajectories import make_trajectory


def _scoring(**overrides: object) -> ArmScoring:
    base: dict[str, object] = {"objective": "rubric_verdict", "rubric": "ask_rubric"}
    return ArmScoring(**{**base, **overrides})


def _task(*, file_set: tuple[str, ...] = ("a.py", "b.py")) -> EvalTask:
    return EvalTask(
        task_id="ccv/cve-2099-0001",
        query="where is the flaw?",
        gold=GoldAnswer(file_set=file_set, extra={"cve_id": "CVE-2099-0001"}),
        corpus_source=lambda: None,  # type: ignore[arg-type]  # checks never touch the corpus
    )


class TestObjectiveVocabulary:
    def test_the_closed_enum_carries_exactly_one_member_today(self) -> None:
        # Widening it is a deliberate measurement event: a second objective
        # means arms whose verdicts are not comparable on one scale.
        assert [kind.value for kind in ObjectiveKind] == ["rubric_verdict"]

    def test_an_unknown_objective_names_the_offending_value(self) -> None:
        with pytest.raises(ValidationError, match="answer_accuracy"):
            _scoring(objective="answer_accuracy")


class TestScoringShape:
    def test_the_happy_path_parses(self) -> None:
        scoring = _scoring(tracked=("gold_recall", "cve_id_exact"))
        assert scoring.objective is ObjectiveKind.RUBRIC_VERDICT
        assert scoring.rubric == "ask_rubric"
        assert scoring.tracked == ("gold_recall", "cve_id_exact")

    def test_tracked_defaults_to_the_empty_tuple(self) -> None:
        assert _scoring().tracked == ()

    def test_an_unknown_scoring_key_is_rejected_by_name(self) -> None:
        with pytest.raises(ValidationError, match="secondary_objective"):
            _scoring(secondary_objective="gold_recall")

    def test_a_missing_rubric_reference_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="rubric"):
            ArmScoring(objective="rubric_verdict")

    def test_an_unresolvable_tracked_name_names_the_offending_value(self) -> None:
        with pytest.raises(ValidationError, match="gold_recal"):
            _scoring(tracked=("gold_recal",))

    def test_a_registered_boolean_gate_is_a_legal_tracked_name(self) -> None:
        # The checks layer already adapts every gate kind to a 0-1 score, so a
        # gate is observable without porting it.
        assert _scoring(tracked=("min_answer_chars",)).tracked == ("min_answer_chars",)

    def test_a_duplicate_tracked_name_is_rejected(self) -> None:
        # Outcomes are keyed by name; a duplicate would silently drop one.
        with pytest.raises(ValidationError, match="duplicate"):
            _scoring(tracked=("gold_recall", "gold_recall"))


class TestOnlyParamsFreeKindsAreObservable:
    """Registered is not enough — an observation is measured with NO params."""

    def test_a_kind_that_cannot_run_params_free_is_rejected_by_name(self) -> None:
        # ``answer_regex`` IS registered but does params["pattern"], while
        # observation checks are minted with no params. Registered-only
        # validation let it load clean and raise KeyError at measurement time
        # — after the rollout and the judge call were paid for, and before the
        # ledger line a rerun could resume from.
        with pytest.raises(ValidationError) as exc:
            _scoring(tracked=("answer_regex",))
        assert "answer_regex" in str(exc.value)
        assert "pattern" in str(exc.value)

    def test_the_exclusion_is_derived_from_the_predicates(self) -> None:
        # Never a hand-kept deny list: everything registered is observable
        # except what declares a param it cannot run without.
        assert _known_metric_kinds() - _observable_kinds() == {"answer_regex"}
        assert required_params_for("answer_regex") == ("pattern",)
        assert required_params_for("gold_recall") == ()

    def test_every_admissible_kind_actually_measures_params_free(self) -> None:
        # The drift guard: a future predicate that reaches into ``params``
        # without declaring ``required_params`` fails HERE, not on a paid run.
        for kind in sorted(_observable_kinds()):
            measured = observe_tracked_metrics(
                (kind,), task=_task(), trajectory=make_trajectory(answer="a.py")
            )
            assert 0.0 <= measured[kind] <= 1.0, kind


class TestRubricReferenceResolution:
    def test_a_configured_name_resolves(self) -> None:
        assert (
            resolve_rubric_section(
                _scoring(), available={"ask_rubric": "the-section"}, arm_label="arms[0]"
            )
            == "the-section"
        )

    def test_an_unconfigured_name_names_the_arm_and_the_available_names(self) -> None:
        with pytest.raises(ValueError, match="strict_ccv") as exc:
            resolve_rubric_section(
                _scoring(rubric="strict_ccv"),
                available={"ask_rubric": "the-section"},
                arm_label="arms[1]",
            )
        assert "arms[1]" in str(exc.value)
        assert "ask_rubric" in str(exc.value)

    def test_no_configured_objective_at_all_names_the_fix(self) -> None:
        # The likelier authoring mistake now that ``scoring`` is required on
        # every arm: an arms: block and no rubric section. Printing an empty
        # list of available names would say nothing actionable.
        with pytest.raises(ValueError, match="ask_rubric") as exc:
            resolve_rubric_section(_scoring(), available={}, arm_label="arms[0]")
        assert "no rubric objective" in str(exc.value)


class TestPureObservationRole:
    def test_a_tracked_metric_neither_gates_nor_weighs(self) -> None:
        # The role the arm binding needs and the role table now documents:
        # recorded, never blocking, zero weight in the composite.
        (check,) = observation_checks(_scoring(tracked=("gold_recall",)).tracked)
        assert check.weight == 0.0
        assert check.required is False
        assert check.fail is None

    def test_tracked_outcomes_are_recorded_per_sample(self) -> None:
        metrics = observe_tracked_metrics(
            _scoring(tracked=("gold_recall", "cve_id_exact")).tracked,
            task=_task(),
            trajectory=make_trajectory(answer="the flaw is in a.py"),
        )
        # Default params: gold_recall spans EVERY gold candidate (both files
        # plus the CVE id), of which the answer names one.
        assert metrics == {"gold_recall": pytest.approx(1 / 3), "cve_id_exact": 0.0}

    def test_no_tracked_names_means_no_measurement(self) -> None:
        assert observe_tracked_metrics((), task=_task(), trajectory=make_trajectory()) == {}

    def test_adding_a_tracked_metric_cannot_move_the_composite(self) -> None:
        # The whole point of weight=0: an observation joins the outcome map
        # without entering the numerator OR the renormalizing denominator.
        from pydocs_eval.optimize.rubric.checks import Check

        scored = (Check(name="files", kind="gold_recall", params={"keys": ["file_set"]}, fail=0.5),)
        task = _task()
        trajectory = make_trajectory(answer="the flaw is in a.py")

        without = score_checks(scored, task, trajectory)
        with_tracked = score_checks(
            scored + observation_checks(_scoring(tracked=("cve_id_exact",)).tracked),
            task,
            trajectory,
        )
        assert with_tracked.score == without.score
        assert with_tracked.blocked == without.blocked
        assert "cve_id_exact" in with_tracked.outcomes
