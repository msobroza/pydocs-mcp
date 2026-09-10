"""Multimodal capability detection for the ask-your-docs agent (spec §3.9).

The ladder: explicit override → static prefix table → optional endpoint
metadata probe → optional one-shot tiny-image probe → conservative text-only
default. Pure-async and Streamlit-free; the two network rungs take injectable
callables so tests use named fakes and production wires thin defaults lazily
(no heavy import at module level — the lazy-import contract holds). Both
production rungs go through the LLM connection's client factory, so they
carry the same bearer as the agent (LLM-connection design §4.6–§4.7, D5).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

# BEARER_ERRORS is imported, never re-listed here: every rung re-raises the SAME
# tuple the model listing re-raises, so a fourth bearer error cannot reach one
# site and be swallowed by the other (design H3).
from pydocs_mcp.harness.ask_your_docs.bearer_tokens import BEARER_ERRORS, translate_auth_errors
from pydocs_mcp.retrieval.config.ask_your_docs_models import MultimodalDetectionConfig

if TYPE_CHECKING:
    from pydocs_mcp.harness.ask_your_docs.bearer_tokens import BearerSource
    from pydocs_mcp.harness.ask_your_docs.llm_connection import LlmConnection

log = logging.getLogger("pydocs-mcp.harness.ask-your-docs")


class CapabilitySource(StrEnum):
    """Which ladder rung (or the YAML ``vision`` key) decided — surfaced in the UI badge."""

    OVERRIDE = "override"
    STATIC = "static"
    ENDPOINT = "endpoint"
    PROBE = "probe"
    DEFAULT = "default"
    CONFIGURED = "configured"  # ask_your_docs.llm.vision: true | false | {model}


# The pre-StrEnum name; kept so existing import sites resolve (the values are unchanged).
DetectionSource = CapabilitySource

# Injectable rung seams, and the seam vocabulary the model listing shares.
# list_models returns the /v1/models entries for the connection (raising on
# transport errors and on a payload that is not a listing); probe_llm runs the
# tiny-image completion on the connection's endpoint and returns the reply text.
#
# ListingEntries is Any-valued, not dict-valued, and that is the honest type on a
# REACHABLE path: an out-of-contract endpoint sends entries that are not objects
# at all, and they pass through unconverted so the shape caption can name their
# type (model_listing._entry_as_dict). Rung 3 filters isinstance(entry, dict)
# before reading metadata; the dialog reads ids through _has_model_id.
ListingEntries = list[Any]
ListModels = Callable[["LlmConnection", "BearerSource"], Awaitable[ListingEntries]]
ProbeLlm = Callable[["LlmConnection", "BearerSource", str, float], Awaitable[str]]

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
    "deepseek-vl",
    "phi-3-vision",
    "phi-3.5-vision",
    "phi-4-multimodal",
    "molmo",
    "idefics",
    "smolvlm",
)
# WHY (2026-07-12, review): a NEGATIVE static verdict is decisive — it blocks
# the opt-in probes from correcting it — so mixed-capability families
# (mistral: Small 3.1 sees, most don't; deepseek: -vl sees, -r1 doesn't) get
# only their unambiguous members listed and otherwise FALL THROUGH to the
# probes/default. o3-mini is text-only while o3 sees (longest prefix wins).
_TEXT_ONLY_MODEL_PREFIXES: tuple[str, ...] = (
    "gpt-3.5",
    "gpt-4-0",
    "o3-mini",
    "davinci",
    "text-",
    "qwen2.5-coder",
    "qwen2.5-math",
    "deepseek-r1",
    "deepseek-coder",
    "deepseek-v3",
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
_detection_cache: dict[tuple, ModelCapabilities] = {}


@dataclass(frozen=True, slots=True)
class ModelCapabilities:
    multimodal: bool
    source: CapabilitySource  # which rung decided — the UI badge text (a plain str compares equal)


def clear_detection_cache() -> None:
    """Test seam: reset the per-process detection cache."""
    _detection_cache.clear()


def _longest_prefix_length(name: str, table: tuple[str, ...]) -> int:
    """Length of the longest ``table`` prefix that ``name`` starts with; 0 when none does."""
    return max((len(prefix) for prefix in table if name.startswith(prefix)), default=0)


def _static_lookup(model: str) -> bool | None:
    """Longest-prefix match across both tables; None = unknown (fall through)."""
    name = model.lower().rsplit("/", 1)[-1]  # strip HF-style org prefix
    positive = _longest_prefix_length(name, _MULTIMODAL_MODEL_PREFIXES)
    negative = _longest_prefix_length(name, _TEXT_ONLY_MODEL_PREFIXES)
    if positive == 0 and negative == 0:
        return None
    return positive >= negative  # a tie keeps the positive table's verdict, as the scan order did


async def _with_rung_retry(fn: Callable[[], Awaitable[object]]) -> object:
    """Bounded retry for the endpoint rung (3 attempts, 2s/4s backoff).

    Bearer failures re-raise at once: they are bounded inside the bearer
    already, and retrying them here would cost 3 × 3 token fetches (H3). The
    image probe (rung 4) deliberately does NOT use this: it is one-shot by
    design — an image-rejection 400 is deterministic, and a transient
    failure falls through to the conservative default anyway.
    """
    for attempt in range(_PROBE_ATTEMPTS - 1):
        try:
            return await fn()
        except BEARER_ERRORS:
            raise
        except Exception:
            # Module-level constant so tests can zero the backoff.
            await asyncio.sleep(_PROBE_BACKOFF_SECONDS[min(attempt, 1)])
    return await fn()  # the last attempt: its failure propagates to the caller


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


async def _default_list_models(connection: LlmConnection, bearer: BearerSource) -> ListingEntries:
    """Production rung-3 seam: GET {base_url}/models with the connection's bearer."""
    # WHY function-local: model_listing imports THIS module at module level (ListModels, the
    # rung-3 seam type, lives here), so importing it back at module level closes a cycle today.
    from pydocs_mcp.harness.ask_your_docs.model_listing import fetch_models_payload

    return await fetch_models_payload(connection, bearer)


