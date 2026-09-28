"""What the judge tests share: the planted bearer, the goldens, and a clock that never waits."""

from __future__ import annotations

import json
from pathlib import Path

#: A bearer no error text or log line may ever carry.
PLANTED_BEARER = "sk-or-v1-planted-judge-bearer-0123456789abcdef"

_GOLDENS = Path(__file__).parent / "goldens"
#: The eval suite's root, where its configs and pyproject live.
BENCHMARKS_ROOT = Path(__file__).resolve().parents[2]
#: The judge deployment the owner pinned.
DEPLOYMENT_YAML = BENCHMARKS_ROOT / "configs" / "judge_openrouter.yaml"


def golden(name: str) -> dict[str, object]:
    """The request or response body pinned in ``goldens/<name>``."""
    body: dict[str, object] = json.loads((_GOLDENS / name).read_text(encoding="utf-8"))
    return body


class FakeClock:
    """A clock the client's waits advance, so a deadline passes without real time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now
