"""The Connection dialog's model settings through the real page (model-params v2 §5).

States A-H of model-params-v2-mockup-spec.json, amended by the owner decisions: D5 (vLLM
never offers Thinking Off, so state C shows Auto/On and a YAML ``thinking: off`` is not
sent) and D6 (OpenRouter always shows Max output tokens). The mockup's listing sizes
(436 / 94 / 12 models) are not reproduced; the provider word ending the dialog's status
line is. A test that clicks Apply reads session state right after that run (the dialog's
``st.rerun()`` leaves stale dialog widget state behind, as in test_app_connection_dialog).
"""

from __future__ import annotations

import json
import logging

import httpx
import pytest

pytest.importorskip("streamlit")

from langchain_core.messages import AIMessage

import pydocs_mcp.harness.ask_your_docs.agent as agent_module
import pydocs_mcp.harness.ask_your_docs.reformulation as reformulation_module
from pydocs_mcp.harness.ask_your_docs.chat_wire import STARVATION_MESSAGE
from pydocs_mcp.harness.ask_your_docs.connection_dialog import (
    KEY_APPLY,
    KEY_MODEL,
    KEY_OPEN,
    KEY_TEST,
    STATE_OVERRIDE,
    STATE_TEST_RESULT,
)
from pydocs_mcp.harness.ask_your_docs.llm_connection import ConnectionOverride
from pydocs_mcp.harness.ask_your_docs.model_settings_form import (
    KEY_MAX_TOKENS,
    KEY_RESTORE,
    KEY_SEED,
    KEY_TEMPERATURE,
    KEY_THINKING,
    KEY_TOP_P,
    KEY_USE_YAML,
)
from pydocs_mcp.harness.ask_your_docs.param_feedback import STATE_LEARNED_REJECTIONS
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ChatParamsConfig

from ._connection_fakes import (
    FakeBearer,
    FakeModelGroupInfo,
    FakeModelsEndpoint,
    RecordingTransport,
)

# page_env is autouse: importing it into this module is what arms it.
from ._page_fixtures import PAGE_LOGGER, open_dialog, page, page_env, write_config

_ALL = ["Auto", "Off", "Low", "Medium", "High"]
_NUMBER_KEYS = {
    "temperature": KEY_TEMPERATURE,
    "max_tokens": KEY_MAX_TOKENS,
    "top_p": KEY_TOP_P,
    "seed": KEY_SEED,
}
_CONTROL_WORDS = ("Temperature", "Max output tokens", "Top p", "Seed", "not sent", "hidden")
_UNKNOWN = "provider unknown — settings unverified"
_QUESTION = "what does Pool.acquire return?"

_OPENROUTER = "https://openrouter.ai/api/v1"
_OPENROUTER_MODEL = "qwen/qwen3.8-27b"
_OPENROUTER_ENTRY = FakeModelsEndpoint.openrouter_entry(
    _OPENROUTER_MODEL,
    ("reasoning_effort", "temperature", "top_p", "max_tokens", "seed"),
    efforts=("xhigh", "medium", "low"),
    default_parameters={"temperature": 1, "top_p": 0.95},
)
_OPENROUTER_PARAMS = {"thinking": "low", "temperature": 0.2, "max_tokens": 4096}
_UNKNOWN_URL = "https://inference.example.net/v1"
_UNKNOWN_IDS = ("acme-chat-7b", "acme-code-3b", "acme-tiny-1b")
_VLLM_URL = "http://localhost:8000/v1"
_QWEN = "Qwen/Qwen3-8B"


def _dialog(
    tmp_path, monkeypatch, *, base_url, model, listing, params=None, provider=None, **seeds
):
    config = write_config(
        tmp_path, base_url=base_url, model=model, params=params, provider=provider
    )
    monkeypatch.setenv("PYDOCS_CONFIG", config)
    transport = seeds.pop("transport", None)
    if transport is not None:
        seeds["connection_transport"] = transport.transport
    return open_dialog(page(connection_bearer=FakeBearer(), listing=listing, **seeds))


