"""Typed run-config loading + registry-key validation (plan Task 11, spec §D7).

Both shipped YAMLs must load into the typed ``OptimizeRunConfig`` with the
spec's canonical shape (artifact, ladder rungs, accept margin, judge-parity
floor), and an unknown registry key in the YAML must fail loud at load time —
byte-identical names are the §D7 contract, so a typo like ``gradient_descent``
is a config error, not a silent no-op. No subprocess, no network, no live LLM.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from importlib.resources import files
from pathlib import Path

import pytest
from pydantic import ValidationError

import pydocs_eval
import pydocs_mcp

from pydocs_eval.optimize.__main__ import _dry_ask_rubric_fitness
from pydocs_eval.optimize.arm_scoring import ObjectiveKind
from pydocs_eval.optimize.run_config import arm_objective_hash, load_run_config

# The roots a probe subprocess needs on PYTHONPATH — derived from the imported
# packages so the pins work regardless of how pytest was invoked (the
# ``test_registry_population`` idiom).
_PROBE_PATH = (
    str(Path(pydocs_eval.__file__).resolve().parents[1]),
    str(Path(pydocs_mcp.__file__).resolve().parents[1]),
)


def _shipped(name: str) -> Path:
    """Resolve a shipped ``optimize/configs/<name>`` YAML to a real filesystem path."""
    return Path(str(files("pydocs_eval.optimize.configs").joinpath(name)))


def test_both_shipped_configs_load_typed() -> None:
    for name in ("optimize_tool_docs.yaml", "optimize_usage_skill.yaml"):
        cfg = load_run_config(_shipped(name))
        assert cfg.artifact in ("tool_docs", "usage_skill")
        assert cfg.ladder.rungs[0].fitness_name == "paired_agent"
        assert cfg.accept_margin == pytest.approx(0.02)
        assert cfg.fitness.judge_parity_floor == pytest.approx(-0.25)


def test_unknown_registry_key_is_a_clear_error(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _shipped("optimize_tool_docs.yaml")
        .read_text()
        .replace("critique_refine", "gradient_descent")
    )
    with pytest.raises(KeyError, match="gradient_descent"):
        load_run_config(bad)


_ASK_RUBRIC_YAML = """
artifact: tool_docs
optimizer: critique_refine
ladder:
  - [paired_agent, 12, 1]
ask_rubric:
  runner:
    model: claude-sonnet-5
    architecture: text_react
  gates:
    - {name: non_empty, kind: min_answer_chars, params: {n: 40}}
  criteria:
    - {name: correctness, weight: 0.6, description: "Factually correct."}
    - {name: grounding, weight: 0.4, description: "Traceable."}
  fail_fast: true
  gate_weight: 0.3
  rubric_weight: 0.7
budget:
  max_trials: 5
  max_judge_calls: 50
