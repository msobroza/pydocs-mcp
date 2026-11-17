"""The local reference run: the runner command a bench VM runs for a retrieval spec, on a local disk.

The stop-and-resume acceptance compares a run that was stopped and relaunched
on bench VMs with an uninterrupted run of the same spec on the owner's
machine (``leg_comparison``). The local command is the supervisor's child argv
for the spec (``kind_command.kind_argv``), with two differences only:

- its state directory is ``<results dir>/<run id>/`` on the local disk, so the
  runner writes its leg files under ``<run id>/results/`` and its report to
  ``<run id>/report.md``, the layout of the VM's state directory;
- it carries no ``--run-id``: the reference is the plain runner, one
  timestamped file per leg, outside the resume path the comparison checks.

Only the retrieval kind has a reference: its leg files are what ``compare``
reads, and the paid kinds would spend provider money.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.bench_vm.container_paths import RunDirectories
from pydocs_eval.bench_vm.kind_command import KIND_CLI_INTERPRETER, kind_argv, spec_invocation
from pydocs_eval.bench_vm.run_kinds import RunKind
from pydocs_eval.bench_vm.run_spec_file import RunSpecFile

# Under ``benchmarks/results/``, which git ignores, beside ``bench-vm/`` where
# ``pull`` lands the VM's side.
LOCAL_REFERENCE_RESULTS_DIR = Path("benchmarks/results/bench-vm-local")


class LocalReferenceRefused(ValueError):
    """A spec whose kind has no local reference run."""


def local_reference_dir(results_dir: Path, run_id: str) -> Path:
    """The local reference's state directory: ``<results dir>/<run id>/``."""
    return results_dir / run_id


def local_reference_argv(spec_file: RunSpecFile, results_dir: Path) -> tuple[str, ...]:
    """The runner argv for an uninterrupted local run of ``spec_file``, under ``results_dir``."""
    spec = spec_file.spec
    if spec.kind is not RunKind.RETRIEVAL:
        raise LocalReferenceRefused(
            f"got kind {spec.kind}, expected kind {RunKind.RETRIEVAL}: only the retrieval "
            "runner's leg files have a local reference to compare with"
        )
    state_dir = local_reference_dir(results_dir, spec_file.run_id)
    dirs = RunDirectories(state_dir=state_dir, cache_dir=state_dir / "cache")
    invocation = spec_invocation(spec, dirs, run_id=None)
    return kind_argv(spec, invocation, (KIND_CLI_INTERPRETER,))
