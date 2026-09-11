"""build_agent × the LLM connection (design §4.5, §4.7, §4.8 — AC-17, AC-18 gate rows, AC-34,
AC-42). Fake MCP client + fake graph builder, as in test_prompt_seam.py."""

from __future__ import annotations

import pytest

pytest.importorskip("langgraph")

from pydocs_mcp.harness.ask_your_docs import agent as agent_mod
from pydocs_mcp.harness.ask_your_docs.architectures import AgentArchitectureError
from pydocs_mcp.harness.ask_your_docs.multimodal import CapabilitySource, ModelCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

from ._agent_fakes import FakeLlm, FakeMultiServerMCPClient, FakeVisionLlm

_SEES = ModelCapabilities(True, CapabilitySource.CONFIGURED)
_BLIND = ModelCapabilities(False, CapabilitySource.CONFIGURED)


def _build(name: str, **kw):
    return agent_mod._build_architecture(
        name, llm=FakeLlm(), tools=(), prompt="P", config=AskYourDocsConfig(), model="main-a", **kw
    )


def test_explicit_inline_under_a_separate_vision_model_is_rejected() -> None:
    """AC-18 / E13: inline routes images to the main model, which is blind under SEPARATE_MODEL."""
    with pytest.raises(AgentArchitectureError) as excinfo:
        _build("inline", capabilities=_BLIND, vision_llm=FakeVisionLlm(), vision_capabilities=_SEES)
    assert "ask_your_docs.llm.vision" in str(excinfo.value) and "'main-a'" in str(excinfo.value)


def test_vision_route_gate_checks_the_image_model() -> None:
    """AC-18 / E13: vision_subagent on a text-only separate vision model is rejected naming
    vision.model; on a seeing one it builds even though the main model is blind."""
    with pytest.raises(AgentArchitectureError, match="vision.model is text-only"):
        _build(
            "vision_subagent",
            capabilities=_BLIND,
            vision_llm=FakeVisionLlm(),
            vision_capabilities=_BLIND,
        )
    graph = _build(
        "vision_subagent",
        capabilities=_BLIND,
        vision_llm=FakeVisionLlm(),
        vision_capabilities=_SEES,
    )
    assert "vision_extract" in set(graph.get_graph().nodes)


def test_separate_route_refusal_names_the_blind_vision_model() -> None:
    """E13 carries the OFFENDING VALUE, not just the YAML key it lives under — no model
    object knows its configured name, so the build seam passes it to the gate."""
    with pytest.raises(AgentArchitectureError, match="vision.model 'vision-b' is text-only"):
        _build(
            "vision_subagent",
            capabilities=_BLIND,
            vision_llm=FakeVisionLlm(),
            vision_capabilities=_BLIND,
            vision_model="vision-b",
        )


def test_main_route_refusal_drops_dead_detection_advice_for_a_configured_verdict() -> None:
    """A CONFIGURED verdict came from ask_your_docs.llm.vision, which the §4.7 resolver answers
    WITHOUT reading the detection ladder — so detection.override cannot be the advertised fix."""
    with pytest.raises(AgentArchitectureError) as configured:
        _build("inline", capabilities=_BLIND)
    assert "ask_your_docs.multimodal.detection.override" not in str(configured.value)
    assert "ask_your_docs.llm.vision: true" in str(configured.value)
    with pytest.raises(AgentArchitectureError) as detected:
        _build("inline", capabilities=ModelCapabilities(False, CapabilitySource.STATIC))
    assert "ask_your_docs.multimodal.detection.override" in str(detected.value)


def test_text_react_never_checks_capabilities() -> None:
    graph = _build("text_react", capabilities=_BLIND)
    assert "vision_extract" not in set(graph.get_graph().nodes)


