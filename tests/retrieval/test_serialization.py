"""Tests for ComponentRegistry + BuildContext."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    ComponentRegistry,
    formatter_registry,
    retriever_registry,
    stage_registry,
)


def _ctx(tmp_path: Path) -> BuildContext:
    return BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )


def test_register_and_build(tmp_path):
    registry: ComponentRegistry = ComponentRegistry()

    @registry.register("echo")
    @dataclass(frozen=True, slots=True)
    class Echo:
        msg: str
        name: str = "echo"

        def to_dict(self): return {"type": "echo", "msg": self.msg}

        @classmethod
        def from_dict(cls, d, ctx): return cls(msg=d["msg"])

    assert registry.names() == ("echo",)
    instance = registry.build({"type": "echo", "msg": "hi"}, _ctx(tmp_path))
    assert isinstance(instance, Echo)
    assert instance.msg == "hi"


def test_collision_raises():
    registry: ComponentRegistry = ComponentRegistry()

    @registry.register("dup")
    @dataclass(frozen=True, slots=True)
    class A:
        name: str = "a"

    with pytest.raises(ValueError, match="already registered"):
        @registry.register("dup")
        @dataclass(frozen=True, slots=True)
        class B:
            name: str = "b"


def test_unknown_type_raises_listing_known(tmp_path):
    registry: ComponentRegistry = ComponentRegistry()

    @registry.register("known")
    @dataclass(frozen=True, slots=True)
    class K:
        name: str = "k"
        def to_dict(self): return {"type": "known"}
        @classmethod
        def from_dict(cls, d, ctx): return cls()

    with pytest.raises(KeyError, match="unknown component type"):
        registry.build({"type": "missing"}, _ctx(tmp_path))


def test_shared_registries_exist():
    assert isinstance(stage_registry, ComponentRegistry)
    assert isinstance(retriever_registry, ComponentRegistry)
    assert isinstance(formatter_registry, ComponentRegistry)


def test_build_context_defaults(tmp_path):
    ctx = _ctx(tmp_path)
    assert ctx.stage_registry is stage_registry
    assert ctx.retriever_registry is retriever_registry
    assert ctx.formatter_registry is formatter_registry
    assert ctx.predicate_registry is not None
