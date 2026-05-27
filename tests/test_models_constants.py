"""Tests for the PR-C Task 20 relocations (S5 + S7 + S32).

S5: ``PROJECT_PACKAGE_NAME = "__project__"`` is the single source of truth
for the special project-package identifier; every call site reaches it
through ``pydocs_mcp.models``.

S7: ``IndexingStats`` lives in the application layer — the
``pydocs_mcp.models`` symbol is a re-export shim for backward
compatibility.

S32: filter-tree value objects + ``format_registry`` live in the
top-level ``pydocs_mcp.filters`` module; ``pydocs_mcp.storage.filters``
is a re-export shim so the historical import path keeps working.
"""
from __future__ import annotations


def test_project_package_name_constant() -> None:
    """``PROJECT_PACKAGE_NAME`` exists on ``pydocs_mcp.models`` with the
    canonical value used across the indexer + retrieval layers."""
    from pydocs_mcp.models import PROJECT_PACKAGE_NAME

    assert PROJECT_PACKAGE_NAME == "__project__"


def test_indexing_stats_lives_in_application() -> None:
    """``IndexingStats`` lives in ``application.indexing_service``; the
    legacy ``pydocs_mcp.models`` import path is a re-export shim that
    binds to the exact same class object."""
    from pydocs_mcp.application.indexing_service import IndexingStats
    from pydocs_mcp.models import IndexingStats as Shimmed

    assert Shimmed is IndexingStats


def test_format_registry_lives_in_pydocs_filters() -> None:
    """``format_registry`` + filter-tree value objects live in
    ``pydocs_mcp.filters``; ``pydocs_mcp.storage.filters`` is a shim
    that re-exports the same objects (identity, not copy)."""
    from pydocs_mcp.filters import All, format_registry
    from pydocs_mcp.storage.filters import All as ShimAll
    from pydocs_mcp.storage.filters import format_registry as Shim

    assert Shim is format_registry
    assert ShimAll is All


def test_filter_tree_value_objects_exported() -> None:
    """The full filter-tree vocabulary is reachable from
    ``pydocs_mcp.filters`` (the new canonical location)."""
    from pydocs_mcp.filters import (
        All,
        Any_,
        FieldEq,
        FieldIn,
        FieldLike,
        FieldSpec,
        Filter,  # noqa: F401  — Union type alias
        FilterFormat,  # noqa: F401  — Protocol
        MetadataFilterFormat,
        MetadataSchema,
        MultiFieldFormat,
        Not,
        format_registry,
        register_format,
        unregister_format,
    )

    # Sanity-check construction; mirrors tests/storage/test_filters.py
    # against the new canonical location so a regression that drops one
    # of these symbols is loud.
    eq = FieldEq(field="package", value="x")
    all_ = All(clauses=(eq,))
    not_ = Not(clause=eq)
    any_ = Any_(clauses=(eq,))
    in_ = FieldIn(field="package", values=("x", "y"))
    like = FieldLike(field="title", substring="foo")
    spec = FieldSpec(name="package")
    schema = MetadataSchema(fields=(spec,))
    multifield = MultiFieldFormat()

    assert all_.clauses[0] is eq
    assert not_.clause is eq
    assert any_.clauses[0] is eq
    assert in_.values == ("x", "y")
    assert like.substring == "foo"
    assert schema.field_names() == frozenset({"package"})
    assert multifield.format is MetadataFilterFormat.MULTIFIELD
    assert MetadataFilterFormat.MULTIFIELD in format_registry
    assert callable(register_format)
    assert callable(unregister_format)
