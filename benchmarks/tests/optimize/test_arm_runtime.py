"""The orchestrator consuming ``arms:`` — per-arm construction + bookkeeping (design §6).

Four surfaces are pinned here: each arm's fitness is built from its OWN cell
(objective, tracked observations, task framing, arm hash), the harness runner
factory resolves LAZILY (a dry run over an arms config never imports the agent
runtime), an arm's ``dataset`` resolves through the one dataset registry, and a
config without ``arms:`` keeps its single implicit arm unchanged.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from dataclasses import replace
from importlib.resources import files
from pathlib import Path

import pytest

import pydocs_eval
import pydocs_mcp

from pydocs_eval.optimize.arm_runtime import (
    _SENTINEL_MAX_AGENT_TURNS,
    arm_runner_factory,
    arm_runner_settings,
    build_arm_fitness,
    resolve_arm_dataset,
    resolve_arms,
)
from pydocs_eval.optimize.arms import ArmCell
from pydocs_eval.optimize.fitness.ask_rubric import JudgeCallCounter
from pydocs_eval.optimize.rubric.gates import _DEFAULT_MAX_TURNS as _GATE_DEFAULT_MAX_TURNS
from pydocs_eval.optimize.rubric.judge import FakeRubricJudge
from pydocs_eval.optimize.rubric.sample_ledger import SampleRubricLedger
from pydocs_eval.optimize.run_config import arm_objective_hash, load_run_config

_PROBE_PATH = (
    str(Path(pydocs_eval.__file__).resolve().parents[1]),
    str(Path(pydocs_mcp.__file__).resolve().parents[1]),
)
_ASK_RUNNER = "pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner"


def _shipped(name: str) -> Path:
    return Path(str(files("pydocs_eval.optimize.configs").joinpath(name)))


@pytest.fixture
def arms_cfg():
    return load_run_config(_shipped("optimize_search_skill.yaml"))


def _with_settings(cfg, settings: dict[str, object]):
    """The config's first resolved arm, with its opaque settings replaced."""
    arm = resolve_arms(cfg)[0]
    return replace(arm, cell=ArmCell(**{**arm.cell.model_dump(), "settings": settings}))


class TestResolveArms:
    def test_every_arm_gets_a_distinct_identity(self, arms_cfg) -> None:
        arms = resolve_arms(arms_cfg)
        assert [a.label for a in arms] == ["arms[0]", "arms[1]"]
        assert len({a.arm_hash for a in arms}) == 2
        assert all(len(a.arm_hash) == 64 for a in arms)

    def test_arms_sharing_one_objective_share_its_hash(self, arms_cfg) -> None:
        # The two shipped arms differ ONLY in tool_names, so they are the exact
        # collision the arm-keyed ledgers exist to separate: same objective,
        # same candidate, same tasks — different measurement.
        arms = resolve_arms(arms_cfg)
        assert arms[0].objective_hash == arms[1].objective_hash
        assert arms[0].objective_hash == arm_objective_hash(arms_cfg, arms_cfg.arms[0])

    def test_a_config_without_arms_resolves_to_none(self) -> None:
        assert resolve_arms(load_run_config(_shipped("optimize_tool_docs.yaml"))) == ()

    def test_the_resolved_section_is_the_one_the_arm_names(self, arms_cfg) -> None:
        arm = resolve_arms(arms_cfg)[0]
        assert arm.rubric_settings is arms_cfg.ask_rubric
        assert arm.rubric_settings.runner.architecture == "text_react"


