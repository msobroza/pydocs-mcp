"""The chat slice through ``before-after``: a bare, ``auto`` arm IS the vendored runner.

Issue #386 moves the chat instrument from the vendored runner
(``benchmarks/tools/run_example_needle_chat_repro.py``) to a before/after arm
under the shipped overlay ``ask_example_needle_chat_bare_auto_arm.yaml``
(``task_scaffold: bare``, ``architecture: auto``). That move is sound only if,
for the same questions, the arm sends the model what the chat page sends and
writes the rows the runner writes; and if the arm's settings digest is the one
the runner records, so ``--reuse-baseline`` accepts a runner output.

One scripted fake model plays both paths (``_chat_runner_fakes``): the
runner's chat-page ``ask``, and the arm's PRODUCT binding behind its two
documented seams. Nothing reaches a model, a child process or the network; the
only key is a synthetic one.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("langgraph")

from pydocs_eval.campaign.before_after_arm import ArmSettings, ArmSummary, run_arm
from pydocs_eval.campaign.before_after_arm_settings import load_arm_overlay
from pydocs_eval.campaign.before_after_arm_variant import ArmVariant, variant_of
from pydocs_eval.campaign.before_after_llm_block import (
    load_arm_llm_block,
    refuse_file_sourced_model_settings,
)
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.task_rendering import TaskScaffold
from pydocs_eval.trajectory.ask_outcome import TaskOutcome
from pydocs_mcp.harness.ask_your_docs import agent, binding
from pydocs_mcp.harness.ask_your_docs.llm_connection import VisionRule

from ._chat_runner_fakes import (
    BARE_AUTO_OVERLAY,
    CHAT_CONFIG,
    CHAT_MODEL,
    CHAT_SERVE_CONFIG,
    COMMIT,
    LLM_BLOCK,
    TWO_TURNS,
    FakeAgentBuilder,
    FakeBindingServeSession,
    FakeTracedServe,
    load_runner,
    runner_options,
    serving_turns,
)

# The chat block's credential variable; a synthetic value, never a real key.
_KEY_ENV = "OPENROUTER_API_KEY"
# What differs between two runs of one question by construction: where the
# trace landed and how long it took. Every other row field must match.
_PER_RUN_FIELDS = ("trajectory_id", "trace_dir", "wall_seconds")


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def bare_auto_variant(overlay: Path = BARE_AUTO_OVERLAY) -> ArmVariant:
    """The baseline a before-after plan runs under the shipped overlay and ``--llm-block``."""
    return variant_of(
        load_arm_overlay(overlay, flag="--baseline-settings"),
        llm_block=load_arm_llm_block(LLM_BLOCK),
        serving_turns=serving_turns(),
    )


def _overlay_capped_at(tmp_path: Path, turns: int) -> Path:
    """The shipped overlay plus a forced turn cap: the runner's ``--max-agent-turns``."""
    path = tmp_path / "bare_auto_capped.yaml"
    payload = {**_load_yaml(BARE_AUTO_OVERLAY), "max_agent_turns": turns}
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return path


async def _runner_arm(tmp_path: Path, builder: FakeAgentBuilder, turns: int | None) -> ArmSummary:
    runner = load_runner()
    options = runner_options(runner, tmp_path / "runner", max_agent_turns=turns)
    return await runner.run_chat_arm(options, open_serve=FakeTracedServe(), build=builder)


async def _chat_tasks() -> list[EvalTask]:
    return await load_runner()._tasks("dev")


def _arm_settings(tmp_path: Path, variant: ArmVariant) -> ArmSettings:
    out = tmp_path / "before-after"
    return ArmSettings(
        role="baseline",
        commit=COMMIT,
        workspace=str(tmp_path / "ws"),
        model=CHAT_MODEL,
        trace_root=str(out / "traces"),
        out_dir=str(out),
        estimated_usd_per_rollout=0.0,
        cost_ceiling_usd=1.0,
        pydocs_config=str(CHAT_SERVE_CONFIG),
        **variant.arm_settings_fields(),
    )


async def _before_after_arm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, builder: FakeAgentBuilder, variant: ArmVariant
) -> ArmSummary:
    """The arm a before/after run spawns, through the REAL product binding and its fakes."""
    monkeypatch.setattr(agent, "build_agent_with_scope_capabilities", builder)
    monkeypatch.setattr(binding, "_serve_session_tools", FakeBindingServeSession().session)
    monkeypatch.setenv(_KEY_ENV, "sk-or-synthetic-test-key")
    return await run_arm(
        _arm_settings(tmp_path, variant), await _chat_tasks(), refresh_indexes=lambda s: ()
    )


def _comparable_rows(summary: ArmSummary) -> list[dict[str, object]]:
    rows = [asdict(row) for row in summary.tasks]
    return [{k: v for k, v in row.items() if k not in _PER_RUN_FIELDS} for row in rows]


# ── the arm sends what the chat page sends, and writes the runner's rows ──


