"""JavaScriptAnalyzer — CALLS / INHERITS / IMPORTS for ``.js`` (spec §5.4).

``require(...)`` calls are consumed by the imports pass and filtered out of
CALLS. Module-source normalization is purely syntactic (D8): strip leading
``./`` / ``../`` segments and a trailing extension, ``/`` → ``.`` — no
filesystem resolution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import register_analyzer
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    CaptureSession,
    ReferenceQueryRole,
    add_reference,
    canonical_target,
    capabilities_for,
    capture_named_edges,
    emit_statement_import,
    node_text,
    open_capture_session,
    record_aliases,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

# Single source of the extension literal — registration and the capability
# declaration must never drift apart (a module registered for one extension
# while declaring another's capabilities is a silent, per-deployment lie).
_EXT = ".js"

_JS_CALLS_QUERY = """
(call_expression function: (identifier) @callee)
(call_expression function: (member_expression) @callee)
"""

# Both patterns DESCEND to the heritage clause's inner expression: the
# `class_heritage` wrapper's text carries the `extends` keyword
# (`extends A`), which `canonical_target` rejects as a non-identifier chain,
# silently dropping the edge. A clause is EITHER an identifier OR a
# member_expression, so no clause is captured twice; computed heritage
# (`extends mixin(Base)`) is deliberately not captured (drop-don't-guess).
_JS_INHERITS_QUERY = """
(class_heritage (identifier) @parent)
(class_heritage (member_expression) @parent)
"""

# File-scope only: ESM imports are top-level by grammar; CommonJS require is
# captured only at program-level lexical declarations (spec §5.4).
_JS_IMPORTS_QUERY = """
(program (import_statement) @import)
(program (lexical_declaration (variable_declarator
    name: (identifier) @binding
    value: (call_expression
        function: (identifier) @callee
        arguments: (arguments (string) @source)))))
"""

# Source specifier inside `from '…'` — the ESM anchor the text normalizer
# keys on. Named/default/namespace clauses are parsed from the same text.
_SOURCE_RE = re.compile(r"""from\s+['"]([^'"]+)['"]""")
_NAMED_RE = re.compile(r"\{([^}]*)\}")
_NAMESPACE_RE = re.compile(r"\*\s+as\s+([A-Za-z_$][\w$]*)")
# The (?!type\b) lookahead blocks a backtracking trap: without it, on
# `import type { T } from './t'` the optional `type\s+` group is skipped when
# the capture fails at `{`, and the capture then matches the KEYWORD `type`
# itself — a spurious default alias. With the lookahead, type-only named /
# namespace imports yield no default binding while `import type Z from './m'`
# and `import Z from './m'` still capture Z (spec §5.5: `import type`
# treated identically to a value import).
_DEFAULT_RE = re.compile(r"^import\s+(?:type\s+)?(?!type\b)([A-Za-z_$][\w$]*)")

# `require` is an import mechanism, not a call: the imports pass consumes it
# (spec §5.4), so the CALLS pass must not also emit an edge to it.
_REQUIRE_CALLEES = frozenset({"require"})


@register_analyzer(_EXT)
@dataclass(frozen=True, slots=True)
class JavaScriptAnalyzer:
    """Tree-sitter syntactic reference backend for JavaScript."""

    @property
    def capabilities(self) -> LanguageCapabilities:
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
        _JS_CALLS_QUERY,
        capture="callee",
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
        _JS_INHERITS_QUERY,
        capture="parent",
        kind=ReferenceKind.INHERITS,
        from_package=from_package,
        collector=collector,
    )


def _capture_imports(
    session: CaptureSession, from_package: str, collector: ReferenceCollector
) -> None:
    """ESM statements and CommonJS requires share ONE query (one (ext, role)
    cache slot), so the dispatch between the two shapes lives here."""
    for captures in session.matches(ReferenceQueryRole.IMPORTS, _JS_IMPORTS_QUERY):
        stmt = captures.get("import")
        if stmt:
            emit_statement_import(
                session,
                stmt[0],
                normalize=normalize_js_import,
                from_package=from_package,
                collector=collector,
            )
            continue
        _emit_require(session, captures, from_package, collector)


def _emit_require(
    session: CaptureSession,
    captures: dict[str, Any],
    from_package: str,
    collector: ReferenceCollector,
) -> None:
    callee, binding, source = (captures.get(k) for k in ("callee", "binding", "source"))
    if not (callee and binding and source) or node_text(callee[0]) != "require":
        return
    module = normalize_js_module_source(node_text(source[0]).strip("'\""))
    if not module:
        return
    record_aliases(collector, session.module, {node_text(binding[0]): module})
    add_reference(
        collector,
        from_package=from_package,
        from_node_id=session.enclosing_qname(binding[0]),
        to_name=canonical_target(module),
        kind=ReferenceKind.IMPORTS,
    )


def normalize_js_module_source(source: str) -> str:
    """``'./a/b'`` → ``a.b``; ``'../x/y.js'`` → ``x.y`` (spec §5.4).

    Purely syntactic: leading relative segments dropped, one trailing
    extension stripped, ``/`` → ``.`` — never resolved against the tree.
    """
    path = source
    while path.startswith(("./", "../")):
        path = path[2:] if path.startswith("./") else path[3:]
    stem, dot, ext = path.rpartition(".")
    if dot and "/" not in ext:
        path = stem
    return path.replace("/", ".")


def normalize_js_import(stmt_text: str) -> tuple[dict[str, str], list[str]]:
    """ESM import/export statement text → (alias entries, IMPORTS targets).

    D8 canonical example: ``import {X as Y} from './a/b'`` →
    ``({"Y": "a.b.X"}, ["a.b"])``. Re-exports (``export { X } from './a'``)
    and ``import type`` are handled by the same shapes (spec §5.5 reuses
    this via ``normalize_ts_import``). No ``from`` source → no rows.
    """
    match = _SOURCE_RE.search(stmt_text)
    if match is None:
        return {}, []
    module = normalize_js_module_source(match.group(1))
    if not module:
        return {}, []
    aliases: dict[str, str] = {}
    for name, alias in _named_clauses(stmt_text):
        aliases[alias] = f"{module}.{name}"
    namespace = _NAMESPACE_RE.search(stmt_text)
    if namespace is not None:
        aliases[namespace.group(1)] = module
    default = _DEFAULT_RE.match(stmt_text)
    if default is not None:
        aliases[default.group(1)] = module
    return aliases, [module]


def _named_clauses(stmt_text: str) -> list[tuple[str, str]]:
    """``{A as B, C, type D}`` → [("A", "B"), ("C", "C"), ("D", "D")]."""
    match = _NAMED_RE.search(stmt_text)
    if match is None:
        return []
    clauses: list[tuple[str, str]] = []
    for raw in match.group(1).split(","):
        words = raw.strip().split()
        if words and words[0] == "type" and len(words) > 1:
            words = words[1:]  # TS inline `type X` — treated identically (§5.5)
        if len(words) == 3 and words[1] == "as":
            clauses.append((words[0], words[2]))
        elif len(words) == 1:
            clauses.append((words[0], words[0]))
    return clauses


__all__ = ("JavaScriptAnalyzer", "normalize_js_import", "normalize_js_module_source")
