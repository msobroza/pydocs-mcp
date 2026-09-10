"""harness/ask_your_docs/binding — the HarnessRunner implementation.

Fake-based: ``_build_and_execute`` is monkeypatched with a fake graph run
that writes a REAL trace through the recorder, so the derived-view pin is
exercised against the writer's actual bytes without spawning a server.
Real-serve trace lifecycle is stage 3's owned validation.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")

from pydocs_mcp.harness.ask_your_docs import binding
from pydocs_mcp.harness.core.run_contract import (
    ToolCallObservation,
    UndeliverableGuidanceError,
)
from pydocs_mcp.observability.trace_recorder import TraceRecorder
from pydocs_mcp.retrieval.config.app_config import AppConfig

from tests.harness.core._runner_contract import HarnessRunnerContract, conformant_sample


def _settings(tmp_path: Path) -> dict[str, object]:
    return {
        "workspace": str(tmp_path / "ws"),
        "model": "fake-model",
        "trace_root": str(tmp_path / "traces"),
    }


class _FakeExecution:
    """Stands in for _build_and_execute: records a server trace + returns
    an answer with one client-only tool call in the message stream."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def __call__(self, *, sample, settings, overrides, skill_override, task_name, trace_env):
        self.calls.append(
            {
                "sample": dict(sample),
                "overrides": overrides,
                "skill_override": skill_override,
                "task_name": task_name,
                "trace_env": dict(trace_env),
            }
        )
        assert trace_env["PYDOCS_TRACE__ENABLED"] == "true"
        trace_root = Path(trace_env["PYDOCS_TRACE__DIR"])
        trajectory_id = trace_env["PYDOCS_TRACE__TRAJECTORY_ID"]
        recorder = TraceRecorder(trace_dir=trace_root, trajectory_id=trajectory_id)
        recorder.open_trace()
        await recorder.record_tool_success(
            seq=recorder.begin_tool_call(),
            tool="search_codebase",
            args={"query": "q"},
            result={"results": []},
            latency_ms=1.0,
        )
        recorder.close()

        from langchain_core.messages import AIMessage, HumanMessage

        server_call = {"name": "search_codebase", "args": {"query": "q"}, "id": "1"}
        client_call = {"name": "reinspect_images", "args": {"names": ["a"]}, "id": "2"}
        messages = [
            HumanMessage(content=str(sample["rendered_prompt"])),
            AIMessage(content="", tool_calls=[server_call, client_call]),
            AIMessage(content="the answer"),
        ]
        return "the answer", messages


@pytest.fixture
def fake_execution(monkeypatch: pytest.MonkeyPatch) -> _FakeExecution:
    fake = _FakeExecution()
    monkeypatch.setattr(binding, "_build_and_execute", fake)
    return fake


# --- The shared contract suite, bound to this harness ---------------------


class TestAskHarnessRunnerContract(HarnessRunnerContract):
    @pytest.fixture(autouse=True)
    def _bind(self, tmp_path: Path, fake_execution: _FakeExecution) -> None:
        self._tmp_path = tmp_path

    def make_runner(self):
        return binding.make_harness_runner(_settings(self._tmp_path))

    def invalid_settings(self):
        return {**_settings(self._tmp_path), "mystery_knob": True}

    def build_runner_from(self, settings):
        return binding.make_harness_runner(settings)


# --- Binding-specific behavior --------------------------------------------


async def test_trajectory_joins_server_and_client_observations(
    tmp_path: Path, fake_execution: _FakeExecution
) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    trajectory = await runner.run(conformant_sample(), {})
    observed = [(r.tool_name, r.observed_by) for r in trajectory.tool_calls]
    assert observed == [
        ("search_codebase", ToolCallObservation.SERVER),
        ("reinspect_images", ToolCallObservation.CLIENT),
    ]
    assert trajectory.answer == "the answer"
    assert trajectory.turns == 2
    assert trajectory.wall_seconds >= 0.0


