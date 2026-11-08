"""Load a run-spec file into a ``RunSpec`` and its run id, or name everything wrong with it.

Reading is offline and credential-free: YAML in, pydantic validation, the kind
table's rules, the stem rule. Every refusal becomes a ``FieldError``, and a
``RunSpecError`` carries all of them at once, so one ``check`` shows the owner
every problem in a spec instead of the first.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from pydocs_eval.bench_vm.run_id import derive_run_id, run_spec_stem_errors
from pydocs_eval.bench_vm.run_spec import (
    FieldError,
    GpuRequest,
    KindRuleViolations,
    RunSpec,
    RunSpecError,
)

_REQUIRED_EXPECTED = "a value: the field is required"
# pydantic phrases a shape as "Input should be <shape>"; the FieldError
# rendering already says "expected", so keep only the shape.
_PYDANTIC_MESSAGE_PREFIXES = ("Value error, ", "Input should be ", "String should ")
_YAML_EXPECTED = "valid YAML, each key at most once per mapping"


@dataclass(frozen=True, slots=True)
class RunSpecFile:
    """A validated run spec, the file it came from, and its run id."""

    path: Path
    spec: RunSpec
    run_id: str


def load_run_spec(path: Path) -> RunSpecFile:
    """Read, validate and identify the run spec at ``path``.

    Raises ``RunSpecError`` naming every problem found: the stem's, then the
    file's (unreadable, not YAML, not a mapping) or every field's.
    """
    stem_errors = run_spec_stem_errors(path.stem)
    try:
        spec = RunSpec.model_validate(_read_spec_mapping(path))
    except _UnreadableRunSpec as exc:
        raise RunSpecError(path, (*stem_errors, exc.error)) from None
    except ValidationError as exc:
        raise RunSpecError(path, stem_errors + field_errors_from(exc)) from None
    if stem_errors:
        raise RunSpecError(path, stem_errors)
    return RunSpecFile(path=path, spec=spec, run_id=derive_run_id(path.stem, spec))


def field_errors_from(exc: ValidationError) -> tuple[FieldError, ...]:
    """One ``FieldError`` per pydantic error, kind-rule violations unpacked."""
    errors: list[FieldError] = []
    for error in exc.errors():
        violations = error.get("ctx", {}).get("error")
        if isinstance(violations, KindRuleViolations):
            errors.extend(violations.errors)
        else:
            errors.append(_field_error_from(error))
    return tuple(errors)


def _field_error_from(error: ErrorDetails) -> FieldError:
    loc = tuple(error["loc"])
    field = ".".join(str(part) for part in loc)
    if error["type"] == "missing":
        return FieldError(field, None, _REQUIRED_EXPECTED)
    if error["type"] == "extra_forbidden":
        allowed = ", ".join(_model_at(loc).model_fields)
        return FieldError(field, error["input"], f"a known field; this level takes {allowed}")
    return FieldError(field, error["input"], _shape_from(error["msg"]))


def _model_at(loc: tuple[object, ...]) -> type[BaseModel]:
    return GpuRequest if len(loc) > 1 and loc[0] == "gpu" else RunSpec


def _shape_from(message: str) -> str:
    for prefix in _PYDANTIC_MESSAGE_PREFIXES:
        message = message.removeprefix(prefix)
    return message


class _UnreadableRunSpec(Exception):
    """The file never got as far as field validation; ``error`` says why."""

    def __init__(self, error: FieldError) -> None:
        super().__init__(error.render())
        self.error = error


def _read_spec_mapping(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _UnreadableRunSpec(
            FieldError("(file)", str(path), f"a readable file ({exc})")
        ) from None
    try:
        data = yaml.load(text, Loader=_UniqueKeySafeLoader)  # noqa: S506 — a SafeLoader subclass
    except yaml.YAMLError as exc:
        error = FieldError("(yaml)", _yaml_problem(exc), _YAML_EXPECTED)
        raise _UnreadableRunSpec(error) from None
    if not isinstance(data, dict):
        raise _UnreadableRunSpec(FieldError("(top level)", data, "a mapping of run-spec fields"))
    return data


def _yaml_problem(exc: yaml.YAMLError) -> str:
    mark = getattr(exc, "problem_mark", None)
    problem = getattr(exc, "problem", None) or str(exc)
    if mark is None:
        return problem
    return f"{problem} at line {mark.line + 1}, column {mark.column + 1}"


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """``yaml.SafeLoader`` that refuses a key repeated in one mapping.

    PyYAML keeps the last of two equal keys silently, so a pasted ``spot: false``
    under an older ``spot: true`` would launch something the owner did not read.
    """


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    seen: list[object] = []
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                problem=f"duplicate key {key!r}", problem_mark=key_node.start_mark
            )
        seen.append(key)
    # WHY the annotated local, not a `type: ignore` (#422): without
    # types-PyYAML the call is Any (warn_return_any), with it a dict, and an
    # ignore would be unused there (warn_unused_ignores).
    mapping: dict[Any, Any] = loader.construct_mapping(node, deep=True)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)
