"""The Jev judge: ``judge.jev``'s pinned model on OpenRouter's System One route.

``jev_wire.JevJudge`` is the port a scorer depends on; :class:`JevJudgeClient` is
its owned plain-HTTP adapter (no vendor SDK), and :class:`FakeJevJudgeClient`
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

import httpx

from pydocs_eval.judge.config import JevConfig
from pydocs_eval.judge.jev_cache import JevResponseCache
from pydocs_eval.judge.jev_wire import (
    JevRequest,
    JevResponse,
    jev_cache_key,
    parse_jev_response,
)
from pydocs_eval.judge.judge_errors import (
    JudgeModelMismatchError,
    JudgeResponseError,
    JudgeUnavailableError,
)
from pydocs_eval.judge.model_ids import check_served_model
from pydocs_eval.judge.openrouter_body import redact
from pydocs_eval.judge.openrouter_http import CallPolicy, bearer_from_env, post_json, route_url
from pydocs_eval.judge.role_config import JEV_MODEL_KEY, pinned_model

_SYSTEM_ONE_ROUTE = "/systemone"
# The pin the fake answers under unless a test names another: the deployment's.
_DEFAULT_FAKE_JEV_MODEL = "jev-1.13"


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
        pinned_model(JEV_MODEL_KEY, self.config.model)

    def judge(self, request: JevRequest) -> JevResponse:
        """Jev's answers to every question of ``request``, from the cache when it holds them."""
        body = request.body(self.config.model)
        key = jev_cache_key(body)
        cached = self._from_cache(key, request)
        if cached is not None:
            return cached
        bearer = bearer_from_env(self.config.api_key_env)
        url = route_url(self.config, _SYSTEM_ONE_ROUTE)
        policy = CallPolicy.of(self._label(), self.config, self.sleep)
        payload = post_json(self.http, url, body, bearer=bearer, policy=policy)
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
        try:
            response = parse_jev_response(payload)
        except JudgeResponseError as exc:
            raise JudgeResponseError(f"{self._label()}: {redact(str(exc), bearer)}") from None
        check_served_model(self.config.model, response.served_model, bearer=bearer)
        unanswered = sorted(set(request.questions) - set(response.answers))
        if unanswered:
            raise JudgeResponseError(f"{self._label()}: no answer to {unanswered!r}")
        return response

    def _label(self) -> str:
        return f"Jev {self.config.model}"


@dataclass(slots=True)
class FakeJevJudgeClient:
    """Scripted offline double: canned responses by input hash, plus a call counter.

    ``scripted`` maps a request's input hash under ``model`` to the response Jev
    would give. An unscripted request is an outage (``JudgeUnavailableError``),
    the real client's failure path; ``calls`` counts every request, answered or not.

    Example:
        >>> FakeJevJudgeClient(scripted={}).calls
        0
    """

    scripted: Mapping[str, JevResponse]
    model: str = _DEFAULT_FAKE_JEV_MODEL
    calls: int = field(default=0, init=False)

    @classmethod
    def answering(
        cls,
        pairs: Iterable[tuple[JevRequest, JevResponse]],
        model: str = _DEFAULT_FAKE_JEV_MODEL,
    ) -> FakeJevJudgeClient:
        """A fake that answers each request of ``pairs`` with its response."""
        scripted = {jev_cache_key(request.body(model)): response for request, response in pairs}
        return cls(scripted, model)

    def judge(self, request: JevRequest) -> JevResponse:
        self.calls += 1
        key = jev_cache_key(request.body(self.model))
        response = self.scripted.get(key)
        if response is None:
            raise JudgeUnavailableError(f"fake Jev: no scripted answer for request {key[:12]}")
        return response


__all__ = ("FakeJevJudgeClient", "JevJudgeClient")
