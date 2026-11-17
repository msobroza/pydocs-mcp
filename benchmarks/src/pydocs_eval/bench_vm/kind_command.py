"""The kind's command line for one run: its CLI on the image's interpreter, and its owned-flag inputs.

The supervisor's child and the kind's pre-flight share both: the same
interpreter in place of the kind table's leading ``python``, and the same
``KindInvocation`` (the spec's typed inputs, the record's frozen commits, the
container's directories) for the owned flags. The local reference run
(``local_reference``) builds its command from the same two, with local
directories and no run id.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydocs_eval.bench_vm.container_paths import RunDirectories
from pydocs_eval.bench_vm.kind_flags import KindInvocation
from pydocs_eval.bench_vm.run_spec import RunSpec
from pydocs_eval.bench_vm.supervised_run import SupervisedRun

# The image's interpreter; the kind table's CLIs start with it.
KIND_CLI_INTERPRETER = "python"


def with_interpreter(cli: Sequence[str], python: Sequence[str]) -> tuple[str, ...]:
    """``cli`` with its leading ``python`` replaced by ``python`` (the image's, or a test's)."""
    if cli and cli[0] == KIND_CLI_INTERPRETER:
        return (*python, *cli[1:])
    return tuple(cli)


def kind_invocation(run: SupervisedRun, dirs: RunDirectories) -> KindInvocation:
    """What this run's owned flags are filled from."""
    record = run.record
    return spec_invocation(
        run.spec,
        dirs,
        run_id=record.run_id,
        baseline_commit=record.baseline_commit,
        candidate_commit=record.candidate_commit,
    )


def spec_invocation(
    spec: RunSpec,
    dirs: RunDirectories,
    *,
    run_id: str | None,
    baseline_commit: str | None = None,
    candidate_commit: str | None = None,
) -> KindInvocation:
    """The owned-flag inputs of ``spec``'s run in ``dirs``, under ``run_id`` and the given commits."""
    return KindInvocation(
        run_id=run_id,
        dataset=spec.dataset,
        split=spec.split,
        configs=spec.configs,
        corpus_dir=spec.corpus_dir,
        baseline_commit=baseline_commit,
        candidate_commit=candidate_commit,
        max_usd=spec.max_usd,
        gpu=spec.gpu is not None,
        state_dir=dirs.state_dir,
        cache_dir=dirs.cache_dir,
    )


def kind_argv(spec: RunSpec, invocation: KindInvocation, python: Sequence[str]) -> tuple[str, ...]:
    """The kind's CLI on ``python``, its owned flags for ``invocation``, then the spec's ``args``."""
    owned = spec.row.owned_flag_values(invocation)
    return (*with_interpreter(spec.row.cli, python), *owned, *spec.args)
