"""Stands in for running ``gcloud`` / ``terraform``: scripted results, recorded calls."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_eval.bench_vm.bounded_command import CompletedCommand


@dataclass(frozen=True, slots=True)
class CommandCall:
    """One command the runner was asked to run.

    ``metadata_files`` holds the contents of every ``--metadata-from-file``
    file as the command would have read them, since the adapter removes them
    once the command returns.
    """

    argv: tuple[str, ...]
    stdin: bytes | None
    timeout_s: float
    env: Mapping[str, str]
    metadata_files: Mapping[str, str]


def _metadata_files_named_by(argv: Sequence[str]) -> dict[str, str]:
    prefix = "--metadata-from-file="
    contents: dict[str, str] = {}
    for arg in argv:
        if arg.startswith(prefix):
            for pair in arg.removeprefix(prefix).split(","):
                key, path = pair.split("=", 1)
                contents[key] = Path(path).read_text(encoding="utf-8")
    return contents


@dataclass
class ScriptedCommandRunner:
    """Stands in for running ``gcloud`` / ``terraform``: replays scripted results, records calls.

    Each script entry is a ``CompletedCommand`` or ``"timeout"``; the runner
    replays them in order and repeats the last one when it runs out.
    """

    script: list[CompletedCommand | str]
    calls: list[CommandCall] = field(default_factory=list)

    def __call__(
        self, argv: Sequence[str], *, stdin: bytes | None, timeout_s: float, env: Mapping[str, str]
    ) -> CompletedCommand:
        self.calls.append(
            CommandCall(tuple(argv), stdin, timeout_s, dict(env), _metadata_files_named_by(argv))
        )
        outcome = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(list(argv), timeout_s)
        assert isinstance(outcome, CompletedCommand)
        return outcome


def ok(stdout: str = "") -> CompletedCommand:
    return CompletedCommand(returncode=0, stdout=stdout.encode(), stderr=b"")


def failed(stderr: str, returncode: int = 1) -> CompletedCommand:
    return CompletedCommand(returncode=returncode, stdout=b"", stderr=stderr.encode())
