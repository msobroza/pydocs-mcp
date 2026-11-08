"""Parse what a ``gcloud ... --format=json`` call printed; an unexpected shape is a ``BenchCloudError``.

Every ``GcloudBenchCloud`` call that reads gcloud's JSON goes through these,
so the error wording and the shape checks cannot drift apart between them.
"""

from __future__ import annotations

import json
from typing import Any

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError


def json_object(stdout: bytes, what: str) -> dict[str, Any]:
    answer = json_value(stdout, what)
    if not isinstance(answer, dict):
        raise BenchCloudError(f"{what}: got {answer!r}, expected a JSON object")
    return answer


def json_list(stdout: bytes, what: str) -> list[dict[str, Any]]:
    answer = json_value(stdout, what)
    if not isinstance(answer, list) or not all(isinstance(item, dict) for item in answer):
        raise BenchCloudError(f"{what}: got {answer!r}, expected a JSON list of objects")
    return answer


def json_value(stdout: bytes, what: str) -> object:
    try:
        return json.loads(stdout or b"null")
    except json.JSONDecodeError as exc:
        raise BenchCloudError(f"{what}: got unreadable JSON ({exc}), expected JSON") from None
