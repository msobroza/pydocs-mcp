"""The Jev judge: ``judge.jev``'s pinned model on OpenRouter's System One route.

:class:`JevJudge` is the port a scorer depends on; :class:`JevJudgeClient` is
the owned plain-HTTP adapter (no vendor SDK), and :class:`FakeJevJudgeClient`
the offline double every test uses. The client asks the cache first, so a warm
cache makes no call; it caches only an answer from the pinned model.

Example:
    >>> with httpx.Client() as http:  # doctest: +SKIP
    ...     client = JevJudgeClient(config, cache=JevResponseCache(jev_cache_dir(config)), http=http)
    ...     client.judge(request).answers["needle_identified"]
    NoulAnswer(probability=0.97)
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx

from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.jev_cache import JevResponseCache
from pydocs_eval.judge.jev_wire import JevRequest, JevResponse, jev_cache_key, parse_jev_response
from pydocs_eval.judge.openrouter_http import (
    CallPolicy,
    JudgeModelMismatchError,
    JudgeResponseError,
    JudgeUnavailableError,
    bearer_from_env,
    check_served_model,
    post_json,
)
from pydocs_eval.judge.role_config import pinned_model

_JEV_MODEL_KEY = "judge.jev.model"
_SYSTEM_ONE_ROUTE = "/systemone"


@runtime_checkable
class JevJudge(Protocol):
    """Anything that answers a Jev request."""

    def judge(self, request: JevRequest) -> JevResponse: ...


@dataclass(frozen=True, slots=True)
class JevJudgeClient:
    """``config.model`` on ``<config.endpoint>/systemone``, cached by input hash in ``cache``.

    Raises:
        JudgeConfigError: ``config.model`` is empty (named ``judge.jev.model``),
            or, at call time, the key variable is unset.
    """

    config: JevConfig
    cache: JevResponseCache
    http: httpx.Client
    sleep: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        pinned_model(_JEV_MODEL_KEY, self.config.model)

    def judge(self, request: JevRequest) -> JevResponse:
        """Jev's answers to every question of ``request``, from the cache when it holds them."""
        body = request.body(self.config.model)
        key = jev_cache_key(body)
        cached = self._from_cache(key, request)
        if cached is not None:
            return cached
        bearer = bearer_from_env(self.config.api_key_env)
        payload = post_json(self.http, self._url(), body, bearer=bearer, policy=self._policy())
        response = self._checked(payload, request, bearer=bearer)
        self.cache.put(key, payload)
        return response

    def _from_cache(self, key: str, request: JevRequest) -> JevResponse | None:
        """The cached answer to ``request``, or ``None`` when the entry is absent or unusable."""
        payload = self.cache.get(key)
        if payload is None:
            return None
        try:
            return self._checked(payload, request, bearer="")
        except (JudgeResponseError, JudgeModelMismatchError):
            return None

    def _checked(self, payload: object, request: JevRequest, *, bearer: str) -> JevResponse:
        """``payload`` read, from the pinned model, answering every question asked."""
        response = parse_jev_response(payload)
        check_served_model(self.config.model, response.served_model, bearer=bearer)
        unanswered = sorted(set(request.questions) - set(response.answers))
        if unanswered:
            raise JudgeResponseError(f"{self._label()}: no answer to {unanswered!r}")
        return response

    def _url(self) -> str:
        return f"{self.config.endpoint.rstrip('/')}{_SYSTEM_ONE_ROUTE}"

    def _policy(self) -> CallPolicy:
        return CallPolicy(
            label=self._label(),
            timeout_seconds=self.config.timeout_seconds,
            retries=self.config.retries,
            sleep=self.sleep,
        )

    def _label(self) -> str:
        return f"Jev {self.config.model}"


@dataclass(slots=True)
class FakeJevJudgeClient:
    """Scripted offline double: canned responses by input hash, plus a call counter.

    ``scripted`` maps a request's input hash under ``model`` to the response Jev
    would give. An unscripted request is an outage (``JudgeUnavailableError``),
    the real client's failure path; ``calls`` counts every request, answered or not.
    """

    scripted: Mapping[str, JevResponse]
    model: str = "jev-1.13"
    calls: int = field(default=0, init=False)

    @classmethod
    def answering(
        cls, pairs: Iterable[tuple[JevRequest, JevResponse]], model: str = "jev-1.13"
    ) -> FakeJevJudgeClient:
        """A fake that answers each request of ``pairs`` with its response."""
        return cls(
            {jev_cache_key(request.body(model)): response for request, response in pairs}, model
        )

    def judge(self, request: JevRequest) -> JevResponse:
        self.calls += 1
        key = jev_cache_key(request.body(self.model))
        response = self.scripted.get(key)
        if response is None:
            raise JudgeUnavailableError(f"fake Jev: no scripted answer for request {key[:12]}")
        return response


__all__ = ("FakeJevJudgeClient", "JevJudge", "JevJudgeClient")
