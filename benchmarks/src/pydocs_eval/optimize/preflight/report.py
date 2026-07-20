"""Human-readable dry-run health-check report (ADR 0018 §2 precondition gate).

Deterministic (sorted, fixed precision) so a delete+rerun is byte-identical — the
report is the artifact the standing gate is read from, and the paid arc's
precondition is that it renders ``HEALTHY``.
"""

from __future__ import annotations

from pydocs_eval.optimize.preflight.health_check import PreflightResult

__all__ = ["render_preflight_report"]


def render_preflight_report(result: PreflightResult) -> str:
    """Render each loop leg as a scannable PASS/verdict line; ends with the gate verdict."""
    lines = [
        "pydocs-eval optimizer dry-run — loop health check (ADR 0018 §2)",
        f"verdict: {'HEALTHY' if result.ok else 'BROKEN'}",
        "",
        *_leg_lines(result),
    ]
    return "\n".join(lines) + "\n"


def _leg_lines(result: PreflightResult) -> list[str]:
    return [
        f"1. mutation      component={result.mutated_component} "
        f"seed={result.seed_hash[:12]} -> mutated={result.mutated_hash[:12]}",
        f"2. validity      valid={result.validity.valid} "
        f"violations={list(result.validity.violations)}",
        f"3. render+hash   {result.rendered_bytes} bytes  hash={result.mutated_hash[:12]}",
        "4. rollout       canned (offline widgetlib fixture; paid arc = live capture)",
        f"5. derived       hard={result.derived.hard} soft={result.derived.soft:.4f} "
        f"label={result.derived.label} cost=${result.derived.cost_usd:.4f}",
        f"6. minibatch     filter={result.filter_decision.value} "
        "(canned shaped scores; campaign m_mb is [TO BE MEASURED])",
        f"7. gate          resolve_rate={result.gate.resolve_rate:.4f} "
        f"n_graded={result.gate.n_graded} within_budget={result.gate.within_budget}",
        f"8. ledger        {result.ledger_path.name}  entry={result.record.candidate_hash[:12]} "
        f"n_rollouts={result.record.n_rollouts}",
    ]
