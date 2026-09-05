"""Optimizer loop dry-run (ADR 0018 §2) — the standing no-spend precondition gate.

The health check walks the WHOLE Phase 4 candidate loop with zero model spend
(mutation → validity firewall → render+hash → canned rollout → derived record →
gate → ledger entry). It is the standing precondition for any paid candidate
evaluation: the loop must render ``HEALTHY`` first.
"""

from pydocs_eval.optimize.preflight.health_check import (
    CannedRollout,
    PreflightResult,
    default_canned_rollout,
    default_rollout_dir,
    run_preflight,
    synthetic_mutation,
)
from pydocs_eval.optimize.preflight.report import render_preflight_report

__all__ = [
    "CannedRollout",
    "PreflightResult",
    "default_canned_rollout",
    "default_rollout_dir",
    "render_preflight_report",
    "run_preflight",
    "synthetic_mutation",
]