@pytest.mark.parametrize(
    ("script", "turns", "outcome"),
    [
        (TWO_TURNS, None, TaskOutcome.ANSWERED),
        (TWO_TURNS * 3, 3, TaskOutcome.EXHAUSTED_FINALIZED),
    ],
    ids=["answered", "exhausted-at-a-forced-cap"],
)
async def test_a_bare_auto_arm_yields_the_rows_the_runner_yields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    script: Any,
    turns: int | None,
    outcome: TaskOutcome,
) -> None:
    overlay = BARE_AUTO_OVERLAY if turns is None else _overlay_capped_at(tmp_path, turns)
    via_runner, via_arm = FakeAgentBuilder(script), FakeAgentBuilder(script)

    expected = await _runner_arm(tmp_path, via_runner, turns)
    measured = await _before_after_arm(tmp_path, monkeypatch, via_arm, bare_auto_variant(overlay))

    assert len(measured.tasks) == len(expected.tasks) == 10
    assert _comparable_rows(measured) == _comparable_rows(expected)
    assert {row.outcome for row in measured.tasks} == {outcome}
    assert measured.settings_digest == expected.settings_digest


async def test_the_arm_sends_the_model_the_bare_question_as_the_chat_page_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    via_runner, via_arm = FakeAgentBuilder(), FakeAgentBuilder()

    await _runner_arm(tmp_path, via_runner, None)
    await _before_after_arm(tmp_path, monkeypatch, via_arm, bare_auto_variant())

    questions = [task.query for task in await _chat_tasks()]
    assert via_arm.prompts == via_runner.prompts == questions
    assert via_arm.graphs[0].configs == via_runner.graphs[0].configs[:1]


async def test_the_arm_builds_the_multimodal_auto_agent_the_runner_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``auto`` routes by the vision rule, so both paths must hand the build the same one."""
    via_runner, via_arm = FakeAgentBuilder(), FakeAgentBuilder()

    await _runner_arm(tmp_path, via_runner, None)
    await _before_after_arm(tmp_path, monkeypatch, via_arm, bare_auto_variant())

    runner_build, arm_build = via_runner.builds[0], via_arm.builds[0]
    assert (runner_build["config"].architecture, arm_build["architecture"]) == ("auto", "auto")
    assert runner_build["config"].llm.vision is True
    assert arm_build["connection"].vision_rule is VisionRule.MULTIMODAL
    assert arm_build["model"] == runner_build["model"] == CHAT_MODEL


# ── the digest: the runner records what it sends, so a matching plan reproduces it ──


async def test_the_runner_records_the_llm_block_it_sends_the_agent(tmp_path: Path) -> None:
    """The served vision rule reaches the agent, so it is part of the recorded block."""
    summary = await _runner_arm(tmp_path, FakeAgentBuilder(), None)

    settings = _load_yaml(tmp_path / "runner" / "arm_settings.json")  # JSON is YAML
    assert settings["llm_block"] == {**load_arm_llm_block(LLM_BLOCK).settings, "vision": True}
    assert summary.task_scaffold == TaskScaffold.BARE


async def test_the_shipped_overlay_reproduces_the_runner_digest(tmp_path: Path) -> None:
    summary = await _runner_arm(tmp_path, FakeAgentBuilder(), None)

    variant = bare_auto_variant()

    assert (variant.architecture, variant.task_scaffold) == ("auto", TaskScaffold.BARE)
    assert variant.max_agent_turns == summary.max_agent_turns
    assert variant.digest == summary.settings_digest


async def test_a_citing_auto_plan_does_not_reproduce_the_runner_digest(tmp_path: Path) -> None:
    """The scaffold is part of the instrument: the same settings, citing, is another one."""
    summary = await _runner_arm(tmp_path, FakeAgentBuilder(), None)
    citing = tmp_path / "citing_auto.yaml"
    payload = {**_load_yaml(BARE_AUTO_OVERLAY), "task_scaffold": "citing"}
    citing.write_text(yaml.safe_dump(payload), encoding="utf-8")

    assert bare_auto_variant(citing).digest != summary.settings_digest


# ── the shipped instrument files ──


def test_the_serve_config_passes_the_plans_model_settings_refusal() -> None:
    """``before-after`` refuses a serving file carrying model settings; the chat YAML does."""
    refuse_file_sourced_model_settings(CHAT_SERVE_CONFIG)


def test_the_serve_config_serves_what_the_chat_config_serves() -> None:
    """Same embedder and discovery as the runner's YAML: the same bundle, the same index."""
    serve, chat = _load_yaml(CHAT_SERVE_CONFIG), _load_yaml(CHAT_CONFIG)

    assert {key: serve[key] for key in ("embedding", "extraction")} == {
        key: chat[key] for key in ("embedding", "extraction")
    }
    assert "ask_your_docs" not in serve


def test_the_overlay_states_the_chat_pages_settings_and_no_model() -> None:
    chat = _load_yaml(CHAT_CONFIG)["ask_your_docs"]

    overlay = _load_yaml(BARE_AUTO_OVERLAY)

    assert overlay == {
        "architecture": chat["architecture"],
        "task_scaffold": "bare",
        "llm": {"vision": chat["llm"]["vision"]},
    }
