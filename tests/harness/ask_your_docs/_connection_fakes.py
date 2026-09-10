"""Named fakes for the LLM-connection tests (clean-code rule: no ad-hoc mocks).

Every fake is an ``httpx.MockTransport`` handler or a ``BearerSource`` — no
network leaves a test. ``FakeModelsEndpoint`` / ``FakeProbeLlm`` are the
capability ladder's injectable seams (moved here from
test_multimodal_detection.py when the seams widened to ``(connection, bearer)``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from pydocs_mcp.harness.ask_your_docs.bearer_tokens import (
    BearerStatus,
    TokenServiceError,
    last_four_of,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import AuthMode


def chat_completion_body(text: str) -> dict:
    """The minimal chat-completion JSON langchain-openai parses into an AIMessage."""
    return {
        "id": "cmpl-fake",
        "object": "chat.completion",
        "created": 0,
        "model": "fake",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


class FakeTokenService:
    """The token endpoint as an ``httpx.MockTransport`` handler; records every call.

    ``fail_first`` answers 503 that many times and then recovers; ``fail_from``
    answers 503 from that call number on (1-based); ``body_shape`` is
    ``"text"`` (the body IS the token), ``"json"`` (``{json_key: token}``) or
    ``"html"`` (a non-JSON body, to provoke E2).
    """

    def __init__(
        self,
        tokens: list[str],
        *,
        fail_first: int = 0,
        fail_from: int | None = None,
        body_shape: str = "text",
        json_key: str = "access_token",
    ) -> None:
        self.tokens = list(tokens)
        self.fail_first = fail_first
        self.fail_from = fail_from
        self.body_shape = body_shape
        self.json_key = json_key
        self.calls = 0
        self.served = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.fail_first or (self.fail_from and self.calls >= self.fail_from):
            return httpx.Response(503, text="token service down")
        token = self.tokens[min(self.served, len(self.tokens) - 1)]
        self.served += 1
        if self.body_shape == "json":
            return httpx.Response(200, json={self.json_key: token})
        if self.body_shape == "html":
            return httpx.Response(
                200, text="<html>nope</html>", headers={"content-type": "text/html"}
            )
        return httpx.Response(200, text=token)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


class RecordingTransport:
    """The chat endpoint (and ``/models``) as an ``httpx.MockTransport`` handler.

    Answers the scripted statuses in order (200 after the script runs out; an
    ``Exception`` item is raised instead of answered), records every request,
    and — with ``echo_bearer_in_401`` — puts the presented bearer into the 401
    body, the way gateways do (AC-35).
    """

    def __init__(
        self,
        script: list[int | Exception] | None = None,
        *,
        echo_bearer_in_401: bool = False,
        model_ids: tuple[str, ...] = ("model-a", "model-b"),
        reply: str = "OK",
    ) -> None:
        self.script = list(script or [])
        self.echo_bearer_in_401 = echo_bearer_in_401
        self.model_ids = model_ids
        self.reply = reply
        self.requests: list[httpx.Request] = []
        # WHY snapshots: an httpx.Auth flow re-sends the SAME Request object
        # after rewriting its Authorization header, so reading the header off
        # ``self.requests`` afterwards would show the renewed bearer on every
        # entry. The headers are captured per call, as the transport saw them.
        self.auth_seen: list[str | None] = []
        self.retry_counts_seen: list[str | None] = []

    def record(self, request: httpx.Request) -> None:
        """The snapshot every handler owes: the request AND the headers it carried.

        Subclasses that answer their own body MUST call this instead of appending
        to ``requests`` themselves — otherwise ``authorizations()`` passes vacuously.
        """
        self.requests.append(request)
        self.auth_seen.append(request.headers.get("Authorization"))
        self.retry_counts_seen.append(request.headers.get("x-stainless-retry-count"))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.record(request)
        status = self.script.pop(0) if self.script else 200
        if isinstance(status, Exception):
            raise status
        if status == 200 and request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": i} for i in self.model_ids]})
        if status == 200:
            return httpx.Response(200, json=chat_completion_body(self.reply))
        presented = request.headers.get("Authorization", "")
        message = f"rejected {presented}" if self.echo_bearer_in_401 else "rejected"
        return httpx.Response(status, json={"error": {"message": message, "type": "auth"}})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def authorizations(self) -> list[str | None]:
        return list(self.auth_seen)

    def retry_counts(self) -> list[str | None]:
        return list(self.retry_counts_seen)


class RotatingBearer:
    """A ``BearerSource`` whose ``current()`` returns k1, k2, k3… (proves per-attempt re-evaluation)."""

    def __init__(self) -> None:
        self.count = 0
        self.last = ""

    def current(self) -> str:
        self.count += 1
        self.last = f"k{self.count}"
        return self.last

    def peek(self) -> str:
        return self.last

    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str:
        return self.current()

    def describe(self) -> BearerStatus:
        return BearerStatus(AuthMode.ENV_KEY, None, last_four_of(self.last))


class FakeBearer:
    """A fixed token-service bearer for page tests: fixed renewal time, renew counter, optional failure.

    ``fail_message`` is the E1 text ``current()`` raises with (the default is the
    real bearer's unreachable-service sentence); a renewal stamps ``renewed_at``
    the way ``TokenServiceBearer._replace`` does, so a page comparing the status
    before and after a Renew sees a real renewal here and a cache hit there.
    """

    def __init__(
        self,
        value: str = "tok-fixed-abcd",
        *,
        renewed_at: datetime | None = None,
        fail: bool = False,
        fail_message: str | None = None,
    ) -> None:
        self.value = value
        self.renewed_at = renewed_at
        self.fail = fail
        self.fail_message = fail_message
        self.renewals = 0
        self.last_error: str | None = None

    def current(self) -> str:
        if self.fail:
            raise TokenServiceError(
                self.fail_message
                or "token service http://localhost:8899/access-token unreachable after 3 attempts "
                "(last: ConnectError)"
            )
        return self.value

    def peek(self) -> str:
        return self.value

    def renew(self, rejected: str | None = None, *, reason: str = "manual") -> str:
        self.renewals += 1
        token = self.current()
        self.renewed_at = datetime.now().astimezone()
        return token

    def describe(self) -> BearerStatus:
        return BearerStatus(AuthMode.TOKEN_SERVICE, self.renewed_at, last_four_of(self.value))


class FakeClock:
    """An injectable ``now`` for the renew interval and the listing TTL — tests never sleep."""

    def __init__(self, start: float = 1000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeModelsEndpoint:
    """The rung-3 / listing seam ``(connection, bearer) -> list[dict]``; records the bearer it saw."""

    def __init__(
        self,
        entry: dict | None = None,
        error: Exception | None = None,
        *,
        ids: tuple[str, ...] = (),
    ) -> None:
        self.entry = entry
        self.error = error
        self.ids = ids
        self.calls = 0
        self.seen_bearer: str | None = None
        self.seen_base_url: str | None = None

    async def __call__(self, connection: Any, bearer: Any) -> list[dict]:
        self.calls += 1
        self.seen_bearer = bearer.current()
        self.seen_base_url = connection.base_url
        if self.error is not None:
            raise self.error
        if self.entry is not None:
            return [self.entry]
        return [{"id": i} for i in self.ids]

    # The per-server ``/models`` entry shapes (model-params v2 §0), so profile and
    # control-support tests read like the endpoint that produced them.

    @staticmethod
    def openrouter_entry(
        model_id: str,
        supported_parameters: tuple[str, ...],
        *,
        efforts: tuple[str, ...] | None = None,
        mandatory: bool = False,
    ) -> dict:
        reasoning: dict[str, Any] = {"mandatory": mandatory}
        if efforts is not None:
            reasoning["supported_efforts"] = list(efforts)
        return {
            "id": model_id,
            "supported_parameters": list(supported_parameters),
            "reasoning": reasoning,
        }

    @staticmethod
    def vllm_entry(model_id: str, *, max_model_len: int = 40960) -> dict:
        return {"id": model_id, "owned_by": "vllm", "max_model_len": max_model_len}

    @staticmethod
    def ollama_entry(model_id: str) -> dict:
        return {"id": model_id, "object": "model", "owned_by": "library"}

    @staticmethod
    def llamacpp_entry(model_id: str) -> dict:
        return {"id": model_id, "object": "model", "owned_by": "llamacpp"}


class FakeModelGroupInfo:
    """A LiteLLM ``/model_group/info`` row (litellm/types/router.py ModelGroupInfo shape).

    ``row()`` is the one entry for the chosen model; ``payload()`` is the whole
    200 body; awaiting the instance is the probe seam and records its calls.
    """

    def __init__(
        self,
        model_group: str = "team-sonnet",
        *,
        providers: tuple[str, ...] = ("anthropic",),
        supported_openai_params: tuple[str, ...] | None = (
            "temperature",
            "top_p",
            "max_tokens",
            "max_completion_tokens",
            "reasoning_effort",
        ),
        supported_reasoning_efforts: tuple[str, ...] | None = None,
        max_output_tokens: float | None = 64000.0,
    ) -> None:
        self.model_group = model_group
        self.providers = providers
        self.supported_openai_params = supported_openai_params
        self.supported_reasoning_efforts = supported_reasoning_efforts
        self.max_output_tokens = max_output_tokens
        self.calls = 0

    def row(self) -> dict:
        params = self.supported_openai_params
        return {
            "model_group": self.model_group,
            "providers": list(self.providers),
            "max_output_tokens": self.max_output_tokens,
            "supported_openai_params": None if params is None else list(params),
            "supported_reasoning_efforts": self.supported_reasoning_efforts,
        }

    def payload(self) -> dict:
        return {"data": [self.row()]}

    async def __call__(self, connection: Any, bearer: Any) -> dict:
        self.calls += 1
        return self.payload()


class FakeProbeLlm:
    """The rung-4 seam ``(connection, bearer, model, timeout) -> str``; simulates the tiny-image call."""

    def __init__(self, outcome: str = "ok") -> None:
        self.outcome = outcome  # "ok" | "image_error" | "server_error"
        self.calls = 0
        self.seen_bearer: str | None = None

    async def __call__(self, connection: Any, bearer: Any, model: str, timeout: float) -> str:
        self.calls += 1
        self.seen_bearer = bearer.current()
        if self.outcome == "image_error":
            raise ValueError("400: image content not supported by this model")
        if self.outcome == "server_error":
            raise TimeoutError("upstream timeout")
        return "OK"
