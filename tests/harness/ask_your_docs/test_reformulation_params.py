"""P3: the rewrite step pins temperature 0 — only when the wire sends a temperature and
LangChain did not drop it client-side (model-params v2 §5 rule 7). A no-params arm's rewrite
call is unchanged."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from pydocs_mcp.harness.ask_your_docs.chat_wire import NO_WIRE_PARAMS, resolve_wire
from pydocs_mcp.harness.ask_your_docs.control_support import ControlSupport
from pydocs_mcp.harness.ask_your_docs.reformulation import reformulate
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ChatParamsConfig

_HISTORY = [SimpleNamespace(type="human", content="what is X?")]
_TEMPERATURE_WIRE, _ = resolve_wire(ChatParamsConfig(temperature=0.7), ControlSupport())
_SEED_WIRE, _ = resolve_wire(ChatParamsConfig(seed=3), ControlSupport())


class FakeRewriteLlm:
    """A chat model stand-in: its own ``temperature`` (None = dropped/unset) and a bind log."""

    def __init__(self, temperature: float | None, *, bound_kwargs: dict | None = None) -> None:
        self.temperature = temperature
        self.bound_kwargs = bound_kwargs
        self.binds: list[FakeRewriteLlm] = []
        self.prompts: list[str] = []

    def bind(self, **kwargs: Any) -> FakeRewriteLlm:
        bound = FakeRewriteLlm(self.temperature, bound_kwargs=kwargs)
        self.binds.append(bound)
        return bound

    async def ainvoke(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(content="standalone?")


def _rewrite(llm: FakeRewriteLlm, wire=NO_WIRE_PARAMS) -> str:
    return asyncio.run(reformulate(llm, _HISTORY, "and Y?", wire=wire))


def test_temperature_zero_is_bound_when_a_temperature_is_sent() -> None:
    llm = FakeRewriteLlm(0.7)
    assert _rewrite(llm, _TEMPERATURE_WIRE) == "standalone?"
    (bound,) = llm.binds
    assert bound.bound_kwargs == {"temperature": 0} and bound.prompts and not llm.prompts


def test_no_bind_when_langchain_dropped_the_temperature_client_side() -> None:
    llm = FakeRewriteLlm(None)  # e.g. gpt-5-mini: validate_temperature popped it
    _rewrite(llm, _TEMPERATURE_WIRE)
    assert llm.binds == [] and len(llm.prompts) == 1


def test_no_params_or_no_temperature_leaves_the_rewrite_call_unchanged() -> None:
    """o1 carries temperature=1 by LangChain's own default — no params still means no bind."""
    for wire in (NO_WIRE_PARAMS, _SEED_WIRE):
        llm = FakeRewriteLlm(1.0)
        _rewrite(llm, wire)
        assert llm.binds == [] and len(llm.prompts) == 1


def test_the_real_client_drop_is_what_the_rule_reads() -> None:
    """Pinned on the locked SDK: gpt-5-mini drops a sent temperature, a plain model keeps it."""
    pytest.importorskip("langchain_openai")
    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer
    from pydocs_mcp.harness.ask_your_docs.llm_connection import (
        ConnectionOverride,
        build_chat_model,
        resolve_llm_connection,
    )
    from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

    cfg = LlmConnectionConfig.model_validate({"base_url": "http://llm.test/v1", "model": "m"})
    connection = resolve_llm_connection(
        cfg, {}, ConnectionOverride(), ConnectionOverride(), config_path=None
    )
    kept = build_chat_model(connection, NoBearer(), wire=_TEMPERATURE_WIRE)
    dropped = build_chat_model(connection, NoBearer(), model="gpt-5-mini", wire=_TEMPERATURE_WIRE)
    assert kept.temperature == 0.7 and dropped.temperature is None
