"""What an eval arm SENDS to the chat model, sealed before any spend (model-params v2 §6).

Beside ``binding`` to keep that module inside its line budget; ``binding`` re-exports
:func:`sent_settings_fingerprint`, the dotted path the eval harness bridge names.

- **Raise before spend** (§6 rule 3): the wire profile is the declared provider or
  the host — never a listing, never the LiteLLM probe — and a setting the static
  tables would hide raises :class:`ArmSettingNotHonouredError` before the serve
  child spawns. Unknown support is sent as configured: a 400 then fails the rollout
  loudly and is never learned (eval has no session to learn in).
- **Record** (§6 rule 5): ``sent_settings.json`` beside the trajectory, like
  ``candidate_skill.md``, carries ``{provider, sent, thinking_map}``.
- **Identity** (D3): :func:`sent_settings_fingerprint` hashes that same record, and
  is ``None`` for an arm without params, so no existing arm hash moves.

Example:
    >>> sent_settings_fingerprint({"model": "gpt-5-mini"}) is None
    True
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydocs_mcp.exceptions import PydocsMCPError

# The module, not the name: THINKING_MAP_VERSION is read at call time, so a bump
# (phase 2) moves every params arm's fingerprint without touching this file.
from pydocs_mcp.harness.ask_your_docs import provider_profiles
from pydocs_mcp.harness.ask_your_docs.chat_wire import (
    NO_WIRE_PARAMS,
    WireParams,
    log_chat_params_effective,
    resolve_wire,
    static_support,
    wire_value,
)
from pydocs_mcp.harness.ask_your_docs.provider_profiles import ProviderProfile, wire_profile
from pydocs_mcp.observability.trace_env import TRACE_DIR_ENV_VAR, TRACE_TRAJECTORY_ID_ENV_VAR
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig, LlmConnectionConfig
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ChatParamsConfig

if TYPE_CHECKING:
    from pydocs_mcp.harness.ask_your_docs.llm_connection import LlmConnection

SENT_SETTINGS_FILENAME = "sent_settings.json"
_NO_PARAMS = ChatParamsConfig()
_SAMPLING_REASON = "no sampling with this model's Thinking"
_HIDDEN_REASONS = {"temperature": _SAMPLING_REASON, "top_p": _SAMPLING_REASON}
_TABLE_REASON = "hidden by the provider's static support table"


class ArmSettingNotHonouredError(PydocsMCPError, ValueError):
    """An arm's ``harness.llm.params`` value the static tables say this model cannot honour."""

    def __init__(self, *, names: tuple[str, ...], params: ChatParamsConfig, model: str | None):
        self.names = names
        super().__init__(
            "; ".join(_not_honoured(name, getattr(params, name), model) for name in names)
        )


def _not_honoured(name: str, value: object, model: str | None) -> str:
    """The spec's sentence; arm values are numbers or levels, never credentials."""
    shown = getattr(value, "value", value)  # a ThinkingLevel shows its YAML spelling
    reason = f"no {wire_value(value)!r} effort" if name == "thinking" else None
    return (
        f"arm harness.llm.params.{name}={shown!r} is not honoured by model {model!r} "
        f"({reason or _HIDDEN_REASONS.get(name, _TABLE_REASON)}); remove it or change the model"
    )


def sealed_arm_wire(connection: LlmConnection) -> tuple[ProviderProfile, WireParams]:
    """The wire this arm sends: declared-or-host profile + the frozen tables, nothing fetched.

    Raises:
        ArmSettingNotHonouredError: a configured value the tables would hide.
    """
    profile = wire_profile(connection.provider, connection.base_url)
    wire, hidden = resolve_wire(connection.params, static_support(profile, connection.model))
    if hidden:
        raise ArmSettingNotHonouredError(
            names=hidden, params=connection.params, model=connection.model
        )
    log_chat_params_effective(wire, hidden)
    return profile, wire


def sent_settings_record(profile: ProviderProfile, wire: WireParams) -> dict[str, Any]:
    """``{provider, sent, thinking_map}`` — the rollout record AND the fingerprint's input."""
    return {
        "provider": profile.value,
        "sent": wire.wire_fields(),
        "thinking_map": provider_profiles.THINKING_MAP_VERSION,
    }


def write_sent_settings(
    trace_env: Mapping[str, str], profile: ProviderProfile, wire: WireParams
) -> Path | None:
    """Persist the record beside the trajectory; the control arm writes nothing (byte identity).

    The trajectory directory is read from ``trace_env`` — the run's ADR 0009 correlation
    identity (root + id), spelled once in ``observability.trace_env``; without it there
    is no trajectory directory to write beside.
    """
    root, trajectory_id = (
        trace_env.get(TRACE_DIR_ENV_VAR),
        trace_env.get(TRACE_TRAJECTORY_ID_ENV_VAR),
    )
    if wire == NO_WIRE_PARAMS or root is None or trajectory_id is None:
        return None
    trajectory_dir = Path(root) / trajectory_id
    trajectory_dir.mkdir(parents=True, exist_ok=True)
    path = trajectory_dir / SENT_SETTINGS_FILENAME
    record = sent_settings_record(profile, wire)
    path.write_text(json.dumps(record, sort_keys=True, indent=2), encoding="utf-8")
    return path


def sent_settings_fingerprint(settings: Mapping[str, object]) -> str | None:
    """SHA-256 of the record an arm's settings would send (D3); ``None`` without params.

    Reads ONLY the arm's own ``harness.llm`` (P4: a file block is refused at run time)
    and is pure — no listing, no probe, no langgraph — so a zero-spend dry run can mint
    arm hashes. A value the tables hide is not sent, so it does not move the fingerprint.

    Example:
        >>> sent_settings_fingerprint({"harness": {"llm": {"params": {"seed": 7}}}}) is not None
        True
    """
    block = _arm_llm_block(settings)
    if block is None or block.params == _NO_PARAMS:
        return None
    # WHY not resolve_llm_connection: it logs every resolution, and a dry run must stay
    # quiet. The launch tier (the arm's model/base_url) beats the block, as it does there.
    model = _launch_or_block(settings, "model", block.model)
    profile = wire_profile(block.provider, _launch_or_block(settings, "base_url", block.base_url))
    wire, _hidden = resolve_wire(block.params, static_support(profile, model))
    return _canonical_sha256(sent_settings_record(profile, wire))


def _arm_llm_block(settings: Mapping[str, object]) -> LlmConnectionConfig | None:
    harness = settings.get("harness")
    return None if harness is None else AskYourDocsConfig.model_validate(harness).llm


def _launch_or_block(
    settings: Mapping[str, object], key: str, block_value: str | None
) -> str | None:
    value = settings.get(key)
    return block_value if value is None else str(value)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = (
    "SENT_SETTINGS_FILENAME",
    "ArmSettingNotHonouredError",
    "sealed_arm_wire",
    "sent_settings_fingerprint",
    "sent_settings_record",
    "write_sent_settings",
)
