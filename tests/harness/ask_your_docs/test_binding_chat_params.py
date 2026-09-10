"""Model-params v2 §6 at the eval binding: a sealed source, raise before spend, the sent record.

Fake-based like ``test_binding``: ``agent.build_agent`` is the factory spy and
``binding._serve_session_tools`` the spawn spy, so "before any spend" is an
assertion that the spawn spy was never entered.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

pytest.importorskip("langchain_core")

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs import binding
from pydocs_mcp.observability.trace_env import trace_subprocess_env
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ThinkingLevel

from tests.harness.core._runner_contract import conformant_sample

_TRAJECTORY_ID = "t" * 32
_FAKE_BEARER = "sk-fake-arm-bearer-not-a-real-key"  # a test fixture, never a real key


def _settings(tmp_path: Path, model: str = "gpt-5-mini", **extra: object) -> dict[str, object]:
    return {
        "workspace": str(tmp_path / "ws"),
        "model": model,
        "trace_root": str(tmp_path / "traces"),
        **extra,
    }


def _params_arm(tmp_path: Path, llm: dict[str, object], **extra: object) -> dict[str, object]:
    return _settings(tmp_path, harness={"llm": llm}, **extra)


class _AnsweringGraph:
    async def ainvoke(self, _state: object, _config: object) -> dict[str, list]:
        from langchain_core.messages import AIMessage

        return {"messages": [AIMessage("answer")]}


class FakeAgentFactory:
    """Stands in for ``agent.build_agent``: records every build's keyword arguments."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def __call__(self, *_args: object, **kwargs: object) -> tuple[_AnsweringGraph, object]:
        self.calls.append(kwargs)
        return _AnsweringGraph(), object()


class FakeServeSpawn:
    """Stands in for ``binding._serve_session_tools``: counts serve subprocess spawns."""

    def __init__(self) -> None:
        self.spawns = 0

    @contextlib.asynccontextmanager
    async def session(self, _settings: object, _trace_env: object) -> AsyncIterator[list]:
        self.spawns += 1
        yield []


