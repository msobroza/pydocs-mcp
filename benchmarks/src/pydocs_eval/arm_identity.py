"""Arm identity — the one hash formula both evaluation tracks share (design §6).

    arm_hash = sha256(canonical_json({
        "cell":                 <the arm cell, canonicalized>,
        "guidance_fingerprint": <the candidate artifact's fingerprint>,
        "delivery_map":         <the harness's section→channel map hash>,
    }))

Three components, each of which MOVES the hash: what the arm runs, what
guidance it carries, and HOW that guidance reaches the agent (two arms
delivering the same text through different channels are different arms —
run-contract design §4's delivery-maps-are-experiment-state rule).

BASE install by contract: stdlib + the eval-local ``canonical_json`` only, no
``pydocs_mcp`` import (ADR 0009's 2026-07-27 floor), because the external
track's console script consumes this module. Product-coupled callers compute
their delivery-map hash on their own side and pass it in as an opaque string.

The ``cell`` argument is a plain ``Mapping`` rather than one config class:
the optimize run config spells a cell as ``ArmCell`` (the normative six-key
set) while the external CLI's cell is its own arm knobs — one formula, two
spellings, no shared type across the base-install floor.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

# WHY a named sentinel and not "": a run that delivers no candidate guidance
# (the bare CLI track) must say so explicitly in the hash, so its rows can
# never be confused with a run whose guidance happened to render empty.
NO_GUIDANCE_FINGERPRINT = "no-guidance"


def _canonical_json(payload: Mapping[str, object]) -> str:
    """The eval-wide canonicalizer, imported function-locally to break a cycle.

    ``trajectory``'s package init reaches ``agent_track``, which imports the
    external track's arm identity, which lands back here — the registries
    module's function-local-import precedent applies: importing at call time
    keeps this module a leaf while still reusing the ONE canonicalizer instead
    of minting a second.
    """
    from pydocs_eval.trajectory.blob_store import canonical_json

    return canonical_json(payload)


def arm_fingerprint(
    *,
    cell: Mapping[str, object],
    guidance_fingerprint: str,
    delivery_map_hash: str,
) -> str:
    """Return the 64-char arm hash for one (cell, guidance, delivery) triple.

    Deterministic across processes and insertion orders: ``canonical_json``
    sorts every key, so two equal cells always hash equal.

    Example:
        >>> arm_fingerprint(
        ...     cell={"task_name": "ccv"},
        ...     guidance_fingerprint="abc",
        ...     delivery_map_hash="def",
        ... )[:8]
        'd23bd694'
    """
    payload: dict[str, object] = {
        "cell": dict(cell),
        "guidance_fingerprint": guidance_fingerprint,
        "delivery_map": delivery_map_hash,
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def delivery_map_hash(delivery_map: Mapping[str, str]) -> str:
    """Hash a harness's static section→channel delivery map.

    The eval-side counterpart of the product's own ``delivery_map_digest``:
    a harness whose delivery map lives in the eval package (the external CLI
    track appends guidance to the task prompt) hashes it here instead. The two
    canonicalizations differ in one inert flag (``ensure_ascii``); both are
    exact-match keys and never mix inside one hash, so no reconciliation is
    owed — only the note that they are separate digests of separate maps.
    """
    return hashlib.sha256(_canonical_json(dict(delivery_map)).encode("utf-8")).hexdigest()
