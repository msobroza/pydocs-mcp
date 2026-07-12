"""Rubric data model — weights validation + objective identity hash (spec AC-8, AC-12)."""

from __future__ import annotations

import pytest

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

    def test_empty_gates_and_criteria_raise(self) -> None:
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
