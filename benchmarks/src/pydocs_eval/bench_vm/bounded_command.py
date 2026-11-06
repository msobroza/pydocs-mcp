"""Run an external CLI (``gcloud``, ``terraform``) with a timeout and a bounded retry.

Every external call the bench VM makes goes through ``run_bounded``: each
attempt has a timeout, a transient failure (a timeout, or stderr naming a
retryable server condition) is retried a bounded number of times with a short
backoff, and anything else fails at once. The runner is a seam, so tests replay
scripted outcomes instead of spawning anything.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

DEFAULT_ATTEMPTS = 3
_BACKOFF_BASE_S = 2.0
# Server-side conditions worth another try; anything else is a real answer. The
# status codes need word boundaries: a 16-digit GCS generation can contain "503".
_TRANSIENT = re.compile(
    r"\b(?:429|500|502|503|504)\b|rateLimitExceeded|backendError|internalError"
    r"|ServiceUnavailable|temporarily unavailable|Connection reset|Connection aborted"
    r"|TLS handshake timeout"
)
# Credentials the operator's session must not be swapped for: the launcher runs
# as `gcloud auth login`, and no key file is read, written or referenced (ADR 0026).
_KEY_FILE_ENV_VARS = (
    "GOOGLE_APPLICATION_CREDENTIALS",
    "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE",
    "CLOUDSDK_AUTH_ACCESS_TOKEN_FILE",
)


@dataclass(frozen=True, slots=True)
class CompletedCommand:
    returncode: int
    stdout: bytes
    stderr: bytes

    @property
    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace").strip()


class CommandRunner(Protocol):
    def __call__(
        self, argv: Sequence[str], *, stdin: bytes | None, timeout_s: float, env: Mapping[str, str]
    ) -> CompletedCommand: ...


class BoundedCommandError(RuntimeError):
    """The command failed for good: a permanent error, or every attempt was transient."""

    def __init__(self, argv: Sequence[str], detail: str, *, stderr: str = "") -> None:
        self.argv = tuple(argv)
        self.stderr = stderr
        super().__init__(f"{' '.join(argv[:4])} … failed: {detail}")


def run_subprocess(
    argv: Sequence[str], *, stdin: bytes | None, timeout_s: float, env: Mapping[str, str]
) -> CompletedCommand:
    """The default runner: one real process, killed at ``timeout_s``."""
    completed = subprocess.run(  # argv built here, never a shell
        list(argv), input=stdin, capture_output=True, timeout=timeout_s, env=dict(env), check=False
    )
    return CompletedCommand(completed.returncode, completed.stdout, completed.stderr)


def operator_environment() -> dict[str, str]:
    """This process's environment without any credential-file override."""
    return {key: value for key, value in os.environ.items() if key not in _KEY_FILE_ENV_VARS}


def is_transient(stderr: str) -> bool:
    return _TRANSIENT.search(stderr) is not None


def run_bounded(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    timeout_s: float,
    stdin: bytes | None = None,
    attempts: int = DEFAULT_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> CompletedCommand:
    """Run ``argv`` until it succeeds or fails permanently, at most ``attempts`` times.

    A non-zero exit whose stderr is not transient is returned to the caller,
    which knows what the error means (a missing object, a stockout); only
    timeouts and transient server errors are retried here.
    """
    last_detail = ""
    for attempt in range(1, attempts + 1):
        try:
            completed = runner(argv, stdin=stdin, timeout_s=timeout_s, env=operator_environment())
        except subprocess.TimeoutExpired:
            last_detail = f"timed out after {timeout_s}s"
        else:
            if completed.returncode == 0 or not is_transient(completed.stderr_text):
                return completed
            last_detail = completed.stderr_text
        if attempt < attempts:
            sleep(_BACKOFF_BASE_S * attempt)
    raise BoundedCommandError(argv, f"{attempts} attempts, last: {last_detail}")
