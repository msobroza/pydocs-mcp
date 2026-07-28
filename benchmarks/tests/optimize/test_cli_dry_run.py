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

import json
import os
import re
import subprocess
import sys
import textwrap
from importlib.resources import files

import pydocs_eval
import pydocs_mcp

from pydocs_eval.optimize.arm_runtime import resolve_arms
from pydocs_eval.optimize.dry_run import dry_provenance
from pydocs_eval.optimize.ask_binding import ask_binding_identity
from pydocs_eval.optimize.run_config import load_run_config
from pydocs_eval.optimize.rubric.model import rubric_config_hash
from pathlib import Path

from pydocs_eval.optimize.__main__ import cli_main

_PROBE_PATH = (
    str(Path(pydocs_eval.__file__).resolve().parents[1]),
    str(Path(pydocs_mcp.__file__).resolve().parents[1]),
)


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
    # architecture=<pinned runner architecture>, binding_identity=<the ask
    # execution path's identity>) whenever a rubric is configured.
    cfg = load_run_config(_shipped("optimize_ask_prompt.yaml"))
    seed_stub = type("S", (), {"fingerprint": "f" * 64, "name": "ask_prompt"})()
    provenance = dry_provenance(cfg, seed_stub)
    assert provenance.rubric_hash == rubric_config_hash(
        cfg.ask_rubric.rubric_config,
        architecture=cfg.ask_rubric.runner.architecture,
        binding_identity=ask_binding_identity(),
    )

    plain = load_run_config(_shipped("optimize_tool_docs.yaml"))
    assert dry_provenance(plain, seed_stub).rubric_hash is None


