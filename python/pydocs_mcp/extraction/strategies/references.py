"""Reference capture + custom AST→str walker (spec §7.1).

Two surfaces:

- :func:`canonical_dotted` — normalises an AST expression to its dotted
  form (``a.b.c``) or ``None`` for shapes the resolver can't handle.
  Replaces ``ast.unparse`` because CPython's unparse output is not
  version-stable (3.11 emits ``a.b``; 3.13 may emit ``(a).b`` for
  subscripted bases), and the reference table is PK'd on the output.

- :class:`ReferenceCollector` — callable threaded into chunker
  ``build_tree(..., ref_collector=collector)`` to receive
  :class:`NodeReference` candidates as the chunker walks the AST. The
  resolver runs as a separate pass (see :class:`ReferenceResolver`).

Sub-PR #5b ships Python-only capture. Markdown / notebook chunkers do
NOT emit references (per spec Decision 7); MENTIONS lands in #5c.
"""
from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference

log = logging.getLogger("pydocs-mcp")

# Defensive cap: pathologically nested expressions (200+ levels) would
# blow up the `node_references` row size. Truncate with an ellipsis to
# preserve the prefix and signal truncation to inspectors.
_MAX_TO_NAME_CHARS = 256


def canonical_dotted(node: ast.expr) -> str | None:
    """AST→str without ast.unparse. Returns dotted form or None.

    Walks ``Attribute(Attribute(...))`` chains until the root must be a
    bare ``Name`` for the result to be a valid dotted target. Anything
    else (Call, Subscript, Lambda, BinOp, etc.) returns ``None`` and is
    silently dropped by the collector — counted in a future metric, never
    written.
    """
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    result = ".".join(reversed(parts))
    if len(result) > _MAX_TO_NAME_CHARS:
        return result[: _MAX_TO_NAME_CHARS - 1] + "…"  # trailing ellipsis
    return result


@dataclass
class ReferenceCollector:
    """Mutable buffer of (unresolved) NodeReference candidates.

    Threaded into ``AstPythonChunker.build_tree(..., ref_collector=...)``
    so the chunker emits one candidate per call/import/inherit site.
    ``to_node_id`` is None for every emitted ref — the resolver flips
    that field in a post-pass. Alias info is also captured here so a
    second pass can use it (the resolver merges per-module alias tables
    from this collector).
    """

    refs: list[NodeReference] = field(default_factory=list)
    # Per-module alias table: module_qname → {alias_name: dotted_target}.
    # Populated by `capture_imports` (and used by the resolver).
    aliases: dict[str, dict[str, str]] = field(default_factory=dict)

    def add(self, ref: NodeReference) -> None:
        self.refs.append(ref)


def capture_calls(
    body: list[ast.stmt],
    *,
    from_package: str,
    from_node_id: str,
    collector: ReferenceCollector,
) -> None:
    """Walk a function/method body's AST, emit CALLS candidates.

    Per-call try/except keeps one malformed ast.Call from aborting the
    whole walk (spec §7.1 — per-call error containment).
    """
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        try:
            to_name = canonical_dotted(node.func)
        except Exception as exc:  # noqa: BLE001 -- defensive per-call
            log.debug("canonical_dotted failed on %r: %s", node.func, exc)
            continue
        if to_name is None:
            continue  # dropped — non-dotted shape
        collector.add(NodeReference(
            from_package=from_package,
            from_node_id=from_node_id,
            to_name=to_name,
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ))


def capture_imports(
    body: list[ast.stmt],
    *,
    from_package: str,
    module_qname: str,
    collector: ReferenceCollector,
) -> None:
    """Walk module-level imports, emit IMPORTS candidates AND populate the
    per-module alias table (spec §7.2 — alias awareness Rule A).

    ``from X import Y as Z`` records ``aliases[module][Z] = "X.Y"``.
    ``import X as Z`` records ``aliases[module][Z] = "X"``.
    Function-scoped imports are ignored — only module-top-level imports
    feed the alias table.
    """
    aliases = collector.aliases.setdefault(module_qname, {})
    for stmt in body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                to_name = alias.name
                collector.add(NodeReference(
                    from_package=from_package,
                    from_node_id=module_qname,
                    to_name=to_name,
                    to_node_id=None,
                    kind=ReferenceKind.IMPORTS,
                ))
                if alias.asname:
                    aliases[alias.asname] = to_name
        elif isinstance(stmt, ast.ImportFrom):
            module = stmt.module or ""
            for alias in stmt.names:
                to_name = f"{module}.{alias.name}" if module else alias.name
                collector.add(NodeReference(
                    from_package=from_package,
                    from_node_id=module_qname,
                    to_name=to_name,
                    to_node_id=None,
                    kind=ReferenceKind.IMPORTS,
                ))
                alias_key = alias.asname or alias.name
                aliases[alias_key] = to_name


def capture_inherits(
    bases: list[ast.expr],
    *,
    from_package: str,
    class_qname: str,
    collector: ReferenceCollector,
) -> None:
    """Emit one INHERITS edge per base class (spec §7.1)."""
    for base in bases:
        try:
            to_name = canonical_dotted(base)
        except Exception as exc:  # noqa: BLE001 -- defensive per-base
            log.debug("canonical_dotted failed on base %r: %s", base, exc)
            continue
        if to_name is None:
            continue
        collector.add(NodeReference(
            from_package=from_package,
            from_node_id=class_qname,
            to_name=to_name,
            to_node_id=None,
            kind=ReferenceKind.INHERITS,
        ))
