"""Tests for domain models in pydocs_mcp.models.

Per sub-PR #1 spec §5, every enum subclasses enum.StrEnum and values round-trip
through SQLite TEXT columns, YAML, and JSON without glue code.
"""
from __future__ import annotations

import pytest

from pydocs_mcp.models import (
    ChunkFilterField,
    ChunkOrigin,
    MemberKind,
    MetadataFilterFormat,
    ModuleMemberFilterField,
    PackageOrigin,
    SearchScope,
)


@pytest.mark.parametrize("enum_cls,value", [
    (ChunkOrigin, "project_module_doc"),
    (ChunkOrigin, "project_code_section"),
    (ChunkOrigin, "dependency_code_section"),
    (ChunkOrigin, "dependency_doc_file"),
    (ChunkOrigin, "dependency_readme"),
    (ChunkOrigin, "dependency_module_doc"),
    (ChunkOrigin, "composite_output"),
    (MemberKind, "function"),
    (MemberKind, "class"),
    (MemberKind, "method"),
    (PackageOrigin, "project"),
    (PackageOrigin, "dependency"),
    (SearchScope, "project_only"),
    (SearchScope, "dependencies_only"),
    (SearchScope, "all"),
    (MetadataFilterFormat, "multifield"),
    (MetadataFilterFormat, "filter_tree"),
    (MetadataFilterFormat, "chromadb"),
    (MetadataFilterFormat, "elasticsearch"),
    (MetadataFilterFormat, "qdrant"),
    (ChunkFilterField, "package"),
    (ChunkFilterField, "title"),
    (ChunkFilterField, "origin"),
    (ChunkFilterField, "module"),
    (ChunkFilterField, "scope"),
    (ModuleMemberFilterField, "package"),
    (ModuleMemberFilterField, "module"),
    (ModuleMemberFilterField, "name"),
    (ModuleMemberFilterField, "kind"),
])
def test_enum_value_roundtrip(enum_cls, value):
    """Every enum value round-trips: str ↔ enum member."""
    member = enum_cls(value)
    assert member.value == value
    assert str(member) == value


from pydocs_mcp.models import Package, PackageOrigin, Parameter


def test_parameter_defaults():
    p = Parameter(name="prefix")
    assert p.name == "prefix"
    assert p.annotation == ""
    assert p.default == ""


def test_parameter_is_frozen():
    p = Parameter(name="prefix")
    with pytest.raises(Exception):
        p.name = "other"


def test_package_construction():
    pkg = Package(
        name="fastapi",
        version="0.104.1",
        summary="Web framework.",
        homepage="https://fastapi.tiangolo.com",
        dependencies=("starlette>=0.27",),
        content_hash="abc123",
        origin=PackageOrigin.DEPENDENCY,
    )
    assert pkg.kind == "package"
    assert pkg.dependencies == ("starlette>=0.27",)
    assert pkg.origin is PackageOrigin.DEPENDENCY


def test_package_is_frozen():
    pkg = Package(
        name="fastapi", version="0.1", summary="", homepage="",
        dependencies=(), content_hash="h", origin=PackageOrigin.DEPENDENCY,
    )
    with pytest.raises(Exception):
        pkg.name = "other"