rng_seed: 7
"""


def _write(tmp_path, text: str) -> Path:
    path = tmp_path / "cfg.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_ask_rubric_section_loads_typed(tmp_path) -> None:
    cfg = load_run_config(_write(tmp_path, _ASK_RUBRIC_YAML))
    assert cfg.ask_rubric is not None
    assert cfg.ask_rubric.runner.architecture == "text_react"
    assert cfg.ask_rubric.rubric_config.criteria[0].name == "correctness"
    assert cfg.budget.max_judge_calls == 50
    assert cfg.rng_seed == 7


def test_rng_seed_and_judge_calls_default(tmp_path) -> None:
    cfg = load_run_config(
        _write(
            tmp_path,
            "artifact: tool_docs\noptimizer: critique_refine\nladder: [[paired_agent, 12, 1]]\n",
        )
    )
    assert cfg.rng_seed == 0
    assert cfg.budget.max_judge_calls == 200
    assert cfg.ask_rubric is None


def test_bad_criterion_weights_raise_at_load(tmp_path) -> None:
    # AC-8: weights summing to 1.02 are a config error at load, never trial 14.
    bad = _ASK_RUBRIC_YAML.replace("weight: 0.4", "weight: 0.42")
    with pytest.raises(ValueError, match="criterion weights"):
        load_run_config(_write(tmp_path, bad))


def test_unregistered_gate_kind_raises_at_load(tmp_path) -> None:
    # AC-7: a gate-kind typo names the registered kinds at load time.
    bad = _ASK_RUBRIC_YAML.replace("kind: min_answer_chars", "kind: min_chars")
    with pytest.raises(KeyError, match="min_answer_chars"):
        load_run_config(_write(tmp_path, bad))


def test_shipped_ask_configs_load_typed() -> None:
    prompt_cfg = load_run_config(_shipped("optimize_ask_prompt.yaml"))
    assert prompt_cfg.artifact == "ask_prompt" and prompt_cfg.optimizer == "skillopt"
    # Both rungs are the paid fitness (the tool_docs degenerate-halving
    # precedent) — a prompt cannot move retrieval metrics, so the free
    # retrieval rung is reserved for the overlay-carrying artifacts.
    assert [r.fitness_name for r in prompt_cfg.ladder.rungs] == ["ask_rubric", "ask_rubric"]
    assert prompt_cfg.ask_rubric is not None
    assert prompt_cfg.budget.max_judge_calls == 200

    arch_cfg = load_run_config(_shipped("optimize_ask_architecture.yaml"))
    assert arch_cfg.artifact == "ask_architecture" and arch_cfg.optimizer == "config_search"
    assert arch_cfg.config_search is not None
    assert arch_cfg.config_search.strategy == "halving"
    assert set(arch_cfg.config_search.dimensions) == {
        "architecture",
        "retrieval_config",
        "max_agent_turns",
    }


def test_config_search_factory_threads_the_section(tmp_path) -> None:
    from pydocs_eval.optimize.run_config import build_config_search_optimizer

    cfg = load_run_config(_shipped("optimize_ask_architecture.yaml"))
    optimizer = build_config_search_optimizer(cfg)
    assert optimizer.strategy == cfg.config_search.strategy
    assert optimizer.seed == cfg.config_search.seed
    assert optimizer.sample_size == cfg.config_search.sample_size
    assert dict(optimizer.dimensions) == dict(cfg.config_search.dimensions)


def test_config_search_seed_falls_back_to_rng_seed(tmp_path) -> None:
    from pydocs_eval.optimize.run_config import build_config_search_optimizer

    text = (
        "artifact: ask_architecture\noptimizer: config_search\n"
        "ladder: [[retrieval, 12, 1]]\nrng_seed: 41\n"
        "config_search:\n  strategy: grid\n  dimensions: {architecture: [text_react]}\n"
    )
    cfg = load_run_config(_write(tmp_path, text))
    assert build_config_search_optimizer(cfg).seed == 41


def test_shipped_architecture_dims_resolve_against_real_pipelines() -> None:
    # Every cell of the shipped grid must pass validate() against the REAL
    # pipelines dir — a renamed exp_*.yaml stem fails here, not mid-campaign.
    from pydocs_eval.optimize.artifacts.ask_architecture import AskArchitectureArtifact

    cfg = load_run_config(_shipped("optimize_ask_architecture.yaml"))
    pipelines = Path(__file__).resolve().parents[2] / "configs" / "pipelines"
    cells = AskArchitectureArtifact.enumerate_space(
        cfg.config_search.dimensions, pipelines_dir=pipelines
    )
    assert len(cells) == 1 * 3 * 2
    for cell in cells:
        assert cell.validate() == (), cell.render()


def test_retrieval_rung_requires_an_overlay_carrying_artifact(tmp_path) -> None:
    text = (
        "artifact: ask_prompt\noptimizer: skillopt\n"
        "ladder: [[retrieval, 12, 4], [ask_rubric, 24, 1]]\n"
    )
    with pytest.raises(ValueError, match="retrieval overlay"):
        load_run_config(_write(tmp_path, text))


_ARMS_YAML = """
artifact: search_skill
optimizer: skillopt
ladder:
  - [ask_rubric, 12, 1]
ask_rubric:
  gates:
    - {name: non_empty, kind: min_answer_chars, params: {n: 40}}
  criteria:
    - {name: correctness, weight: 1.0, description: "Names the right code."}
arms:
  - runner: pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner
    settings: {workspace: ~/pydocs-index, model: qwen3-4b}
    tool_names: null
    dataset: crosscommitvuln
    task_name: ccv
    guidance: search_skill
    scoring:
      objective: rubric_verdict
      rubric: ask_rubric
      tracked: [gold_recall]
"""

# The two blocks whose ABSENCE is a load-time error; sliced out of the YAML
# above by exact text so the tests delete them rather than rename them (a
# renamed key proves only the extra="forbid" firewall).
_SCORING_BLOCK = """    scoring:
      objective: rubric_verdict
      rubric: ask_rubric
      tracked: [gold_recall]
"""
_ASK_RUBRIC_SECTION = """ask_rubric:
  gates:
    - {name: non_empty, kind: min_answer_chars, params: {n: 40}}
  criteria:
    - {name: correctness, weight: 1.0, description: "Names the right code."}