def _tiny_image_content() -> list[dict]:
    """The one-shot probe payload: an instruction plus the 1x1 PNG as a data URL."""
    return [
        {"type": "text", "text": "Reply with the single word OK."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{_TINY_PNG_B64}"}},
    ]


async def _default_probe_llm(
    connection: LlmConnection, bearer: BearerSource, model: str, timeout: float
) -> str:
    """Production rung-4 seam: one tiny-image chat completion through the client factory."""
    from langchain_core.messages import HumanMessage  # heavy; lazy by contract

    # WHY function-local: ListModels lives in THIS module, so model_listing imports it at module
    # level, and llm_connection now imports this module at module level too (ModelCapabilities /
    # detect_capabilities) — a module-level edge from here would close that cycle.
    from pydocs_mcp.harness.ask_your_docs.llm_connection import build_chat_model

    llm = build_chat_model(connection, bearer, model=model, timeout_seconds=timeout, max_retries=0)
    with translate_auth_errors(bearer):
        reply = await llm.ainvoke([HumanMessage(content=_tiny_image_content())])
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
    connection: LlmConnection | None = None,
    bearer: BearerSource | None = None,
    list_models: ListModels | None = None,
    probe_llm: ProbeLlm | None = None,
) -> ModelCapabilities:
    """Run the detection ladder (spec §3.9), cached per (model, base_url, cfg).

    The cfg fingerprint is part of the key so the advertised escape hatch
    (flipping ``detection.override`` in YAML) takes effect without a process
    restart — a (model, base_url)-only key would pin the stale verdict.
    ``connection`` / ``bearer`` default to the no-block connection for
    ``(model, base_url)``, so pre-existing callers keep their shape. A bearer
    failure propagates before the cache is written (H3).
    """
    key = (model, base_url, cfg.override, cfg.static_table, cfg.endpoint_probe, cfg.image_probe)
    if key in _detection_cache:
        return _detection_cache[key]
    connection, bearer = _connection_and_bearer(model, base_url, connection, bearer)
    caps = await _run_ladder(
        model, cfg, connection, bearer, list_models=list_models, probe_llm=probe_llm
    )
    _detection_cache[key] = caps
    log.info("multimodal detection: model=%s -> %s (%s)", model, caps.multimodal, caps.source)
    return caps


