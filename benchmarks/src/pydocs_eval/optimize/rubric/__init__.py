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

# WHY this import exists at all: ``check_registry`` is constructed with no
# populate callback, so a check kind reaches it ONLY by its module being
# imported. Every other kind lives in ``checks`` itself (which ``arm_scoring``
# imports); the trajectory-grounded ones live in their own module and would
# otherwise be silently absent — every ``tracked:`` / ``checks:`` reference to
# them failing at load time with "unknown check kind".
from pydocs_eval.optimize.rubric.trajectory_evidence import GoldLocationEvidenced

__all__ = [
    "ConfigurableRubricJudge",
    "FakeRubricJudge",
    "GateCheck",
    "GoldLocationEvidenced",
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