class TestPerArmFitness:
    def _fitness(self, cfg, index: int, tmp_path: Path):
        arm = resolve_arms(cfg)[index]
        return arm, build_arm_fitness(
            arm,
            cfg=cfg,
            dataset=object(),  # type: ignore[arg-type]  # never drained here
            runner_factory=lambda artifact: object(),  # type: ignore[arg-type,return-value]
            judge=FakeRubricJudge(scripted={}),
            sample_ledger=SampleRubricLedger(tmp_path / "samples.jsonl"),
            output_dir=tmp_path,
            judge_calls=JudgeCallCounter(),
        )

    def test_the_arms_own_cell_reaches_its_fitness(self, arms_cfg, tmp_path: Path) -> None:
        arm, fitness = self._fitness(arms_cfg, 0, tmp_path)
        assert fitness.tracked_metrics == ("gold_recall", "cve_id_exact")
        assert fitness.task_name == "vuln"
        assert fitness.arm_hash == arm.arm_hash
        assert fitness.rubric == arms_cfg.ask_rubric.rubric_config
        assert fitness.objective_hash() == arm.objective_hash

    def test_the_judge_call_ceiling_is_one_run_level_pool(self, arms_cfg, tmp_path: Path) -> None:
        # A per-arm counter would silently buy N x max_judge_calls; the shared
        # object is what keeps the ceiling the number the config states.
        shared = JudgeCallCounter()
        arms = resolve_arms(arms_cfg)
        built = [
            build_arm_fitness(
                arm,
                cfg=arms_cfg,
                dataset=object(),  # type: ignore[arg-type]
                runner_factory=lambda artifact: object(),  # type: ignore[arg-type,return-value]
                judge=FakeRubricJudge(scripted={}),
                sample_ledger=SampleRubricLedger(tmp_path / "samples.jsonl"),
                output_dir=tmp_path,
                judge_calls=shared,
            )
            for arm in arms
        ]
        built[0].judge_calls.calls += 1
        assert built[1].judge_calls.calls == 1
        assert {f.max_judge_calls for f in built} == {arms_cfg.budget.max_judge_calls}


