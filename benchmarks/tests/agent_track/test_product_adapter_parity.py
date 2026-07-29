"""The definition/execution split, proved by execution.

The standalone measurement CLI (``pydocs-eval-agent-track``) is a BASE install
by contract — ADR 0009's floor forbids it importing ``pydocs_mcp`` — so when
the argv builder and the transcript fold moved into the product's engine
adapter, this package kept a byte-identical copy rather than delegating behind
an import guard. Delegation would give ONE console script two argv behaviors
depending on what happens to be installed, which is exactly the silent
divergence arm hashes exist to prevent.

A copy is only safe while something executes both. That is this module: it is
``importorskip``-gated (it is the one agent-track test that needs the product),
and it runs the two spellings against the same inputs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.agent_track._arm import EXTERNAL_DELIVERY_MAP
from pydocs_eval.agent_track._command import (
    _allowed_tools,
    build_claude_command,
    render_mcp_config,
)
from pydocs_eval.agent_track._guidance import (
    ExternalUndeliverableGuidanceError,
    deliverable_section_keys,
    fold_guidance,
)
from pydocs_eval.agent_track._parse import parse_stream_events
from pydocs_eval.agent_track._runner import _merge_metrics
from pydocs_eval.agent_track._types import ArmConfig
from pydocs_eval.trajectory.rollout import build_rollout_command, trace_env_map

pytest.importorskip("pydocs_mcp.harness.cli_agents.claude_code")

from pydocs_mcp.harness.cli_agents.base import CliRunRequest
from pydocs_mcp.harness.cli_agents.claude_code import ClaudeCodeAdapter
from pydocs_mcp.harness.core.guidance_fold import fold_guidance_sections
from pydocs_mcp.harness.core.run_contract import UndeliverableGuidanceError
from pydocs_mcp.harness.external import binding as external_binding
from pydocs_mcp.harness.external.serve_config import render_serve_mcp_config

_FIXTURES = Path(__file__).parent / "fixtures"
_HARNESS = "external"
_SESSION_ID = "0f9d1c1e-0000-4000-8000-000000000001"

_ARMS = (
    ArmConfig(name="bare", mcp=False),
    ArmConfig(name="indexed", mcp=True),
    ArmConfig(name="judge", no_tools=True),
    ArmConfig(name="drop-one", mcp=True, tools=("mcp__pydocs-mcp__search_codebase",)),
)


def _request(arm: ArmConfig, *, cwd: Path, mcp_config: Path, suffix: str) -> CliRunRequest:
    """The eval arm's three profiles, resolved into the engine-neutral request."""
    grant = _allowed_tools(arm)
    return CliRunRequest(
        prompt="q?",
        cwd=cwd,
        model=arm.model,
        max_turns=arm.max_turns,
        allowed_tools=tuple(grant.split()) if grant else (),
        mcp_config=mcp_config if arm.mcp else None,
        system_prompt_suffix=suffix,
    )


@pytest.mark.parametrize("arm", _ARMS, ids=lambda arm: arm.name)
@pytest.mark.parametrize("suffix", ["", "GUIDE"], ids=["no-guidance", "guidance"])
def test_the_product_adapter_builds_the_same_argv(arm: ArmConfig, suffix: str, tmp_path) -> None:
    mcp_config = tmp_path / "mcp.json"
    theirs = build_claude_command(
        arm, prompt="q?", cwd=tmp_path, mcp_config=mcp_config, system_prompt_suffix=suffix
    )
    ours = ClaudeCodeAdapter().build_command(
        _request(arm, cwd=tmp_path, mcp_config=mcp_config, suffix=suffix)
    )
    assert ours == theirs


def test_the_product_adapter_builds_the_same_rollout_argv(tmp_path) -> None:
    # The trajectory driver appends ``--session-id`` after the base builder; the
    # adapter carries it as a request field, and the two argv must still match.
    arm = ArmConfig(name="indexed", mcp=True)
    mcp_config = tmp_path / "mcp.json"
    theirs = build_rollout_command(
        arm, prompt="q?", cwd=tmp_path, mcp_config=mcp_config, trajectory_id=_SESSION_ID
    )
    request = _request(arm, cwd=tmp_path, mcp_config=mcp_config, suffix="")
    from dataclasses import replace

    ours = ClaudeCodeAdapter().build_command(replace(request, session_id=_SESSION_ID))
    assert ours == theirs


