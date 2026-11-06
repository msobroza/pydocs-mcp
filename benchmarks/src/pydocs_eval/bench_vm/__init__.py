"""Bench VM: ephemeral GCP benchmark runs described by versioned run specs (ADR 0026).

On the laptop, without credentials: the run-spec model
(``run_spec``), the kind table (``run_kinds``), run-id derivation
(``run_id``), the file loader (``run_spec_file``) and the ``check`` verb
(``python -m pydocs_eval.bench_vm check infra/bench-vm/runs/*.yaml``). With
the owner's ``gcloud auth login`` session: the ``launch`` verb
(``make bench-vm RUN=runs/<name>.yaml``, ``launcher``), which writes the run
record (``run_record``) and creates the VM through the one cloud port shared
with the supervisor (``bench_cloud.BenchCloud``, over ``gcloud`` in
``gcloud_bench_cloud``). ``launch --dry-run`` needs no credentials either. The same session runs the
run-control verbs (``run_control``): ``status`` (``make bench-vm-status``),
``stop`` (``make bench-vm-stop``) and ``pull`` (``make bench-vm-pull``).

On the VM, the ``supervise`` verb (``supervisor``) is the run container's
entrypoint, started by the host startup script
(``infra/bench-vm/host/startup.sh``): it runs the kind's CLI as a child, syncs
the run's state to GCS, records how the run ended and deletes its own VM,
through the same cloud port as the VM's service account.

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
