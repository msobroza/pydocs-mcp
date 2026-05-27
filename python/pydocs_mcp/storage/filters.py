"""Re-export shim — the canonical filter-tree module lives at
:mod:`pydocs_mcp.filters` (post-PR-C Task 20 / S32).

Kept so callers using the historical import path
(``from pydocs_mcp.storage.filters import FieldEq``) keep working
unchanged. New code should prefer ``from pydocs_mcp.filters import ...``.

The shim re-exports the SAME object identities — the ``format_registry``
imported here IS the canonical mapping; nothing is copied or rebuilt.
"""
from __future__ import annotations

from pydocs_mcp.filters import (
    All,
    Any_,
    FieldEq,
    FieldIn,
    FieldLike,
    FieldSpec,
    Filter,
    FilterFormat,
    MetadataFilterFormat,
    MetadataSchema,
    MultiFieldFormat,
    Not,
    format_registry,
    register_format,
    unregister_format,
)

__all__ = [
    "All",
    "Any_",
    "FieldEq",
    "FieldIn",
    "FieldLike",
    "FieldSpec",
    "Filter",
    "FilterFormat",
    "MetadataFilterFormat",
    "MetadataSchema",
    "MultiFieldFormat",
    "Not",
    "format_registry",
    "register_format",
    "unregister_format",
]
