"""The settings every judge role shares: where it calls, with which key, how hard and how long.

A role block names ONE pinned model (``model``) and how it is called. ``model``
is empty by default so a role refuses to run, naming its key, until the
deployment YAML pins it (:func:`pinned_model`); the pinned ids live only there.

Example:
    >>> EscalationConfig().reasoning_effort
    <ReasoningEffort.XHIGH: 'xhigh'>
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

#: Every role calls OpenRouter; each client adds its own route to this base
#: (``/systemone``, ``/chat/completions``, ``/batches``).
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
#: The variable the bearer is read from, at call time — never a file.
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
#: Where every role's model is pinned; the refusal points the reader at it.
DEPLOYMENT_YAML = "benchmarks/configs/judge_openrouter.yaml"

_DEFAULT_RETRIES = 2
# A batch role waits on its whole batch within this; a synchronous role waits
# on one request.
_DEFAULT_BATCH_TIMEOUT_SECONDS = 600.0
_DEFAULT_ESCALATION_TIMEOUT_SECONDS = 120.0


class ReasoningEffort(StrEnum):
    """The effort levels OpenRouter's ``reasoning_effort`` accepts, highest first."""

    MAX = "max"
    XHIGH = "xhigh"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    MINIMAL = "minimal"
    NONE = "none"


class JudgeConfigError(Exception):
    """A judge role that cannot run as configured, named by its YAML key."""


def pinned_model(key: str, model: str) -> str:
    """``model``, or a refusal naming ``key`` when the deployment left it empty.

    Example:
        >>> pinned_model("judge.escalation.model", "openai/gpt-6-luna")
        'openai/gpt-6-luna'
    """
    if not model.strip():
        raise JudgeConfigError(
            f"{key} is empty: pin the model in {DEPLOYMENT_YAML} before this role runs"
        )
    return model


class ChatRoleConfig(BaseModel):
    """One chat-completion role: the model it is pinned to and how it is called.

    A ``:batch`` model runs through OpenRouter's Batch API and waits on the
    whole batch within ``timeout_seconds``; any other model is called one
    request at a time, each within ``timeout_seconds``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = ""
    endpoint: str = OPENROUTER_API_BASE
    api_key_env: str = OPENROUTER_API_KEY_ENV
    reasoning_effort: ReasoningEffort = ReasoningEffort.HIGH
    timeout_seconds: float = Field(default=_DEFAULT_BATCH_TIMEOUT_SECONDS, gt=0)
    retries: int = Field(default=_DEFAULT_RETRIES, ge=0)


class EscalationConfig(ChatRoleConfig):
    """``judge.escalation``: the in-band judge, at the highest effort its endpoint accepts."""

    reasoning_effort: ReasoningEffort = ReasoningEffort.XHIGH
    timeout_seconds: float = Field(default=_DEFAULT_ESCALATION_TIMEOUT_SECONDS, gt=0)


class AlignmentConfig(BaseModel):
    """``judge.alignment``: the two blind labellers, each its own role block."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    labellers: tuple[ChatRoleConfig, ChatRoleConfig] = (ChatRoleConfig(), ChatRoleConfig())


class ReferenceWriterConfig(ChatRoleConfig):
    """``reference_writer``: the model that writes reference answers, and its fallback.

    ``fallback_model`` runs under this block's settings, only for a row whose
    ``model`` job failed or timed out.
    """

    fallback_model: str = ""


__all__ = (
    "DEPLOYMENT_YAML",
    "OPENROUTER_API_BASE",
    "OPENROUTER_API_KEY_ENV",
    "AlignmentConfig",
    "ChatRoleConfig",
    "EscalationConfig",
    "JudgeConfigError",
    "ReasoningEffort",
    "ReferenceWriterConfig",
    "pinned_model",
)
