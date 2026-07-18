"""Product grammar for the externalized description source (ADR 0005).

One delimited text document holds every optimizable description string:
``=== SECTION ===`` header lines introduce sections; a section's content is
every following line up to the next header or EOF, with the single trailing
newline ``render_sections`` appends trimmed back off by ``parse_sections``.
The header set is CLOSED (``_HEADER_RE``): a content line that itself looks
like a legal header is *promoted to a section* by the parser and rejected as
a collision (``find_header_collisions``) — never escaped. That loud-failure
rule is what keeps the format escaping-free and is why delimiters beat
markdown headings (ADR 0005 §Decision 4).

**One-normalization-pass rule (load-bearing invariant).** ``parse_sections``
trims exactly one trailing newline per section — the one ``render_sections``
appends. So ``render → parse → render`` is NOT byte-stable on the first pass
when a section's content already ended in a newline; it IS idempotent after
that first pass: ``normalize(normalize(text)) == normalize(text)``. Every
fingerprint consumer MUST hash the normalized surface (one ``normalize``
call), never the raw input — hashing raw text makes equal surfaces compare
unequal across passes.

**Header-widening protocol.** The grammar is shared by every delimited
artifact (this product document AND the benchmarks optimizer artifacts, which
delegate here). Adding a new section kind requires: (1) widen the one
``_HEADER_RE`` alternation below; (2) extend each artifact's *allowed* set —
the product's ``CANONICAL_HEADERS`` here, the benchmarks artifacts' sets on
their side. A key present in the regex but absent from an artifact's allowed
set is parseable but rejected for that artifact — which is exactly how the
product document firewalls the benchmarks-only ``SYSTEM_PROMPT`` /
``REWRITE_PROMPT`` keys.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from pydocs_mcp.exceptions import PydocsMCPError

# --- Contract constants (canonical home; application/tool_docs.py re-exports
# them because the benchmarks optimizer's validate() and the §D13 lint import
# from there) ---
REQUIRED_MARKERS = (
    "When to use",
    "When NOT to use",
    "Workflow",
    "Response contract",
    "Examples",
)
CHARS_PER_TOKEN = 4
PER_TOOL_TOKEN_BUDGET = 500
TOTAL_TOKEN_BUDGET = 3600

# The nine frozen tool names of docs/tool-contracts.md §1, in contract order.
# Section IDs derive from these, so a tool rename can never silently orphan a
# section — it surfaces as an unknown-header collision instead.
FROZEN_TOOL_NAMES = (
    "get_overview",
    "search_codebase",
    "get_symbol",
    "get_context",
    "get_references",
    "get_why",
    "grep",
    "glob",
    "read_file",
)

SERVER_INSTRUCTIONS_HEADER = "SERVER_INSTRUCTIONS"
TURN0_PREAMBLE_HEADER = "TURN0_PREAMBLE"

_TOOL_HEADER_TEMPLATE = "TOOL: {name}"


def tool_section_header(tool_name: str) -> str:
    """Return the section header key for a tool name (``"TOOL: grep"``)."""
    return _TOOL_HEADER_TEMPLATE.format(name=tool_name)


# The eleven required product-document sections, in canonical document order
# (ADR 0005 §Decision 2). TURN0_PREAMBLE is always required even though the
# turn-0 feature is off by default — the section set stays fixed so
# validation is unconditional.
CANONICAL_HEADERS: tuple[str, ...] = (
    SERVER_INSTRUCTIONS_HEADER,
    *(tool_section_header(name) for name in FROZEN_TOOL_NAMES),
    TURN0_PREAMBLE_HEADER,
)

# WHY: single source of truth for the format — render, parse, and the
# collision check all read this one pattern, so the grammar cannot drift.
# The header set is CLOSED: every legal section key across every delimited
# artifact (this product document, the benchmarks tool_docs / ask_prompt
# artifacts) is enumerated here, so a key smuggled into content is promoted
# to a section and rejected as a collision. Widening it is a deliberate
# event — see the header-widening protocol in the module docstring.
_HEADER_RE = re.compile(
    r"^=== (SERVER_INSTRUCTIONS|SYSTEM_PROMPT|REWRITE_PROMPT|TURN0_PREAMBLE|TOOL: [a-z_]+) ===$"
)


class DescriptionSourceError(PydocsMCPError, ValueError):
    """Root for every description-source grammar / validation failure.

    Inherits ``ValueError`` so callers treating a bad document as invalid
    input keep their existing ``except ValueError`` handling.
    """


class HeaderCollisionError(DescriptionSourceError):
    """A section header outside the allowed set (smuggled or unknown)."""

    def __init__(self, violations: tuple[str, ...], *, allowed: tuple[str, ...]) -> None:
        self.violations = violations
        self.allowed = allowed
        super().__init__(
            f"{len(violations)} header collision(s): "
            + "; ".join(violations)
            + f" — allowed headers: {list(allowed)}"
        )


class MissingSectionError(DescriptionSourceError):
    """A required section is absent from the document."""

    def __init__(self, *, missing: tuple[str, ...], expected: tuple[str, ...]) -> None:
        self.missing = missing
        self.expected = expected
        super().__init__(
            f"missing required section(s) {list(missing)} — the document must "
            f"contain exactly: {list(expected)}"
        )


class MissingMarkerError(DescriptionSourceError):
    """A TOOL section body lacks one or more required markers."""

    def __init__(self, *, section: str, missing_markers: tuple[str, ...]) -> None:
        self.section = section
        self.missing_markers = missing_markers
        super().__init__(
            f"section {section!r} missing required marker(s) "
            f"{list(missing_markers)} — every TOOL section must contain all "
            f"of: {list(REQUIRED_MARKERS)}"
        )


class TokenBudgetExceededError(DescriptionSourceError):
    """A TOOL section (or the whole surface) exceeds its token budget."""

    def __init__(self, *, section: str | None, tokens: int, budget: int) -> None:
        self.section = section
        self.tokens = tokens
        self.budget = budget
        scope = f"section {section!r}" if section is not None else "surface total"
        super().__init__(
            f"{scope}: {tokens} tokens > {budget} budget "
            f"(estimated at {CHARS_PER_TOKEN} chars/token)"
        )


def render_sections(sections: Mapping[str, str]) -> str:
    """Render ``{key: content}`` to the delimited document, in insertion order.

    Each section is ``=== <key> ===`` followed by its content and one trailing
    newline. ``parse_sections`` trims exactly that one newline, so the pair is
    idempotent after the first normalization pass — but NOT byte-stable on the
    first pass when a section's content already ended in a newline; see the
    one-normalization-pass rule in the module docstring.

    Example:
        >>> render_sections({"SERVER_INSTRUCTIONS": "hi"})
        '=== SERVER_INSTRUCTIONS ===\\nhi\\n'
    """
    return "".join(f"=== {key} ===\n{content}\n" for key, content in sections.items())


def parse_sections(text: str, *, allowed: Iterable[str] | None = None) -> dict[str, str]:
    """Parse a delimited document back to ``{key: content}`` in document order.

    Content is every line until the next header or EOF with a single trailing
    newline trimmed. A leading non-header preamble is dropped (there is none
    in a well-formed document — ``validate_sections`` catches malformed input
    upstream). With ``allowed`` given, any parsed header outside that set
    raises :class:`HeaderCollisionError` — the strict mode loaders use;
    without it, parsing is permissive (the normalization / delegation mode).

    Example:
        >>> parse_sections("=== SERVER_INSTRUCTIONS ===\\nhi\\n")
        {'SERVER_INSTRUCTIONS': 'hi'}
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
    if allowed is not None:
        permitted = tuple(allowed)
        violations = find_header_collisions(sections, allowed=permitted)
        if violations:
            raise HeaderCollisionError(violations, allowed=permitted)
    return sections


