"""Capability-detection ladder (spec 2026-07-11-multimodal-image-agent §3.9).

Pure-async, Streamlit-free; HTTP and LLM rungs are injectable named fakes.
No heavy imports — runs in the core venv (multimodal.py imports nothing heavy
at module level).
"""

from __future__ import annotations

import asyncio

from pydocs_mcp.ask_your_docs.multimodal import (
    ModelCapabilities,
    clear_detection_cache,
    detect_capabilities,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import MultimodalDetectionConfig


class FakeModelsEndpoint:
    """Records calls; returns a canned /v1/models entry (or raises)."""

    def __init__(self, entry: dict | None = None, error: Exception | None = None) -> None:
        self.entry = entry
        self.error = error
        self.calls = 0

    async def __call__(self, base_url: str, model: str, timeout: float) -> dict | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.entry


class FakeProbeLlm:
    """Records probe calls; simulates the one-shot tiny-image completion."""

    def __init__(self, outcome: str = "ok") -> None:
        self.outcome = outcome  # "ok" | "image_error" | "server_error"
        self.calls = 0

    async def __call__(self, model: str, base_url: str | None, timeout: float) -> str:
        self.calls += 1
        if self.outcome == "image_error":
            raise ValueError("400: image content not supported by this model")
        if self.outcome == "server_error":
            raise TimeoutError("upstream timeout")
        return "OK"


def _detect(model: str, cfg: MultimodalDetectionConfig, **kw) -> ModelCapabilities:
    clear_detection_cache()
    return asyncio.run(detect_capabilities(model, "http://localhost:8000/v1", cfg, **kw))


def test_override_short_circuits_ladder() -> None:
    """AC10: override wins with no table lookup and no HTTP."""
    endpoint = FakeModelsEndpoint()
    probe = FakeProbeLlm()
    cfg = MultimodalDetectionConfig(override=True, endpoint_probe=True, image_probe=True)
    caps = _detect("gpt-3.5-turbo", cfg, http_get=endpoint, probe_llm=probe)
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
    from pydocs_mcp.ask_your_docs import multimodal as mm

    monkeypatch.setattr(mm, "_PROBE_BACKOFF_SECONDS", (0.0, 0.0))
    cfg = MultimodalDetectionConfig(static_table=False, endpoint_probe=True)
    hit = FakeModelsEndpoint(entry={"id": "x", "capabilities": {"vision": True}})
    assert _detect("my-vlm", cfg, http_get=hit) == ModelCapabilities(True, "endpoint")
    bare = FakeModelsEndpoint(entry={"id": "x"})
    assert _detect("my-vlm", cfg, http_get=bare) == ModelCapabilities(False, "default")
    down = FakeModelsEndpoint(error=ConnectionError("refused"))
    assert _detect("my-vlm", cfg, http_get=down) == ModelCapabilities(False, "default")
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
    clear_detection_cache()
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
    clear_detection_cache()
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
