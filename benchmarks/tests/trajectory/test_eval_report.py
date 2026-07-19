"""Eval-report parser tests — both dialects, strict re-derived resolve,
truncated-id matching, and infra/apply-failure classification (ADR 0012)."""

from __future__ import annotations

import pytest

from pydocs_eval.trajectory.eval_report import (
    GroundTruthOutcome,
    classify_infra_marker,
    infra_outcome,
    normalize_test_name,
    outcome_from_report,
    patch_apply_failed_outcome,
)


def _mainline(instance_id, f2p_ok, f2p_bad, p2p_ok, p2p_bad, applied=True, resolved=True):
    return {
        instance_id: {
            "patch_is_None": False,
            "patch_exists": True,
            "patch_successfully_applied": applied,
            "resolved": resolved,
            "tests_status": {
                "FAIL_TO_PASS": {"success": f2p_ok, "failure": f2p_bad},
                "PASS_TO_PASS": {"success": p2p_ok, "failure": p2p_bad},
                "FAIL_TO_FAIL": {"success": [], "failure": []},
                "PASS_TO_FAIL": {"success": [], "failure": []},
            },
        }
    }


def _live(instance_id, f2p_ok, f2p_bad, p2p_ok, p2p_bad, resolved=False):
    return {
        "instance_id": instance_id,
        "resolved": resolved,
        "FAIL_TO_PASS": {"success": f2p_ok, "failure": f2p_bad},
        "PASS_TO_PASS": {"success": p2p_ok, "failure": p2p_bad},
    }


def test_mainline_dialect_resolves():
    report = _mainline("r-1", ["t::a"], [], ["t::b"], [])
    out = outcome_from_report("r-1", report, gold_f2p=["t::a"], gold_p2p=["t::b"])
    assert out.resolved is True
    assert out.patch_applied is True
    assert out.upstream_resolved is True


def test_live_flat_dialect_resolves():
    report = _live("r-1", ["t::a"], [], ["t::b"], [], resolved=True)
    out = outcome_from_report("r-1", report, gold_f2p=["t::a"], gold_p2p=["t::b"])
    assert out.resolved is True
    assert out.patch_applied is True


def test_missing_p2p_counts_as_failed_even_when_live_says_resolved():
    # Live's flat report omits a P2P test entirely and calls the run resolved;
    # our strict re-derivation counts the missing gold P2P as failed → NOT resolved.
    report = _live("r-1", ["t::a"], [], ["t::b"], [], resolved=True)
    out = outcome_from_report("r-1", report, gold_f2p=["t::a"], gold_p2p=["t::b", "t::c"])
    assert out.resolved is False
    assert out.upstream_resolved is True  # recorded but not trusted


def test_f2p_failure_not_resolved():
    report = _mainline("r-1", [], ["t::a"], ["t::b"], [], resolved=False)
    out = outcome_from_report("r-1", report, gold_f2p=["t::a"], gold_p2p=["t::b"])
    assert out.resolved is False


def test_empty_gold_lists_resolve_vacuously():
    report = _mainline("r-1", [], [], [], [])
    out = outcome_from_report("r-1", report, gold_f2p=[], gold_p2p=[])
    assert out.resolved is True


def test_truncated_parametrized_id_matches_gold():
    # The report stores the harness-truncated form; the gold list carries the
    # full space-containing id. Normalization truncates the gold side to match.
    truncated = "test/x.py::test_validate[Invalid"
    report = _mainline("r-1", [truncated], [], [], [])
    out = outcome_from_report(
        "r-1", report, gold_f2p=["test/x.py::test_validate[Invalid foo]"], gold_p2p=[]
    )
    assert out.resolved is True


def test_normalize_test_name_truncates_at_space():
    assert normalize_test_name("a.py::x[Invalid foo]") == "a.py::x[Invalid"
    assert normalize_test_name("a.py::x") == "a.py::x"
    assert normalize_test_name("") == ""


def test_unrecognized_dialect_raises():
    with pytest.raises(Exception, match="dialect"):
        outcome_from_report("r-1", {"nonsense": 1}, gold_f2p=[], gold_p2p=[])


def test_classify_apply_failure_marker():
    log = "some output\n>>>>> Patch Apply Failed\nmore"
    assert classify_infra_marker(log) == "patch_apply_failed"


def test_classify_infra_markers():
    assert classify_infra_marker(">>>>> Tests Timed Out") == "infra_error"
    assert classify_infra_marker(">>>>> Reset Failed") == "infra_error"
    assert classify_infra_marker("Timeout error: 900 seconds exceeded.") == "infra_error"


def test_classify_no_marker_returns_none():
    assert classify_infra_marker("all good, tests passed") is None


def test_apply_failure_precedence_over_infra_marker():
    # Both present — the apply-failure carve-out wins (model fault, not infra).
    log = ">>>>> Tests Errored\n>>>>> Patch Apply Failed"
    assert classify_infra_marker(log) == "patch_apply_failed"


def test_infra_outcome_flags():
    out = infra_outcome("r-1")
    assert out.infra_error is True
    assert out.patch_apply_failed is False
    assert out.resolved is False
    assert out.patch_applied is False


def test_patch_apply_failed_outcome_flags():
    out = patch_apply_failed_outcome("r-1")
    assert out.patch_apply_failed is True
    assert out.infra_error is False
    assert out.resolved is False


def test_outcome_is_frozen():
    out = infra_outcome("r-1")
    with pytest.raises(Exception):
        out.resolved = True  # type: ignore[misc]
