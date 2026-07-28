"""Rubric data model — weights validation + objective identity hash (spec AC-8, AC-12)."""

from __future__ import annotations

import pytest

from pydocs_eval.optimize.rubric.checks import Check, check_registry
from pydocs_eval.optimize.rubric.gates import gate_registry
from pydocs_eval.optimize.rubric.model import (
    GateCheck,
    RubricConfig,
    RubricCriterion,
    rubric_config_hash,
    validate_rubric_config,
)


def _config(
    *,
    criteria_weights: tuple[float, ...] = (0.6, 0.4),
    gate_weight: float = 0.3,
    rubric_weight: float = 0.7,
    gates: tuple[GateCheck, ...] = (GateCheck(name="g", kind="min_answer_chars", params={}),),
) -> RubricConfig:
    criteria = tuple(
        RubricCriterion(name=f"c{i}", weight=w, description=f"criterion {i}")
        for i, w in enumerate(criteria_weights)
    )
    return RubricConfig(
        gates=gates,
        criteria=criteria,
        gate_weight=gate_weight,
        rubric_weight=rubric_weight,
    )


def _validate(config: RubricConfig) -> None:
    validate_rubric_config(config, registered_gate_kinds=gate_registry.names())


def _validate_with_checks(config: RubricConfig) -> None:
    """Load-time validation as ``run_config`` calls it — both vocabularies."""
    validate_rubric_config(
        config,
        registered_gate_kinds=gate_registry.names(),
        registered_check_kinds=(*check_registry.names(), *gate_registry.names()),
    )


class TestWeightValidation:
    def test_criterion_weights_summing_low_raise(self) -> None:
        with pytest.raises(ValueError, match="criterion weights"):
            _validate(_config(criteria_weights=(0.6, 0.38)))  # 0.98

    def test_criterion_weights_summing_high_raise(self) -> None:
        with pytest.raises(ValueError, match="criterion weights"):
            _validate(_config(criteria_weights=(0.6, 0.42)))  # 1.02

    def test_weights_within_tolerance_pass(self) -> None:
        _validate(_config(criteria_weights=(0.6, 0.3995)))  # 0.9995, inside 1e-3

    def test_layer_weights_must_sum_to_one(self) -> None:
        with pytest.raises(ValueError, match="gate_weight"):
            _validate(_config(gate_weight=0.3, rubric_weight=0.72))

    def test_empty_gates_checks_and_criteria_raise(self) -> None:
        config = RubricConfig(gates=(), criteria=())
        with pytest.raises(ValueError, match="at least one"):
            _validate(config)

    def test_gates_only_config_is_valid(self) -> None:
        config = RubricConfig(
            gates=(GateCheck(name="g", kind="max_turns", params={}),),
            criteria=(),
            gate_weight=1.0,
            rubric_weight=0.0,
        )
        _validate(config)

    def test_duplicate_criterion_names_raise(self) -> None:
        criteria = (
            RubricCriterion(name="dup", weight=0.5, description="a"),
            RubricCriterion(name="dup", weight=0.5, description="b"),
        )
        config = RubricConfig(
            gates=(GateCheck(name="g", kind="max_turns", params={}),), criteria=criteria
        )
        with pytest.raises(ValueError, match="unique"):
            _validate(config)

    def test_gates_only_config_with_rubric_weight_raises(self) -> None:
        # rubric_weight > 0 with no criteria silently caps every verdict at
        # gate_weight — a config error, not a tuning choice.
        config = RubricConfig(
            gates=(GateCheck(name="g", kind="max_turns", params={}),),
            criteria=(),
            gate_weight=0.3,
            rubric_weight=0.7,
        )
        with pytest.raises(ValueError, match="rubric_weight"):
            _validate(config)

    def test_duplicate_gate_names_raise(self) -> None:
        gates = (
            GateCheck(name="dup", kind="min_answer_chars", params={}),
            GateCheck(name="dup", kind="max_turns", params={}),
        )
        with pytest.raises(ValueError, match="unique"):
            _validate(_config(gates=gates))

    def test_unknown_gate_kind_raises_keyerror_naming_registered(self) -> None:
        gates = (GateCheck(name="g", kind="no_such_gate", params={}),)
        with pytest.raises(KeyError, match="min_answer_chars"):
            _validate(_config(gates=gates))


