"""Delegation shim over the product's delimited grammar (ADR 0005, spec §D2a).

The grammar itself lives in ``pydocs_mcp.application.description_source`` —
render, parse, and the collision check all read ONE closed header pattern
there, so the product description document and the optimizer artifacts
(``tool_docs``, ``ask_prompt``) can never drift apart. This module keeps the
benchmarks-side names (``render_delimited`` / ``parse_delimited`` /
``find_header_collisions``) as thin wrappers so every existing caller is
untouched.

The benchmarks-only headers (``SYSTEM_PROMPT`` / ``REWRITE_PROMPT``) stay
supported because closed-ness is an *allowed-set* concern, not a grammar
concern: the union regex parses every legal key across every artifact, and
each artifact passes its own ``allowed`` set to ``find_header_collisions``.
A key legal for one artifact (or only for the product document, e.g.
``TURN0_PREAMBLE``) is a header collision for the others — the loud-failure
rule that keeps the format escaping-free.

Round-trip semantics are the product's one-normalization-pass rule: parse
trims exactly the one trailing newline render appends, so ``render(parse(x))``
is stable from the first parse onward and fingerprint consumers hash the
normalized surface. See the product module docstring for the full invariant.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

# Module-level ``pydocs_mcp`` boundary: the grammar IS the product's
# description-source grammar — there is no library-free way to keep the two
# in lockstep. A base install without the [retrieval] extra gets the
# actionable install hint instead of a bare ModuleNotFoundError.
try:
    from pydocs_mcp.application.description_source import (
        find_header_collisions as find_header_collisions,
    )
    from pydocs_mcp.application.description_source import (
        parse_sections as _parse_sections,
    )
    from pydocs_mcp.application.description_source import (
        render_sections as _render_sections,
    )
except ImportError as exc:
    raise_missing_retrieval_extra(exc)

__all__ = ["find_header_collisions", "parse_delimited", "render_delimited"]


def render_delimited(sections: Mapping[str, str]) -> str:
    """Render ``{key: content}`` to the delimited document, in insertion order.

    Delegates to the product grammar's ``render_sections``; see its docstring
    for the exact trailing-newline invariant.

    Example:
        >>> render_delimited({"SERVER_INSTRUCTIONS": "hi"})
        '=== SERVER_INSTRUCTIONS ===\\nhi\\n'
    """
    return _render_sections(sections)


def parse_delimited(text: str) -> dict[str, str]:
    """Parse a delimited document back to ``{key: content}`` in document order.

    Delegates to the product grammar's permissive mode (no ``allowed`` set):
    each artifact applies its own closed set via ``find_header_collisions``,
    which is where an out-of-set header becomes a violation.
    """
    return _parse_sections(text)
