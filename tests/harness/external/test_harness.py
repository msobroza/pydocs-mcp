"""harness/external/harness — the COMPOSED harness's run contract + run flow.

Fake-based: ``_run_cli_process`` is monkeypatched with a fake that writes a
REAL trace through the recorder (correlated from the ``.mcp.json`` env block it
reads back), so both observation points and the derived-view pin are exercised
without spawning a CLI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.harness.core.run_contract import (
    ToolCallObservation,
    TurnBudgetExceededError,
    UndeliverableGuidanceError,
)
from pydocs_mcp.harness.external import binding, harness as harness_module

from tests.harness.core._runner_contract import HarnessRunnerContract, conformant_sample
from tests.harness.external._fake_cli import (
    CLIENT_TOOL_NAME,
    TURN_CAPPED_TRANSCRIPT,
    FakeCliProcess,
    SERVER_TOOL_NAME,
)


def _settings(tmp_path: Path, **overrides: object) -> dict[str, object]:
    settings: dict[str, object] = {
        "workspace": str(tmp_path / "ws"),
        "model": "a-model",
        "trace_root": str(tmp_path / "traces"),
    }
    settings.update(overrides)
    return settings


@pytest.fixture
def fake_cli(monkeypatch: pytest.MonkeyPatch) -> FakeCliProcess:
    fake = FakeCliProcess()
    monkeypatch.setattr(harness_module, "_run_cli_process", fake)
    return fake


# --- The shared contract suite, bound to this harness ----------------------


class TestExternalHarnessRunnerContract(HarnessRunnerContract):
    @pytest.fixture(autouse=True)
    def _bind(self, tmp_path: Path, fake_cli: FakeCliProcess) -> None:
        self._tmp_path = tmp_path

    def make_runner(self):
        return binding.make_harness_runner(_settings(self._tmp_path))

    def invalid_settings(self):
        return {**_settings(self._tmp_path), "mystery_knob": True}

    def build_runner_from(self, settings):
        return binding.make_harness_runner(settings)


# --- Observation join ------------------------------------------------------


async def test_trajectory_joins_server_and_client_observations(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    trajectory = await runner.run(conformant_sample(), {})
    observed = [(record.tool_name, record.observed_by) for record in trajectory.tool_calls]
    # ONE record per call. The two observation points name the MCP call
    # differently (server: the bare registration name; transcript: the CLI's
    # ``mcp__<server>__`` namespacing), so a raw-name join would emit it twice —
    # once SERVER, once as a phantom CLIENT call that no gate should ever see.
    assert observed == [
        (SERVER_TOOL_NAME, ToolCallObservation.SERVER),
        ("Read", ToolCallObservation.CLIENT),
    ]
    assert CLIENT_TOOL_NAME not in [record.tool_name for record in trajectory.tool_calls]
    assert trajectory.answer == "the answer"
    assert trajectory.turns == 3
    # The engine reports real spend, so this harness does NOT record 0.0.
    assert trajectory.cost_usd == 0.25
    assert trajectory.wall_seconds >= 0.0


async def test_a_bare_arm_records_client_observations_only(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    # An arm that attaches no MCP server leaves no server trace by design; its
    # calls are still visible, tagged CLIENT, and the indexed-tool gates that
    # read the SERVER slice correctly see nothing.
    runner = binding.make_harness_runner(_settings(tmp_path, mcp=False))
    trajectory = await runner.run(conformant_sample(), {})
    assert trajectory.server_tool_calls() == ()
    assert {record.observed_by for record in trajectory.tool_calls} == {ToolCallObservation.CLIENT}
    assert "--mcp-config" not in fake_cli.argv()


async def test_a_traced_run_without_a_trace_is_a_hard_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(harness_module, "_run_cli_process", FakeCliProcess(write_trace=False))
    runner = binding.make_harness_runner(_settings(tmp_path))
    with pytest.raises(harness_module.ExternalTraceMissingError):
        await runner.run(conformant_sample(), {})


async def test_the_turn_cap_becomes_the_typed_contract_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        harness_module, "_run_cli_process", FakeCliProcess(stdout=TURN_CAPPED_TRANSCRIPT)
    )
    runner = binding.make_harness_runner(_settings(tmp_path, max_agent_turns=40))
    with pytest.raises(TurnBudgetExceededError) as excinfo:
        await runner.run(conformant_sample(), {})
    assert excinfo.value.turn_limit == 40
    # The engine METERED this run before it hit the cap. Dropping that figure
    # would bill the campaign $0.00 for a rollout that really spent — and the
    # budget guard enforces max_usd against exactly this number.
    assert excinfo.value.cost_usd == 3.10


# --- Nothing spends before the contract checks -----------------------------


async def test_a_nonconformant_sample_fails_before_any_spawn(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    with pytest.raises(harness_module.ExternalSampleContractError) as excinfo:
        await runner.run({"record_id": "r"}, {})
    assert "task_name" in str(excinfo.value)
    assert fake_cli.calls == []


async def test_an_undeliverable_section_fails_before_any_spawn(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    with pytest.raises(UndeliverableGuidanceError):
        await runner.run(conformant_sample(), {"TOOL: grep": "not ours"})
    assert fake_cli.calls == []


# --- Guidance delivery ------------------------------------------------------


async def test_guidance_folds_onto_the_engines_channel_flag(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    sample = {**conformant_sample(), "task_name": "vuln"}
    await runner.run(
        sample,
        {
            "BACKBONE": "search first",
            "TASK_HEAD: vuln": "name the symbol",
            "HARNESS_TASK_HEAD: external.vuln": "prefer get_references",
            "HARNESS_TASK_HEAD: ask_your_docs.vuln": "another arm's slice",
        },
    )
    argv = fake_cli.argv()
    assert argv[argv.index("--append-system-prompt") + 1] == (
        "search first\nname the symbol\nprefer get_references"
    )
    # The task prompt is untouched — the two channels never merge.
    assert argv[argv.index("-p") + 1] == "value-rendered_prompt"


async def test_no_guidance_emits_no_channel_flag(tmp_path: Path, fake_cli: FakeCliProcess) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    await runner.run(conformant_sample(), {})
    assert "--append-system-prompt" not in fake_cli.argv()


# --- Surfaces and wiring ----------------------------------------------------


async def test_the_indexed_profile_grants_file_tools_plus_the_server_wildcard(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    await runner.run(conformant_sample(), {})
    argv = fake_cli.argv()
    assert argv[argv.index("--allowedTools") + 1] == "Read Grep Glob Bash mcp__pydocs-mcp__*"


async def test_an_explicit_tool_surface_replaces_the_profile_grant(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    # The arms platform hands over the SERVER's vocabulary — ``ArmCell``
    # rejects anything outside the frozen nine, so a namespaced name cannot
    # even be spelled in an arm's YAML. The engine adds its own namespace;
    # passing these through verbatim would grant tools the CLI cannot resolve,
    # i.e. a drop-one arm that silently runs tool-less.
    runner = binding.make_harness_runner(
        _settings(tmp_path, tool_names=["search_codebase", "get_symbol"])
    )
    await runner.run(conformant_sample(), {})
    argv = fake_cli.argv()
    assert argv[argv.index("--allowedTools") + 1] == (
        "mcp__pydocs-mcp__search_codebase mcp__pydocs-mcp__get_symbol"
    )


async def test_a_bare_arm_grants_the_engines_built_ins_only(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path, mcp=False))
    await runner.run(conformant_sample(), {})
    argv = fake_cli.argv()
    assert argv[argv.index("--allowedTools") + 1] == "Read Grep Glob Bash"


async def test_the_serve_child_is_correlated_through_the_mcp_config(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    import json

    runner = binding.make_harness_runner(_settings(tmp_path))
    trajectory = await runner.run(conformant_sample(), {})
    argv = fake_cli.argv()
    config_path = Path(argv[argv.index("--mcp-config") + 1])
    # The config lands beside the trace it correlates, so "what ran" is
    # readable from the trajectory directory alone.
    assert config_path.parent == trajectory.trace_dir
    env = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["pydocs-mcp"]["env"]
    assert env["PYDOCS_TRACE__TRAJECTORY_ID"] == trajectory.trajectory_id
    # The env var carries the ROOT; the trajectory carries root / id.
    assert Path(env["PYDOCS_TRACE__DIR"]) == trajectory.trace_dir.parent
    # The CLI validates its session id as a UUID.
    import uuid

    assert uuid.UUID(argv[argv.index("--session-id") + 1])


async def test_a_serve_overlay_rides_the_products_top_level_config_flag(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    import json

    overlay = tmp_path / "suggestions_off.yaml"
    runner = binding.make_harness_runner(_settings(tmp_path, pydocs_config=str(overlay)))
    await runner.run(conformant_sample(), {})
    argv = fake_cli.argv()
    config = json.loads(Path(argv[argv.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
    args = config["mcpServers"]["pydocs-mcp"]["args"]
    assert args.index("--config") < args.index("serve")
    assert str(overlay) in args


async def test_the_run_is_bounded_by_the_harnesss_own_timeout(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    # The inner bound is what kills the process group; the campaign wrapper's
    # outer bound cannot do that, so the harness must carry its own.
    runner = binding.make_harness_runner(_settings(tmp_path, task_timeout_seconds=12.5))
    await runner.run(conformant_sample(), {})
    assert fake_cli.calls[0]["timeout_seconds"] == 12.5


async def test_the_cli_runs_in_the_configured_workspace(
    tmp_path: Path, fake_cli: FakeCliProcess
) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    await runner.run(conformant_sample(), {})
    assert fake_cli.calls[0]["cwd"] == tmp_path / "ws"
