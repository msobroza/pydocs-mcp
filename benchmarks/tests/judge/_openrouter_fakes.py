"""A mock OpenRouter the chat-client tests share: sync completions, and batches end to end.

A batch is submitted, polled until it finishes after ``polls_to_finish`` polls,
and deleted; the endpoint keeps every request it saw.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping

import httpx

from pydocs_eval.judge.openrouter_chat import OpenRouterChatClient
from pydocs_eval.judge.role_config import ChatRole

from ._judge_fakes import FakeClock

#: The structured content every scripted completion answers with, unless told otherwise.
VERDICT_CONTENT: Mapping[str, object] = {"verdict": True, "evidence": "It names `get_params`."}
#: The id the mock gives the batch it accepts.
BATCH_ID = "batch_123"


def completion_response(
    model: str, content: object = VERDICT_CONTENT, *, cost: float | None = 0.0021
) -> dict[str, object]:
    """A ``/chat/completions`` response body answered by ``model`` with ``content`` as JSON."""
    body: dict[str, object] = {
        "id": "gen-1",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps(content)},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }
    if cost is not None:
        body["usage"] = {"prompt_tokens": 120, "completion_tokens": 30, "cost": cost}
    return body


class FakeOpenRouterEndpoint:
    """A mock OpenRouter: sync completions, and batches that finish after ``polls_to_finish`` polls."""

    def __init__(
        self,
        *,
        served: str,
        completion: Callable[[httpx.Request], httpx.Response] | None = None,
        polls_to_finish: int = 1,
        finished: Mapping[str, object] | None = None,
        submit: Callable[[httpx.Request], httpx.Response] | None = None,
        poll_failures: tuple[int, ...] = (),
        delete_status: int = 200,
    ) -> None:
        self.served = served
        self.completion = completion or (
            lambda request: httpx.Response(200, json=completion_response(served))
        )
        self.polls_to_finish = polls_to_finish
        self.finished = finished
        self.submit = submit
        self.poll_failures = list(poll_failures)
        self.delete_status = delete_status
        self.requests: list[httpx.Request] = []
        self.submitted: dict[str, object] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/chat/completions"):
            return self.completion(request)
        if request.method == "POST" and path.endswith("/batches"):
            return self._submit(request)
        if request.method == "DELETE":
            return self._delete()
        return self._poll(request)

    def sent(self, method: str) -> list[httpx.Request]:
        """Every request sent with ``method``, in order."""
        return [request for request in self.requests if request.method == method]

    def submitted_ids(self) -> list[str]:
        """The ``custom_id`` of every row in the last batch submitted, as sent."""
        return [str(item["custom_id"]) for item in self._submitted_items()]

    def _submitted_items(self) -> list[Mapping[str, object]]:
        items = self.submitted.get("requests", [])
        assert isinstance(items, list), items
        return [item for item in items if isinstance(item, Mapping)]

    def _submit(self, request: httpx.Request) -> httpx.Response:
        if self.submit is not None:
            return self.submit(request)
        self.submitted = json.loads(request.content)
        return httpx.Response(202, json={"id": BATCH_ID, "object": "batch", "status": "validating"})

    def _delete(self) -> httpx.Response:
        if self.delete_status != 200:
            return httpx.Response(self.delete_status, text="batch is not in a terminal state")
        return httpx.Response(200, json={"id": BATCH_ID, "object": "batch", "deleted": True})

    def _poll(self, request: httpx.Request) -> httpx.Response:
        if self.poll_failures:
            return httpx.Response(self.poll_failures.pop(0), text="upstream")
        if len(self.sent("GET")) < self.polls_to_finish:
            return httpx.Response(
                200, json={"id": BATCH_ID, "status": "in_progress", "results": None}
            )
        return httpx.Response(200, json=self.finished or self._completed())

    def _completed(self) -> dict[str, object]:
        results = [
            {
                "id": f"batch_req_{i}",
                "custom_id": item["custom_id"],
                "response": {
                    "status_code": 200,
                    "request_id": f"r{i}",
                    "body": completion_response(self.served, cost=None),
                },
                "error": None,
            }
            for i, item in enumerate(self._submitted_items())
        ]
        return {"id": BATCH_ID, "status": "completed", "results": results, "error": None}


def openrouter_client(
    role: ChatRole, openrouter: FakeOpenRouterEndpoint, clock: FakeClock | None = None
) -> OpenRouterChatClient:
    """``role``'s real client over the mock, on a clock that never waits."""
    clock = clock or FakeClock()
    return OpenRouterChatClient(
        role=role,
        http=httpx.Client(transport=httpx.MockTransport(openrouter)),
        sleep=clock.sleep,
        clock=clock.monotonic,
    )
