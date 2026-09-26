"""The chat model's request settings: timeout, retry count and OpenRouter provider route.

Spec 2026-09-25 step 2b (Q48(c)/(d)): the ``ask_your_docs.llm`` block — for an eval
arm, the pinned ``--llm-block`` file — is the single source of how long one chat
request may take, how often a failed one is retried, and which OpenRouter upstream
serves it. ``resolve_llm_connection`` carries the three onto the connection and
refuses a route outside the OpenRouter wire profile
(:func:`refuse_provider_routing_off_openrouter`); :func:`block_request_kwargs` turns
them into the chat factory's keywords.

Every model the agent converses with passes them (the main model, a separate vision
model, the answer written at the turn budget); a capability probe and the Test
button keep their own short bounds and send no route. That is the
``parallel_tool_calls`` rule: the factory reads keywords, never the connection
record, so a build that is not the agent's stays byte-identical.

Example:
    >>> provider_routing_body(None) is None
    True
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.ask_your_docs.provider_profiles import ProviderProfile, wire_profile
from pydocs_mcp.retrieval.config.ask_your_docs_llm_models import OpenRouterProviderRouting
from pydocs_mcp.retrieval.config.ask_your_docs_params_models import ProviderName

if TYPE_CHECKING:  # the record type only; llm_connection imports this module at runtime
    from pydocs_mcp.harness.ask_your_docs.llm_connection import LlmConnection

# OpenRouter's request-body object for provider preferences (its provider-routing docs).
_OPENROUTER_PROVIDER_FIELD = "provider"


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


def provider_routing_body(routing: OpenRouterProviderRouting | None) -> dict[str, Any] | None:
    """The request-body fields a route adds; ``None`` adds nothing.

    Example:
        >>> route = OpenRouterProviderRouting(order=("deepinfra/bf16",))
        >>> provider_routing_body(route)
        {'provider': {'order': ['deepinfra/bf16'], 'allow_fallbacks': False}}
    """
    if routing is None:
        return None
    preferences = {"order": list(routing.order), "allow_fallbacks": routing.allow_fallbacks}
    return {_OPENROUTER_PROVIDER_FIELD: preferences}


class BlockRequestKwargs(TypedDict, total=False):
    """``build_chat_model``'s keywords for the block's request settings — set ones only."""

    timeout_seconds: float
    max_retries: int
    extra_body: dict[str, Any]


def block_request_kwargs(connection: LlmConnection) -> BlockRequestKwargs:
    """The request settings every model the agent builds passes to the chat factory.

    Only the keys the block set, so a block without them builds exactly today's
    model — the ``ChatOpenAI`` kwargs are byte-identical.
    """
    kwargs: BlockRequestKwargs = {}
    if connection.timeout_seconds is not None:
        kwargs["timeout_seconds"] = connection.timeout_seconds
    if connection.max_retries is not None:
        kwargs["max_retries"] = connection.max_retries
    body = provider_routing_body(connection.provider_routing)
    if body is not None:
        kwargs["extra_body"] = body
    return kwargs


__all__ = (
    "BlockRequestKwargs",
    "ProviderRoutingProfileError",
    "block_request_kwargs",
    "provider_routing_body",
    "refuse_provider_routing_off_openrouter",
)
