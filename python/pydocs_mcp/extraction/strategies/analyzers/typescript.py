"""TypeScriptAnalyzer — CALLS / INHERITS / IMPORTS for ``.ts`` + ``.tsx``
(spec §5.5).

The TS grammar names classes with ``type_identifier`` where JS uses
``identifier`` (the "impossible pattern" note in ``multilang_queries.py``),
and adds interfaces / implements / re-exports — hence its own queries, not
JS + extras. Import normalization REUSES the JS text normalizer: the TS
additions (``import type``, re-exports) are shapes it already parses.
Dialect note: capture derives the extension from ``path`` (the session
loads ``language_tsx`` for ``.tsx``), while ``capabilities`` hardcodes the
primary extension ``.ts`` — one wheel, two accessors; the per-accessor skew
window is accepted and AC-7 pins per MODULE (spec §4.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import register_analyzer
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    CaptureSession,
    ReferenceQueryRole,
    capabilities_for,
    capture_named_edges,
    open_capture_session,
    register_reference_queries,
)
from pydocs_mcp.extraction.strategies.analyzers.javascript import (
    emit_esm_import,
    normalize_js_import,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

# Single source of the extension literals — registration and the capability
# declaration must never drift apart. ``_EXT`` is the PRIMARY extension: it
# drives one registration and the capability probe. Unlike C, the two dialects
# have DIFFERENT accessors (language_typescript / language_tsx), but they ship
# in ONE wheel and the analyzer registers as two FIELDLESS instances of one
# class — a per-instance extension is structurally unavailable, so the primary
# probe is the module's honest answer (spec §4.2, AC-7 pins per MODULE).
_EXT = ".ts"
_TSX_EXT = ".tsx"

_TS_CALLS_QUERY = """
(call_expression function: (identifier) @callee)
(call_expression function: (member_expression) @callee)
"""

# Every pattern DESCENDS to the heritage clause's inner NAME: the clause text
# carries its keyword (`extends A`), which `canonical_target` rejects as a
# non-identifier chain, silently dropping the edge. `extends_clause` holds a
# bare identifier / member_expression even when type arguments follow
# (`extends A<T>` → value:(identifier) + type_arguments:…), while the
# implements / interface-extends clauses wrap a parameterized parent in
# `generic_type` — hence the paired patterns. Probe evidence (tree-sitter
# 0.25.2 + tree-sitter-typescript 0.23.2):
#   class B extends A implements I  → (extends_clause value:(identifier))
#                                     (implements_clause (type_identifier))
#   class C implements I<X>         → (implements_clause (generic_type
#                                        name:(type_identifier) …))
#   interface J extends K<Q>        → (extends_type_clause type:(generic_type
#                                        name:(type_identifier) …))
# Computed heritage (`extends mixin(Base)`) stays uncaptured (drop-don't-guess).
_TS_INHERITS_QUERY = """
(extends_clause [(identifier) (member_expression)] @parent)
(implements_clause (type_identifier) @parent)
(implements_clause (generic_type (type_identifier) @parent))
(extends_type_clause (type_identifier) @parent)
(extends_type_clause (generic_type (type_identifier) @parent))
"""

# File-scope only: ESM imports and re-exports are top-level by grammar. The
# export pattern is anchored on a `source:` string, so only re-exports
# (`export { X } from`, `export * from`, `export * as ns from`) reach the text
# normalizer. A bare `(export_statement)` capture also fed every exported
# declaration's BODY to it, and a body containing `from '…'` / `{…}` text
# fabricated IMPORTS rows and aliases (wrong edges, not missing ones).
_TS_IMPORTS_QUERY = """
(program (import_statement source: (string) @esm_source) @stmt)
(program (export_statement source: (string) @esm_source) @stmt)
"""

# Every query above joins the grammar loadability probe (ADR 0022), for both
# dialects: a grammar that rejects one degrades that extension whole.
register_reference_queries((_EXT, _TSX_EXT), _TS_CALLS_QUERY, _TS_INHERITS_QUERY, _TS_IMPORTS_QUERY)

# `require` is an import mechanism, not a call (spec §5.4). TypeScript's
# imports query is ESM-only, so a CommonJS require in a `.ts` file yields no
# row at all in v1 — deliberately no edge rather than a bogus CALLS target.
_REQUIRE_CALLEES = frozenset({"require"})


@register_analyzer(_TSX_EXT)
@register_analyzer(_EXT)
@dataclass(frozen=True, slots=True)
class TypeScriptAnalyzer:
    """Tree-sitter syntactic reference backend for TypeScript and TSX."""

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
        _capture_imports(session, from_package, collector)
        if "calls" in allowed:
            _capture_calls(session, from_package, collector)
        if "inherits" in allowed:
            _capture_inherits(session, from_package, collector)


def _capture_calls(
    session: CaptureSession, from_package: str, collector: ReferenceCollector
) -> None:
    capture_named_edges(
        session,
        ReferenceQueryRole.CALLS,
        _TS_CALLS_QUERY,
        capture_name="callee",
        kind=ReferenceKind.CALLS,
        from_package=from_package,
        collector=collector,
        skip_names=_REQUIRE_CALLEES,
    )


def _capture_inherits(
    session: CaptureSession, from_package: str, collector: ReferenceCollector
) -> None:
    capture_named_edges(
        session,
        ReferenceQueryRole.INHERITS,
        _TS_INHERITS_QUERY,
        capture_name="parent",
        kind=ReferenceKind.INHERITS,
        from_package=from_package,
        collector=collector,
    )


def _capture_imports(
    session: CaptureSession, from_package: str, collector: ReferenceCollector
) -> None:
    """Imports and re-exports are both ONE statement node, so there is no
    per-match dispatch here (unlike JS, whose query also carries CommonJS)."""
    for captures in session.matches(ReferenceQueryRole.IMPORTS, _TS_IMPORTS_QUERY):
        emit_esm_import(
            session,
            captures["stmt"][0],
            captures["esm_source"][0],
            from_package=from_package,
            collector=collector,
        )


def normalize_ts_import(stmt_text: str, module: str) -> dict[str, str]:
    """TS import / export / re-export clause text → alias entries.

    Delegates to the JS normalizer (spec §5.5): ``import type { T }`` and
    ``export { X } from './a'`` are shapes its clause parser already handles.
    ``module`` is read from the statement's ``source:`` node by the caller, so a
    statement carrying no source never reaches here. Example::

        normalize_ts_import("export { X } from './a'", "a")  # {"X": "a.X"}
    """
    return normalize_js_import(stmt_text, module)


__all__ = ("TypeScriptAnalyzer", "normalize_ts_import")
