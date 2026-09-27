"""The chat model's request settings: timeout, retry count and OpenRouter provider route.

Spec 2026-09-25 step 2b (Q48(c)/(d)): the ``ask_your_docs.llm`` block — for an eval
arm, the pinned ``--llm-block`` file — is the single source of how long one chat
request may take, how often a failed one is retried, and which OpenRouter upstream
serves it. ``resolve_llm_connection`` carries the three onto the connection as one
:class:`ChatRequestSettings` and refuses a route outside the OpenRouter wire profile
(:func:`refuse_provider_routing_off_openrouter`).

Every model the agent converses with is built with
:meth:`ChatRequestSettings.chat_factory_kwargs` (the main model, a separate vision
model, and the finalize model that will write the answer at the turn budget). A
capability probe and the Test button keep their own short bounds and send no route —
the ``parallel_tool_calls`` rule: the factory reads keywords, never the connection
record, so a build that is not the agent's stays byte-identical. The price of that
rule: with a route set, the image probe asks whichever upstream OpenRouter picks, not
the pinned one.

Example:
    >>> NO_REQUEST_SETTINGS.chat_factory_kwargs()
    {}
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypedDict

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs.provider_profiles import ProviderProfile, wire_profile
from pydocs_mcp.retrieval.config.ask_your_docs_llm_models import (
    LlmConnectionConfig,
    OpenRouterProviderRouting,
)
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ProviderName

# OpenRouter's request-body object for provider preferences (its provider-routing docs).
_OPENROUTER_PROVIDER_FIELD = "provider"


class ChatFactoryKwargs(TypedDict, total=False):
    """``build_chat_model``'s keywords for the request settings a block set — only those."""

    timeout_seconds: float
    max_retries: int
    extra_body: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChatRequestSettings:
    """How every chat request the agent sends is bounded and routed.

    ``None`` fields send nothing, so :data:`NO_REQUEST_SETTINGS` builds exactly
    today's model. ``provider_routing`` becomes the request body's ``provider``
    object and is only ever resolved under the OpenRouter wire profile.
    """

    timeout_seconds: float | None = None
    max_retries: int | None = None
    provider_routing: OpenRouterProviderRouting | None = None

    @classmethod
    def of_block(cls, block: LlmConnectionConfig | None) -> ChatRequestSettings:
        """The settings an ``ask_your_docs.llm`` block pins; no block pins none."""
        if block is None:
            return NO_REQUEST_SETTINGS
        return cls(block.timeout_seconds, block.max_retries, block.provider_routing)

    def chat_factory_kwargs(self) -> ChatFactoryKwargs:
        """The chat factory's keywords for the settings that are set, and nothing else."""
        kwargs: ChatFactoryKwargs = {}
        if self.timeout_seconds is not None:
            kwargs["timeout_seconds"] = self.timeout_seconds
        if self.max_retries is not None:
            kwargs["max_retries"] = self.max_retries
        if self.provider_routing is not None:
            kwargs["extra_body"] = provider_routing_body(self.provider_routing)
        return kwargs


NO_REQUEST_SETTINGS = ChatRequestSettings()  # frozen: one shared "send nothing extra"


class ProviderRoutingProfileError(PydocsMCPError, ValueError):
    """A ``provider_routing`` block for an endpoint that is not OpenRouter.

    The ``provider`` body field means something to OpenRouter alone: another
    server rejects it, or worse, ignores it and serves whatever it serves.
    """

    def __init__(self, *, profile: ProviderProfile) -> None:
        self.profile = profile
        super().__init__(
            "ask_your_docs.llm.provider_routing is sent only under the openrouter wire "
            f"profile; got profile {profile.value!r}. Remove the key, or point the block "
            "at OpenRouter (provider: openrouter, or an openrouter.ai base_url)"
        )


def refuse_provider_routing_off_openrouter(
    routing: OpenRouterProviderRouting | None, provider: ProviderName, base_url: str | None
) -> None:
    """Raise when a route would reach an endpoint whose wire profile is not OpenRouter.

    The profile is the wire's own (declared provider, else the host), read off the
    RESOLVED endpoint, so a dialog that re-points ``base_url`` is gated too.

    Raises:
        ProviderRoutingProfileError: a route set for any other profile.
    """
    if routing is None:
        return
    profile = wire_profile(provider, base_url)
    if profile is not ProviderProfile.OPENROUTER:
        raise ProviderRoutingProfileError(profile=profile)


def provider_routing_body(routing: OpenRouterProviderRouting) -> dict[str, Any]:
    """The request-body fields a route adds.

    Example:
        >>> route = OpenRouterProviderRouting(order=("deepinfra/bf16",))
        >>> provider_routing_body(route)
        {'provider': {'order': ['deepinfra/bf16'], 'allow_fallbacks': False}}
    """
    preferences = {"order": list(routing.order), "allow_fallbacks": routing.allow_fallbacks}
    return {_OPENROUTER_PROVIDER_FIELD: preferences}


__all__ = (
    "NO_REQUEST_SETTINGS",
    "ChatFactoryKwargs",
    "ChatRequestSettings",
    "ProviderRoutingProfileError",
    "provider_routing_body",
    "refuse_provider_routing_off_openrouter",
)