def _controls(at) -> dict:
    """The settings section as rendered: Thinking's (options, value), each field's value."""
    shown: dict = {}
    for group in at.segmented_control:
        if group.key == KEY_THINKING:
            shown["thinking"] = (list(group.options), group.value)
    fields = {field.key: field.value for field in at.number_input}
    shown.update({name: fields[key] for name, key in _NUMBER_KEYS.items() if key in fields})
    return shown


def _placeholder(at, key: str) -> str:
    return at.number_input(key=key).proto.placeholder


def _more_shown(at) -> bool:
    return any(expander.label == "More" for expander in at.expander)


def _dialog_status(at) -> str:
    return next(c.value for c in at.caption if " listed · vision:" in c.value)


def _assert_no_settings_captions(at) -> None:
    """MASK: nothing ever explains a hidden control; the Test line and the log do that."""
    for caption in at.caption:
        assert not any(word in caption.value for word in _CONTROL_WORDS), caption.value


def _test_line(at) -> str:
    at.button(key=KEY_TEST).click().run()
    assert not at.exception, at.exception
    return at.session_state[STATE_TEST_RESULT]


def _sent_body(transport: RecordingTransport) -> dict:
    return json.loads(transport.requests[-1].content)


def test_state_a_openrouter_intersects_efforts_and_keeps_the_cap(tmp_path, monkeypatch) -> None:
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url=_OPENROUTER,
        model=_OPENROUTER_MODEL,
        listing=FakeModelsEndpoint(entry=_OPENROUTER_ENTRY),
        params=_OPENROUTER_PARAMS,
    )
    assert _dialog_status(at).endswith(" · OpenRouter")
    assert _controls(at) == {
        "thinking": (["Auto", "Low", "Medium"], "Low"),
        "temperature": 0.2,
        "max_tokens": 4096,
        "top_p": None,
        "seed": None,
    }
    assert _placeholder(at, KEY_TEMPERATURE) == "1" and _placeholder(at, KEY_TOP_P) == "0.95"
    assert _more_shown(at)
    _assert_no_settings_captions(at)


def test_the_test_line_lists_exactly_the_wire_apply_would_send(tmp_path, monkeypatch) -> None:
    ok = RecordingTransport([200], reply="OK")
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url=_OPENROUTER,
        model=_OPENROUTER_MODEL,
        listing=FakeModelsEndpoint(entry=_OPENROUTER_ENTRY),
        params=_OPENROUTER_PARAMS,
        transport=ok,
    )
    assert _test_line(at) == (
        "test passed: OK · sent reasoning_effort=low, temperature=0.2, max_completion_tokens=4096"
    )
    body = _sent_body(ok)
    assert (body["reasoning_effort"], body["temperature"]) == ("low", 0.2)
    assert body["max_completion_tokens"] == 4096
    assert "top_p" not in body and "seed" not in body and len(ok.requests) == 1


def test_state_b_openai_gpt5_hides_sampling_and_off(tmp_path, monkeypatch) -> None:
    ok = RecordingTransport([200], reply="OK")
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url="https://api.openai.com/v1",
        model="gpt-5-mini",
        listing=FakeModelsEndpoint(ids=("gpt-5-mini",)),
        params={"temperature": 0.2},
        transport=ok,
    )
    assert _dialog_status(at) == "1 model listed · vision: yes (configured) · OpenAI"
    assert _controls(at) == {
        "thinking": (["Auto", "Low", "Medium", "High"], "Auto"),
        "max_tokens": None,
        "seed": None,
    }
    assert _placeholder(at, KEY_MAX_TOKENS) == "model default"
    _assert_no_settings_captions(at)
    assert _test_line(at) == "test passed: OK · sent nothing beyond the model"  # 0.2 is masked
    assert "temperature" not in _sent_body(ok)


