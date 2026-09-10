"""Capability-detection ladder (spec 2026-07-11-multimodal-image-agent §3.9;
LLM-connection design §4.7 — the rungs take (connection, bearer), AC-15, AC-34).

Pure-async, Streamlit-free; HTTP and LLM rungs are injectable named fakes
from _connection_fakes. No heavy imports — runs in the core venv.
"""

from __future__ import annotations

import asyncio

import pytest

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import NoBearer, TokenServiceError
from pydocs_mcp.harness.ask_your_docs.llm_connection import (
    ConnectionOverride,
    clear_bearer_registry,
    resolve_llm_connection,
)
from pydocs_mcp.harness.ask_your_docs.multimodal import (
    CapabilitySource,
    ModelCapabilities,
    _detection_cache,
    clear_detection_cache,
    detect_capabilities,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    LlmConnectionConfig,
    MultimodalDetectionConfig,
)

from ._connection_fakes import FakeBearer, FakeModelsEndpoint, FakeProbeLlm

_BASE_URL = "http://localhost:8000/v1"


@pytest.fixture(autouse=True)
def _fresh_caches():
    clear_detection_cache()
    clear_bearer_registry()
    yield
    clear_detection_cache()
    clear_bearer_registry()


def _detect(model: str, cfg: MultimodalDetectionConfig, **kw) -> ModelCapabilities:
    clear_detection_cache()
    return asyncio.run(detect_capabilities(model, _BASE_URL, cfg, **kw))


def _token_connection(model: str = "my-vlm"):
    block = LlmConnectionConfig.model_validate(
        {"base_url": _BASE_URL, "model": model, "auth": {"token_url": "http://localhost:8899/t"}}
    )
    return resolve_llm_connection(
        block, {}, ConnectionOverride(), ConnectionOverride(), config_path=None
    )


def test_override_short_circuits_ladder() -> None:
    """AC10: override wins with no table lookup and no HTTP."""
    endpoint = FakeModelsEndpoint()
    probe = FakeProbeLlm()
    cfg = MultimodalDetectionConfig(override=True, endpoint_probe=True, image_probe=True)
    caps = _detect("gpt-3.5-turbo", cfg, list_models=endpoint, probe_llm=probe)
    assert caps == ModelCapabilities(multimodal=True, source="override")
    assert endpoint.calls == 0 and probe.calls == 0
    cfg_off = MultimodalDetectionConfig(override=False)
    assert _detect("gpt-4o", cfg_off).multimodal is False


def test_static_table_longest_prefix_wins() -> None:
    """AC11: longest-prefix semantics across the positive AND negative tables
    (mirrors model_budget.py's context_window_tokens)."""
    cfg = MultimodalDetectionConfig()
    # phi-3-vision matches negative 'phi-3' AND positive 'phi-3-vision' — the
    # longer positive prefix must win.
    assert _detect("phi-3-vision-128k", cfg) == ModelCapabilities(True, "static")
    assert _detect("phi-3-mini", cfg) == ModelCapabilities(False, "static")
    assert _detect("gpt-4o-mini", cfg) == ModelCapabilities(True, "static")
    assert _detect("gpt-3.5-turbo", cfg) == ModelCapabilities(False, "static")
    # HF-style org prefix is stripped before matching.
    assert _detect("Qwen/qwen2.5-vl-7b-instruct", cfg) == ModelCapabilities(True, "static")


def test_unknown_model_conservative_default() -> None:
    """AC12: probes off + unknown name → (False, 'default')."""
    caps = _detect("my-custom-vlm-v2", MultimodalDetectionConfig())
    assert caps == ModelCapabilities(multimodal=False, source="default")


def test_endpoint_probe_positive_absent_and_error(monkeypatch) -> None:
    """AC13: a vision hint decides positive; absence and errors fall through
    (never decide text-only); errors are retried ≤3 times."""
    from pydocs_mcp.harness.ask_your_docs import multimodal as mm

    monkeypatch.setattr(mm, "_PROBE_BACKOFF_SECONDS", (0.0, 0.0))
    cfg = MultimodalDetectionConfig(static_table=False, endpoint_probe=True)
    hit = FakeModelsEndpoint(entry={"id": "my-vlm", "capabilities": {"vision": True}})
    assert _detect("my-vlm", cfg, list_models=hit) == ModelCapabilities(True, "endpoint")
    bare = FakeModelsEndpoint(entry={"id": "my-vlm"})
    assert _detect("my-vlm", cfg, list_models=bare) == ModelCapabilities(False, "default")
    down = FakeModelsEndpoint(error=ConnectionError("refused"))
    assert _detect("my-vlm", cfg, list_models=down) == ModelCapabilities(False, "default")
    assert down.calls == 3  # the full bounded-retry envelope ran


def test_image_probe_outcomes() -> None:
    """AC14: 200→(True,'probe'); image-content 4xx→(False,'probe');
    5xx/timeout→fall through to (False,'default')."""
    cfg = MultimodalDetectionConfig(static_table=False, image_probe=True)
    assert _detect("my-vlm", cfg, probe_llm=FakeProbeLlm("ok")) == ModelCapabilities(True, "probe")
    assert _detect("my-vlm", cfg, probe_llm=FakeProbeLlm("image_error")) == ModelCapabilities(
        False, "probe"
    )
    assert _detect("my-vlm", cfg, probe_llm=FakeProbeLlm("server_error")) == ModelCapabilities(
        False, "default"
    )