def test_context_defaults_survive_the_build_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Null Objects reach the context THROUGH _build_architecture: omitting the three
    connection keywords must not plant a None where a model / capability / bearer belongs
    (the context's own defaults are the single source of that policy)."""
    from pydocs_mcp.harness.ask_your_docs.architectures import AgentBuildContext
    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer

    built: list[AgentBuildContext] = []

    def _recording_context(**kwargs) -> AgentBuildContext:
        built.append(AgentBuildContext(**kwargs))
        return built[-1]

    monkeypatch.setattr(agent_mod, "AgentBuildContext", _recording_context)
    _build("text_react", capabilities=_SEES)
    (ctx,) = built
    assert ctx.vision_llm is ctx.llm
    assert ctx.vision_capabilities is _SEES
    assert isinstance(ctx.bearer, NoBearer)


# ── build_agent × the connection (AC-17, AC-34 build half, AC-42) ──

import asyncio
import inspect

from pydocs_mcp.harness.ask_your_docs import multimodal
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer, TokenServiceError
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    clear_bearer_registry,
    resolve_llm_connection,
)
from pydocs_mcp.harness.ask_your_docs.multimodal import clear_detection_cache
from pydocs_mcp.retrieval.config.ask_your_docs_models import LlmConnectionConfig

from ._connection_fakes import FakeBearer, FakeModelsEndpoint

_CATALOG = {"proj": ["pkg_a"]}


@pytest.fixture
def harness(monkeypatch):
    """A network-free build_agent: fake MCP client, fake graph builder, spied chat models."""
    clear_bearer_registry()
    clear_detection_cache()
    built: list[dict] = []
    models: list[dict] = []

    def _capture_build(name, **kwargs):
        built.append({"name": name, **kwargs})
        return "GRAPH"

    def _spy_chat_model(connection, bearer, **kwargs):
        models.append({"connection": connection, "bearer": bearer, **kwargs})
        return FakeLlm()

    FakeMultiServerMCPClient.recorded.clear()
    monkeypatch.setattr(agent_mod, "MultiServerMCPClient", FakeMultiServerMCPClient)
    monkeypatch.setattr(agent_mod, "_build_architecture", _capture_build)
    monkeypatch.setattr(agent_mod, "build_chat_model", _spy_chat_model)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    yield built, models
    clear_bearer_registry()
    clear_detection_cache()


def _connection(block: dict):
    cfg = LlmConnectionConfig.model_validate(block)
    return resolve_llm_connection(
        cfg, {}, ConnectionOverride(), ConnectionOverride(), config_path=None
    )


def test_build_agent_resolves_capabilities_once_unless_injected(harness, monkeypatch) -> None:
    """AC-17: with capabilities=None the single §4.7 call site runs once; an injected verdict
    skips it and doubles as the vision verdict."""
    built, _models = harness
    calls: list[tuple] = []

    async def _spy_resolve(connection, bearer, detection):
        calls.append((connection, bearer))
        return _SEES, _SEES

    monkeypatch.setattr(agent_mod, "resolve_vision_capabilities", _spy_resolve)
    connection = _connection({"base_url": "http://llm.test/v1", "model": "main-a", "vision": True})
    asyncio.run(
        agent_mod.build_agent(
            "/tmp/ws", None, catalog=_CATALOG, connection=connection, bearer=NoBearer()
        )
    )
    assert (
        len(calls) == 1
        and built[-1]["capabilities"] == _SEES
        and built[-1]["vision_capabilities"] == _SEES
    )
    asyncio.run(
        agent_mod.build_agent(
            "/tmp/ws",
            None,
            catalog=_CATALOG,
            connection=connection,
            bearer=NoBearer(),
            capabilities=_BLIND,
        )
    )
    assert len(calls) == 1
    assert built[-1]["capabilities"] == _BLIND and built[-1]["vision_capabilities"] == _BLIND


def test_separate_vision_model_builds_a_second_chat_model_on_the_same_bearer(harness) -> None:
    """AC-17: SEPARATE_MODEL ⇒ two constructions (main, vision) sharing the bearer object; the
    graph builder receives a distinct vision_llm."""
    built, models = harness
    connection = _connection(
        {"base_url": "http://llm.test/v1", "model": "main-a", "vision": {"model": "vision-b"}}
    )
    bearer = NoBearer()
    asyncio.run(
        agent_mod.build_agent(
            "/tmp/ws", None, catalog=_CATALOG, connection=connection, bearer=bearer
        )
    )
    assert [m.get("model") for m in models] == [None, "vision-b"]
    assert all(m["bearer"] is bearer and m["connection"] is connection for m in models)
    assert built[-1]["vision_llm"] is not built[-1]["llm"]
    assert built[-1]["capabilities"] == _BLIND and built[-1]["vision_capabilities"] == _SEES
    assert built[-1]["bearer"] is bearer