def _connection_and_bearer(
    model: str,
    base_url: str | None,
    connection: LlmConnection | None,
    bearer: BearerSource | None,
) -> tuple[LlmConnection, BearerSource]:
    """Today's callers pass (model, base_url) only: build the no-block connection for them."""
    # WHY function-local: the same llm_connection cycle rule as _default_probe_llm above.
    from pydocs_mcp.harness.ask_your_docs.llm_connection import (
        ConnectionOverride,
        bearer_for_connection,
        resolve_llm_connection,
    )

    if connection is None:
        connection = resolve_llm_connection(
            None, {}, ConnectionOverride(base_url, model), ConnectionOverride(), config_path=None
        )
    if bearer is None:
        bearer = bearer_for_connection(connection)
    return connection, bearer


async def _endpoint_rung(
    model: str,
    connection: LlmConnection,
    bearer: BearerSource,
    list_models: ListModels | None,
) -> ModelCapabilities | None:
    """Rung 3 — positive-only signal; network trouble/absence falls through."""
    lister = list_models or _default_list_models
    try:
        payload = await _with_rung_retry(lambda: lister(connection, bearer))
    except BEARER_ERRORS:
        raise
    except Exception:
        return None  # network trouble → fall through, never decide
    entries = payload if isinstance(payload, list) else []
    entry = next((e for e in entries if isinstance(e, dict) and e.get("id") == model), None)
    if entry is not None and _entry_hints_vision(entry):
        return ModelCapabilities(multimodal=True, source=CapabilitySource.ENDPOINT)
    return None


async def _image_probe_rung(
    model: str,
    connection: LlmConnection,
    bearer: BearerSource,
    probe_llm: ProbeLlm | None,
) -> ModelCapabilities | None:
    """Rung 4 — ground truth, opt-in (costs one real call). Only an
    image-rejection error decides text-only; 5xx/timeout falls through."""
    prober = probe_llm or _default_probe_llm
    try:
        await prober(connection, bearer, model, _PROBE_TIMEOUT_SECONDS)
        return ModelCapabilities(multimodal=True, source=CapabilitySource.PROBE)
    except BEARER_ERRORS:
        raise
    except Exception as exc:
        if _looks_like_image_rejection(exc):
            return ModelCapabilities(multimodal=False, source=CapabilitySource.PROBE)
        return None


async def _run_ladder(
    model: str,
    cfg: MultimodalDetectionConfig,
    connection: LlmConnection,
    bearer: BearerSource,
    *,
    list_models: ListModels | None,
    probe_llm: ProbeLlm | None,
) -> ModelCapabilities:
    if cfg.override is not None:  # rung 1
        return ModelCapabilities(multimodal=cfg.override, source=CapabilitySource.OVERRIDE)
    if cfg.static_table:  # rung 2
        verdict = _static_lookup(model)
        if verdict is not None:
            return ModelCapabilities(multimodal=verdict, source=CapabilitySource.STATIC)
    if cfg.endpoint_probe and connection.base_url:
        caps = await _endpoint_rung(model, connection, bearer, list_models)
        if caps is not None:
            return caps
    if cfg.image_probe:
        caps = await _image_probe_rung(model, connection, bearer, probe_llm)
        if caps is not None:
            return caps
    return ModelCapabilities(multimodal=False, source=CapabilitySource.DEFAULT)  # rung 5


__all__ = (
    "CapabilitySource",
    "DetectionSource",
    "ModelCapabilities",
    "clear_detection_cache",
    "detect_capabilities",
)