def test_state_c_vllm_qwen3_shows_on_off_without_off(tmp_path, monkeypatch) -> None:
    ok = RecordingTransport([200], reply="OK")
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url=_VLLM_URL,
        model=_QWEN,
        listing=FakeModelsEndpoint(entry=FakeModelsEndpoint.vllm_entry(_QWEN)),
        params={"thinking": "off", "max_tokens": 1024},
        transport=ok,
    )
    assert _dialog_status(at).endswith(" · vLLM")
    # D5: Off is hidden on vLLM, so the saved Off selects nothing and is not sent.
    assert _controls(at) == {
        "thinking": (["Auto", "On"], None),
        "temperature": None,
        "max_tokens": 1024,
        "top_p": None,
        "seed": None,
    }
    assert at.number_input(key=KEY_MAX_TOKENS).proto.max == 40960
    _assert_no_settings_captions(at)
    assert _test_line(at) == "test passed: OK · sent max_completion_tokens=1024"
    assert "reasoning_effort" not in _sent_body(ok)


def test_a_declared_generic_provider_is_not_re_routed_to_vllm(tmp_path, monkeypatch) -> None:
    """`provider: generic` is the one line that opts out of the vLLM mask (§2 step 1, D5)."""
    ok = RecordingTransport([200], reply="OK")
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url=_VLLM_URL,
        model=_QWEN,
        listing=FakeModelsEndpoint(entry=FakeModelsEndpoint.vllm_entry(_QWEN)),
        params={"thinking": "off"},
        provider="generic",
        transport=ok,
    )
    assert _dialog_status(at).endswith(_UNKNOWN)
    assert _controls(at)["thinking"] == (_ALL, "Off")
    assert _test_line(at) == "test passed: OK · sent reasoning_effort=none"


def test_state_d_litellm_anthropic_couples_sampling_to_thinking(tmp_path, monkeypatch) -> None:
    ok = RecordingTransport([200], reply="OK")
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url="https://llm.corp.example/v1",
        model="team-sonnet",
        listing=FakeModelsEndpoint(ids=("team-sonnet",)),
        params={"thinking": "medium", "max_tokens": 8192},
        group_info=FakeModelGroupInfo(),
        transport=ok,
    )
    assert _dialog_status(at).endswith(" · LiteLLM")
    assert _controls(at) == {"thinking": (_ALL, "Medium"), "max_tokens": 8192}
    assert at.number_input(key=KEY_MAX_TOKENS).proto.max == 64000
    assert not _more_shown(at)  # Top p hidden while thinking, Seed not listed: More is empty
    _assert_no_settings_captions(at)
    assert _test_line(at) == (
        "test passed: OK · sent reasoning_effort=medium, max_completion_tokens=8192"
    )
    at.segmented_control(key=KEY_THINKING).set_value("Auto").run()
    assert {"temperature", "top_p"} <= set(_controls(at)) and _more_shown(at)


def test_state_e_unknown_endpoint_shows_everything_unverified(tmp_path, monkeypatch) -> None:
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url=_UNKNOWN_URL,
        model="acme-chat-7b",
        listing=FakeModelsEndpoint(ids=_UNKNOWN_IDS),
    )
    assert _dialog_status(at) == f"3 models listed · vision: yes (configured) · {_UNKNOWN}"
    assert _controls(at) == {
        "thinking": (_ALL, "Auto"),
        "temperature": None,
        "max_tokens": None,
        "top_p": None,
        "seed": None,
    }
    assert {_placeholder(at, key) for key in _NUMBER_KEYS.values()} == {"model default"}
    _assert_no_settings_captions(at)


def test_state_e_ollama_hides_max_output_tokens(tmp_path, monkeypatch) -> None:
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url="http://localhost:11434/v1",
        model="qwen3:8b",
        listing=FakeModelsEndpoint(entry=FakeModelsEndpoint.ollama_entry("qwen3:8b")),
    )
    assert _dialog_status(at).endswith(_UNKNOWN)
    assert "max_tokens" not in _controls(at) and "temperature" in _controls(at)


def test_state_f_more_after_one_learned_rejection(tmp_path, monkeypatch) -> None:
    learned = {(_UNKNOWN_URL, "acme-chat-7b"): frozenset({"thinking"})}
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url=_UNKNOWN_URL,
        model="acme-chat-7b",
        listing=FakeModelsEndpoint(ids=_UNKNOWN_IDS),
        **{STATE_LEARNED_REJECTIONS: learned},
    )
    assert "thinking" not in _controls(at)
    assert {"top_p", "seed"} <= set(_controls(at))
    assert at.button(key=KEY_USE_YAML).label == "Use YAML settings"
    assert at.button(key=KEY_RESTORE).label == "Restore hidden settings (1)"
    _assert_no_settings_captions(at)


