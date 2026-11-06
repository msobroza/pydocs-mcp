"""A retrieval leg's identity: what a recorded task's scores depend on (issue #420).

    fingerprint = sha256(canonical_json(components))

The components are everything that changes a recorded task's scores — the
system and config name, the flattened config parameters (embedding device
included), the dataset name, revision and arguments, the metric specs, and
the product commit. A run-id sweep stamps them into the leg's ``run_start``
header; reopening the leg under a different fingerprint fails before any
task runs, naming the first component that differs. The task-id filter and
``--limit`` choose WHICH tasks run, never what a task scores, so they stay
out.

Same idiom as ``arm_identity``: the one eval-wide canonicalizer, imported
function-locally so this module stays a leaf.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

# The component order is the order a mismatch is diagnosed in: the first key
# here whose values differ is the one the error names.
COMPONENT_NAMES: tuple[str, ...] = (
    "system",
    "config_name",
    "config_params",
    "dataset",
    "dataset_revision",
    "dataset_args",
    "metric_specs",
    "commit",
)


@dataclass(frozen=True, slots=True)
class LegIdentity:
    """A leg's identity components (plain JSON values) and their fingerprint."""

    components: Mapping[str, object]
    fingerprint: str


class LegIdentityMismatch(ValueError):
    """A run-id leg was reopened under a different identity than it was recorded with."""


def build_leg_identity(
    *,
    system: str,
    config_name: str,
    config_params: Mapping[str, str],
    dataset: str,
    dataset_revision: str,
    dataset_args: Mapping[str, object],
    metric_specs: Sequence[str],
    commit: str,
) -> LegIdentity:
    """Assemble the components as plain JSON values and fingerprint them."""
    components: dict[str, object] = {
        "system": system,
        "config_name": config_name,
        "config_params": dict(config_params),
        "dataset": dataset,
        "dataset_revision": dataset_revision,
        "dataset_args": {str(k): _json_value(v) for k, v in dataset_args.items()},
        "metric_specs": list(metric_specs),
        "commit": commit,
    }
    return LegIdentity(components=components, fingerprint=identity_fingerprint(components))


def identity_fingerprint(components: Mapping[str, object]) -> str:
    """Return the 64-char sha256 of the components' canonical JSON."""
    from pydocs_eval.trajectory.blob_store import canonical_json

    return hashlib.sha256(canonical_json(components).encode("utf-8")).hexdigest()


def check_leg_identity(
    *,
    run_id: str,
    leg: str,
    recorded: LegIdentity,
    current: LegIdentity,
) -> None:
    """Raise ``LegIdentityMismatch`` unless the two fingerprints are equal."""
    if recorded.fingerprint == current.fingerprint:
        return
    detail = _first_difference(recorded.components, current.components)
    raise LegIdentityMismatch(
        f"run {run_id!r}, leg {leg!r}: recorded under identity {recorded.fingerprint} "
        f"but this invocation's identity is {current.fingerprint}; first differing "
        f"component: {detail}. Resume with the recorded settings, or start a new "
        "--run-id for the changed experiment."
    )


def _first_difference(recorded: Mapping[str, object], current: Mapping[str, object]) -> str:
    extra = (set(recorded) | set(current)) - set(COMPONENT_NAMES)
    names = [*COMPONENT_NAMES, *sorted(extra)]
    for name in names:
        before, after = recorded.get(name), current.get(name)
        if before == after:
            continue
        if isinstance(before, Mapping) and isinstance(after, Mapping):
            return f"{name!r} ({_first_key_difference(before, after)})"
        return f"{name!r} (recorded {before!r}, now {after!r})"
    return "none found — the recorded fingerprint does not match its own components"


def _first_key_difference(before: Mapping[str, object], after: Mapping[str, object]) -> str:
    # WHY one key, not the whole mapping: config_params flattens the entire
    # AppConfig — printing hundreds of keys would bury the one that moved.
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            return f"key {key!r}: recorded {before.get(key)!r}, now {after.get(key)!r}"
    return "no key differs"


def _json_value(value: object) -> object:
    """Map a dataset kwarg onto the value its JSON round trip yields."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple | list):
        return [_json_value(v) for v in value]
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in value.items()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return str(value)


__all__ = (
    "COMPONENT_NAMES",
    "LegIdentity",
    "LegIdentityMismatch",
    "build_leg_identity",
    "check_leg_identity",
    "identity_fingerprint",
)
