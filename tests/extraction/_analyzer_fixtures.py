"""Shared fixture plumbing for the per-language analyzer test files.

Runs the CHUNKER (persisted-tree side) and the ANALYZER (edge side) over the
same in-memory fixture files, then resolves through the real
``ReferenceResolver`` — the per-language ACs (AC-13..AC-17) assert against
this end-to-end path with the resolver byte-untouched (D8)."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.extraction.strategies.analyzers import analyzer_registry
from pydocs_mcp.extraction.strategies.chunkers import MultilangChunker
from pydocs_mcp.extraction.strategies.reference_resolver import ReferenceResolver
from pydocs_mcp.extraction.strategies.references import ReferenceCollector
from pydocs_mcp.storage.node_reference import NodeReference

ALL_KINDS = frozenset({"calls", "imports", "inherits"})


def tree_qnames(node: DocumentNode) -> set[str]:
    names = {node.qualified_name}
    for child in node.children:
        names |= tree_qnames(child)
    return names


def capture_fixture(
    files: dict[str, str],
    allowed: frozenset[str] = ALL_KINDS,
) -> tuple[frozenset[str], ReferenceCollector]:
    """Chunk + capture every fixture file under package 'pkg'; returns the
    persisted-qname universe and the filled collector. Fixture paths MUST
    start with 'pkg/' so qnames carry the from_package prefix Rule C scopes
    on (``q.startswith("pkg.")``)."""
    collector = ReferenceCollector()
    universe: set[str] = set()
    for relpath, source in files.items():
        tree = MultilangChunker().build_tree(
            path=relpath, content=source, package="pkg", root=Path()
        )
        universe |= tree_qnames(tree)
        capture_with_analyzer(relpath, source, collector, allowed)
    return frozenset(universe), collector


def capture_with_analyzer(
    relpath: str,
    source: str,
    collector: ReferenceCollector,
    allowed: frozenset[str] = ALL_KINDS,
) -> None:
    """Run ONLY the registered analyzer for ``relpath``'s extension — no
    chunker, so no ``multilang_fallback`` log (the degrade seams assert its
    absence), under package 'pkg' like ``capture_fixture``."""
    analyzer = analyzer_registry[Path(relpath).suffix.lower()]
    analyzer.capture(
        source,
        path=relpath,
        root=Path(),
        from_package="pkg",
        allowed=allowed,
        collector=collector,
    )


def resolve_fixture(universe: frozenset[str], collector: ReferenceCollector) -> list[NodeReference]:
    resolver = ReferenceResolver(qname_universe=universe, aliases=collector.aliases)
    return resolver.resolve(collector.refs)


def edge_map(refs: list[NodeReference]) -> dict[tuple[str, str, str], str | None]:
    """(from_node_id, to_name, kind) → to_node_id. Duplicate PKs collapse —
    fine here; the fixtures pin unique edges."""
    return {(r.from_node_id, r.to_name, r.kind.value): r.to_node_id for r in refs}
