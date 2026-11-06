"""The run spec: one YAML file under ``infra/bench-vm/runs/`` describing one bench VM run.

``RunSpec`` is strict: unknown keys, bad enum values and wrong scalar types are
refused, and the kind table's rules (``run_kinds.RUN_KIND_TABLE``) run as part
of validation, so a ``RunSpec`` that exists is launchable as far as the laptop
can tell without credentials. ``run_spec_file.load_run_spec`` turns a file into
a spec plus its run id, and every refusal into ``FieldError``s that name the
field, the offending value and the expected shape.

Every default lives once, as a ``Field(default=…)`` here or a module constant.
The machine type is the one computed default: it follows the GPU request, so
the field stays ``None`` and ``resolved_machine_type`` applies the constant.

Example (``infra/bench-vm/runs/cpu-smoke.yaml``)::

    git_ref: main
    kind: retrieval
    dataset: repoqa
    configs: [benchmarks/configs/baseline.yaml]
    args: [--fixture, benchmarks/tests/fixtures/repoqa_mini.json]
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    model_validator,
)

from pydocs_eval.bench_vm.run_kinds import (
    RUN_KIND_TABLE,
    BenchmarkInput,
    RunKind,
    RunKindRow,
    owned_flags_named_by,
)

# ADR 0026 Decision 1: one region, and ADR 0014's 8 vCPU / 32 GB floor.
BENCH_REGION = "europe-west4"
DEFAULT_CPU_MACHINE_TYPE = "n2-standard-8"
DEFAULT_GPU_MACHINE_TYPE = "g2-standard-8"
# Compute Engine's smallest persistent disk; the default is ADR 0014's 250 GB.
_MIN_DISK_GB = 10
# The most GPUs one G2 machine carries (g2-standard-96 has 8 L4s).
_MAX_GPU_COUNT = 8

_DURATION_SHAPE = re.compile(r"(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?")
_DURATION_EXPECTED = "a positive duration such as 6h, 90m or 1h30m (units d, h, m, s)"
_REPO_PATH_EXPECTED = (
    "a repository-relative POSIX path with no '..', such as benchmarks/configs/baseline.yaml"
)


class GpuType(StrEnum):
    """The accelerator a GPU run asks for, in Compute Engine's accelerator naming."""

    NVIDIA_L4 = "nvidia-l4"


@dataclass(frozen=True, slots=True)
class FieldError:
    """One refusal: the field, the value it had, and the shape it should have had."""

    field: str
    value: object
    expected: str

    def render(self) -> str:
        return f"{self.field}: got {self.value!r}, expected {self.expected}"


class RunSpecError(ValueError):
    """A run spec that cannot be launched, with every ``FieldError`` found in it."""

    def __init__(self, source: Path | str, errors: tuple[FieldError, ...]) -> None:
        self.source = source
        self.errors = errors
        super().__init__(f"{source}: " + "; ".join(error.render() for error in errors))


class KindRuleViolations(ValueError):
    """The kind-table rules a field-valid spec still breaks.

    Raised from the model validator so a ``RunSpec`` never exists in breach of
    its kind's row; pydantic keeps the exception on the error's ``ctx``, which
    is how the loader recovers the individual ``FieldError``s.
    """

    def __init__(self, errors: tuple[FieldError, ...]) -> None:
        self.errors = errors
        super().__init__("; ".join(error.render() for error in errors))


def _parse_duration(value: object) -> timedelta:
    """``6h`` / ``90m`` / ``1h30m`` / ``2d`` → timedelta; anything else is refused.

    A bare number is refused on purpose: ``max_runtime: 6`` is six what?
    """
    if isinstance(value, timedelta):
        return value
    match = _DURATION_SHAPE.fullmatch(value) if isinstance(value, str) else None
    if match is None or not any(match.groups()):
        raise ValueError(_DURATION_EXPECTED)
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    duration = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    if duration <= timedelta(0):
        raise ValueError(_DURATION_EXPECTED)
    return duration


def _check_repo_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value.strip() or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ValueError(_REPO_PATH_EXPECTED)
    return value


Duration = Annotated[timedelta, BeforeValidator(_parse_duration)]
NonEmptyStr = Annotated[StrictStr, Field(min_length=1, pattern=r"^\S+$")]
RepoRelativePath = Annotated[StrictStr, AfterValidator(_check_repo_relative_path)]
UsdCap = Annotated[StrictFloat, Field(gt=0)]

_STRICT_MODEL = ConfigDict(extra="forbid", frozen=True)


class GpuRequest(BaseModel):
    """A GPU type and count; ``gpu: {}`` asks for one NVIDIA L4."""

    model_config = _STRICT_MODEL

    type: GpuType = Field(default=GpuType.NVIDIA_L4)
    count: StrictInt = Field(default=1, ge=1, le=_MAX_GPU_COUNT)


