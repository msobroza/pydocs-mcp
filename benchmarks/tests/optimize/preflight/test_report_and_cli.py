"""Dry-run report determinism + CLI exit codes (ADR 0018 §2 precondition gate)."""

from __future__ import annotations

import functools
from pathlib import Path

import pytest

from pydocs_eval.optimize.preflight import __main__ as preflight_main
from pydocs_eval.optimize.preflight.__main__ import main
from pydocs_eval.optimize.preflight.health_check import default_rollout_dir, run_preflight
from pydocs_eval.optimize.preflight.report import render_preflight_report

_RESOLVED_FIXTURE = Path(__file__).parents[2] / "trajectory/fixtures/run_dir/resolved"


def _rollout() -> Path:
    return _RESOLVED_FIXTURE


def test_report_says_healthy_and_lists_all_legs(tmp_path: Path) -> None:
    """The report headlines HEALTHY and names all eight loop legs."""
    result = run_preflight(rollout_fn=_rollout, workspace=tmp_path)
    text = render_preflight_report(result)
    assert "verdict: HEALTHY" in text
    for leg in (
        "1. mutation",
        "2. validity",
        "3. render+hash",
        "4. rollout",
        "5. derived",
        "6. minibatch",
        "7. gate",
        "8. ledger",
    ):
        assert leg in text


def test_report_is_byte_stable_across_fresh_runs(tmp_path: Path) -> None:
    """Two fresh-workspace runs render an identical report (recomputable gate)."""
    a = render_preflight_report(run_preflight(rollout_fn=_rollout, workspace=tmp_path / "a"))
    b = render_preflight_report(run_preflight(rollout_fn=_rollout, workspace=tmp_path / "b"))
    assert a == b


def test_cli_exit_zero_and_healthy(tmp_path: Path, capsys) -> None:
    """The CLI exits 0 over the default fixture and prints HEALTHY."""
    assert main(["--workspace", str(tmp_path)]) == 0
    assert "HEALTHY" in capsys.readouterr().out


def test_cli_bad_rollout_dir_exits_two(tmp_path: Path, capsys) -> None:
    """A missing rollout dir is exit 2, not a traceback."""
    assert main(["--rollout-dir", str(tmp_path / "nope"), "--workspace", str(tmp_path)]) == 2
    assert "error:" in capsys.readouterr().err


def test_help_says_default_fixture_needs_a_source_checkout(capsys) -> None:
    """--help warns that the default fixture exists only in a source checkout."""
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "only in a source checkout" in " ".join(capsys.readouterr().out.split())


def test_missing_default_fixture_names_path_and_flag(tmp_path: Path, capsys, monkeypatch) -> None:
    """From an installed wheel the default fixture is absent: exit 2 naming it + --rollout-dir."""
    site_packages_module = tmp_path / "site-packages/pydocs_eval/optimize/preflight/health_check.py"
    monkeypatch.setattr(
        preflight_main,
        "default_rollout_dir",
        functools.partial(default_rollout_dir, anchor=site_packages_module),
    )
    assert main(["--workspace", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert "benchmarks/tests/trajectory/fixtures/run_dir/resolved" in err
    assert "--rollout-dir" in err
