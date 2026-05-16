"""Tests for the extraction-layer registries (spec §7.5).

Covers:
- ``stage_registry`` is a fresh :class:`ComponentRegistry` (distinct from
  retrieval's — same class, separate instance).
- ``chunker_registry`` is a plain ``dict[str, type]`` — no YAML/from_dict
  indirection.
- ``_register_chunker`` returns the class unchanged (composable with
  ``@dataclass``).
- Duplicate chunker registration raises ``ValueError``.
- Unknown stage type raises ``KeyError`` with a known-types list (spec AC #24).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.extraction.serialization import (
    _register_chunker,
    chunker_registry,
    stage_registry,
)
from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    ComponentRegistry,
    stage_registry as retrieval_stage_registry,
)


def _ctx(tmp_path: Path) -> BuildContext:
    return BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )


def test_stage_registry_is_component_registry_instance():
    """The extraction stage registry is a :class:`ComponentRegistry` —
    reuses the retrieval machinery (spec §7.5) rather than duplicating it."""
    assert isinstance(stage_registry, ComponentRegistry)


def test_stage_registry_is_separate_instance_from_retrieval():
    """Extraction stages must NOT pollute the retrieval registry and vice
    versa. Sharing the instance would leak ``chunking``/``file_read`` etc.
    into the retrieval pipeline decoder's known-types list."""
    assert stage_registry is not retrieval_stage_registry


def test_chunker_registry_is_plain_dict():
    """Chunker lookup is extension→class; no type-name dispatch so a plain
    ``dict`` is sufficient and clearer than reusing ComponentRegistry."""
    assert isinstance(chunker_registry, dict)


def test_register_chunker_returns_class_unchanged():
    """The decorator returns the class so it can sit below ``@dataclass``."""
    @_register_chunker(".xyz")
    @dataclass(frozen=True, slots=True)
    class XyzChunker:
        name: str = "xyz"

    # The class reference round-trips via the registry.
    assert chunker_registry[".xyz"] is XyzChunker
    # And decorator returned it — can instantiate, use normally.
    inst = XyzChunker()
    assert inst.name == "xyz"
    # Cleanup so other tests aren't polluted (chunker_registry is
    # module-global, shared across the test session).
    del chunker_registry[".xyz"]


def test_register_chunker_duplicate_raises():
    """Registering twice for the same extension is a wiring bug — surface
    at import time, not at first extraction."""
    @_register_chunker(".duplicate")
    @dataclass(frozen=True, slots=True)
    class A:
        name: str = "a"

    try:
        with pytest.raises(ValueError, match="already registered"):
            @_register_chunker(".duplicate")
            @dataclass(frozen=True, slots=True)
            class B:
                name: str = "b"
    finally:
        del chunker_registry[".duplicate"]


def test_register_chunker_different_extensions_independent():
    """Each extension gets its own slot; registering ``.a`` does not block
    ``.b`` (regression guard against a buggy global-slot implementation)."""
    @_register_chunker(".aaa")
    @dataclass(frozen=True, slots=True)
    class A:
        name: str = "a"

    @_register_chunker(".bbb")
    @dataclass(frozen=True, slots=True)
    class B:
        name: str = "b"

    try:
        assert chunker_registry[".aaa"] is A
        assert chunker_registry[".bbb"] is B
        assert A is not B
    finally:
        del chunker_registry[".aaa"]
        del chunker_registry[".bbb"]


def test_stage_registry_unknown_type_raises_with_known_list(tmp_path):
    """Spec AC #24 — unknown ``type:`` in ingestion YAML must raise
    ``KeyError`` naming the known-stages list. Closed allowlist enforced."""
    # Register a single dummy stage so ``known`` list is non-empty.
    @stage_registry.register("_test_dummy_stage")
    @dataclass(frozen=True, slots=True)
    class _Dummy:
        name: str = "_test_dummy_stage"

        def to_dict(self): return {"type": self.name}

        @classmethod
        def from_dict(cls, d, ctx): return cls()

    try:
        with pytest.raises(KeyError, match="unknown component"):
            stage_registry.build({"type": "not_a_real_stage"}, _ctx(tmp_path))
    finally:
        # Don't leak the dummy stage into other test files.
        del stage_registry._types["_test_dummy_stage"]
        del stage_registry._forwards_depth["_test_dummy_stage"]
