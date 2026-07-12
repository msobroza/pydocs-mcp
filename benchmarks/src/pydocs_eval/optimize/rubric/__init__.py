"""Layered gate → rubric → verdict objective for the ask agent (spec §3.4).

Deterministic gates screen each sample for free; a configurable judged rubric
scores what survives; the weighted verdict feeds the fitness ladder. Sample
level results persist to an append-only sidecar so every low score is
inspectable and every rerun resumes for free.
"""

from pydocs_eval.optimize.rubric.gates import evaluate_gate, gate_registry
from pydocs_eval.optimize.rubric.judge import (
    ConfigurableRubricJudge,
    FakeRubricJudge,
    RubricJudge,
    RubricVerdict,
)
from pydocs_eval.optimize.rubric.model import (
    GateCheck,
    RubricConfig,
    RubricCriterion,
    SampleRubricRecord,
    rubric_config_hash,
    validate_rubric_config,
)
from pydocs_eval.optimize.rubric.sample_ledger import SampleRubricLedger

__all__ = [
    "ConfigurableRubricJudge",
    "FakeRubricJudge",
    "GateCheck",
    "RubricConfig",
    "RubricCriterion",
    "RubricJudge",
    "RubricVerdict",
    "SampleRubricLedger",
    "SampleRubricRecord",
    "evaluate_gate",
    "gate_registry",
    "rubric_config_hash",
    "validate_rubric_config",
]
