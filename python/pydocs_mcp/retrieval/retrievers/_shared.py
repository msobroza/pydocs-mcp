"""Helpers shared by the metadata-aware retrievers.

``Bm25ChunkRetriever`` and ``LikeMemberRetriever`` both:

- accept a ``pre_filter`` parsed through the configured
  ``MetadataFilterFormat`` and validated against an
  ``allowed_fields`` allowlist;
- split the ``scope`` clause out of the pushdown filter (the SQL layer
  rejects ``scope`` as an "unsafe column") and re-apply it in-process
  via :func:`_matches_scope`;
- expose a :class:`MetadataSchema` built from a flat field allowlist.

Task 8: the helper bodies moved to :mod:`pydocs_mcp.retrieval.filter_helpers`
(the new fetcher steps need them but importing through ``retrievers/``
triggers a circular import via ``storage.filters → extraction → retrieval.steps``).
This module is a thin re-export shim kept alive for the legacy
retrievers until Task 9 deletes them.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.filter_helpers import (
    _PROJECT,
    _matches_scope,
    _schema_from_fields,
    _split_scope,
)

__all__ = (
    "_PROJECT",
    "_matches_scope",
    "_schema_from_fields",
    "_split_scope",
)