async def test_sample_missing_keys_fails_before_any_execution(
    tmp_path: Path, fake_execution: _FakeExecution
) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    with pytest.raises(binding.AskSampleContractError) as excinfo:
        await runner.run({"record_id": "r"}, {})
    assert "task_name" in str(excinfo.value)
    assert fake_execution.calls == []


async def test_prompt_sections_route_to_the_override_seam(
    tmp_path: Path, fake_execution: _FakeExecution
) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    await runner.run(conformant_sample(), {"SYSTEM_PROMPT": "custom system"})
    overrides = fake_execution.calls[0]["overrides"]
    assert overrides.system_prompt == "custom system"
    assert fake_execution.calls[0]["skill_override"] is None


async def test_skill_sections_persist_a_validated_candidate_document(
    tmp_path: Path, fake_execution: _FakeExecution
) -> None:
    from pydocs_mcp.harness.core.skill_artifact_loader import (
        SKILL_ARTIFACT_HEADERS,
        load_skill_artifact,
    )

    guidance = {key: f"text for {key}" for key in SKILL_ARTIFACT_HEADERS}
    runner = binding.make_harness_runner(_settings(tmp_path))
    trajectory = await runner.run(conformant_sample(), guidance)
    written = fake_execution.calls[0]["skill_override"]
    assert written is not None and written.parent == trajectory.trace_dir
    artifact = load_skill_artifact(written)
    assert artifact.backbone == "text for BACKBONE"
    assert artifact.task_head("vuln") == "text for TASK_HEAD: vuln"
    assert fake_execution.calls[0]["task_name"] == "value-task_name"


async def test_incomplete_skill_sections_fail_the_product_firewall(
    tmp_path: Path, fake_execution: _FakeExecution
) -> None:
    from pydocs_mcp.application.description_source import MissingSectionError

    runner = binding.make_harness_runner(_settings(tmp_path))
    with pytest.raises(MissingSectionError):
        await runner.run(conformant_sample(), {"BACKBONE": "alone"})
    assert fake_execution.calls == []


async def test_external_harness_task_heads_are_recognized_but_undelivered(
    tmp_path: Path, fake_execution: _FakeExecution
) -> None:
    # The same candidate serves several harnesses; another harness's
    # harness task head is not an error here — but a genuinely unknown
    # section is.
    from pydocs_mcp.harness.core.skill_artifact_loader import SKILL_ARTIFACT_HEADERS

    guidance = {key: f"text for {key}" for key in SKILL_ARTIFACT_HEADERS}
    runner = binding.make_harness_runner(_settings(tmp_path))
    await runner.run(conformant_sample(), guidance)  # external heads included: fine
    with pytest.raises(UndeliverableGuidanceError) as excinfo:
        await runner.run(conformant_sample(), {"TOOL: grep": "not ours"})
    assert "TOOL: grep" in str(excinfo.value)


def test_delivery_map_digest_is_stable_and_documents_the_channels() -> None:
    # The digest is a PERSISTED, money-costing key: it folds into every ask arm
    # hash (``optimize/arm_runtime.py`` -> ``arm_fingerprint(delivery_map_hash=…)``),
    # so ledger rows carry it and a resume matches it exactly. The self-equality
    # below cannot notice a channel added or re-spelled, and the set assertion
    # that follows is deliberately open at the top — this literal is the only
    # thing that fails when the map MOVES. Moving it orphans every ledger row and
    # forces a re-spend, so it changes only as a deliberate, reviewed measurement
    # bump (the ADR 0017 golden-bytes doctrine that ``benchmarks/tests/optimize/
    # test_arms.py``'s arm-fingerprint literal carries).
    assert (
        binding.delivery_map_digest()
        == "5072aa2e926f9c508233ce84b21edd937a3efe9706a00408f3ff84a998115e6d"
    )
    assert binding.delivery_map_digest() == binding.delivery_map_digest()
    assert set(binding.DELIVERED_SECTION_CHANNELS) >= {
        "BACKBONE",
        "TASK_HEAD: repo_qa",
        "TASK_HEAD: vuln",
        "TASK_HEAD: bug_loc",
        "HARNESS_TASK_HEAD: ask_your_docs.repo_qa",
        "HARNESS_TASK_HEAD: ask_your_docs.vuln",
        "HARNESS_TASK_HEAD: ask_your_docs.bug_loc",
        "SYSTEM_PROMPT",
    }
    assert "HARNESS_TASK_HEAD: external.vuln" in binding.RECOGNIZED_UNDELIVERED_SECTIONS
    assert "HARNESS_TASK_HEAD: external.repo_qa" in binding.RECOGNIZED_UNDELIVERED_SECTIONS
    assert "HARNESS_TASK_HEAD: external.bug_loc" in binding.RECOGNIZED_UNDELIVERED_SECTIONS


