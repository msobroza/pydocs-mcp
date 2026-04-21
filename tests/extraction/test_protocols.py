"""Smoke tests for extraction Protocols (Task 1 — sub-PR #5)."""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.protocols import (
    Chunker,
    DependencyFileDiscoverer,
    ProjectFileDiscoverer,
)


class _FakeDocumentNode:
    """Stand-in — real DocumentNode lands in Task 2."""


def test_chunker_runtime_checkable_accepts_build_tree_method():
    class _FakeChunker:
        def build_tree(self, path, content, package, root):
            return _FakeDocumentNode()

    assert isinstance(_FakeChunker(), Chunker)


def test_chunker_rejects_class_without_build_tree():
    class _Empty:
        pass

    assert not isinstance(_Empty(), Chunker)


def test_chunker_conforming_instance_exposes_from_config_classmethod():
    """Plan Task 1 contract: ``from_config(cfg)`` classmethod MUST exist.

    ``@runtime_checkable`` can't enforce this (PEP 544 method-presence
    only), so we assert it explicitly as a guardrail. Tasks 14-16 ship
    chunkers that satisfy this; this test pins it so a 4th chunker can't
    silently skip it.
    """
    class _ConformingChunker:
        def build_tree(self, path, content, package, root):
            return _FakeDocumentNode()

        @classmethod
        def from_config(cls, cfg):
            return cls()

    assert isinstance(_ConformingChunker(), Chunker)
    assert hasattr(_ConformingChunker, "from_config")
    assert callable(_ConformingChunker.from_config)
    # classmethod descriptor (not plain method or staticmethod)
    assert isinstance(
        _ConformingChunker.__dict__["from_config"], classmethod,
    )


def test_project_file_discoverer_runtime_checkable_accepts_discover():
    class _FakeProjectDiscoverer:
        def discover(self, target: Path) -> tuple[list[str], Path]:
            return [], target

    assert isinstance(_FakeProjectDiscoverer(), ProjectFileDiscoverer)


def test_project_file_discoverer_rejects_class_without_discover():
    class _Empty:
        pass

    assert not isinstance(_Empty(), ProjectFileDiscoverer)


def test_dependency_file_discoverer_runtime_checkable_accepts_discover():
    class _FakeDepDiscoverer:
        def discover(self, target: str) -> tuple[list[str], Path]:
            return [], Path(".")

    assert isinstance(_FakeDepDiscoverer(), DependencyFileDiscoverer)


def test_dependency_file_discoverer_rejects_class_without_discover():
    class _Empty:
        pass

    assert not isinstance(_Empty(), DependencyFileDiscoverer)