def test_apply_stores_the_complete_snapshot_and_a_model_change_keeps_it(
    tmp_path, monkeypatch
) -> None:
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url=_UNKNOWN_URL,
        model="acme-chat-7b",
        listing=FakeModelsEndpoint(ids=_UNKNOWN_IDS),
        params={"seed": 7},
    )
    at.number_input(key=KEY_TEMPERATURE).set_value(0.3).run()
    at.selectbox(key=KEY_MODEL).select("acme-code-3b").run()
    assert at.number_input(key=KEY_TEMPERATURE).value == 0.3
    at.button(key=KEY_APPLY).click().run()
    assert at.session_state[STATE_OVERRIDE] == ConnectionOverride(
        _UNKNOWN_URL, "acme-code-3b", ChatParamsConfig(temperature=0.3, seed=7)
    )


def test_use_yaml_settings_clears_the_override_params(tmp_path, monkeypatch) -> None:
    stored = ConnectionOverride(model="acme-chat-7b", params=ChatParamsConfig(temperature=0.9))
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url=_UNKNOWN_URL,
        model="acme-chat-7b",
        listing=FakeModelsEndpoint(ids=_UNKNOWN_IDS),
        params={"temperature": 0.2},
        **{STATE_OVERRIDE: stored},
    )
    assert at.number_input(key=KEY_TEMPERATURE).value == 0.9  # pre-filled from the effective set
    at.button(key=KEY_USE_YAML).click().run()
    assert at.number_input(key=KEY_TEMPERATURE).value == 0.2
    at.button(key=KEY_APPLY).click().run()
    assert at.session_state[STATE_OVERRIDE].params is None


def _seed_agent(monkeypatch, ask) -> None:
    """build hands back a stub pair, reformulate is the identity, ``ask`` is the test's."""

    async def build_agent(*_args, **_kwargs):
        return object(), object()

    async def reformulate(_llm, _history, question, **_kwargs):
        return question

    monkeypatch.setattr(agent_module, "build_agent", build_agent)
    monkeypatch.setattr(agent_module, "ask", ask)
    monkeypatch.setattr(reformulation_module, "reformulate", reformulate)


def _bad_request(param: str) -> Exception:
    """The SDK's 400 as an endpoint without that field sends it (``.param`` names it)."""
    import openai

    request = httpx.Request("POST", f"{_VLLM_URL}/chat/completions")
    body = {"message": f"unsupported parameter '{param}'", "type": "invalid", "param": param}
    response = httpx.Response(400, json={"error": body}, request=request)
    return openai.BadRequestError(f"Error code: 400 - {body}", response=response, body=body)


def _events(caplog, event: str) -> list[dict]:
    records = [json.loads(r.getMessage()) for r in caplog.records if r.name == PAGE_LOGGER]
    return [record for record in records if record.get("event") == event]


def _qwen_page(tmp_path, monkeypatch, params: dict):
    monkeypatch.setenv(
        "PYDOCS_CONFIG", write_config(tmp_path, base_url=_VLLM_URL, model=_QWEN, params=params)
    )
    at = page(connection_bearer=FakeBearer(), listing=FakeModelsEndpoint(ids=(_QWEN,)))
    at.run()
    assert not at.exception, at.exception
    return at


def test_state_g_a_rejected_field_is_hidden_for_the_session(tmp_path, monkeypatch, caplog) -> None:
    async def ask(*_args, **_kwargs):
        raise _bad_request("reasoning_effort")

    _seed_agent(monkeypatch, ask)
    at = _qwen_page(tmp_path, monkeypatch, {"thinking": "low"})
    with caplog.at_level(logging.WARNING, logger=PAGE_LOGGER):
        at.chat_input[0].set_value(_QUESTION).run()
    assert not at.exception, at.exception
    assert [e.value for e in at.error] == [
        "The endpoint rejected Thinking for Qwen/Qwen3-8B (400), so it's off for this session. "
        "Send your question again."
    ]
    assert _events(caplog, "chat_param_rejected") == [
        {"event": "chat_param_rejected", "param": "reasoning_effort", "status": 400}
    ]
    at.button(key=KEY_OPEN).click().run()
    assert not at.exception, at.exception
    assert "thinking" not in _controls(at)
    assert at.button(key=KEY_RESTORE).label == "Restore hidden settings (1)"
    at.button(key=KEY_RESTORE).click().run()
    assert _controls(at)["thinking"] == (_ALL, "Low")


