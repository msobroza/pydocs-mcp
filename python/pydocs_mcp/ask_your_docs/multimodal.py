"""Multimodal capability detection for the ask-your-docs agent (spec §3.9).

The ladder: explicit override → static prefix table → optional endpoint
metadata probe → optional one-shot tiny-image probe → conservative text-only
default. Pure-async and Streamlit-free; the two network rungs take injectable
callables so tests use named fakes and production wires thin defaults lazily
(no heavy import at module level — the lazy-import contract holds).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pydocs_mcp.retrieval.config.ask_your_docs_models import MultimodalDetectionConfig

log = logging.getLogger("pydocs-mcp.ask-your-docs")

DetectionSource = Literal["override", "static", "endpoint", "probe", "default"]

# Injectable rung seams. http_get returns the /v1/models entry dict for the
# model (None if unavailable); probe_llm runs the tiny-image completion and
# returns the reply text (raising on provider errors).
HttpGet = Callable[[str, str, float], Awaitable[dict | None]]
ProbeLlm = Callable[[str, "str | None", float], Awaitable[str]]

# WHY (2026-07-12): name-based capability inference mirrors the accepted
# precedent of _MODEL_CONTEXT_TOKENS / _REASONING_MODEL_PREFIXES — longest
# prefix wins across BOTH tables so 'phi-3-vision' (positive) beats 'phi-3'
# (negative). llama-3.2 is deliberately positive: the line's 11B/90B are
# vision models; text-only 1B/3B deployments correct via detection.override.
_MULTIMODAL_MODEL_PREFIXES: tuple[str, ...] = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-5",
    "chatgpt-4o",
    "o3",
    "o4",
    "gemini",
    "claude",
    "gemma-3",
    "llava",
    "llama-3.2",
    "llama-4",
    "qwen2-vl",
    "qwen2.5-vl",
    "qwen3-vl",
    "qwen2.5-omni",
    "pixtral",
    "internvl",
    "minicpm-v",
    "phi-3-vision",
    "phi-3.5-vision",
    "phi-4-multimodal",
    "molmo",
    "idefics",
    "smolvlm",
)
_TEXT_ONLY_MODEL_PREFIXES: tuple[str, ...] = (
    "gpt-3.5",
    "gpt-4-0",
    "davinci",
    "text-",
    "qwen2.5-coder",
    "qwen2.5-math",
    "deepseek",
    "mistral-",
    "mixtral",
    "llama-3.1",
    "llama-3-",
    "llama-2",
    "phi-3",
    "phi-2",
    "starcoder",
    "codellama",
    "gemma-2",
    "gemma-7b",
    "gemma-2b",
)

# One-shot probe payload: a 1x1 transparent PNG (67 bytes decoded).
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

_PROBE_TIMEOUT_SECONDS = 5.0
_PROBE_ATTEMPTS = 3
_PROBE_BACKOFF_SECONDS = (2.0, 4.0)  # mirrors llm_clients/openai._with_retry_async

# Process-level cache per (model, base_url) — detection of a fixed pair does
# not change between questions (spec §3.7; persisted cache deferred, §7 Q2).
_detection_cache: dict[tuple[str, str | None], ModelCapabilities] = {}


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    multimodal: bool
    source: DetectionSource  # which ladder rung decided — surfaced in the UI badge


def clear_detection_cache() -> None:
    """Test seam: reset the per-process detection cache."""
    _detection_cache.clear()


def _static_lookup(model: str) -> bool | None:
    """Longest-prefix match across both tables; None = unknown (fall through)."""
    name = model.lower().rsplit("/", 1)[-1]  # strip HF-style org prefix
    best_len, best_verdict = 0, None
    for table, verdict in (
        (_MULTIMODAL_MODEL_PREFIXES, True),
        (_TEXT_ONLY_MODEL_PREFIXES, False),
    ):
        for prefix in table:
            if name.startswith(prefix) and len(prefix) > best_len:
                best_len, best_verdict = len(prefix), verdict
    return best_verdict


async def _with_rung_retry(fn: Callable[[], Awaitable[object]]) -> object:
    """Bounded retry for the endpoint rung (3 attempts, 2s/4s backoff).

    The image probe (rung 4) deliberately does NOT use this: it is one-shot
    by design — an image-rejection 400 is deterministic, and a transient
    failure falls through to the conservative default anyway.
    """
    for attempt in range(_PROBE_ATTEMPTS):
        try:
            return await fn()
        except Exception:
            if attempt == _PROBE_ATTEMPTS - 1:
                raise
            # Module-level constant so tests can zero the backoff.
            await asyncio.sleep(_PROBE_BACKOFF_SECONDS[min(attempt, 1)])
    raise AssertionError("unreachable")


def _entry_hints_vision(entry: dict) -> bool:
    """Positive-only heuristic over commonly-seen /v1/models metadata fields.

    WHY (2026-07-12): there is no modality-field standard across
    OpenAI-compatible servers — absence of a hint proves nothing, so this
    rung only ever decides POSITIVE; unknown shapes fall through (§7 Q1).
    """
    for field in ("capabilities", "modality", "modalities", "architecture", "tags"):
        value = entry.get(field)
        if value is None:
            continue
        text = str(value).lower()
        if "vision" in text or "image" in text or "multimodal" in text:
            return True
    return False


async def _default_http_get(base_url: str, model: str, timeout: float) -> dict | None:
    """Production rung-3 seam: GET {base_url}/models, return the model's entry."""
    import httpx  # transitive via the extra's langchain stack; lazy by contract

    url = base_url.rstrip("/") + "/models"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        for entry in resp.json().get("data", []):
            if entry.get("id") == model:
                return entry
    return None


