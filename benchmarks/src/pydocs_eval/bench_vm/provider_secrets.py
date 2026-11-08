"""Provider keys: read from Secret Manager per kind, and put only in the child's environment.

Each row of the kind table declares the provider keys its CLI needs, by the
environment variable the CLI reads (``ANTHROPIC_API_KEY`` for the agent
track's metered billing, ADR 0014/0015; ``OPENROUTER_API_KEY`` for
before/after's shipped serving and chat configs). The secret holding a key is
named after it (``bench-anthropic-api-key``), as ``infra/bench-vm/terraform/
secrets.tf`` declares; a test pins the two together.

A value read here goes into the child's environment and nowhere else: never
into the supervisor's own environment, a log line, the run record or the
state directory. If the child or its pre-flight prints one anyway, the
supervisor masks it in their output before any byte is written
(``ProviderKeyRedactor``, ADR 0026, amendment 2026-11-08). For that guard to
hold, ``unusable_provider_key`` refuses a value shorter than
``PROVIDER_KEY_MIN_BYTES`` (it would mask ordinary words throughout the log)
or with leading or trailing whitespace (a CLI that trims it prints a value
the redaction does not know). A batch run fails on it rather than skipping
the value: a skipped value would be a silent hole in the guarantee.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from pydocs_eval.bench_vm.bench_cloud import BenchCloud
from pydocs_eval.bench_vm.run_kinds import RUN_KIND_TABLE

_SECRET_ID_PREFIX = "bench-"  # noqa: S105 — a secret-name prefix, not a secret
# Every key any kind declares: the child inherits none of them from the
# container, only the ones its own kind declares and the supervisor read.
PROVIDER_KEYS = frozenset(key for row in RUN_KIND_TABLE.values() for key in row.provider_keys)
# The Anthropic and OpenRouter keys in use run to roughly 70-110 characters.
PROVIDER_KEY_MIN_BYTES = 20


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


def unusable_provider_key(provider_keys: Mapping[str, str]) -> str | None:
    """Why the first unusable value is refused, naming its key and never the value; else ``None``."""
    for name, value in provider_keys.items():
        if len(value.encode("utf-8")) < PROVIDER_KEY_MIN_BYTES:
            return (
                f"provider key {name}: got a value shorter than {PROVIDER_KEY_MIN_BYTES} bytes, "
                f"expected at least {PROVIDER_KEY_MIN_BYTES} bytes"
            )
        if value != value.strip():
            return (
                f"provider key {name}: got a value with leading or trailing whitespace, "
                "expected the key alone, with no leading or trailing whitespace"
            )
    return None