@pytest.fixture(autouse=True)
def _hermetic_config_layer(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No ``PYDOCS_*`` from the developer's shell, no block memo between tests."""
    for name in list(os.environ):
        if name.upper().startswith("PYDOCS_"):
            monkeypatch.delenv(name, raising=False)
    binding.clear_config_block_cache()
    yield
    binding.clear_config_block_cache()


@pytest.fixture
def seams(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeAgentFactory, FakeServeSpawn]:
    pytest.importorskip("langgraph")
    import pydocs_mcp.harness.ask_your_docs.agent as agent_module

    factory, spawn = FakeAgentFactory(), FakeServeSpawn()
    monkeypatch.setattr(agent_module, "build_agent", factory)
    monkeypatch.setattr(binding, "_serve_session_tools", spawn.session)
    return factory, spawn


async def _execute(tmp_path: Path, settings: dict[str, object]) -> None:
    await binding._build_and_execute(
        sample=conformant_sample(),
        settings=binding.AskYourDocsRunnerSettings.model_validate(settings),
        overrides=binding.PromptOverrides(),
        skill_override=None,
        task_name=None,
        trace_env=trace_subprocess_env(tmp_path / "traces", _TRAJECTORY_ID),
    )


def _record_path(tmp_path: Path) -> Path:
    return tmp_path / "traces" / _TRAJECTORY_ID / "sent_settings.json"


def _pydocs_yaml(tmp_path: Path, llm_lines: str = "") -> str:
    path = tmp_path / "pydocs.yaml"
    path.write_text(
        "ask_your_docs:\n  llm:\n    base_url: http://llm.internal/v1\n" + llm_lines,
        encoding="utf-8",
    )
    return str(path)


async def test_arm_params_reach_the_factory(tmp_path: Path, seams) -> None:
    factory, spawn = seams
    arm = _params_arm(tmp_path, {"params": {"thinking": "low", "max_tokens": 900}})

    await _execute(tmp_path, arm)

    (build,) = factory.calls
    assert build["wire"].chat_model_kwargs() == {"reasoning_effort": "low", "max_tokens": 900}
    assert build["connection"].params.thinking is ThinkingLevel.LOW
    assert spawn.spawns == 1


@pytest.mark.parametrize(
    ("model", "llm", "message"),
    [
        (
            "gpt-5-mini",
            {"params": {"thinking": "off"}},
            r"arm harness\.llm\.params\.thinking='off' is not honoured by model 'gpt-5-mini' "
            r"\(no 'none' effort\); remove it or change the model",
        ),
        # D5: no vLLM profile offers Thinking Off until it is verified end to end.
        (
            "meta-llama/Llama-3.1-8B-Instruct",
            {"provider": "vllm", "base_url": "http://gpu:8000/v1", "params": {"thinking": "off"}},
            r"thinking='off' is not honoured by model 'meta-llama/Llama-3\.1-8B-Instruct'",
        ),
        ("o3-mini", {"params": {"temperature": 0.2}}, r"params\.temperature=0\.2 is not honoured"),
    ],
)
async def test_a_table_known_conflict_raises_before_the_serve_spawn(
    tmp_path: Path, seams, model: str, llm: dict[str, object], message: str
) -> None:
    factory, spawn = seams

    with pytest.raises(PydocsMCPError, match=message):
        await _execute(tmp_path, _params_arm(tmp_path, llm, model=model))

    assert spawn.spawns == 0 and factory.calls == []
    assert not _record_path(tmp_path).exists()


async def test_file_sourced_params_are_refused_by_key_name(tmp_path: Path, seams) -> None:
    factory, spawn = seams
    config = _pydocs_yaml(
        tmp_path,
        "    provider: openrouter\n    params:\n      temperature: 0.37\n      thinking: low\n",
    )

    with pytest.raises(PydocsMCPError) as excinfo:
        await _execute(tmp_path, _settings(tmp_path, pydocs_config=config))

    message = str(excinfo.value)
    assert "params.temperature" in message and "params.thinking" in message
    assert "provider" in message
    assert "0.37" not in message and "openrouter" not in message and "'low'" not in message
    assert spawn.spawns == 0 and factory.calls == []


@pytest.mark.parametrize(
    ("variable", "value", "key"),
    [
        ("PYDOCS_ASK_YOUR_DOCS__LLM__PARAMS__THINKING", "high", "params.thinking"),
        ("PYDOCS_ASK_YOUR_DOCS__LLM__PROVIDER", "vllm", "provider"),
    ],
)
async def test_env_sourced_params_over_a_file_block_are_refused(
    tmp_path: Path, seams, monkeypatch: pytest.MonkeyPatch, variable: str, value: str, key: str
) -> None:
    """The file block already carries AppConfig's env layer, so one rule seals both (P4)."""
    factory, spawn = seams
    monkeypatch.setenv(variable, value)

    with pytest.raises(PydocsMCPError, match=key) as excinfo:
        await _execute(tmp_path, _settings(tmp_path, pydocs_config=_pydocs_yaml(tmp_path)))

    assert value not in str(excinfo.value)
    assert spawn.spawns == 0 and factory.calls == []


async def test_an_arm_pinned_block_ignores_the_file(tmp_path: Path, seams) -> None:
    factory, _spawn = seams
    config = _pydocs_yaml(tmp_path, "    params:\n      temperature: 0.37\n")
    arm = _params_arm(tmp_path, {"params": {"thinking": "low"}}, pydocs_config=config)

    await _execute(tmp_path, arm)

    (build,) = factory.calls
    assert build["wire"].chat_model_kwargs() == {"reasoning_effort": "low"}


async def test_the_rollout_records_what_was_sent(tmp_path: Path, seams) -> None:
    arm = _params_arm(tmp_path, {"params": {"thinking": "low", "max_tokens": 900}})

    await _execute(tmp_path, arm)

    record = json.loads(_record_path(tmp_path).read_text(encoding="utf-8"))
    assert record == {
        "provider": "openai",
        "sent": {"reasoning_effort": "low", "max_completion_tokens": 900},
        "thinking_map": 1,
    }
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"))
    assert binding.sent_settings_fingerprint(arm) == hashlib.sha256(canonical.encode()).hexdigest()


async def test_unknown_support_on_generic_is_sent_as_configured(tmp_path: Path, seams) -> None:
    """Nothing is known about a generic server, so nothing raises; a 400 would fail loudly."""
    params = {"thinking": "off", "temperature": 0.3, "top_p": 0.9, "seed": 7}
    arm = _params_arm(tmp_path, {"base_url": "http://llm.internal/v1", "params": params}, model="m")

    await _execute(tmp_path, arm)

    record = json.loads(_record_path(tmp_path).read_text(encoding="utf-8"))
    assert record["provider"] == "generic"
    assert record["sent"] == {
        "reasoning_effort": "none",
        "temperature": 0.3,
        "top_p": 0.9,
        "seed": 7,
    }


def _refuse_endpoint_call(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("the eval binding made an endpoint call to decide what it sends")


async def test_the_binding_decides_the_wire_without_any_endpoint_call(
    tmp_path: Path, seams, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§6 rule 2: the wire profile is the declared provider or the host, never a fetched
    listing and never the LiteLLM probe — so a rollout is deterministic and sealing it
    costs no call. A gateway that WOULD answer /model_group/info still reads as generic."""
    import pydocs_mcp.harness.ask_your_docs.litellm_probe as probe_module
    import pydocs_mcp.harness.ask_your_docs.model_listing as listing_module

    monkeypatch.setattr(probe_module, "fetch_litellm_group_info", _refuse_endpoint_call)
    monkeypatch.setattr(listing_module, "fetch_models_payload", _refuse_endpoint_call)
    gateway = {"base_url": "http://gateway.internal/v1", "params": {"thinking": "off"}}

    await _execute(tmp_path, _params_arm(tmp_path, gateway, model="team-sonnet"))

    record = json.loads(_record_path(tmp_path).read_text(encoding="utf-8"))
    assert record["provider"] == "generic"
    assert record["sent"] == {"reasoning_effort": "none"}


async def test_no_credential_reaches_the_record_or_the_log(
    tmp_path: Path, seams, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:
    """G8: model settings are not secrets, but the bearer beside them is — and neither the
    rollout record nor the params log line has any field that could carry one."""
    monkeypatch.setenv("ARM_BEARER_ENV", _FAKE_BEARER)
    authed = {
        "base_url": "http://llm.internal/v1",
        "auth": {"api_key_env": "ARM_BEARER_ENV"},
        "params": {"thinking": "low", "seed": 7},
    }

    with caplog.at_level(logging.INFO):
        await _execute(tmp_path, _params_arm(tmp_path, authed, model="m"))

    written = _record_path(tmp_path).read_text(encoding="utf-8")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert _FAKE_BEARER not in written and _FAKE_BEARER not in logged
    assert set(json.loads(written)) == {"provider", "sent", "thinking_map"}
    assert json.loads(written)["sent"] == {"reasoning_effort": "low", "seed": 7}


@pytest.mark.parametrize(
    "harness",
    [None, {}, {"llm": {"base_url": "http://llm.internal/v1"}}, {"llm": {"params": {}}}],
)
def test_the_sent_fingerprint_is_none_without_params(harness: dict | None) -> None:
    """D3: an arm without params adds nothing to its identity, so no existing hash moves."""
    settings: dict[str, object] = {"model": "gpt-5-mini"}
    if harness is not None:
        settings["harness"] = harness
    assert binding.sent_settings_fingerprint(settings) is None