def test_task_heads_are_delivered_not_merely_recognized() -> None:
    # The TASK_HEAD tier is harness-invariant but still DELIVERED here: every
    # harness running the task folds the same section into its own channel.
    for key in ("TASK_HEAD: repo_qa", "TASK_HEAD: vuln", "TASK_HEAD: bug_loc"):
        assert binding.DELIVERED_SECTION_CHANNELS[key] == "system_prompt_suffix.skill_block"
        assert key not in binding.RECOGNIZED_UNDELIVERED_SECTIONS


async def test_missing_trace_after_run_is_a_hard_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _traceless_execute(**_kwargs):
        return "answer", []

    monkeypatch.setattr(binding, "_build_and_execute", _traceless_execute)
    runner = binding.make_harness_runner(_settings(tmp_path))
    with pytest.raises(binding.AskTraceMissingError):
        await runner.run(conformant_sample(), {})


async def test_turn_budget_translation_is_the_typed_contract_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Contract rule 3: GraphRecursionError from the toolkit becomes the
    # contract's TurnBudgetExceededError — never a truncated scored answer.
    pytest.importorskip("langgraph")
    from langgraph.errors import GraphRecursionError

    from pydocs_mcp.harness.core.run_contract import TurnBudgetExceededError

    class _ExhaustedGraph:
        async def ainvoke(self, _state, _config):
            raise GraphRecursionError("out of steps")

    async def _fake_build_agent(*_args, **_kwargs):
        return _ExhaustedGraph(), object()

    import contextlib as _contextlib

    import pydocs_mcp.harness.ask_your_docs.agent as agent_module

    @_contextlib.asynccontextmanager
    async def _fake_session_tools(_settings, _trace_env):
        yield []

    monkeypatch.setattr(agent_module, "build_agent", _fake_build_agent)
    monkeypatch.setattr(binding, "_serve_session_tools", _fake_session_tools)
    settings = binding.AskYourDocsRunnerSettings.model_validate(_settings(tmp_path))
    with pytest.raises(TurnBudgetExceededError) as excinfo:
        await binding._build_and_execute(
            sample=conformant_sample(),
            settings=settings,
            overrides=binding.PromptOverrides(),
            skill_override=None,
            task_name=None,
            trace_env={},
        )
    assert excinfo.value.turn_limit == settings.max_agent_turns


# ── LLM-connection design §4.11 (AC-27, AC-40 binding half) ──


def _token_block_yaml(tmp_path: Path) -> str:
    cfg = tmp_path / "pydocs.yaml"
    cfg.write_text(
        "ask_your_docs:\n"
        "  llm:\n"
        "    base_url: http://llm.internal/v1\n"
        "    auth:\n"
        "      token_url: http://localhost:8899/access-token\n"
        "    vision: true\n",
        encoding="utf-8",
    )
    return str(cfg)


