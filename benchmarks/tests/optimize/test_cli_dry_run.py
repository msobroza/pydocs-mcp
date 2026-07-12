"""The ``--dry-run`` preflight walks the whole pipeline spending nothing (plan
Task 11, spec §D5/§D7).

Drives ``cli_main`` on the shipped ``usage_skill`` config with ``--dry-run``:
the seed validates, the ladder is wired, split determinism is checked, the
adapters are importable, and one full orchestrator pass runs on a zero-cost
fake fitness — every step printed, ``$0.00`` spent. No subprocess, no socket,
no live LLM (the ``skillopt`` extra is absent, so its availability check is
reported SKIPPED, never required for a dry run).
"""

from __future__ import annotations

import re
from importlib.resources import files

from pydocs_eval.optimize.__main__ import _dry_provenance
from pydocs_eval.optimize.run_config import load_run_config
from pydocs_eval.optimize.rubric.model import rubric_config_hash
from pathlib import Path

from pydocs_eval.optimize.__main__ import cli_main


def _shipped(name: str) -> Path:
    return Path(str(files("pydocs_eval.optimize.configs").joinpath(name)))


async def test_dry_run_walks_pipeline_spending_nothing(tmp_path, capsys) -> None:
    code = await cli_main(
        [
            "--config",
            str(_shipped("optimize_usage_skill.yaml")),
            "--dry-run",
            "--ledger",
            str(tmp_path / "trials.jsonl"),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "DRY RUN" in out and "$0.00" in out
    # seed validated, ladder wired, split determinism checked, adapters importable —
    # all printed.
    for check in ("seed", "ladder", "split", "optimizer"):
        assert check in out.lower()


async def test_dry_run_covers_both_shipped_ask_configs(tmp_path, capsys) -> None:
    # AC-17: $0.00, full orchestrator pass, on each shipped ask config.
    for name in ("optimize_ask_prompt.yaml", "optimize_ask_architecture.yaml"):
        code = await cli_main(
            [
                "--config",
                str(_shipped(name)),
                "--dry-run",
                "--ledger",
                str(tmp_path / f"{name}.jsonl"),
            ]
        )
        out = capsys.readouterr().out
        assert code == 0 and "$0.00" in out, name
        assert "orchestrator pass" in out, name
        assert "ask binding" in out, name
        # AC-17: the pass runs the REAL AskRubricFitness on the scripted
        # fakes — both doubles must have actually been exercised.
        match = re.search(r"runner calls=(\d+), judge calls=(\d+)", out)
        assert match is not None, name
        assert int(match.group(1)) > 0 and int(match.group(2)) > 0, name


async def test_dry_run_reports_missing_ask_extra_as_skipped(tmp_path, capsys, monkeypatch) -> None:
    # AC-17: an absent [ask] extra is reported SKIPPED, never required.
    from pydocs_eval.optimize import ask_binding

    monkeypatch.setattr(ask_binding, "_ask_extra_missing_module", lambda: "langgraph")
    code = await cli_main(
        [
            "--config",
            str(_shipped("optimize_ask_prompt.yaml")),
            "--dry-run",
            "--ledger",
            str(tmp_path / "t.jsonl"),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "ask binding: SKIPPED (extra not installed)" in out


def test_dry_provenance_pins_the_rubric_hash() -> None:
    # AC-19: provenance.rubric_hash matches rubric_config_hash(config,
    # architecture=<pinned runner architecture>) whenever a rubric is configured.
    cfg = load_run_config(_shipped("optimize_ask_prompt.yaml"))
    seed_stub = type("S", (), {"fingerprint": "f" * 64, "name": "ask_prompt"})()
    provenance = _dry_provenance(cfg, seed_stub)
    assert provenance.rubric_hash == rubric_config_hash(
        cfg.ask_rubric.rubric_config, architecture=cfg.ask_rubric.runner.architecture
    )

    plain = load_run_config(_shipped("optimize_tool_docs.yaml"))
    assert _dry_provenance(plain, seed_stub).rubric_hash is None
