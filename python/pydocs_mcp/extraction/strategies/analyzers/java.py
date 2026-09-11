"""JavaAnalyzer — CALLS / INHERITS / IMPORTS for ``.java`` (spec §5.6).

Method invocations capture receiver + name as a dotted target
(``svc.run()`` → ``svc.run``; ``Files.read(p)`` → ``Files.read``); bare
calls capture the identifier alone. ``new G(...)`` object creation is a
CALLS edge to the constructed type (constructor call). ``package …;``
declarations are not captured — not an import: they parse as
``package_declaration``, a different node type from the
``import_declaration`` the IMPORTS query names.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.analyzers import register_analyzer
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    CaptureSession,
    ReferenceQueryRole,
    add_reference,
    canonical_chain_target,
    canonical_target,
    capabilities_for,
    capture_named_edges,
    capture_statement_imports,
    node_text,
    open_capture_session,
    register_reference_queries,
    token_chain,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

# Single source of the extension literal — registration and the capability
# declaration must never drift apart.
_EXT = ".java"

# Receiver + name join for method invocations; `field_access` receivers cover
# `a.b.c()` → `a.b.c`. A BARE `this` / `super` receiver (`this.m()`) or a
# call-expression receiver (`f().g()`) matches no pattern: computed receivers
# are dropped, not guessed (spec §5.1). A chained `this.a.m()` / `super.a.m()`
# IS captured (`this.a.m`): its receiver is a `field_access`.
# Constructor patterns DESCEND to the type's inner name for the same reason
# Rust's trait clauses do — `ArrayList<String>` is not a dotted chain, so
# capturing the `generic_type` wrapper would silently drop the edge. Probe
# evidence (tree-sitter 0.25.2 + tree-sitter-java 0.23.5):
#   new G()                     → type:(type_identifier)
#   new com.acme.G()            → type:(scoped_type_identifier)
#   new ArrayList<String>()     → type:(generic_type (type_identifier) …)
#   new java.util.ArrayList<>() → type:(generic_type (scoped_type_identifier) …)
_JAVA_CALLS_QUERY = """
(method_invocation object: (identifier) @recv name: (identifier) @meth)
(method_invocation object: (field_access) @recv name: (identifier) @meth)
(method_invocation !object name: (identifier) @meth)
(object_creation_expression type: [(type_identifier) (scoped_type_identifier)] @ctor)
(object_creation_expression type: (generic_type
    [(type_identifier) (scoped_type_identifier)] @ctor))
"""

# Three heritage clauses, each in a plain and a `generic_type`-wrapped form —
# the same descend-to-the-inner-name rule as above. Patterns are deliberately
# NOT anchored to `class_declaration`: `super_interfaces` is also how enums and
# records declare `implements`, and both map to NodeKind.CLASS here. Probe
# evidence (tree-sitter 0.25.2 + tree-sitter-java 0.23.5):
#   class A extends Base implements I, J → (superclass (type_identifier))
#                                          (super_interfaces (type_list
#                                              (type_identifier)
#                                              (type_identifier)))
#   class B extends Base<T>              → (superclass (generic_type
#                                              (type_identifier) …))
#   class C extends p.q.Base             → (superclass (scoped_type_identifier))
#   interface P extends Q, R             → (extends_interfaces (type_list …))
#   record Rec(int x) implements I       → (super_interfaces (type_list …))
_JAVA_INHERITS_QUERY = """
(superclass [(type_identifier) (scoped_type_identifier)] @parent)
(superclass (generic_type [(type_identifier) (scoped_type_identifier)] @parent))
(super_interfaces (type_list [(type_identifier) (scoped_type_identifier)] @parent))
(super_interfaces (type_list (generic_type
    [(type_identifier) (scoped_type_identifier)] @parent)))
(extends_interfaces (type_list [(type_identifier) (scoped_type_identifier)] @parent))
(extends_interfaces (type_list (generic_type
    [(type_identifier) (scoped_type_identifier)] @parent)))
