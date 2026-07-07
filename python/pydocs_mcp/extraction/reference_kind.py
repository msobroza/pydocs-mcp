"""ReferenceKind enum for the cross-node reference graph (spec §4.1, §D18).

Six kinds. Three are AST-precise — CALLS, IMPORTS, INHERITS — captured during
Python file ingestion. MENTIONS is regex-fuzzy: backtick-quoted dotted names in
markdown, lower-precision than AST capture and therefore opt-in via YAML.
SIMILAR is index-time *synthetic*: embedding-kNN edges between a node and its
top-m nearest neighbours, densifying the otherwise-sparse AST graph so graph
expansion can reach semantically-related code that has no call/inherit edge.
Opt-in (off by default). GOVERNS is index-time *projected* (spec §D18): one edge
per ``affected_qname`` of a mined decision, ``from_node_id='decision:<key>'``,
making decisions first-class graph nodes so "which decisions govern this symbol?"
is a resolver-backed edge query instead of an ``affected_qnames`` substring scan.

StrEnum so the on-disk ``kind`` column stays plain text — readable in
SQLite shell, no enum-import-needed for ad-hoc queries.
"""

from __future__ import annotations

from enum import StrEnum


class ReferenceKind(StrEnum):
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    MENTIONS = "mentions"
    SIMILAR = "similar"
    GOVERNS = "governs"
