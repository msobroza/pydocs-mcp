"""Canonical domain models for pydocs-mcp.

This module is the single source of truth for the domain vocabulary — see
docs/superpowers/specs/2026-04-19-sub-pr-1-naming-and-models-design.md §5.

All dataclasses are frozen + slotted. All enums subclass enum.StrEnum so values
round-trip through SQLite TEXT columns and JSON without glue code.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar


class ChunkOrigin(StrEnum):
    PROJECT_MODULE_DOC       = "project_module_doc"
    PROJECT_CODE_SECTION     = "project_code_section"
    DEPENDENCY_CODE_SECTION  = "dependency_code_section"
    DEPENDENCY_DOC_FILE      = "dependency_doc_file"
    DEPENDENCY_README        = "dependency_readme"
    DEPENDENCY_MODULE_DOC    = "dependency_module_doc"
    COMPOSITE_OUTPUT         = "composite_output"


class MemberKind(StrEnum):
    FUNCTION = "function"
    CLASS    = "class"
    METHOD   = "method"


class PackageOrigin(StrEnum):
    PROJECT    = "project"
    DEPENDENCY = "dependency"


class SearchScope(StrEnum):
    PROJECT_ONLY      = "project_only"
    DEPENDENCIES_ONLY = "dependencies_only"
    ALL               = "all"


class MetadataFilterFormat(StrEnum):
    MULTIFIELD    = "multifield"
    FILTER_TREE   = "filter_tree"
    CHROMADB      = "chromadb"
    ELASTICSEARCH = "elasticsearch"
    QDRANT        = "qdrant"


class ChunkFilterField(StrEnum):
    """Canonical metadata keys for Chunk queries (keys in the `metadata` mapping,
    not dataclass fields). Used by MCP handlers to build pre_filter dicts."""
    PACKAGE = "package"
    TITLE   = "title"
    ORIGIN  = "origin"
    MODULE  = "module"
    SCOPE   = "scope"


class ModuleMemberFilterField(StrEnum):
    PACKAGE = "package"
    MODULE  = "module"
    NAME    = "name"
    KIND    = "kind"


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    annotation: str = ""
    default: str = ""


@dataclass(frozen=True, slots=True)
class Package:
    kind: ClassVar[str] = "package"
    name: str
    version: str
    summary: str
    homepage: str
    dependencies: tuple[str, ...]
    content_hash: str
    origin: PackageOrigin