class TestRunnerBinding:
    def test_settings_carry_the_arms_tool_surface_and_run_level_fills(self, arms_cfg) -> None:
        narrowed = arm_runner_settings(resolve_arms(arms_cfg)[1])
        assert narrowed["tool_names"] == ["search_codebase", "get_symbol", "read_file"]
        assert narrowed["workspace"] == "~/pydocs-index"
        # Filled in from the section the arm binds, not hardcoded here.
        assert narrowed["architecture"] == arms_cfg.ask_rubric.runner.architecture
        assert narrowed["trace_root"] == arms_cfg.ask_rubric.runner.trace_root

    def test_the_full_surface_arm_says_so_explicitly(self, arms_cfg) -> None:
        assert arm_runner_settings(resolve_arms(arms_cfg)[0])["tool_names"] is None

    def test_an_explicit_arm_setting_is_never_overridden(self, arms_cfg) -> None:
        # A key that carries no identity is the arm's to set.
        arm = _with_settings(arms_cfg, {"trace_root": "/tmp/arm-traces"})
        assert arm_runner_settings(arm)["trace_root"] == "/tmp/arm-traces"

    def test_the_required_harness_keys_are_filled_from_the_bound_section(self, arms_cfg) -> None:
        # The product settings REQUIRE model + workspace; an arm that omits
        # them must not die at its first rollout, after earlier arms spent.
        settings = arm_runner_settings(_with_settings(arms_cfg, {}))
        assert settings["model"] == arms_cfg.ask_rubric.runner.model
        assert settings["workspace"] == str(arms_cfg.ask_rubric.runner.workspace)

    def test_tool_names_inside_settings_names_the_offending_value(self, arms_cfg) -> None:
        arm = _with_settings(arms_cfg, {"tool_names": ["read_file"]})
        with pytest.raises(ValueError, match="read_file"):
            arm_runner_settings(arm)

    def test_architecture_inside_settings_is_refused(self, arms_cfg) -> None:
        # The objective hash folds the SECTION's architecture, so an arm that
        # ran a different graph through settings would record every row under
        # an objective naming a graph that never answered.
        arm = _with_settings(arms_cfg, {"architecture": "inline"})
        with pytest.raises(ValueError, match="inline"):
            arm_runner_settings(arm)

    def test_the_bound_graph_is_always_the_one_the_objective_hash_names(self, arms_cfg) -> None:
        for arm in resolve_arms(arms_cfg):
            assert (
                arm_runner_settings(arm)["architecture"] == arm.rubric_settings.runner.architecture
            )

    def test_the_timeout_sentinel_always_trips_the_max_turns_gate(self) -> None:
        # The sentinel a timed-out rollout reports (cap + 1) must EXCEED the
        # gate threshold it is meant to fail; re-typing either number is how a
        # never-finished run gets scored as if it completed.
        assert _SENTINEL_MAX_AGENT_TURNS + 1 > _GATE_DEFAULT_MAX_TURNS

    def test_the_turn_cap_is_stamped_so_both_sides_read_one_number(self, arms_cfg) -> None:
        # Harnesses default the cap differently (12 in-process, 40 for the CLI
        # track). An arm that spells none must not run at the HARNESS default
        # while the sentinel reports the EVAL default + 1: a sentinel below the
        # real cap lets a turn-capped rollout PASS max_turns and score as if it
        # had finished. Stamping removes the second source.
        for arm in resolve_arms(arms_cfg):
            assert arm_runner_settings(arm)["max_agent_turns"] == _SENTINEL_MAX_AGENT_TURNS

    def test_an_arms_own_turn_cap_is_what_the_sentinel_reads(self, arms_cfg) -> None:
        arm = _with_settings(arms_cfg, {"max_agent_turns": 40})
        assert arm_runner_settings(arm)["max_agent_turns"] == 40

    def test_calling_the_factory_hands_the_arms_settings_to_the_harness(
        self, arms_cfg, monkeypatch
    ) -> None:
        # The arm's opaque mapping travels UNINSPECTED to the harness factory
        # (which owns and validates its keys) — including the tool narrowing
        # that is the whole point of the second shipped arm.
        product = pytest.importorskip("pydocs_mcp.harness.ask_your_docs.binding")
        captured: dict[str, object] = {}
        monkeypatch.setattr(product, "make_harness_runner", lambda s: captured.update(s))

        arm = resolve_arms(arms_cfg)[1]
        runner = arm_runner_factory(arm)(object())  # type: ignore[arg-type]
        assert captured["tool_names"] == ["search_codebase", "get_symbol", "read_file"]
        assert captured["model"] == "claude-sonnet-5"
        assert runner.task_timeout_seconds == arms_cfg.ask_rubric.runner.task_timeout_seconds

    def test_building_the_factory_never_resolves_the_harness(self, arms_cfg, monkeypatch) -> None:
        # The residency probe below cannot see this: ``resolve_arms``
        # legitimately imports the binding MODULE for its delivery-map digest,
        # so "the module is absent" is false even on a correct build. What must
        # stay true is that BUILDING a factory never RESOLVES the runner — that
        # call raises without the [ask] extra, and the paid path builds one
        # factory per arm before it spends a cent.
        from pydocs_eval.optimize import ask_binding

        def _refuse(dotted_path: str) -> None:
            raise RuntimeError(f"[ask] extra absent: {dotted_path}")

        monkeypatch.setattr(ask_binding, "resolve_harness_runner_factory", _refuse)
        factory = arm_runner_factory(resolve_arms(arms_cfg)[0])
        with pytest.raises(RuntimeError, match=r"\[ask\] extra absent"):
            factory(object())  # type: ignore[arg-type]

    def test_building_the_factory_imports_no_harness_runtime(self) -> None:
        # The laziness the ``arms:`` contract promises, proved by ABSENCE in a
        # FRESH interpreter: a run may be fully DESCRIBED (and its arm hashes
        # minted) on a machine that cannot run the agent at all.
        probe = textwrap.dedent(
            f"""
            import sys
            from pathlib import Path
            from pydocs_eval.optimize.arm_runtime import arm_runner_factory, resolve_arms
            from pydocs_eval.optimize.run_config import load_run_config

            cfg = load_run_config(Path({str(_shipped("optimize_search_skill.yaml"))!r}))
            arms = resolve_arms(cfg)
            assert len(arms) == 2
            for arm in arms:
                arm_runner_factory(arm)
            resident = [n for n in sys.modules if n == "langgraph" or n.startswith("langgraph.")]
            assert not resident, f"building an arm factory imported the runtime: {{resident}}"
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


class TestArmDataset:
    def test_the_cell_name_resolves_through_the_dataset_registry(self, arms_cfg) -> None:
        dataset = resolve_arm_dataset(arms_cfg.arms[0])
        assert dataset.name == "crosscommitvuln"

    def test_an_unregistered_dataset_names_the_offending_value(self) -> None:
        cell = ArmCell(
            runner=_ASK_RUNNER,
            settings={},
            tool_names=None,
            dataset="ccv",  # the task-id PREFIX, not a registered dataset
            task_name="vuln",
            guidance="search_skill",
            scoring={"objective": "rubric_verdict", "rubric": "ask_rubric"},
        )
        with pytest.raises(KeyError, match="ccv"):
            resolve_arm_dataset(cell)
