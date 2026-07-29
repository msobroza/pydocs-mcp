"""RustAnalyzer — CALLS / INHERITS / IMPORTS capture for ``.rs`` (spec §5.2).

Queries are unanchored (references occur at any depth); attribution comes
from the shared top-level span index, so a call inside ``impl Node { … }``
attributes to the impl span's qname (``….Node`` — or ``….Node_2`` when the
file also defines ``struct Node``; the dedup-verbatim rule of spec §4.4).
Macro invocations are deliberately not captured (not calls in the graph's
sense). The import normalizer is purely syntactic (D8): ``crate::`` /
``self::`` / repeated ``super::`` prefixes are stripped, never resolved.
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
    node_text,
    open_capture_session,
    record_aliases,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

_RUST_CALLS_QUERY = """
(call_expression function: (identifier) @callee)
(call_expression function: (scoped_identifier) @callee)
(call_expression function: (field_expression) @callee)
"""

_RUST_INHERITS_QUERY = """
(impl_item trait: (type_identifier) @parent)
(impl_item trait: (scoped_type_identifier) @parent)
(trait_item bounds: (trait_bounds (type_identifier) @parent))
(trait_item bounds: (trait_bounds (scoped_type_identifier) @parent))
"""

_RUST_IMPORTS_QUERY = """
(use_declaration) @import
"""


@register_analyzer(".rs")
@dataclass(frozen=True, slots=True)
class RustAnalyzer:
    """Tree-sitter syntactic reference backend for Rust."""

    @property
    def capabilities(self) -> LanguageCapabilities:
        return capabilities_for(".rs")

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
    for captures in session.matches(ReferenceQueryRole.CALLS, _RUST_CALLS_QUERY):
        nodes = captures.get("callee")
        if not nodes:
            continue
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(nodes[0]),
            to_name=canonical_target(node_text(nodes[0])),
            kind=ReferenceKind.CALLS,
        )


def _capture_inherits(
    session: CaptureSession, from_package: str, collector: ReferenceCollector
) -> None:
    for captures in session.matches(ReferenceQueryRole.INHERITS, _RUST_INHERITS_QUERY):
        nodes = captures.get("parent")
        if not nodes:
            continue
        add_reference(
            collector,
            from_package=from_package,
            from_node_id=session.enclosing_qname(nodes[0]),
            to_name=canonical_target(node_text(nodes[0])),
            kind=ReferenceKind.INHERITS,
        )


def _capture_imports(
    session: CaptureSession, from_package: str, collector: ReferenceCollector
) -> None:
    for captures in session.matches(ReferenceQueryRole.IMPORTS, _RUST_IMPORTS_QUERY):
        nodes = captures.get("import")
        if not nodes:
            continue
        aliases, targets = normalize_rust_use(node_text(nodes[0]))
        record_aliases(collector, session.module, aliases)
        for target in targets:
            add_reference(
                collector,
                from_package=from_package,
                from_node_id=session.enclosing_qname(nodes[0]),
                to_name=canonical_target(target),
                kind=ReferenceKind.IMPORTS,
            )


def normalize_rust_use(declaration_text: str) -> tuple[dict[str, str], list[str]]:
    """``use …;`` text → (alias entries, IMPORTS targets). Spec §5.2 / D8.

    Purely syntactic: ``crate::`` / ``self::`` and repeated ``super::``
    prefixes are stripped greedily, ``::`` maps to ``.``, and no filesystem
    resolution happens. Example: ``use crate::a::B as C;`` →
    ``({"C": "a.B"}, ["a.B"])``.
    """
    text = declaration_text.strip().rstrip(";").strip()
    text = text.removeprefix("pub").strip()
    text = text.removeprefix("use").strip()
    if not text:
        return {}, []
    return _use_tree(text, prefix="")


def _use_tree(text: str, prefix: str) -> tuple[dict[str, str], list[str]]:
    brace = text.find("{")
    if brace >= 0 and text.endswith("}"):
        head = text[:brace].rstrip().rstrip(":")
        return _use_list(text[brace + 1 : -1], _join_path(prefix, head))
    if " as " in text:
        path, _, alias = text.partition(" as ")
        target = _dotted_use_path(prefix, path.strip())
        return ({alias.strip(): target}, [target]) if target else ({}, [])
    if text.endswith("*"):
        target = _dotted_use_path(prefix, text[:-1].rstrip().rstrip(":"))
        return ({}, [target]) if target else ({}, [])
    target = _dotted_use_path(prefix, text)
    if not target:
        return {}, []
    return {target.rsplit(".", 1)[-1]: target}, [target]


def _use_list(inner: str, prefix: str) -> tuple[dict[str, str], list[str]]:
    aliases: dict[str, str] = {}
    targets: list[str] = []
    for item in _split_top_level_commas(inner):
        item_aliases, item_targets = _use_tree(item, prefix)
        aliases.update(item_aliases)
        targets.extend(item_targets)
    return aliases, targets


def _split_top_level_commas(text: str) -> list[str]:
    """Brace-depth-aware comma split — `use a::{B, c::{D, E}}` keeps nested
    groups intact for the recursive `_use_tree` pass."""
    items: list[str] = []
    depth, start = 0, 0
    for i, ch in enumerate(text):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        elif ch == "," and depth == 0:
            items.append(text[start:i].strip())
            start = i + 1
    tail = text[start:].strip()
    if tail:
        items.append(tail)
    return [item for item in items if item]


def _join_path(prefix: str, head: str) -> str:
    head = head.strip()
    if not head:
        return prefix
    return f"{prefix}::{head}" if prefix else head


def _dotted_use_path(prefix: str, path: str) -> str | None:
    full = _join_path(prefix, path)
    for marker in ("crate::", "self::"):
        full = full.removeprefix(marker)
    while full.startswith("super::"):
        full = full.removeprefix("super::")
    return canonical_target(full)


__all__ = ("RustAnalyzer", "normalize_rust_use")