def test_no_model_chosen_fails_before_any_construction(harness, monkeypatch) -> None:
    """AC-42 / E19: connection.model is None ⇒ AgentArchitectureError before the serve
    subprocess or any chat model is built."""
    _built, models = harness
    served: list[tuple] = []
    monkeypatch.setattr(agent_mod, "serve_connection", lambda *a, **k: served.append(a) or {})
    connection = _connection({"base_url": "http://llm.test/v1"})
    with pytest.raises(
        AgentArchitectureError, match="no model chosen; set ask_your_docs.llm.model"
    ):
        asyncio.run(agent_mod.build_agent("/tmp/ws", None, catalog=_CATALOG, connection=connection))
    assert served == [] and models == []


def test_token_service_down_at_build_time_raises_and_caches_nothing(harness, monkeypatch) -> None:
    """AC-34 (build half, H3): vision: null + endpoint_probe ⇒ the ladder's rung 3 asks the
    bearer, the bearer raises, nothing is cached, and the next build succeeds once it recovers."""
    built, _models = harness
    endpoint = FakeModelsEndpoint(entry={"id": "main-a", "capabilities": {"vision": True}})
    monkeypatch.setattr(multimodal, "_default_list_models", endpoint)
    cfg = AskYourDocsConfig.model_validate(
        {"multimodal": {"detection": {"static_table": False, "endpoint_probe": True}}}
    )
    connection = _connection(
        {
            "base_url": "http://llm.test/v1",
            "model": "main-a",
            "auth": {"token_url": "http://localhost:8899/t"},
        }
    )
    with pytest.raises(TokenServiceError):
        asyncio.run(
            agent_mod.build_agent(
                "/tmp/ws",
                None,
                catalog=_CATALOG,
                config=cfg,
                connection=connection,
                bearer=FakeBearer(fail=True),
            )
        )
    assert endpoint.calls == 1 and multimodal._detection_cache == {} and built == []
    asyncio.run(
        agent_mod.build_agent(
            "/tmp/ws",
            None,
            catalog=_CATALOG,
            config=cfg,
            connection=connection,
            bearer=FakeBearer("tok-fixed-abcd"),
        )
    )
    assert built[-1]["capabilities"] == ModelCapabilities(True, CapabilitySource.ENDPOINT)


def test_build_agent_defaults_resolve_the_no_block_connection(harness) -> None:
    """Today's positional call shape still works: (workspace, model, base_url) ⇒ the no-block
    connection, the lenient bearer, rule-1 construction."""
    _built, models = harness
    asyncio.run(
        agent_mod.build_agent("/tmp/ws", "m", "http://x/v1", catalog=_CATALOG, capabilities=_BLIND)
    )
    connection = models[-1]["connection"]
    assert connection.block_present is False and connection.model == "m"
    assert connection.base_url == "http://x/v1"


