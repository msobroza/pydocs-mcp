"""Run ids: ``<spec file stem>-<first 8 hex of sha256(canonical JSON of the set fields)>``.

Only the fields the file sets explicitly enter the hash (pydantic's
``exclude_unset``), so a tooling upgrade that changes a code default between a
preemption and its relaunch still finds the same run, while any edit to a value
the file states makes a new one. The values are hashed after validation, so
comments, key order, whitespace and equal spellings (``90m`` / ``1h30m``) do
not move the id. The canonical JSON is the eval-wide ``canonical_json`` that
``arm_identity`` hashes too.

The stem bound comes from Compute Engine's 63-character name rule. Of the
three names built from a run id — the id itself (also a VM label value), its
bucket prefix and the VM name ``bench-<run-id>-<attempt>`` — the VM name is the
longest, so bounding it bounds the other two (bucket object names allow 1024).
"""

from __future__ import annotations

import hashlib
import re

from pydocs_eval.bench_vm.run_spec import FieldError, RunSpec, RunSpecError

RUN_ID_HASH_HEX_CHARS = 8
GCE_NAME_MAX_CHARS = 63
VM_NAME_PREFIX = "bench-"
# Attempts are numbered from 1; three digits keep the VM name's tail fixed-width
# in the bound below, and a run relaunched 999 times has a different problem.
MAX_ATTEMPT = 999
MAX_STEM_CHARS = (
    GCE_NAME_MAX_CHARS
    - len(VM_NAME_PREFIX)
    - len("-") - RUN_ID_HASH_HEX_CHARS
    - len("-") - len(str(MAX_ATTEMPT))
)  # fmt: skip

_STEM_SHAPE = re.compile(r"[a-z0-9-]+")


def run_spec_stem_errors(stem: str) -> tuple[FieldError, ...]:
    """Why ``stem`` cannot name a run, or ``()`` when it can."""
    errors = []
    if not _STEM_SHAPE.fullmatch(stem):
        errors.append(FieldError("file stem", stem, "lowercase letters, digits and hyphens only"))
    if len(stem) > MAX_STEM_CHARS:
        expected = (
            f"at most {MAX_STEM_CHARS} characters (got {len(stem)}), so the VM name "
            f"{VM_NAME_PREFIX}<run-id>-<attempt> fits Compute Engine's {GCE_NAME_MAX_CHARS}"
        )
        errors.append(FieldError("file stem", stem, expected))
    return tuple(errors)


def run_spec_fingerprint(spec: RunSpec) -> str:
    """sha256 hex of the canonical JSON of the fields the spec set explicitly."""
    # Function-local for the arm_identity reason: the trajectory package init
    # reaches agent_track, so a module-level import would load it on every
    # import of bench_vm; at call time it reuses the ONE canonicalizer.
    from pydocs_eval.trajectory.blob_store import canonical_json

    explicit = spec.model_dump(mode="json", exclude_unset=True)
    return hashlib.sha256(canonical_json(explicit).encode("utf-8")).hexdigest()


def derive_run_id(stem: str, spec: RunSpec) -> str:
    """The run id of the spec file ``<stem>.yaml`` holding ``spec``.

    Example:
        >>> spec = RunSpec.model_validate({"git_ref": "main", "kind": "retrieval", "configs": ["c.yaml"]})
        >>> derive_run_id("cpu-smoke", spec)[: len("cpu-smoke-")]
        'cpu-smoke-'
    """
    errors = run_spec_stem_errors(stem)
    if errors:
        raise RunSpecError(stem, errors)
    return f"{stem}-{run_spec_fingerprint(spec)[:RUN_ID_HASH_HEX_CHARS]}"


def bench_vm_instance_name(run_id: str, attempt: int) -> str:
    """The Compute Engine name of attempt ``attempt`` of run ``run_id``."""
    if not 1 <= attempt <= MAX_ATTEMPT:
        raise ValueError(
            f"invalid attempt: got {attempt!r}, expected an integer in 1..{MAX_ATTEMPT}"
        )
    name = f"{VM_NAME_PREFIX}{run_id}-{attempt}"
    if len(name) > GCE_NAME_MAX_CHARS:
        raise ValueError(
            f"invalid run id: got {run_id!r}, expected one whose VM name {name!r} "
            f"fits {GCE_NAME_MAX_CHARS} characters"
        )
    return name