async def _default_probe_llm(model: str, base_url: str | None, timeout: float) -> str:
    """Production rung-4 seam: one tiny-image chat completion via ChatOpenAI."""
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI  # heavy; lazy by contract

    llm = ChatOpenAI(model=model, base_url=base_url, timeout=timeout, max_retries=0)
    reply = await llm.ainvoke(
        [
            HumanMessage(
                content=[
                    {"type": "text", "text": "Reply with the single word OK."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{_TINY_PNG_B64}"},
                    },
                ]
            )
        ]
    )
    return str(reply.content)


def _looks_like_image_rejection(exc: Exception) -> bool:
    text = str(exc).lower()
    if not any(marker in text for marker in ("image", "vision", "multimodal", "content type")):
        return False
    return "400" in text or "invalid" in text or "not supported" in text or "unsupported" in text


async def detect_capabilities(
    model: str,
    base_url: str | None,
    cfg: MultimodalDetectionConfig,
    *,
    http_get: HttpGet | None = None,
    probe_llm: ProbeLlm | None = None,
) -> ModelCapabilities:
    """Run the detection ladder (spec §3.9), cached per (model, base_url)."""
    key = (model, base_url)
    if key in _detection_cache:
        return _detection_cache[key]
    caps = await _run_ladder(model, base_url, cfg, http_get=http_get, probe_llm=probe_llm)
    _detection_cache[key] = caps
    log.info("multimodal detection: model=%s -> %s (%s)", model, caps.multimodal, caps.source)
    return caps


async def _endpoint_rung(
    model: str, base_url: str, http_get: HttpGet | None
) -> ModelCapabilities | None:
    """Rung 3 — positive-only signal; network trouble/absence falls through."""
    getter = http_get or _default_http_get
    try:
        entry = await _with_rung_retry(lambda: getter(base_url, model, _PROBE_TIMEOUT_SECONDS))
    except Exception:
        return None  # network trouble → fall through, never decide
    if isinstance(entry, dict) and _entry_hints_vision(entry):
        return ModelCapabilities(multimodal=True, source="endpoint")
    return None


async def _image_probe_rung(
    model: str, base_url: str | None, probe_llm: ProbeLlm | None
) -> ModelCapabilities | None:
    """Rung 4 — ground truth, opt-in (costs one real call). Only an
    image-rejection error decides text-only; 5xx/timeout falls through."""
    prober = probe_llm or _default_probe_llm
    try:
        await prober(model, base_url, _PROBE_TIMEOUT_SECONDS)
        return ModelCapabilities(multimodal=True, source="probe")
    except Exception as exc:
        if _looks_like_image_rejection(exc):
            return ModelCapabilities(multimodal=False, source="probe")
        return None


async def _run_ladder(
    model: str,
    base_url: str | None,
    cfg: MultimodalDetectionConfig,
    *,
    http_get: HttpGet | None,
    probe_llm: ProbeLlm | None,
) -> ModelCapabilities:
    if cfg.override is not None:  # rung 1
        return ModelCapabilities(multimodal=cfg.override, source="override")
    if cfg.static_table:  # rung 2
        verdict = _static_lookup(model)
        if verdict is not None:
            return ModelCapabilities(multimodal=verdict, source="static")
    if cfg.endpoint_probe and base_url:
        caps = await _endpoint_rung(model, base_url, http_get)
        if caps is not None:
            return caps
    if cfg.image_probe:
        caps = await _image_probe_rung(model, base_url, probe_llm)
        if caps is not None:
            return caps
    return ModelCapabilities(multimodal=False, source="default")  # rung 5


__all__ = (
    "DetectionSource",
    "ModelCapabilities",
    "clear_detection_cache",
    "detect_capabilities",
)