class RunSpec(BaseModel):
    """One bench VM run: what it measures, on which machine, under which guards."""

    model_config = _STRICT_MODEL

    git_ref: NonEmptyStr
    kind: RunKind
    # Benchmark inputs; which a kind accepts or requires is its row's call.
    dataset: NonEmptyStr | None = None
    split: NonEmptyStr | None = None
    configs: tuple[RepoRelativePath, ...] = ()
    corpus_dir: RepoRelativePath | None = None
    baseline_ref: NonEmptyStr | None = None
    candidate_ref: NonEmptyStr | None = None
    args: tuple[StrictStr, ...] = ()
    # Machine.
    machine_type: Annotated[StrictStr, Field(pattern=r"^[a-z][a-z0-9-]*$")] | None = None
    gpu: GpuRequest | None = None
    spot: StrictBool = Field(default=True)
    zone: Annotated[StrictStr, Field(pattern=rf"^{BENCH_REGION}-[a-z]$")] | None = None
    disk_gb: StrictInt = Field(default=250, ge=_MIN_DISK_GB)
    # Guards.
    max_runtime: Duration = Field(default=timedelta(hours=6))
    self_delete: StrictBool = Field(default=True)
    confirm_spend: StrictBool = Field(default=False)
    max_usd: UsdCap | None = None
    sync_interval: Duration = Field(default=timedelta(minutes=5))

    @property
    def row(self) -> RunKindRow:
        return RUN_KIND_TABLE[self.kind]

    @property
    def resolved_machine_type(self) -> str:
        if self.machine_type is not None:
            return self.machine_type
        return DEFAULT_CPU_MACHINE_TYPE if self.gpu is None else DEFAULT_GPU_MACHINE_TYPE

    @model_validator(mode="after")
    def _apply_kind_rules(self) -> RunSpec:
        errors = (
            *_input_errors(self),
            *_owned_flag_errors(self),
            *_gpu_errors(self),
            *_spend_errors(self),
            *_interval_errors(self),
        )
        if errors:
            raise KindRuleViolations(errors)
        return self


def _input_errors(spec: RunSpec) -> tuple[FieldError, ...]:
    row = spec.row
    accepted = ", ".join(sorted(row.accepted_inputs)) or "none"
    errors = [
        FieldError(
            name, getattr(spec, name), f"no {name} for kind {spec.kind}, which takes {accepted}"
        )
        for name in sorted(BenchmarkInput)
        if name in spec.model_fields_set and name not in row.accepted_inputs
    ]
    errors += [
        FieldError(name, getattr(spec, name), f"a value: kind {spec.kind} requires {name}")
        for name in sorted(row.required_inputs)
        if not getattr(spec, name)
    ]
    count = row.exact_config_count
    if count is not None and spec.configs and len(spec.configs) != count:
        errors.append(FieldError("configs", spec.configs, f"exactly {count} for kind {spec.kind}"))
    return tuple(errors)


def _owned_flag_errors(spec: RunSpec) -> tuple[FieldError, ...]:
    owned = ", ".join(sorted(spec.row.owned_flags))
    return tuple(
        FieldError(
            "args",
            arg,
            f"a flag kind {spec.kind} does not own; the bench VM sets {owned} itself "
            "from the spec's typed fields and the run's directories",
        )
        for arg in owned_flags_named_by(spec.args, spec.row)
    )


def _gpu_errors(spec: RunSpec) -> tuple[FieldError, ...]:
    if spec.gpu is None or spec.row.may_use_gpu:
        return ()
    gpu = spec.gpu.model_dump(mode="json")
    return (FieldError("gpu", gpu, f"no gpu: kind {spec.kind} runs on CPU only"),)


def _spend_errors(spec: RunSpec) -> tuple[FieldError, ...]:
    if not spec.row.paid:
        return tuple(
            FieldError(
                name, getattr(spec, name), f"no {name}: kind {spec.kind} spends no model tokens"
            )
            for name in ("confirm_spend", "max_usd")
            if name in spec.model_fields_set
        )
    errors = []
    if spec.confirm_spend is not True:
        errors.append(
            FieldError(
                "confirm_spend", spec.confirm_spend, f"true: kind {spec.kind} spends model tokens"
            )
        )
    if spec.max_usd is None:
        errors.append(FieldError("max_usd", None, f"a positive USD cap: kind {spec.kind} is paid"))
    return tuple(errors)


def _interval_errors(spec: RunSpec) -> tuple[FieldError, ...]:
    if spec.sync_interval < spec.max_runtime:
        return ()
    expected = f"shorter than max_runtime ({spec.max_runtime}), or no sync would ever run"
    return (FieldError("sync_interval", str(spec.sync_interval), expected),)
