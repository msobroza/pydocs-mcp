"""Bench VM: ephemeral GCP benchmark runs described by versioned run specs (ADR 0026).

This package holds the laptop side that needs no credentials: the run-spec
model (``run_spec``), the kind table (``run_kinds``), run-id derivation
(``run_id``), the file loader (``run_spec_file``) and the ``check`` verb
(``python -m pydocs_eval.bench_vm check infra/bench-vm/runs/*.yaml``).

It stays on the eval base-install floor: pydantic and PyYAML only, and nothing
from ``pydocs_mcp`` — the launcher runs from a laptop checkout that may be
newer than the measured product commit.
"""

from pydocs_eval.bench_vm.run_id import derive_run_id
from pydocs_eval.bench_vm.run_kinds import RUN_KIND_TABLE, RunKind, RunKindRow
from pydocs_eval.bench_vm.run_spec import FieldError, GpuRequest, GpuType, RunSpec, RunSpecError
from pydocs_eval.bench_vm.run_spec_file import RunSpecFile, load_run_spec

__all__ = [
    "RUN_KIND_TABLE",
    "FieldError",
    "GpuRequest",
    "GpuType",
    "RunKind",
    "RunKindRow",
    "RunSpec",
    "RunSpecError",
    "RunSpecFile",
    "derive_run_id",
    "load_run_spec",
]