def test_build_agent_signature_keeps_the_two_tuple_and_keyword_only_seams(harness) -> None:
    built, _models = harness
    params = inspect.signature(agent_mod.build_agent).parameters
    for name in ("prompts", "connection", "bearer", "vision_capabilities"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY and params[name].default is None
    graph, llm = asyncio.run(
        agent_mod.build_agent("/tmp/ws", "m", catalog=_CATALOG, capabilities=_BLIND)
    )
    assert graph == "GRAPH" and llm is built[-1]["llm"]


def test_build_agent_ui_path_spawns_child_with_parent_env(harness, monkeypatch) -> None:
    """0.6.1: the UI path (no mcp_tools, no subprocess_env) hands the serve child the parent's
    environment. On 0.6.0 the connection had no env map, so the SDK started the child from six
    variables and an API-key embedder could not find its key."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    asyncio.run(agent_mod.build_agent("/tmp/ws", "m", catalog=_CATALOG, capabilities=_BLIND))
    assert FakeMultiServerMCPClient.recorded[-1]["pydocs"]["env"]["OPENROUTER_API_KEY"] == "dummy"


def test_page_serve_opener_spawns_child_with_parent_env(monkeypatch) -> None:
    """0.6.1's guard, moved onto the page's PRODUCTION opener: the page no longer takes
    build_agent's default spawn path, so this is where the UI serve child's env is decided.
    The child inherits the parent's key, is NOT config-sealed, runs the serve_connection argv,
    and every request is bounded by a read timeout."""
    import langchain_mcp_adapters.client as adapter_client
    import langchain_mcp_adapters.tools as adapter_tools

    from pydocs_mcp.harness.ask_your_docs.serve_session import page_serve_opener
    from pydocs_mcp.harness.ask_your_docs.serve_spawn import serve_connection

    from ._agent_fakes import FakeLoadMcpTools

    FakeMultiServerMCPClient.recorded.clear()
    monkeypatch.setattr(adapter_client, "MultiServerMCPClient", FakeMultiServerMCPClient)
    monkeypatch.setattr(adapter_tools, "load_mcp_tools", FakeLoadMcpTools())
    monkeypatch.setenv("OPENROUTER_API_KEY", "dummy")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://embed-gw/v1")

    async def _open_once() -> None:
        async with page_serve_opener("/tmp/ws", "/cfg.yaml")([]):
            pass

    asyncio.run(_open_once())
    conn = FakeMultiServerMCPClient.recorded[-1]["pydocs"]
    assert conn["env"]["OPENROUTER_API_KEY"] == "dummy"
    assert conn["env"]["OPENAI_BASE_URL"] == "http://embed-gw/v1"  # the config tier is not sealed
    assert conn["args"] == serve_connection("/tmp/ws", "/cfg.yaml")["args"]
    assert conn["session_kwargs"]["read_timeout_seconds"].total_seconds() > 0


def test_reasoning_capture_follows_the_ui_setting(harness) -> None:
    """ui.reasoning.capture reaches the one chat-model build: false = the stock class."""
    _built, models = harness
    connection = _connection({"base_url": "http://llm.test/v1", "model": "main-a", "vision": True})
    off = AskYourDocsConfig.model_validate({"ui": {"reasoning": {"capture": False}}})
    for config in (AskYourDocsConfig(), off):
        asyncio.run(
            agent_mod.build_agent(
                "/tmp/ws",
                None,
                catalog=_CATALOG,
                connection=connection,
                bearer=NoBearer(),
                config=config,
                capabilities=_SEES,
            )
        )
    assert [model["capture_reasoning"] for model in models] == [True, False]


# ── the wire at the call sites (model-params v2 §5 rule 7) ──

from pydocs_mcp.harness.ask_your_docs import llm_connection as lc_mod
from pydocs_mcp.harness.ask_your_docs.chat_wire import NO_WIRE_PARAMS, WireParams


def test_the_main_model_gets_the_params_and_the_vision_model_none(harness) -> None:
    _built, models = harness
    connection = _connection(
        {
            "base_url": "http://llm.test/v1",
            "model": "main-a",
            "vision": {"model": "vision-b"},
            "params": {"temperature": 0.2, "seed": 7},
        }
    )
    asyncio.run(
        agent_mod.build_agent(
            "/tmp/ws", None, catalog=_CATALOG, connection=connection, bearer=NoBearer()
        )
    )
    main, vision = models
    assert main["wire"] == WireParams((("seed", 7), ("temperature", 0.2)))
    assert vision["model"] == "vision-b" and vision["wire"] is NO_WIRE_PARAMS


def test_no_params_give_the_main_model_no_wire(harness) -> None:
    _built, models = harness
    connection = _connection({"base_url": "http://llm.test/v1", "model": "main-a", "vision": True})
    asyncio.run(
        agent_mod.build_agent(
            "/tmp/ws", None, catalog=_CATALOG, connection=connection, bearer=NoBearer()
        )
    )
    assert models[0]["wire"] is NO_WIRE_PARAMS


def test_the_image_probe_never_gets_params(monkeypatch) -> None:
    """Rung 4 stays paramless even on a connection that carries params."""
    seen: list[dict] = []

    class _ProbeReply:
        async def ainvoke(self, messages):
            return type("Reply", (), {"content": "OK"})()

    def _spy_build(connection, bearer, **kwargs):
        seen.append(kwargs)
        return _ProbeReply()

    monkeypatch.setattr(lc_mod, "build_chat_model", _spy_build)
    connection = _connection(
        {"base_url": "http://llm.test/v1", "model": "vlm", "params": {"temperature": 0.2}}
    )
    asyncio.run(multimodal._default_probe_llm(connection, NoBearer(), "vlm", 5.0))
    assert seen == [{"model": "vlm", "timeout_seconds": 5.0, "max_retries": 0}]