class _CountingConfigLoader:
    """Named fake for ``binding.AppConfig``: the real loader, plus a call log."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    def load(self, *, explicit_path: Path) -> AppConfig:
        self.paths.append(str(explicit_path))
        return AppConfig.load(explicit_path=explicit_path)


def test_connection_block_prefers_the_arm_then_the_file(tmp_path: Path, monkeypatch) -> None:
    """R8 / D8: an arm-level harness.llm wins; else the pydocs_config file; else none."""
    for var in list(os.environ):
        if var.startswith("PYDOCS_"):
            monkeypatch.delenv(var, raising=False)
    control = binding.AskYourDocsRunnerSettings.model_validate(_settings(tmp_path))
    assert binding.connection_block_for_binding(control) is None
    from_file = binding.AskYourDocsRunnerSettings.model_validate(
        {**_settings(tmp_path), "pydocs_config": _token_block_yaml(tmp_path)}
    )
    block = binding.connection_block_for_binding(from_file)
    assert block is not None and block.auth is not None
    assert block.auth.token_url == "http://localhost:8899/access-token" and block.vision is True
    from_arm = binding.AskYourDocsRunnerSettings.model_validate(
        {
            **_settings(tmp_path),
            "pydocs_config": _token_block_yaml(tmp_path),
            "harness": {"llm": {"base_url": "http://arm/v1", "auth": {"api_key_env": "ARM_KEY"}}},
        }
    )
    arm_block = binding.connection_block_for_binding(from_arm)
    assert arm_block is not None and arm_block.base_url == "http://arm/v1"


def test_the_config_file_is_read_once_for_a_whole_campaign(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 1300-record campaign parses its pydocs YAML once, not once per sample."""
    config_path = _token_block_yaml(tmp_path)
    loader = _CountingConfigLoader()
    binding.clear_config_block_cache()
    monkeypatch.setattr(binding, "AppConfig", loader)
    settings = binding.AskYourDocsRunnerSettings.model_validate(
        {**_settings(tmp_path), "pydocs_config": config_path}
    )
    first = binding.connection_block_for_binding(settings)
    second = binding.connection_block_for_binding(settings)
    assert first is not None and first is second
    assert loader.paths == [config_path]
    binding.clear_config_block_cache()


async def test_build_and_execute_passes_the_resolved_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-27: the control arm resolves a no-block connection (byte identity); a token-service
    file resolves TOKEN_SERVICE; both keep settings.model / settings.base_url; AC-40: two
    executions share one registry bearer."""
    pytest.importorskip("langgraph")
    import contextlib as _contextlib

    import pydocs_mcp.harness.ask_your_docs.agent as agent_module
    from pydocs_mcp.harness.ask_your_docs.llm_connection import (
        bearer_for_connection,
        clear_bearer_registry,
    )
    from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode

    seen: list = []

    class _Graph:
        async def ainvoke(self, _state, _config):
            from langchain_core.messages import AIMessage

            return {"messages": [AIMessage("answer")]}

    async def _fake_build_agent(*_args, **kwargs):
        seen.append(kwargs["connection"])
        return _Graph(), object()

    @_contextlib.asynccontextmanager
    async def _fake_session_tools(_settings, _trace_env):
        yield []

    monkeypatch.setattr(agent_module, "build_agent", _fake_build_agent)
    monkeypatch.setattr(binding, "_serve_session_tools", _fake_session_tools)
    clear_bearer_registry()

    async def _run(settings_dict: dict) -> None:
        settings = binding.AskYourDocsRunnerSettings.model_validate(settings_dict)
        await binding._build_and_execute(
            sample=conformant_sample(),
            settings=settings,
            overrides=binding.PromptOverrides(),
            skill_override=None,
            task_name=None,
            trace_env={},
        )

    await _run({**_settings(tmp_path), "base_url": "http://x/v1"})
    control = seen[-1]
    assert control.block_present is False and control.auth_mode is AuthMode.ENV_KEY
    assert control.model == "fake-model" and control.base_url == "http://x/v1"
    token_settings = {**_settings(tmp_path), "pydocs_config": _token_block_yaml(tmp_path)}
    await _run(token_settings)
    await _run(token_settings)
    first, second = seen[-2:]
    assert first.auth_mode is AuthMode.TOKEN_SERVICE and first.model == "fake-model"
    assert first.base_url == "http://llm.internal/v1"
    assert first.config_path == token_settings["pydocs_config"]
    assert bearer_for_connection(first) is bearer_for_connection(second)
    clear_bearer_registry()
