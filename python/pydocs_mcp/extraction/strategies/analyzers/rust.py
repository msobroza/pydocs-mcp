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
    canonical_target,
    capabilities_for,
    capture_named_edges,
    capture_statement_imports,
    open_capture_session,
    register_reference_queries,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.analyzers import LanguageCapabilities
    from pydocs_mcp.extraction.strategies.references import ReferenceCollector

# Single source of the extension literal — registration and the capability
# declaration must never drift apart (a module registered for one extension
# while declaring another's capabilities is a silent, per-deployment lie).
_EXT = ".rs"

_RUST_CALLS_QUERY = """
(call_expression function: (identifier) @callee)
(call_expression function: (scoped_identifier) @callee)
(call_expression function: (field_expression) @callee)
"""

# Both spellings of a trait clause. `impl From<u8> for T` and `trait G: B<C>`
# wrap the name in a `generic_type` node whose `type:` field holds it, so the
# pattern DESCENDS to the inner name — capturing the wrapper whole would emit
# `From<u8>`, which `canonical_target` rejects as a non-identifier chain,
# silently dropping the edge. The two spellings are mutually exclusive per
# clause (a clause is EITHER a generic_type OR a bare (scoped_)type_identifier),
# so no clause is captured twice.
_RUST_INHERITS_QUERY = """
(impl_item trait: (type_identifier) @parent)
(impl_item trait: (scoped_type_identifier) @parent)
(impl_item trait: (generic_type type: (type_identifier) @parent))
(impl_item trait: (generic_type type: (scoped_type_identifier) @parent))
(trait_item bounds: (trait_bounds (type_identifier) @parent))
(trait_item bounds: (trait_bounds (scoped_type_identifier) @parent))
(trait_item bounds: (trait_bounds (generic_type type: (type_identifier) @parent)))
(trait_item bounds: (trait_bounds (generic_type type: (scoped_type_identifier) @parent)))
"""

_RUST_IMPORTS_QUERY = """
(use_declaration) @import
"""

# Every query above joins the grammar loadability probe (ADR 0022): a grammar
# that rejects one degrades `.rs` whole instead of stranding a partial graph.
register_reference_queries((_EXT,), _RUST_CALLS_QUERY, _RUST_INHERITS_QUERY, _RUST_IMPORTS_QUERY)


@register_analyzer(_EXT)
@dataclass(frozen=True, slots=True)
class RustAnalyzer:
    """Tree-sitter syntactic reference backend for Rust."""

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
        _RUST_CALLS_QUERY,
        capture_name="callee",
        kind=ReferenceKind.CALLS,
        from_package=from_package,
        collector=collector,
    )


def _capture_inherits(
    session: CaptureSession, from_package: str, collector: ReferenceCollector
) -> None:
    capture_named_edges(
        session,
        ReferenceQueryRole.INHERITS,
        _RUST_INHERITS_QUERY,
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
        _RUST_IMPORTS_QUERY,
        normalize=normalize_rust_use,
        from_package=from_package,
        collector=collector,
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
    items.append(text[start:].strip())
    return [item for item in items if item]


def _join_path(prefix: str, head: str) -> str:
    head = head.strip()
    if not head:
        return prefix
    return f"{prefix}::{head}" if prefix else head


def _dotted_use_path(prefix: str, path: str) -> str | None:
    # A use-list `self` item names the PREFIX module itself —
    # `use std::fmt::{self, Display}` imports `fmt` — not a member called
    # `self` (which emitted the bogus `self → std.fmt.self` alias + row).
    full = _join_path(prefix, "" if path == "self" else path)
    for marker in ("crate::", "self::"):
        full = full.removeprefix(marker)
    while full.startswith("super::"):
        full = full.removeprefix("super::")
    return canonical_target(full)


__all__ = ("RustAnalyzer", "normalize_rust_use")
