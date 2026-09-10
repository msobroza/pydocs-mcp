"""CAnalyzer — CALLS / IMPORTS capture for ``.c`` + ``.h`` (spec §5.3).

C has no inheritance, so the analyzer runs no inherits pass at all; struct
embedding is deliberately NOT modeled as inheritance. Includes are not
renaming imports, so the alias table stays EMPTY for C modules (AC-19 pins
this).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import register_analyzer
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    CaptureSession,
    ReferenceQueryRole,
    add_reference,
    canonical_target,
    capabilities_for,
    capture_named_edges,
    node_text,
    open_capture_session,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

# Single source of the extension literals — registration and the capability
# declaration must never drift apart (a module registered for one extension
# while declaring another's capabilities is a silent, per-deployment lie).
# ``_EXT`` is the PRIMARY extension: it drives both a registration and the
# capability probe, because .c and .h ship in ONE grammar wheel behind one
# accessor (LANGUAGE_SPECS) — the pair cannot skew, so probing the primary
# is the whole truth for both (spec §4.2, AC-7 pins per MODULE).
_EXT = ".c"
_HEADER_EXT = ".h"

_C_CALLS_QUERY = """
(call_expression function: (identifier) @callee)
"""

# Both include spellings capture the WRAPPER node, not an inner name: only
# `string_literal` has a `string_content` child to descend to, while
# `system_lib_string` is a leaf. Capturing both wrappers keeps one code path,
# and `normalize_c_include` strips the delimiters before `canonical_target`
# ever sees them — descending would buy asymmetry, not correctness.
_C_IMPORTS_QUERY = """
(preproc_include path: (string_literal) @path)
(preproc_include path: (system_lib_string) @path)
"""


@register_analyzer(_HEADER_EXT)
@register_analyzer(_EXT)
@dataclass(frozen=True, slots=True)
class CAnalyzer:
    """Tree-sitter syntactic reference backend for C sources and headers."""

    @property
    def capabilities(self) -> LanguageCapabilities:
        # Primary-extension hardcode (spec §4.2) — see the _EXT comment.
        return capabilities_for(_EXT)

    def capture(
        self,
        source: str,
        *,
        path: str,
        root: Path,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None:
        session = open_capture_session(source, path=path, root=root)
        if session is None:
            return  # degraded — chunker's multilang_fallback log is the signal (D11)
        _capture_includes(session, from_package, collector)
        if "calls" in allowed:
            _capture_calls(session, from_package, collector)
        # No inherits pass: C has no inheritance (spec §5.3).


def _capture_calls(
    session: CaptureSession, from_package: str, collector: ReferenceCollector
) -> None:
    capture_named_edges(
        session,
        ReferenceQueryRole.CALLS,
        _C_CALLS_QUERY,
        capture_name="callee",
        kind=ReferenceKind.CALLS,
        from_package=from_package,
        collector=collector,
    )


def _capture_includes(
    session: CaptureSession, from_package: str, collector: ReferenceCollector
) -> None:
    """Kept hand-rolled: an include is a THIRD shape neither shared executor
    covers — one already-normalized target (``normalize_c_include``, not
    ``canonical_target``) and NO alias table, because includes are not
    renaming imports (AC-19)."""
    for captures in session.matches(ReferenceQueryRole.IMPORTS, _C_IMPORTS_QUERY):
        nodes = captures.get("path")
        if not nodes:
            continue
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(nodes[0]),
            to_name=normalize_c_include(node_text(nodes[0])),
            kind=ReferenceKind.IMPORTS,
        )


def normalize_c_include(path_text: str) -> str | None:
    """``"graph.h"`` / ``<stdio.h>`` → dotted include target, extension kept.

    The kept ``.h`` segment is what makes suffix matching land on the
    suffix-preserving module qname (``src.include.graph.h``, spec §5.3).
    System includes normalize the same way and resolve to None (Rule E) —
    verbatim intent, like Python's unresolved imports.
    """
    return canonical_target(path_text.strip().strip('"<>'))


__all__ = ("CAnalyzer", "normalize_c_include")
