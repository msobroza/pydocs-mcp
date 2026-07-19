"""Dual-dialect eval-report parser + the ``GroundTruthOutcome`` factory
(ADR 0012 — "eval-report parser and degenerate cases", gate-isolation lock 1).

Reads both verified SWE-bench report dialects — mainline/fork keyed reports with
``tests_status`` (``swebench/harness/grading.py``) and SWE-bench-Live-current
flat reports (``evaluation/evaluation.py``) — and **re-derives strict resolve
semantics from the per-test lists itself**: ``resolved`` ⇔ every gold F2P name
passed AND every gold P2P name passed, where a P2P name absent from the
success list counts as **failed** (matching mainline ``grading.py:31-35``). The
upstream ``resolved`` flag is recorded but never trusted, because the two
dialects disagree on missing tests.

``GroundTruthOutcome`` is the frozen ground-truth record the D4 gate consumes;
this module owns its **sole factory** (gate-isolation lock 1 — no constructor
path accepts trace metrics or shaped scores). Infra failures are classified
per the marker strings + missing-report + ``error_ids``, with the apply-failure
marker carved out to ``patch_apply_failed`` (a **model** failure, in
aggregates), not ``infra_error``.

Test-name matching uses the harness's ``line.split()[1]`` normalization
(here: first whitespace token of a bare name), because 5.8% of F2P entries are
space-truncated parametrized ids (``…::test_validate[Invalid``) — names are
never treated as runnable pytest node ids.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydocs_eval.trajectory.schema import TrajectorySchemaError

# Marker strings the mainline grader writes on infra / apply failures
# (``swebench/harness/constants/__init__.py:80-91``). The apply-failure marker
# is carved out to ``patch_apply_failed`` (model fault, ADR 0012); the rest are
# genuine infrastructure failures.
APPLY_PATCH_FAIL = ">>>>> Patch Apply Failed"
_INFRA_MARKERS = (
    ">>>>> Reset Failed",
    ">>>>> Tests Errored",
    ">>>>> Tests Timed Out",
    "Timeout error:",
)


@dataclass(frozen=True, slots=True)
class GroundTruthOutcome:
    """The ground-truth eval facts for one instance (ADR 0012 gate input).

    Constructed ONLY by this module's factories. ``resolved`` is our strict
    re-derived value; ``upstream_resolved`` is the report's own flag, recorded
    but not trusted. ``infra_error`` and ``patch_apply_failed`` are mutually
    exclusive causes of a non-graded run.
    """

    instance_id: str
    resolved: bool
    patch_applied: bool
    infra_error: bool
    patch_apply_failed: bool
    f2p_passed: frozenset[str]
    f2p_failed: frozenset[str]
    p2p_passed: frozenset[str]
    p2p_failed: frozenset[str]
    upstream_resolved: bool | None


def normalize_test_name(name: str) -> str:
    """Normalize a test name the way the harness log parser truncates it.

    The harness does ``line.split()`` and keeps token ``[1]`` on a status line;
    on a bare name the equivalent is the first whitespace token, which truncates
    a space-containing parametrized id (``…[Invalid foo]`` → ``…[Invalid``) so a
    gold name matches the harness's already-truncated stored form.

    Example:
        >>> normalize_test_name("t.py::x[Invalid foo]")
        't.py::x[Invalid'
    """
    parts = name.split()
    return parts[0] if parts else ""


def _norm_set(names: Iterable[str]) -> frozenset[str]:
    """Normalize a name collection to a set for subset comparison."""
    return frozenset(normalize_test_name(n) for n in names)


def _derive_resolved(
    gold_f2p: frozenset[str],
    gold_p2p: frozenset[str],
    f2p_passed: frozenset[str],
    p2p_passed: frozenset[str],
) -> bool:
    """Strict resolve: every gold F2P AND every gold P2P is in the passed set.

    A missing name (in neither passed nor failed) is not in ``passed`` → not a
    subset → not resolved, matching mainline "missing P2P counts as failed".
    """
    return gold_f2p <= f2p_passed and gold_p2p <= p2p_passed


def _status_sets(status: dict[str, Any], key: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(success, failure)`` name sets for one ``tests_status`` key."""
    entry = status.get(key) or {}
    return _norm_set(entry.get("success", ())), _norm_set(entry.get("failure", ()))


def _detect_dialect(instance_id: str, report: dict[str, Any]) -> dict[str, Any]:
    """Return the per-instance ``tests_status``-bearing block for either dialect.

    Mainline keys the block under ``instance_id`` with a ``tests_status``
    wrapper; Live-current is flat with top-level ``FAIL_TO_PASS``. Anything else
    raises with the offending shape.
    """
    keyed = report.get(instance_id)
    if isinstance(keyed, dict) and "tests_status" in keyed:
        return {
            "applied": keyed.get("patch_successfully_applied", False),
            "upstream": keyed.get("resolved"),
            "status": keyed["tests_status"],
        }
    if "FAIL_TO_PASS" in report:
        return {
            "applied": report.get("resolved") is not None,
            "upstream": report.get("resolved"),
            "status": report,
        }
    raise TrajectorySchemaError(
        f"unrecognized eval-report dialect for {instance_id!r}: got keys "
        f"{sorted(report)!r}; expected a mainline keyed report or a Live flat report"
    )


def outcome_from_report(
    instance_id: str,
    report: dict[str, Any],
    *,
    gold_f2p: Iterable[str],
    gold_p2p: Iterable[str],
) -> GroundTruthOutcome:
    """Build a :class:`GroundTruthOutcome` from a parsed per-instance report.

    ``gold_f2p`` / ``gold_p2p`` are the instance's gold test-name lists (needed
    to detect missing tests for strict resolve). Both dialects are auto-detected.
    """
    block = _detect_dialect(instance_id, report)
    f2p_pass, f2p_fail = _status_sets(block["status"], "FAIL_TO_PASS")
    p2p_pass, p2p_fail = _status_sets(block["status"], "PASS_TO_PASS")
    resolved = _derive_resolved(_norm_set(gold_f2p), _norm_set(gold_p2p), f2p_pass, p2p_pass)
    return GroundTruthOutcome(
        instance_id=instance_id,
        resolved=resolved,
        patch_applied=bool(block["applied"]),
        infra_error=False,
        patch_apply_failed=False,
        f2p_passed=f2p_pass,
        f2p_failed=f2p_fail,
        p2p_passed=p2p_pass,
        p2p_failed=p2p_fail,
        upstream_resolved=block["upstream"],
    )


def classify_infra_marker(log_text: str) -> str | None:
    """Classify infra markers in a run/test log (ADR 0012 marker carve-out).

    Returns ``"patch_apply_failed"`` for the apply-failure marker (a model
    failure), ``"infra_error"`` for the reset/errored/timeout markers, or
    ``None`` when no marker is present.
    """
    if APPLY_PATCH_FAIL in log_text:
        return "patch_apply_failed"
    if any(marker in log_text for marker in _INFRA_MARKERS):
        return "infra_error"
    return None


def infra_outcome(instance_id: str) -> GroundTruthOutcome:
    """Factory for an infrastructure failure (missing/unparseable report,
    ``error_ids`` membership, or an infra marker) — excluded from aggregates."""
    return _degenerate(instance_id, infra_error=True, patch_apply_failed=False)


def patch_apply_failed_outcome(instance_id: str) -> GroundTruthOutcome:
    """Factory for a model-authored apply failure — a task failure (``hard=0``),
    included in aggregates (ADR 0012 carve-out from ``infra_error``)."""
    return _degenerate(instance_id, infra_error=False, patch_apply_failed=True)


def _degenerate(
    instance_id: str, *, infra_error: bool, patch_apply_failed: bool
) -> GroundTruthOutcome:
    """Build an unresolved, un-applied outcome for a degenerate (non-graded) run."""
    empty: frozenset[str] = frozenset()
    return GroundTruthOutcome(
        instance_id=instance_id,
        resolved=False,
        patch_applied=False,
        infra_error=infra_error,
        patch_apply_failed=patch_apply_failed,
        f2p_passed=empty,
        f2p_failed=empty,
        p2p_passed=empty,
        p2p_failed=empty,
        upstream_resolved=None,
    )
