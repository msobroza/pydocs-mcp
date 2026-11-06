"""Provider keys: read from Secret Manager per kind, and put only in the child's environment.

Each row of the kind table declares the provider keys its CLI needs, by the
environment variable the CLI reads (``ANTHROPIC_API_KEY`` for the agent
track's metered billing, ADR 0014/0015; ``OPENROUTER_API_KEY`` for
before/after's shipped serving and chat configs). The secret holding a key is
named after it (``bench-anthropic-api-key``), as ``infra/bench-vm/terraform/
secrets.tf`` declares; a test pins the two together.

A value read here goes into the child's environment and nowhere else: never
into the supervisor's own environment, a log line, the run record or the
state directory. ``redact_provider_keys`` is for the one place the supervisor
repeats a child's words in its own log (the kind pre-flight's output).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydocs_eval.bench_vm.bench_cloud import BenchCloud
from pydocs_eval.bench_vm.run_kinds import RUN_KIND_TABLE

_SECRET_ID_PREFIX = "bench-"  # noqa: S105 — a secret-name prefix, not a secret
# Every key any kind declares: the child inherits none of them from the
# container, only the ones its own kind declares and the supervisor read.
PROVIDER_KEYS = frozenset(key for row in RUN_KIND_TABLE.values() for key in row.provider_keys)


def secret_id_for(provider_key: str) -> str:
    """The Secret Manager secret holding ``provider_key``.

    Example:
        >>> secret_id_for("OPENROUTER_API_KEY")
        'bench-openrouter-api-key'
    """
    return _SECRET_ID_PREFIX + provider_key.lower().replace("_", "-")


def read_provider_keys(
    cloud: BenchCloud, project: str, provider_keys: Iterable[str]
) -> dict[str, str]:
    """Each declared key's value, read from its secret; ``BenchCloudError`` names the first missing."""
    return {key: cloud.access_secret(project, secret_id_for(key)) for key in provider_keys}


def child_environment_with_provider_keys(
    inherited: Mapping[str, str], provider_keys: Mapping[str, str]
) -> dict[str, str]:
    """``inherited`` without any provider key, plus the ones the run's kind declared."""
    kept = {name: value for name, value in inherited.items() if name not in PROVIDER_KEYS}
    return {**kept, **provider_keys}


def redact_provider_keys(text: str, provider_keys: Mapping[str, str]) -> str:
    """``text`` with every key's value replaced by ``<KEY_NAME>``."""
    for name, value in provider_keys.items():
        text = text.replace(value, f"<{name}>")
    return text
