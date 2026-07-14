"""step_to_yaml_dict / yaml_kwargs — generic omit-when-default YAML codec."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar

import pytest
import yaml

from pydocs_mcp.retrieval.serialization import step_to_yaml_dict, yaml_kwargs

_DEFAULT_KNOB = 7
_DEFAULT_TAGS: tuple[str, ...] = ("a", "b")


@dataclass(frozen=True, slots=True)
class _Widget:
    knob: int = _DEFAULT_KNOB
    tags: tuple[str, ...] = _DEFAULT_TAGS
    label: str = "w"
    _YAML_KEYS: ClassVar[tuple[str, ...]] = ("knob", "tags", "label")


@dataclass(frozen=True, slots=True)
class _NoDefault:
    dep: object
    knob: int = _DEFAULT_KNOB


def test_to_dict_omits_defaults() -> None:
    out = step_to_yaml_dict(_Widget(), type_name="widget", keys=_Widget._YAML_KEYS)
    assert out == {"type": "widget"}


def test_to_dict_emits_non_defaults_in_key_order_tuples_as_lists() -> None:
    w = _Widget(knob=9, tags=("x",), label="z")
    out = step_to_yaml_dict(w, type_name="widget", keys=_Widget._YAML_KEYS)
    assert out == {"type": "widget", "knob": 9, "tags": ["x"], "label": "z"}
    # YAML byte-parity depends on dict insertion order following _YAML_KEYS.
    assert list(out) == ["type", "knob", "tags", "label"]


def test_to_dict_rejects_keys_without_a_dataclass_default() -> None:
    with pytest.raises(ValueError, match="dep"):
        step_to_yaml_dict(_NoDefault(dep=object()), type_name="x", keys=("dep",))


def test_yaml_kwargs_falls_back_to_field_defaults() -> None:
    assert yaml_kwargs({}, _Widget, _Widget._YAML_KEYS) == {
        "knob": _DEFAULT_KNOB,
        "tags": _DEFAULT_TAGS,
        "label": "w",
    }


def test_yaml_kwargs_coerces_yaml_lists_to_tuples_by_default_type() -> None:
    kwargs = yaml_kwargs({"tags": ["x", "y"]}, _Widget, _Widget._YAML_KEYS)
    assert kwargs["tags"] == ("x", "y")
    assert isinstance(kwargs["tags"], tuple)


def test_yaml_kwargs_rejects_keys_without_a_dataclass_default() -> None:
    with pytest.raises(ValueError, match="dep"):
        yaml_kwargs({}, _NoDefault, ("dep",))


def test_round_trip_is_stable() -> None:
    w = _Widget(knob=9, tags=("x",))
    data = step_to_yaml_dict(w, type_name="widget", keys=_Widget._YAML_KEYS)
    rebuilt = _Widget(**yaml_kwargs(data, _Widget, _Widget._YAML_KEYS))
    assert rebuilt == w


_DEFAULT_TABLE = {"class": 0.3}


@dataclass(frozen=True, slots=True)
class _MappedWidget:
    table: Mapping[str, float] = field(default_factory=lambda: dict(_DEFAULT_TABLE))
    knob: int = _DEFAULT_KNOB
    _YAML_KEYS: ClassVar[tuple[str, ...]] = ("table", "knob")


def test_to_dict_resolves_default_factory_and_omits_default_mapping() -> None:
    out = step_to_yaml_dict(_MappedWidget(), type_name="mw", keys=_MappedWidget._YAML_KEYS)
    assert out == {"type": "mw"}


def test_to_dict_emits_non_default_mapping_as_plain_dict() -> None:
    w = _MappedWidget(table=MappingProxyType({"class": 0.2}))
    out = step_to_yaml_dict(w, type_name="mw", keys=_MappedWidget._YAML_KEYS)
    assert out == {"type": "mw", "table": {"class": 0.2}}
    assert type(out["table"]) is dict
    # A raw mappingproxy in the output would raise yaml RepresenterError.
    yaml.safe_dump(out)


def test_yaml_kwargs_resolves_default_factory() -> None:
    kwargs = yaml_kwargs({}, _MappedWidget, _MappedWidget._YAML_KEYS)
    assert kwargs == {"table": {"class": 0.3}, "knob": _DEFAULT_KNOB}


def test_yaml_kwargs_passes_yaml_mapping_through_untouched() -> None:
    kwargs = yaml_kwargs({"table": {"module": 0.6}}, _MappedWidget, _MappedWidget._YAML_KEYS)
    assert kwargs["table"] == {"module": 0.6}


def test_helpers_still_reject_keys_with_neither_default_nor_factory() -> None:
    with pytest.raises(ValueError, match="dep"):
        step_to_yaml_dict(_NoDefault(dep=object()), type_name="x", keys=("dep",))
    with pytest.raises(ValueError, match="dep"):
        yaml_kwargs({}, _NoDefault, ("dep",))
