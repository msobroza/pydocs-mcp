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

from importlib.resources import files
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
