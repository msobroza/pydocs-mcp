"""Ask-your-docs agent config sub-models.

Spec 2026-07-11-multimodal-image-agent §3.5 (architecture, multimodal, images)
and 2026-09-05-ask-your-docs-llm-connection-design §5.1 (the ``llm`` block).

The first agent-side consumer of AppConfig — sanctioned because agent architecture
choice and multimodal-detection strategy are "A/B-testable against a benchmark"
behaviors (CLAUDE.md §MCP API surface vs YAML configuration litmus test). Light
pydantic only: importing this from the ``[harness-ask-your-docs]`` extra pulls no
heavy deps. Defaults are duplicated in ``defaults/default_config.yaml`` on purpose
— the YAML is the user-visible knob (CLAUDE.md §Default values).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Single sources (CLAUDE.md §Default values): harness modules import these, never the literals.
_DEFAULT_MODEL = "gpt-4o-mini"  # the fold's no-block bottom; the app's own prefill still spells it
_DEFAULT_API_KEY_ENV = "OPENAI_API_KEY"
_DEFAULT_RENEW_ON_STATUS: tuple[int, ...] = (401,)
# WHY only these: 200 would re-send a successful, non-idempotent completion; the SDK retries
# 408/409/429/5xx itself, so listing them would multiply the two bounds, not compose them (E17).
_RENEWABLE_STATUSES = frozenset({401, 403, 407})
# 2026-09-05: was vision_subagent. A multimodal main model answers and sees in
# one prompt; set vision_subagent back for a separate describe hop (design R6).
_DEFAULT_PREFERRED_ARCHITECTURE = "inline"


class AuthMode(StrEnum):
    """Where the chat model's bearer comes from (design §4.2)."""

    NONE = "none"  # no Authorization header at all
    ENV_KEY = "env_key"  # bearer = os.environ[api_key_env]
    # A vocabulary value, not a credential: the bearer is fetched from token_url, renewable.
    TOKEN_SERVICE = "token_service"  # noqa: S105


class VisionRule(StrEnum):
    """How the ``vision`` key resolves (design §4.2, §4.7)."""

    DETECT = "detect"  # vision: null  -> run the detection ladder as today
    MULTIMODAL = "multimodal"  # vision: true  -> the main model sees, no probe
    TEXT_ONLY = "text_only"  # vision: false -> the main model never sees
    SEPARATE_MODEL = "separate_model"  # vision: {model: ...}


class MultimodalDetectionConfig(BaseModel):
    """The capability-detection ladder's per-rung toggles (spec §3.9).

    ``override`` wins; probes are opt-in — they cost a network call (3) or a real LLM call (4).
    """

    model_config = ConfigDict(extra="forbid")

    override: bool | None = Field(default=None)
    static_table: bool = Field(default=True)
    endpoint_probe: bool = Field(default=False)
    image_probe: bool = Field(default=False)


class MultimodalConfig(BaseModel):
    """Image-handling policy for the ask-your-docs agent."""

    model_config = ConfigDict(extra="forbid")

    # What "auto" builds on a vision-capable model (see the dated constant above).
    preferred_architecture: str = Field(default=_DEFAULT_PREFERRED_ARCHITECTURE)
    detection: MultimodalDetectionConfig = Field(default_factory=MultimodalDetectionConfig)
    # Text-only models + attached images: "reject" fails loudly with the fix in hand
    # (user-requested content must not silently degrade — the raising side of the Null
    # Object asymmetry); "describe" proceeds text-only with an explicit cannot-see note.
    text_only_fallback: Literal["reject", "describe"] = Field(default="reject")


class ImagesConfig(BaseModel):
    """Per-turn image attachment limits + the session reinspect store size."""

    model_config = ConfigDict(extra="forbid")

    max_per_turn: int = Field(default=3, ge=1, le=10)
    max_bytes: int = Field(default=5_000_000, ge=1)
    # How many recently-attached images the session keeps (bytes live OUTSIDE
    # conversation history) so reinspect_images can re-read earlier attachments against
    # a NEW question without re-paying vision tokens per turn. 0 disables retention.
    session_retention: int = Field(default=12, ge=0, le=50)
    # Necessity gating: each reinspect call is a full vision-model call, so a per-turn
    # budget stops a looping agent from burning them; repeated same-args calls are
    # memoized (free) and don't count. 0 disables the tool's vision path entirely.
    max_reinspect_per_turn: int = Field(default=2, ge=0, le=10)