def find_header_collisions(
    sections: Mapping[str, str], *, allowed: Iterable[str]
) -> tuple[str, ...]:
    """Return a violation for any section header outside ``allowed``.

    A header-like line embedded in a section's content does not survive
    ``parse_sections`` as content — the parser promotes it to its own section.
    So a candidate that smuggles ``=== TOOL: fake ===`` into a description
    shows up here as an unexpected header key, never as a content line. Naming
    it a *header collision* (rather than escaping it) is exactly what keeps
    the format escaping-free: the caller passes the closed set of legal
    headers and anything else is rejected.
    """
    permitted = set(allowed)
    return tuple(
        f"unexpected section header {key!r} (header-like line where none is allowed)"
        for key in sections
        if key not in permitted
    )


def normalize(text: str) -> str:
    """One ``parse`` → ``render`` pass: the canonical byte surface.

    Idempotent: ``normalize(normalize(text)) == normalize(text)``. This is the
    surface every fingerprint consumer hashes (one-normalization-pass rule in
    the module docstring); hashing un-normalized text makes equal documents
    compare unequal across passes.
    """
    return render_sections(parse_sections(text))


def validate_sections(sections: Mapping[str, str]) -> None:
    """R5 drift check: raise unless ``sections`` is a valid product document.

    Checks, in order (first failing category raises with every violation of
    that category attached): (1) no headers outside ``CANONICAL_HEADERS``
    (covers renamed tools and smuggled headers), (2) all eleven canonical
    sections present, (3) every TOOL section carries the five
    ``REQUIRED_MARKERS``, (4) token budgets — per-TOOL-section and the
    TOOL-surface total, mirroring the §D13 lint exactly.

    Example:
        >>> validate_sections(parse_sections(document_text))  # doctest: +SKIP
    """
    collisions = find_header_collisions(sections, allowed=CANONICAL_HEADERS)
    if collisions:
        raise HeaderCollisionError(collisions, allowed=CANONICAL_HEADERS)
    missing = tuple(header for header in CANONICAL_HEADERS if header not in sections)
    if missing:
        raise MissingSectionError(missing=missing, expected=CANONICAL_HEADERS)
    _check_required_markers(sections)
    _check_token_budgets(sections)