def test_detection_cached_per_model_base_url_pair() -> None:
    """AC15: repeated same-cfg calls for one (model, base_url) hit the cache —
    the probe fires exactly once. (The cfg fingerprint is part of the key —
    see test_different_cfg_reruns_the_ladder.)"""
    cfg = MultimodalDetectionConfig(static_table=False, image_probe=True)
    probe = FakeProbeLlm("ok")

    async def twice() -> tuple[ModelCapabilities, ModelCapabilities]:
        a = await detect_capabilities("my-vlm", "http://x/v1", cfg, probe_llm=probe)
        b = await detect_capabilities("my-vlm", "http://x/v1", cfg, probe_llm=probe)
        return a, b

    a, b = asyncio.run(twice())
    assert a == b == ModelCapabilities(True, "probe")
    assert probe.calls == 1


def test_different_cfg_reruns_the_ladder() -> None:
    """Regression for the cfg-fingerprinted cache key: flipping
    detection.override for an already-detected (model, base_url) pair must
    take effect without a process restart."""
    probe = FakeProbeLlm("ok")
    cfg_probe = MultimodalDetectionConfig(static_table=False, image_probe=True)

    async def flip() -> tuple[ModelCapabilities, ModelCapabilities]:
        first = await detect_capabilities("my-vlm", "http://x/v1", cfg_probe, probe_llm=probe)
        flipped = await detect_capabilities(
            "my-vlm", "http://x/v1", MultimodalDetectionConfig(override=False), probe_llm=probe
        )
        return first, flipped

    first, flipped = asyncio.run(flip())
    assert first == ModelCapabilities(True, "probe")
    assert flipped == ModelCapabilities(False, "override")  # not the stale probe verdict


# ── LLM-connection design §4.7: the rungs carry the connection's bearer ──


def test_capability_source_is_a_str_enum_with_configured() -> None:
    assert CapabilitySource.STATIC == "static" and CapabilitySource.CONFIGURED == "configured"
    assert ModelCapabilities(True, "static") == ModelCapabilities(True, CapabilitySource.STATIC)
    assert {s.value for s in CapabilitySource} == {
        "override",
        "static",
        "endpoint",
        "probe",
        "default",
        "configured",
    }


def test_rungs_receive_the_connections_bearer() -> None:
    """AC-15 (seam half): both rungs are handed the connection and its bearer (D5)."""
    connection = _token_connection()
    bearer = FakeBearer("tok-fixed-abcd")
    endpoint = FakeModelsEndpoint(entry={"id": "my-vlm"})
    probe = FakeProbeLlm("ok")
    cfg = MultimodalDetectionConfig(static_table=False, endpoint_probe=True, image_probe=True)
    caps = asyncio.run(
        detect_capabilities(
            "my-vlm",
            _BASE_URL,
            cfg,
            connection=connection,
            bearer=bearer,
            list_models=endpoint,
            probe_llm=probe,
        )
    )
    assert caps == ModelCapabilities(True, "probe")
    assert endpoint.seen_bearer == "tok-fixed-abcd" and endpoint.seen_base_url == _BASE_URL
    assert probe.seen_bearer == "tok-fixed-abcd"


def test_todays_callers_get_the_no_block_connection(monkeypatch) -> None:
    """Callers that pass only (model, base_url) — the ladder's pre-existing shape — get the
    lenient no-block connection and bearer built for them."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    endpoint = FakeModelsEndpoint(entry={"id": "my-vlm"})
    cfg = MultimodalDetectionConfig(static_table=False, endpoint_probe=True)
    _detect("my-vlm", cfg, list_models=endpoint)
    assert endpoint.seen_base_url == _BASE_URL
    assert endpoint.seen_bearer == ""  # OPENAI_API_KEY unset in the test env → no header


def test_bearer_failures_propagate_and_are_never_cached(monkeypatch) -> None:
    """AC-34 (ladder half, H3): a token service that is down raises out of the ladder at once —
    one call, not the 3-attempt envelope — and no verdict is cached."""
    from pydocs_mcp.harness.ask_your_docs import multimodal as mm

    monkeypatch.setattr(mm, "_PROBE_BACKOFF_SECONDS", (0.0, 0.0))
    connection = _token_connection()
    endpoint = FakeModelsEndpoint(entry={"id": "my-vlm"})
    cfg = MultimodalDetectionConfig(static_table=False, endpoint_probe=True)
    with pytest.raises(TokenServiceError):
        asyncio.run(
            detect_capabilities(
                "my-vlm",
                _BASE_URL,
                cfg,
                connection=connection,
                bearer=FakeBearer(fail=True),
                list_models=endpoint,
            )
        )
    assert endpoint.calls == 1
    assert _detection_cache == {}
    probe = FakeProbeLlm("ok")
    cfg_probe = MultimodalDetectionConfig(static_table=False, image_probe=True)
    with pytest.raises(TokenServiceError):
        asyncio.run(
            detect_capabilities(
                "my-vlm",
                _BASE_URL,
                cfg_probe,
                connection=connection,
                bearer=FakeBearer(fail=True),
                probe_llm=probe,
            )
        )
    assert _detection_cache == {}
    # After the service recovers the next call runs the ladder for real.
    caps = asyncio.run(
        detect_capabilities(
            "my-vlm",
            _BASE_URL,
            cfg_probe,
            connection=connection,
            bearer=NoBearer(),
            probe_llm=probe,
        )
    )
    assert caps == ModelCapabilities(True, "probe")