class TestArmsDryRun:
    """A dry run over an ``arms:`` config walks EVERY arm, still spending $0.00."""

    async def _run(self, tmp_path, capsys) -> str:
        code = await cli_main(
            [
                "--config",
                str(_shipped("optimize_search_skill.yaml")),
                "--dry-run",
                "--ledger",
                str(tmp_path / "trials.jsonl"),
            ]
        )
        out = capsys.readouterr().out
        assert code == 0 and "$0.00" in out
        return out

    async def test_the_listing_names_every_arm_and_its_identity(self, tmp_path, capsys) -> None:
        out = await self._run(tmp_path, capsys)
        cfg = load_run_config(_shipped("optimize_search_skill.yaml"))
        assert "arms: 2 configured" in out
        for arm in resolve_arms(cfg):
            assert f"{arm.label} {arm.arm_hash[:12]}" in out
            assert arm.objective_hash[:12] in out
        # corpus, framing, bound surface and watched metrics — the four things
        # that make one arm's numbers different from another's.
        assert "dataset=crosscommitvuln@1.0" in out and "task_name=vuln" in out
        assert "tools=[all nine]" in out and "tools=[search_codebase,get_symbol,read_file]" in out
        assert "tracked=[gold_recall,cve_id_exact]" in out

    async def test_each_arm_runs_its_own_pass_and_neither_resumes_the_other(
        self, tmp_path, capsys
    ) -> None:
        await self._run(tmp_path, capsys)
        rows = [
            json.loads(line)
            for line in (tmp_path / "trials.samples.jsonl").read_text().splitlines()
        ]
        # 3 holdout tasks x 2 arms: the second arm re-ran instead of resuming.
        assert len(rows) == 6
        assert len({r["arm_hash"] for r in rows}) == 2
        # And the tracked metrics the config declares are MEASURED now.
        assert all(set(r["tracked"]) == {"gold_recall", "cve_id_exact"} for r in rows)

    async def test_the_two_arms_inspection_files_never_overwrite(self, tmp_path, capsys) -> None:
        await self._run(tmp_path, capsys)
        directories = sorted((tmp_path / "dry-run-samples" / "samples").iterdir())
        assert len(directories) == 2, [d.name for d in directories]

    async def _capture_passes(self, tmp_path, capsys, monkeypatch) -> list[dict]:
        """Every ``run_optimization`` call the arms walk makes, args captured."""
        from pydocs_eval.optimize import dry_run as dry_run_module

        seen: list[dict] = []
        real = dry_run_module.run_optimization

        async def _capture(seed, optimizer, ladder, budget, **kwargs):
            seen.append({"budget": budget, "guard": kwargs.get("guard")})
            return await real(seed, optimizer, ladder, budget, **kwargs)

        monkeypatch.setattr(dry_run_module, "run_optimization", _capture)
        await self._run(tmp_path, capsys)
        return seen

    async def test_every_arm_shares_one_budget_guard(self, tmp_path, capsys, monkeypatch) -> None:
        # The USD ceiling is a RUN pool and the guard is half of what enforces
        # it (the ledger is the other half): a fresh guard per arm resets its
        # cost estimate, so every arm after the first buys one more eval past
        # an already-exhausted cap.
        seen = await self._capture_passes(tmp_path, capsys, monkeypatch)
        assert len(seen) == 2
        assert seen[0]["guard"] is not None and seen[0]["guard"] is seen[1]["guard"]

    async def test_the_trial_ceiling_is_divided_across_arms(
        self, tmp_path, capsys, monkeypatch
    ) -> None:
        # max_trials has no shared enforcer — each optimizer consumes it per
        # optimize() call — so handing every arm the run-level number would
        # search N times the authorized trials.
        cfg = load_run_config(_shipped("optimize_search_skill.yaml"))
        seen = await self._capture_passes(tmp_path, capsys, monkeypatch)
        assert {pass_["budget"].max_trials for pass_ in seen} == {cfg.budget.max_trials // 2}
        # The pooled dimensions stay whole — they are enforced by shared objects.
        assert {pass_["budget"].max_usd for pass_ in seen} == {cfg.budget.max_usd}

    def test_the_dry_walk_never_imports_the_agent_runtime(self, tmp_path) -> None:
        # An arms config resolves every arm's identity and scores every arm on
        # scripted doubles — none of which may pull the harness RUNTIME in.
        probe = textwrap.dedent(
            f"""
            import asyncio, sys
            from pydocs_eval.optimize.__main__ import cli_main

            code = asyncio.run(cli_main([
                "--config", {str(_shipped("optimize_search_skill.yaml"))!r},
                "--dry-run", "--ledger", {str(tmp_path / "probe.jsonl")!r},
            ]))
            assert code == 0
            resident = [n for n in sys.modules if n == "langgraph" or n.startswith("langgraph.")]
            assert not resident, f"the dry run imported the agent runtime: {{resident}}"
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(_PROBE_PATH)},
        )
        assert completed.returncode == 0, completed.stderr


async def test_a_config_without_arms_keeps_the_single_implicit_arm(tmp_path, capsys) -> None:
    # The back-compat pin: no arms: block means no arm listing, one pass, and
    # ledger lines that carry the empty (single implicit) arm hash.
    code = await cli_main(
        [
            "--config",
            str(_shipped("optimize_ask_prompt.yaml")),
            "--dry-run",
            "--ledger",
            str(tmp_path / "trials.jsonl"),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0 and "arms:" not in out
    assert out.count("orchestrator pass") == 1
    rows = [
        json.loads(line) for line in (tmp_path / "trials.samples.jsonl").read_text().splitlines()
    ]
    # Both sidecars keep the exact pre-``arms:`` byte shape — the arm hash is a
    # ``.get``-tolerant sibling written only when an arm actually names one.
    assert rows and all("arm_hash" not in r and r["tracked"] == {} for r in rows)
    assert "arm_hash" not in json.loads((tmp_path / "trials.jsonl").read_text().splitlines()[0])


def test_dry_provenance_matches_the_fitness_objective_hash(tmp_path) -> None:
    # Run-contract design §8: provenance that omitted the binding identity
    # would name a DIFFERENT objective than the one the sample ledger keys on,
    # so a resumed run would look free while re-scoring everything.
    from pydocs_eval.optimize.dry_run import dry_ask_rubric_fitness

    cfg = load_run_config(_shipped("optimize_ask_prompt.yaml"))
    seed_stub = type("S", (), {"fingerprint": "f" * 64, "name": "ask_prompt"})()
    _runner, _judge, fitness = dry_ask_rubric_fitness(cfg, ledger_path=tmp_path / "t.jsonl")
    assert dry_provenance(cfg, seed_stub).rubric_hash == fitness.objective_hash()
