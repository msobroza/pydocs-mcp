"""``pydocs-eval-prereg`` CLI: packaged load, refusal exit code, power preview."""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.optimize.prereg.__main__ import main, packaged_preregistration_path


def test_packaged_path_resolves() -> None:
    assert packaged_preregistration_path().is_file()


def test_default_run_prints_hash_verdict_and_table(capsys) -> None:
    """No args: registration hash + BLOCKED verdict + the power table, exit 0."""
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "registration_hash:" in out
    assert "launch: BLOCKED" in out
    assert "0.9822" in out  # the power table is rendered


def test_authorize_blocks_while_unfilled(capsys) -> None:
    """--authorize exits 3 while measured slots are null, naming them on stderr."""
    assert main(["--authorize"]) == 3
    assert "pi_d" in capsys.readouterr().err


def test_bad_config_exits_two(tmp_path: Path, capsys) -> None:
    """An unreadable config is exit 2, not a traceback."""
    assert main(["--config", str(tmp_path / "nope.yaml")]) == 2
    assert "error:" in capsys.readouterr().err


def test_authorize_zero_when_filled(tmp_path: Path, capsys) -> None:
    """A fully-measured registration authorizes with exit 0."""
    src = packaged_preregistration_path().read_text(encoding="utf-8")
    filled = (
        src.replace("pi_d: null", "pi_d: 0.20")
        .replace("cost_rollout: null", "cost_rollout: 0.40")
        .replace("m_mb: null", "m_mb: 0.02")
        .replace("c_sel: null", "c_sel: 1.50")
        .replace("confirmed_target: null", "confirmed_target: 0.05")
        .replace("g_gate_evals: null", "g_gate_evals: 10")
        .replace("minibatch_panel_instance_ids: null", 'minibatch_panel_instance_ids: ["p1"]')
    )
    path = tmp_path / "filled.yaml"
    path.write_text(filled, encoding="utf-8")
    assert main(["--config", str(path), "--authorize"]) == 0
    assert "launch authorized" in capsys.readouterr().out