def _check_required_markers(sections: Mapping[str, str]) -> None:
    for name in FROZEN_TOOL_NAMES:
        header = tool_section_header(name)
        absent = tuple(marker for marker in REQUIRED_MARKERS if marker not in sections[header])
        if absent:
            raise MissingMarkerError(section=header, missing_markers=absent)


def _check_token_budgets(sections: Mapping[str, str]) -> None:
    # Budgets cover the TOOL sections only — SERVER_INSTRUCTIONS and
    # TURN0_PREAMBLE are outside the §D13 per-tool/total lint (the product
    # lint in tests/application/test_tool_docs_lint.py sums TOOL_DOCS alone,
    # and this check must never disagree with it).
    total = 0
    for name in FROZEN_TOOL_NAMES:
        header = tool_section_header(name)
        tokens = len(sections[header]) // CHARS_PER_TOKEN
        total += tokens
        if tokens > PER_TOOL_TOKEN_BUDGET:
            raise TokenBudgetExceededError(
                section=header, tokens=tokens, budget=PER_TOOL_TOKEN_BUDGET
            )
    if total > TOTAL_TOKEN_BUDGET:
        raise TokenBudgetExceededError(section=None, tokens=total, budget=TOTAL_TOKEN_BUDGET)


def _join(lines: list[str]) -> str:
    # ``split("\n")`` on a section that ended with the rendered trailing
    # newline leaves a final empty element; drop exactly one to trim that one
    # newline back off. Content that was genuinely multi-line keeps its shape.
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return "\n".join(lines)