def test_the_product_adapter_folds_the_same_transcript_facts() -> None:
    stdout = (_FIXTURES / "claude_stream.jsonl").read_text(encoding="utf-8")
    theirs = _merge_metrics(stdout, wall_seconds=0.0)
    stats = parse_stream_events(stdout)
    ours = ClaudeCodeAdapter().parse_transcript(stdout)

    assert ours.answer == theirs.answer
    assert ours.cost_usd == theirs.cost_usd
    assert ours.turns == theirs.turns
    assert len(ours.tool_calls) == theirs.tool_calls
    assert len(ours.files_read) == theirs.distinct_files_read
    assert ours.cache_read_tokens == theirs.cache_read_tokens
    assert ours.cache_write_tokens == theirs.cache_write_tokens
    # The MCP subtotal is derivable from the ordered calls the adapter keeps,
    # which the counting parser never exposed.
    assert sum(call.tool_name.startswith("mcp__") for call in ours.tool_calls) == (
        stats.mcp_tool_calls
    )


def test_the_product_renders_the_same_mcp_config(tmp_path) -> None:
    env = trace_env_map(trajectory_id=_SESSION_ID, trace_dir=tmp_path / "traces")
    overlay = tmp_path / "overlay.yaml"
    for kwargs in ({}, {"env": env}, {"env": env, "overlay": overlay}):
        theirs = render_mcp_config(
            corpus_dir=tmp_path / "corpus", python=Path("/venv/bin/python"), **kwargs
        )
        ours = render_serve_mcp_config(
            corpus_dir=tmp_path / "corpus", python=Path("/venv/bin/python"), **kwargs
        )
        assert json.loads(ours) == json.loads(theirs)
        assert ours == theirs


@pytest.mark.parametrize("task_name", ["vuln", "repo_qa", ""])
def test_the_two_folds_deliver_byte_identical_text(task_name: str) -> None:
    sections = {
        "BACKBONE": "search the index first",
        "TASK_HEAD: vuln": "name the symbol",
        "HARNESS_TASK_HEAD: external.vuln": "prefer get_references",
        "HARNESS_TASK_HEAD: ask_your_docs.vuln": "another arm's slice",
        "SYSTEM_PROMPT": "another harness's channel",
    }
    deliverable = deliverable_section_keys(task_name)
    slice_for_task = {key: sections[key] for key in deliverable if key in sections}
    slice_for_task["HARNESS_TASK_HEAD: ask_your_docs.vuln"] = "another arm's slice"
    assert fold_guidance(slice_for_task, task_name=task_name) == fold_guidance_sections(
        slice_for_task, harness_name=_HARNESS, task_name=task_name
    )


def test_the_two_undeliverable_errors_read_identically() -> None:
    # The base-install twin is format-coupled, not type-coupled; the product
    # side raises the real contract error. Same message, both ValueError.
    sections = {"BACKBONE": "b", "TOOL_DOCS": "not a section"}
    with pytest.raises(ExternalUndeliverableGuidanceError) as theirs:
        fold_guidance(sections, task_name="vuln")
    with pytest.raises(UndeliverableGuidanceError) as ours:
        fold_guidance_sections(sections, harness_name=_HARNESS, task_name="vuln")
    assert str(ours.value) == str(theirs.value)
    assert ours.value.sections == theirs.value.sections
    assert ours.value.deliverable == theirs.value.deliverable


def test_the_two_delivery_maps_declare_the_same_routing() -> None:
    # The two DIGESTS deliberately differ (different canonicalizers, different
    # payload shapes, never mixed inside one hash — see the ADR 0009/0010
    # amendment). What must not differ is the routing they state.
    assert dict(external_binding.delivery_map()) == EXTERNAL_DELIVERY_MAP
