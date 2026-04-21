"""Smoke tests for extraction Protocols (Task 1 — sub-PR #5)."""
from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.protocols import (
    Chunker,
    ChunkerSelector,
    FileDiscoverer,
)


class _FakeDocumentNode:
    """Stand-in for DocumentNode since it doesn't exist yet (Task 2)."""
    pass


def test_chunker_runtime_checkable_accepts_build_tree_method():
    class _FakeChunker:
        def build_tree(self, path, content, package, root):
            return _FakeDocumentNode()

    assert isinstance(_FakeChunker(), Chunker)


def test_chunker_rejects_class_without_build_tree():
    class _Empty:
        pass

    assert not isinstance(_Empty(), Chunker)


def test_file_discoverer_runtime_checkable_accepts_discover():
    class _FakeDiscoverer:
        def discover(self, target):
            return [], Path(".")

    assert isinstance(_FakeDiscoverer(), FileDiscoverer)


def test_file_discoverer_rejects_class_without_discover():
    class _Empty:
        pass

    assert not isinstance(_Empty(), FileDiscoverer)


def test_chunker_selector_runtime_checkable_accepts_pick():
    class _FakeSelector:
        def pick(self, path):
            raise NotImplementedError

    assert isinstance(_FakeSelector(), ChunkerSelector)


def test_chunker_selector_rejects_class_without_pick():
    class _Empty:
        pass

    assert not isinstance(_Empty(), ChunkerSelector)