"""

_JAVA_IMPORTS_QUERY = """
(import_declaration) @import
"""

# Every query above joins the grammar loadability probe (ADR 0022): a grammar
# that rejects one degrades `.java` whole instead of stranding a partial graph.
register_reference_queries((_EXT,), _JAVA_CALLS_QUERY, _JAVA_INHERITS_QUERY, _JAVA_IMPORTS_QUERY)


@register_analyzer(_EXT)
@dataclass(frozen=True, slots=True)
class JavaAnalyzer:
    """Tree-sitter syntactic reference backend for Java."""

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
    """Hand-rolled rather than ``capture_named_edges``: a Java call target is
    built from TWO captures of one match (receiver + method name)."""
    for captures in session.matches(ReferenceQueryRole.CALLS, _JAVA_CALLS_QUERY):
        target_nodes = _call_target_nodes(captures)
        if target_nodes is None:
            continue
        anchor, chain = target_nodes
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(anchor),
            to_name=canonical_chain_target(
                _dotted_chain(chain, node_text), partial(_dotted_chain, chain, token_chain)
            ),
            kind=ReferenceKind.CALLS,
        )


def _call_target_nodes(captures: dict[str, Any]) -> tuple[Any, tuple[Any, ...]] | None:
    """One match → (anchor node, the nodes that join into the target), or None
    when the match captured neither shape (nothing to emit).

    Java is the one language whose target is JOINED from two captures, so it
    needs its own oracle for ``canonical_chain_target`` — the shared
    ``token_chain`` reads a single node. Naming the NODES once, and spelling
    them twice through :func:`_dotted_chain`, is what keeps the raw target and
    that oracle from being taught a new capture shape separately: they would
    then disagree, and a disagreement silently drops the edge.
    """
    ctor = captures.get("ctor")
    if ctor:
        return ctor[0], (ctor[0],)
    meth = captures.get("meth")
    if not meth:
        return None
    recv = captures.get("recv")
    if recv:
        return meth[0], (recv[0], meth[0])
    return meth[0], (meth[0],)


def _dotted_chain(nodes: tuple[Any, ...], spelling: Callable[[Any], str]) -> str:
    """The call target, each node rendered by ``spelling`` (``node_text`` for
    the raw target, ``token_chain`` for the oracle) and joined on ``.``."""
    return ".".join(spelling(node) for node in nodes)


def _capture_inherits(
    session: CaptureSession, from_package: str, collector: ReferenceCollector
) -> None:
    capture_named_edges(
        session,
        ReferenceQueryRole.INHERITS,
        _JAVA_INHERITS_QUERY,
        capture_name="parent",
        kind=ReferenceKind.INHERITS,
        from_package=from_package,
        collector=collector,
    )


def _capture_imports(
    session: CaptureSession, from_package: str, collector: ReferenceCollector
) -> None:
    capture_statement_imports(
        session,
        _JAVA_IMPORTS_QUERY,
        normalize=normalize_java_import,
        from_package=from_package,
        collector=collector,
    )


def normalize_java_import(declaration_text: str) -> tuple[dict[str, str], list[str]]:
    """``import [static] com.acme.G;`` → (alias entries, IMPORTS targets).

    D8 canonical example: ``import com.acme.G;`` →
    ``({"G": "com.acme.G"}, ["com.acme.G"])``. Static imports alias the
    member; wildcards emit an IMPORTS row only (spec §5.6).
    """
    text = declaration_text.strip().rstrip(";").strip()
    text = text.removeprefix("import").strip()
    text = text.removeprefix("static").strip()
    if text.endswith(".*"):
        wildcard = canonical_target(text[:-2])
        return ({}, [wildcard]) if wildcard else ({}, [])
    target = canonical_target(text)
    if target is None:
        return {}, []
    return {target.rsplit(".", 1)[-1]: target}, [target]


__all__ = ("JavaAnalyzer", "normalize_java_import")