def _reject_credentials_in_url(token_url: str) -> None:
    """Design E16: credentials never ride the URL (display_url would strip them anyway)."""
    parts = urlsplit(token_url)
    if parts.username or parts.password or parts.query:
        raise ValueError(
            "ask_your_docs.llm.auth.token_url must not carry credentials in userinfo "
            f"or query; got {parts.scheme}://{parts.hostname or ''}{parts.path}"
        )


class LlmAuthConfig(BaseModel):
    """Where the bearer comes from — exactly one of ``token_url`` / ``api_key_env`` (R2)."""

    # hide_input_in_errors covers ONE path: direct ``LlmAuthConfig(...)``, where this
    # model is the outermost one pydantic validates. Nested (under LlmConnectionConfig
    # or AppConfig) the flag is ignored — see error_redaction.py (design E16 / G8-H4).
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    token_url: str | None = Field(default=None)
    api_key_env: str | None = Field(default=None)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> LlmAuthConfig:
        given = [name for name in ("token_url", "api_key_env") if getattr(self, name)]
        if len(given) != 1:
            raise ValueError(
                f"ask_your_docs.llm.auth: got {given or 'neither'}, "
                "expected exactly one of token_url / api_key_env"
            )
        if self.token_url is not None:
            _reject_credentials_in_url(self.token_url)
        return self


class VisionModelConfig(BaseModel):
    """``vision: {model: <id>}`` — a second model on the same endpoint sees the images."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1)


class LlmConnectionConfig(BaseModel):
    """The ``ask_your_docs.llm`` block (design §5.1); ``None`` on the parent = today."""

    # hide_input_in_errors covers the second direct path, ``LlmConnectionConfig
    # .model_validate({...})``, where THIS model is outermost and would echo the
    # nested auth mapping. Under AppConfig it is ignored — error_redaction.py owns
    # that path, blanking every input in THIS block (sibling blocks keep theirs).
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    base_url: str | None = Field(default=None)  # None = the SDK's vendor default
    model: str | None = Field(default=None)  # None = pick in the dialog
    auth: LlmAuthConfig | None = Field(default=None)  # None = no bearer
    token_field: str | None = Field(default=None)  # None = the whole body is the token
    renew_on_status: tuple[int, ...] = Field(default=_DEFAULT_RENEW_ON_STATUS)
    vision: bool | VisionModelConfig | None = Field(default=None)  # None = detect

    @field_validator("renew_on_status")
    @classmethod
    def _only_renewable_statuses(cls, statuses: tuple[int, ...]) -> tuple[int, ...]:
        for status in statuses:
            if status not in _RENEWABLE_STATUSES:
                raise ValueError(
                    f"ask_your_docs.llm.renew_on_status: got {status}, "
                    f"expected a subset of {sorted(_RENEWABLE_STATUSES)}"
                )
        return statuses

    @model_validator(mode="after")
    def _token_service_names_its_endpoint(self) -> LlmConnectionConfig:
        # Design E14: a token service authenticates one internal endpoint, so the block must
        # name it; api_key_env with base_url: null is the vendor default (D2) and stays valid.
        if self.auth is not None and self.auth.token_url and not self.base_url:
            raise ValueError("ask_your_docs.llm.auth.token_url needs base_url; got null")
        return self


class AskYourDocsConfig(BaseModel):
    """Top-level ``ask_your_docs:`` block — architecture, multimodal policy, LLM connection."""

    model_config = ConfigDict(extra="forbid")

    # One of agent_registry.names(); "text_react" pins pre-image behavior
    # exactly, "auto" routes by the detected capability.
    architecture: str = Field(default="auto")
    multimodal: MultimodalConfig = Field(default_factory=MultimodalConfig)
    images: ImagesConfig = Field(default_factory=ImagesConfig)
    # Endpoint, bearer and vision rule; None = today (vendor default, OPENAI_API_KEY via SDK).
    llm: LlmConnectionConfig | None = Field(default=None)


__all__ = (
    "AskYourDocsConfig",
    "AuthMode",
    "ImagesConfig",
    "LlmAuthConfig",
    "LlmConnectionConfig",
    "MultimodalConfig",
    "MultimodalDetectionConfig",
    "VisionModelConfig",
    "VisionRule",
)
