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