"""


class TestArmsBlock:
    """The design §6 arms block behind the widened key firewall (stage 4)."""

    def test_every_shipped_config_loads_under_the_key_firewall(self) -> None:
        # extra="forbid" newly rejects any stray top-level key, so every
        # shipped config is re-audited here rather than at someone's run time.
        for name in (
            "optimize_tool_docs.yaml",
            "optimize_usage_skill.yaml",
            "optimize_ask_prompt.yaml",
            "optimize_ask_prompt_combined.yaml",
            "optimize_ask_architecture.yaml",
            "optimize_search_skill.yaml",
        ):
            assert load_run_config(_shipped(name)).artifact

    def test_an_unknown_top_level_key_is_no_longer_silently_ignored(self, tmp_path) -> None:
        # Before the widening pydantic's default extra="ignore" swallowed this
        # — an arms: block (or a typo inside one) was a silent no-op.
        text = (
            "artifact: tool_docs\noptimizer: skillopt\nladder: [[paired_agent, 6, 1]]\narmz: []\n"
        )
        with pytest.raises(ValidationError, match="armz"):
            load_run_config(_write(tmp_path, text))

    def test_the_arms_block_parses_into_typed_cells(self) -> None:
        cfg = load_run_config(_shipped("optimize_search_skill.yaml"))
        assert len(cfg.arms) == 2
        assert cfg.arms[0].tool_names is None
        assert cfg.arms[1].tool_names == ("search_codebase", "get_symbol", "read_file")
        assert cfg.arms[0].settings["workspace"] == "~/pydocs-index"

    def test_a_config_without_arms_keeps_its_single_implicit_arm(self) -> None:
        assert load_run_config(_shipped("optimize_tool_docs.yaml")).arms == ()

    def test_an_unknown_arm_key_fails_at_load(self, tmp_path) -> None:
        bad = _ARMS_YAML.replace("    tool_names: null", "    architecture: text_react")
        with pytest.raises(ValidationError, match="architecture"):
            load_run_config(_write(tmp_path, bad))

    def test_an_unregistered_guidance_family_fails_at_load(self, tmp_path) -> None:
        bad = _ARMS_YAML.replace("guidance: search_skill", "guidance: skill_search")
        with pytest.raises(KeyError, match="skill_search"):
            load_run_config(_write(tmp_path, bad))

    def test_an_unregistered_runner_path_fails_at_load(self, tmp_path) -> None:
        bad = _ARMS_YAML.replace(
            "runner: pydocs_mcp.harness.ask_your_docs.binding:make_harness_runner",
            "runner: some.other.harness:make_runner",
        )
        with pytest.raises(KeyError, match="some.other.harness"):
            load_run_config(_write(tmp_path, bad))

    def test_a_task_name_outside_the_v1_set_fails_at_load(self, tmp_path) -> None:
        bad = _ARMS_YAML.replace("task_name: ccv", "task_name: localization")
        with pytest.raises(ValueError, match="localization"):
            load_run_config(_write(tmp_path, bad))

    def test_a_tool_outside_the_frozen_nine_fails_at_load(self, tmp_path) -> None:
        bad = _ARMS_YAML.replace("tool_names: null", "tool_names: [Bash]")
        with pytest.raises(ValidationError, match="Bash"):
            load_run_config(_write(tmp_path, bad))


class TestArmScoringBlock:
    """The seventh canonical cell key: what an arm optimizes, and what it watches."""

    def test_the_scoring_block_parses_into_a_typed_objective(self) -> None:
        cfg = load_run_config(_shipped("optimize_search_skill.yaml"))
        assert cfg.arms[0].scoring.objective is ObjectiveKind.RUBRIC_VERDICT
        assert cfg.arms[0].scoring.rubric == "ask_rubric"
        assert cfg.arms[0].scoring.tracked == ("gold_recall", "cve_id_exact")

    def test_an_arm_without_a_scoring_block_fails_at_load(self, tmp_path) -> None:
        # The block is genuinely ABSENT, not misspelled: a renamed key would be
        # caught by extra="forbid" alone (test_an_unknown_arm_key_fails_at_load
        # already covers that) and would pass even if scoring had a default.
        bad = _ARMS_YAML.replace(_SCORING_BLOCK, "")
        with pytest.raises(ValidationError, match=r"scoring[\s\S]*[Ff]ield required"):
            load_run_config(_write(tmp_path, bad))

    def test_arms_with_no_configured_rubric_section_fail_at_load(self, tmp_path) -> None:
        # The likelier authoring mistake now that scoring: is required on every
        # arm — the arm names an objective the config never spells.
        bad = _ARMS_YAML.replace(_ASK_RUBRIC_SECTION, "")
        with pytest.raises(ValueError, match="no rubric objective"):
            load_run_config(_write(tmp_path, bad))

    def test_an_unknown_scoring_key_fails_at_load(self, tmp_path) -> None:
        bad = _ARMS_YAML.replace("      tracked: [gold_recall]", "      watched: [gold_recall]")
        with pytest.raises(ValidationError, match="watched"):
            load_run_config(_write(tmp_path, bad))

    def test_an_unknown_objective_fails_at_load(self, tmp_path) -> None:
        bad = _ARMS_YAML.replace("objective: rubric_verdict", "objective: answer_accuracy")
        with pytest.raises(ValidationError, match="answer_accuracy"):
            load_run_config(_write(tmp_path, bad))

    def test_an_unresolvable_tracked_name_fails_at_load(self, tmp_path) -> None:
        bad = _ARMS_YAML.replace("tracked: [gold_recall]", "tracked: [gold_recal]")
        with pytest.raises(ValidationError, match="gold_recal"):
            load_run_config(_write(tmp_path, bad))

    def test_a_rubric_reference_with_no_configured_section_fails_at_load(self, tmp_path) -> None:
        bad = _ARMS_YAML.replace("rubric: ask_rubric", "rubric: strict_ccv")
        with pytest.raises(ValueError, match="strict_ccv"):
            load_run_config(_write(tmp_path, bad))

    def test_the_resolved_objective_hash_is_the_arms_identity_input(self, tmp_path) -> None:
        # The fold is over the RESOLVED objective, so editing the referenced
        # rubric moves every arm that binds it — a config edit can never
        # silently resume samples scored under a different objective.
        cfg = load_run_config(_write(tmp_path, _ARMS_YAML))
        edited = load_run_config(
            _write(tmp_path, _ARMS_YAML.replace("weight: 1.0", "weight: 1.00"))
        )
        assert arm_objective_hash(cfg, cfg.arms[0]) == arm_objective_hash(cfg, cfg.arms[0])
        assert len(arm_objective_hash(cfg, cfg.arms[0])) == 64
        assert arm_objective_hash(cfg, cfg.arms[0]) == arm_objective_hash(edited, edited.arms[0])

        moved = load_run_config(
            _write(tmp_path, _ARMS_YAML.replace("Names the right code.", "Names the wrong code."))
        )
        assert arm_objective_hash(cfg, cfg.arms[0]) != arm_objective_hash(moved, moved.arms[0])

    def test_the_arm_identity_input_is_the_fitness_objective_hash(self, tmp_path) -> None:
        # ONE objective identity, folded twice. The sample ledger keys its
        # lines on AskRubricFitness.objective_hash(); arm identity folds
        # arm_objective_hash(). A binding-free arm hash stayed byte-identical
        # across a TASK_SCAFFOLD_VERSION / gate-source bump that correctly
        # re-ran every sample — so the arm would keep resuming arm-keyed rows
        # measured under the OLD execution path (design §8's silent reuse).
        cfg = load_run_config(_write(tmp_path, _ARMS_YAML))
        _runner, _judge, fitness = _dry_ask_rubric_fitness(
            cfg, ledger_path=tmp_path / "trials.jsonl"
        )
        assert arm_objective_hash(cfg, cfg.arms[0]) == fitness.objective_hash()

    def test_loading_never_imports_the_named_harness(self) -> None:
        # Lazy by contract (design §6): a harness behind an optional extra costs
        # NOTHING until an arm runs it — the load path may only check the NAME.
        #
        # Proved by ABSENCE in ``sys.modules`` of a FRESH interpreter, not by
        # intercepting ``importlib.import_module``: an ``import x.y`` statement
        # goes through ``builtins.__import__`` and would sail straight past such
        # a patch, and anything already imported by the test session would mask
        # the violation entirely.
        #
        # Scoped to what laziness actually claims: the harness BINDING an arm's
        # ``runner`` names, and its runtime. ``pydocs_mcp.harness.ask_your_docs
        # .prompts`` IS imported at load (the ``ask_prompt`` artifact reads the
        # product's shared prompt at module scope) — banning that prefix
        # wholesale would assert an invariant the load path does not hold.
        probe = textwrap.dedent(
            f"""
            import sys
            from pathlib import Path
            from pydocs_eval.optimize.run_config import load_run_config

            assert load_run_config(Path({str(_shipped("optimize_search_skill.yaml"))!r})).arms
            forbidden = [
                name
                for name in sys.modules
                if name == "langgraph"
                or name.startswith("langgraph.")
                or name.startswith("pydocs_mcp.harness.ask_your_docs.binding")
            ]
            assert not forbidden, f"config load imported the harness: {{forbidden}}"
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