class TestObjectiveHash:
    def test_equal_configs_hash_equal(self) -> None:
        assert rubric_config_hash(_config(), architecture="text_react") == rubric_config_hash(
            _config(), architecture="text_react"
        )

    def test_architecture_is_part_of_the_identity(self) -> None:
        # AC-12: re-pinning a campaign's runner architecture must never
        # falsely resume samples scored under a different graph.
        assert rubric_config_hash(_config(), architecture="text_react") != rubric_config_hash(
            _config(), architecture="inline"
        )

    def test_weights_change_the_hash(self) -> None:
        assert rubric_config_hash(_config(), architecture="a") != rubric_config_hash(
            _config(gate_weight=0.4, rubric_weight=0.6), architecture="a"
        )

    def test_gate_params_change_the_hash(self) -> None:
        loose = _config(gates=(GateCheck(name="g", kind="max_turns", params={"n": 20}),))
        tight = _config(gates=(GateCheck(name="g", kind="max_turns", params={"n": 8}),))
        assert rubric_config_hash(loose, architecture="a") != rubric_config_hash(
            tight, architecture="a"
        )

    def test_hash_is_hex_sha256_shaped(self) -> None:
        digest = rubric_config_hash(_config(), architecture="a")
        assert len(digest) == 64
        int(digest, 16)  # hex or raise


class TestScoredChecksBlock:
    """``checks:`` is a verdict-moving config surface, so it validates and hashes."""

    _RECALL = Check(name="recall", kind="gold_recall", weight=0.75, required=False, fail=None)

    def _with_checks(self, *checks: Check) -> RubricConfig:
        base = _config()
        return RubricConfig(
            gates=base.gates,
            checks=checks,
            criteria=base.criteria,
            gate_weight=base.gate_weight,
            rubric_weight=base.rubric_weight,
        )

    def test_a_checks_only_config_is_not_empty(self) -> None:
        # A judge-free objective is legal; only "nothing at all" is not.
        config = RubricConfig(
            gates=(), checks=(self._RECALL,), criteria=(), gate_weight=1.0, rubric_weight=0.0
        )
        _validate_with_checks(config)

    def test_an_unknown_check_kind_names_the_registered_kinds(self) -> None:
        config = self._with_checks(Check(name="x", kind="no_such_check", fail=None))
        with pytest.raises(KeyError, match="gold_recall"):
            _validate_with_checks(config)

    def test_a_gate_kind_is_a_legal_check_kind(self) -> None:
        # ``evaluate_check`` already adapts a boolean gate to a 0-1 score, so
        # rejecting a gate name here would be a false negative.
        _validate_with_checks(self._with_checks(Check(name="len", kind="min_answer_chars")))

    def test_a_name_colliding_with_a_gate_is_rejected(self) -> None:
        # Gates and checks share one outcome namespace once the layer merges.
        config = self._with_checks(Check(name="g", kind="gold_recall", fail=None))
        with pytest.raises(ValueError, match="unique"):
            _validate_with_checks(config)

    def test_an_all_zero_weight_checks_block_is_rejected(self) -> None:
        # With checks present the gates fall back to screens, so a weightless
        # block scores every sample a vacuous 1.0 — tuned-looking, measuring
        # nothing. Weight 0 belongs in an arm's scoring.tracked.
        config = self._with_checks(Check(name="obs", kind="gold_recall", weight=0.0, fail=None))
        with pytest.raises(ValueError, match="no weight"):
            _validate_with_checks(config)

    def test_a_non_numeric_weight_is_rejected_even_behind_a_valid_one(self) -> None:
        # The mass test below short-circuits on the first positive weight, so a
        # later string weight would sail through load and raise inside the
        # composite instead — at trial 14, rollout already paid for.
        config = self._with_checks(
            self._RECALL,
            Check(name="evid", kind="gold_recall", weight="0.25", fail=None),  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError, match="weight must be a number"):
            _validate_with_checks(config)

    def test_the_checks_block_is_part_of_the_objective_identity(self) -> None:
        assert rubric_config_hash(_config(), architecture="a") != rubric_config_hash(
            self._with_checks(self._RECALL), architecture="a"
        )

    def test_a_reweighted_check_moves_the_hash(self) -> None:
        # The reason the apportionment is a versioned objective change and not
        # a silent re-scoring of already-ledgered samples.
        heavier = Check(name="recall", kind="gold_recall", weight=0.5, required=False, fail=None)
        assert rubric_config_hash(
            self._with_checks(self._RECALL), architecture="a"
        ) != rubric_config_hash(self._with_checks(heavier), architecture="a")
