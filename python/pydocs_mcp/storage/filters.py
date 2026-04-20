"""Filter tree + MultiFieldFormat + MetadataSchema + format_registry (spec §5.1)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, Union, runtime_checkable


# ── Filter tree ─────────────────────────────────────────────────────────

Filter = Union["FieldEq", "FieldIn", "FieldLike", "All", "Any_", "Not"]


@dataclass(frozen=True, slots=True)
class FieldEq:
    field: str
    value: Any


@dataclass(frozen=True, slots=True)
class FieldIn:
    field: str
    values: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class FieldLike:
    field: str
    substring: str


@dataclass(frozen=True, slots=True)
class All:
    clauses: tuple[Filter, ...]


@dataclass(frozen=True, slots=True)
class Any_:
    """Declared for future FilterTreeFormat; unused in MultiFieldFormat."""
    clauses: tuple[Filter, ...]


@dataclass(frozen=True, slots=True)
class Not:
    """Declared for future FilterTreeFormat; unused in MultiFieldFormat."""
    clause: Filter


# ── Format enum ─────────────────────────────────────────────────────────


class MetadataFilterFormat(StrEnum):
    MULTIFIELD = "multifield"
    FILTER_TREE = "filter_tree"
    CHROMADB = "chromadb"
    ELASTICSEARCH = "elasticsearch"
    QDRANT = "qdrant"


@runtime_checkable
class FilterFormat(Protocol):
    format: MetadataFilterFormat

    def validate(self, native: Any) -> None: ...
    def parse(self, native: Any) -> Filter: ...


# ── MultiFieldFormat ────────────────────────────────────────────────────


_VALID_OPS = frozenset({"eq", "in", "like"})


@dataclass(frozen=True, slots=True)
class MultiFieldFormat:
    format: MetadataFilterFormat = MetadataFilterFormat.MULTIFIELD

    def validate(self, native: Any) -> None:
        if not isinstance(native, Mapping):
            raise ValueError(f"MultiFieldFormat expects a mapping; got {type(native).__name__}")
        for key, value in native.items():
            if key in ("$and", "$or", "$not"):
                raise ValueError(
                    f"MultiFieldFormat does not support boolean operator {key!r}. "
                    f"Use the filter_tree format instead."
                )
            if isinstance(value, Mapping):
                for op in value:
                    if op not in _VALID_OPS:
                        raise ValueError(
                            f"unknown operator {op!r} for field {key!r}; "
                            f"known: {sorted(_VALID_OPS)}"
                        )

    def parse(self, native: Any) -> Filter:
        self.validate(native)
        clauses: list[Filter] = []
        for field, value in native.items():
            if isinstance(value, Mapping):
                op, op_val = next(iter(value.items()))
                if op == "eq":
                    clauses.append(FieldEq(field=field, value=op_val))
                elif op == "in":
                    clauses.append(FieldIn(field=field, values=tuple(op_val)))
                elif op == "like":
                    clauses.append(FieldLike(field=field, substring=str(op_val)))
            else:
                clauses.append(FieldEq(field=field, value=value))
        return All(clauses=tuple(clauses))


# ── MetadataSchema ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    operators: frozenset[str] = frozenset({"eq"})


@dataclass(frozen=True, slots=True)
class MetadataSchema:
    fields: tuple[FieldSpec, ...]

    def field_names(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields)

    def validate(self, filter: Filter) -> None:
        unknown = _walk_fields(filter) - self.field_names()
        if unknown:
            raise ValueError(
                f"filter references unknown fields {sorted(unknown)}; "
                f"schema allows {sorted(self.field_names())}"
            )


def _walk_fields(filter: Filter) -> frozenset[str]:
    if isinstance(filter, FieldEq | FieldIn | FieldLike):
        return frozenset({filter.field})
    if isinstance(filter, All | Any_):
        out: set[str] = set()
        for c in filter.clauses:
            out |= _walk_fields(c)
        return frozenset(out)
    if isinstance(filter, Not):
        return _walk_fields(filter.clause)
    return frozenset()


# ── format_registry ─────────────────────────────────────────────────────


format_registry: dict[MetadataFilterFormat, FilterFormat] = {
    MetadataFilterFormat.MULTIFIELD: MultiFieldFormat(),
}