def test_state_g_a_rejected_output_cap_is_hidden_on_openrouter_too(tmp_path, monkeypatch) -> None:
    """The message promises "off for this session": the resend must not carry the cap again."""

    async def ask(*_args, **_kwargs):
        raise _bad_request("max_completion_tokens")

    _seed_agent(monkeypatch, ask)
    monkeypatch.setenv(
        "PYDOCS_CONFIG",
        write_config(
            tmp_path, base_url=_OPENROUTER, model=_OPENROUTER_MODEL, params={"max_tokens": 4096}
        ),
    )
    at = page(connection_bearer=FakeBearer(), listing=FakeModelsEndpoint(entry=_OPENROUTER_ENTRY))
    at.run()
    at.chat_input[0].set_value(_QUESTION).run()
    assert not at.exception, at.exception
    assert [e.value for e in at.error] == [
        f"The endpoint rejected Max output tokens for {_OPENROUTER_MODEL} (400), so it's off "
        "for this session. Send your question again."
    ]
    at.button(key=KEY_OPEN).click().run()
    assert not at.exception, at.exception
    assert "max_tokens" not in _controls(at)
    assert at.button(key=KEY_RESTORE).label == "Restore hidden settings (1)"


def test_state_h_a_starved_reply_says_how_to_fix_it(tmp_path, monkeypatch, caplog) -> None:
    async def ask(*_args, on_final=None, **_kwargs):
        on_final(AIMessage(content="", response_metadata={"finish_reason": "length"}))
        return ""

    _seed_agent(monkeypatch, ask)
    at = _qwen_page(tmp_path, monkeypatch, {"thinking": "low", "max_tokens": 64})
    with caplog.at_level(logging.WARNING, logger=PAGE_LOGGER):
        at.chat_input[0].set_value(_QUESTION).run()
    assert not at.exception, at.exception
    assert at.session_state.messages[-1] == ("assistant", STARVATION_MESSAGE)
    assert _events(caplog, "chat_reply_starved") == [
        {"event": "chat_reply_starved", "finish_reason": "length"}
    ]


def _sidebar_reasoning(at) -> str:
    """The activity panel's own sidebar line (never a cell of the status line)."""
    [caption] = [c.value for c in at.caption if c.value.startswith("Reasoning:")]
    return caption


def test_the_sidebar_says_reasoning_is_off_when_thinking_off_is_sent(tmp_path, monkeypatch) -> None:
    """Model-params v2 §7: your own setting answers the caption at once — no two-turn wait."""
    monkeypatch.setenv(
        "PYDOCS_CONFIG",
        write_config(
            tmp_path, base_url=_UNKNOWN_URL, model=_UNKNOWN_IDS[0], params={"thinking": "off"}
        ),
    )
    at = page(connection_bearer=FakeBearer(), listing=FakeModelsEndpoint(ids=_UNKNOWN_IDS))
    at.run()
    assert not at.exception, at.exception
    assert _sidebar_reasoning(at) == "Reasoning: off (your setting)"


def test_a_thinking_off_the_model_hides_never_claims_the_sidebar(tmp_path, monkeypatch) -> None:
    """The caption follows the WIRE: on vLLM (D5) Off is hidden and not sent, so it is not off."""
    at = _dialog(
        tmp_path,
        monkeypatch,
        base_url=_VLLM_URL,
        model=_QWEN,
        listing=FakeModelsEndpoint(entry=FakeModelsEndpoint.vllm_entry(_QWEN)),
        params={"thinking": "off"},
    )
    at.run()  # the dialog's reading of this model now decides the page's wire
    assert not at.exception, at.exception
    assert _sidebar_reasoning(at) == "Reasoning: unknown until the first answer"
