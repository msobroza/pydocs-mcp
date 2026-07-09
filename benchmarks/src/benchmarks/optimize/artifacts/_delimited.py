"""The shared delimited document format (spec §D2a).

``render()``/``with_content()``/the §D6 overlay all speak this one grammar:
line-delimited, key-order-preserved, and escaping-free *by construction*. A
section is introduced by a header line matching ``_HEADER_RE``; its content is
every following line up to the next header or EOF, with a single trailing
newline trimmed. Because a content line that itself looks like a header is a
declared violation (``find_header_collisions``), no escaping is ever needed —
that rule is the whole reason the format stays escaping-free.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

# WHY: single source of truth for the format — render, parse, and the
# collision check all read this one pattern, so the grammar cannot drift.
_HEADER_RE = re.compile(r"^=== (SERVER_INSTRUCTIONS|TOOL: [a-z_]+) ===$")


def render_delimited(sections: Mapping[str, str]) -> str:
    """Render ``{key: content}`` to the delimited document, in insertion order.

    Each section is ``=== <key> ===`` followed by its content and one trailing
    newline (the newline ``parse_delimited`` trims), so ``render → parse →
    render`` is byte-stable.

    Example:
        >>> render_delimited({"SERVER_INSTRUCTIONS": "hi"})
        '=== SERVER_INSTRUCTIONS ===\\nhi\\n'
    """
    return "".join(f"=== {key} ===\n{content}\n" for key, content in sections.items())


def parse_delimited(text: str) -> dict[str, str]:
    """Parse a delimited document back to ``{key: content}`` in document order.

    Content is every line until the next header or EOF with a single trailing
    newline trimmed. A leading non-header preamble is dropped (there is none in
    a well-formed document — ``validate()`` catches malformed input upstream).
    """
    sections: dict[str, str] = {}
    key: str | None = None
    lines: list[str] = []
    for line in text.split("\n"):
        match = _HEADER_RE.match(line)
        if match is not None:
            if key is not None:
                sections[key] = _join(lines)
            key, lines = match.group(1), []
            continue
        if key is not None:
            lines.append(line)
    if key is not None:
        sections[key] = _join(lines)
    return sections


def find_header_collisions(
    sections: Mapping[str, str], *, allowed: Iterable[str]
) -> tuple[str, ...]:
    """Return a violation for any section header outside ``allowed``.

    A header-like line embedded in a section's content does not survive
    ``parse_delimited`` as content — the parser promotes it to its own section.
    So a candidate that smuggles ``=== TOOL: fake ===`` into a description
    shows up here as an unexpected header key, never as a content line. Naming
    it a *header collision* (rather than escaping it) is exactly what keeps the
    format escaping-free: the caller passes the closed set of legal headers and
    anything else is rejected.
    """
    permitted = set(allowed)
    return tuple(
        f"unexpected section header {key!r} (header-like line where none is allowed)"
        for key in sections
        if key not in permitted
    )


def _join(lines: list[str]) -> str:
    # ``split("\n")`` on a section that ended with the rendered trailing
    # newline leaves a final empty element; drop exactly one to trim that one
    # newline back off. Content that was genuinely multi-line keeps its shape.
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return "\n".join(lines)
