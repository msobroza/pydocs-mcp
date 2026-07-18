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

import hashlib
import re
from collections.abc import Iterable, Mapping
from importlib import resources
from pathlib import Path

from pydocs_mcp.exceptions import PydocsMCPError

# Version stamp folded into the artifact hash (ADR 0006 §6): bump on any
# change to how the document is rendered/normalized so equal source bytes
# under a different renderer never collide with an old fingerprint.
RENDERER_VERSION = 1

_PACKAGED_PACKAGE = "pydocs_mcp.defaults"
_PACKAGED_FILENAME = "descriptions.md"

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
SESSION_START_PREAMBLE_HEADER = "SESSION_START_PREAMBLE"

_TOOL_HEADER_TEMPLATE = "TOOL: {name}"


def tool_section_header(tool_name: str) -> str:
    """Return the section header key for a tool name (``"TOOL: grep"``)."""
    return _TOOL_HEADER_TEMPLATE.format(name=tool_name)


# The eleven required product-document sections, in canonical document order
# (ADR 0005 §Decision 2). SESSION_START_PREAMBLE is always required even though the
# session-start-context feature is off by default — the section set stays fixed so
# validation is unconditional.
CANONICAL_HEADERS: tuple[str, ...] = (
    SERVER_INSTRUCTIONS_HEADER,
    *(tool_section_header(name) for name in FROZEN_TOOL_NAMES),
    SESSION_START_PREAMBLE_HEADER,
)

# WHY: single source of truth for the format — render, parse, and the
# collision check all read this one pattern, so the grammar cannot drift.
# The header set is CLOSED: every legal section key across every delimited
# artifact (this product document, the benchmarks tool_docs / ask_prompt
# artifacts) is enumerated here, so a key smuggled into content is promoted
# to a section and rejected as a collision. Widening it is a deliberate
# event — see the header-widening protocol in the module docstring.
_HEADER_RE = re.compile(
    r"^=== (SERVER_INSTRUCTIONS|SYSTEM_PROMPT|REWRITE_PROMPT|SESSION_START_PREAMBLE|TOOL: [a-z_]+) ===$"
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


class StrayContentError(DescriptionSourceError):
    """Strict mode: non-blank content precedes the first section header.

    The lenient parse silently drops such a preamble (e.g. a git-conflict
    marker block) — data loss an explicitly named source must never absorb.
    """

    def __init__(self, *, line: str) -> None:
        self.line = line
        super().__init__(
            f"content before the first section header would be dropped: {line!r} "
            "— the document must start with an '=== <SECTION> ===' header line"
        )


class DuplicateSectionError(DescriptionSourceError):
    """Strict mode: the same section header appears more than once.

    The lenient parse keeps only the last copy (silent data loss); strict
    mode rejects the document naming the duplicated key.
    """

    def __init__(self, *, key: str) -> None:
        self.key = key
        super().__init__(
            f"duplicate section header {key!r} — the later copy would silently "
            "overwrite the earlier one (each section may appear exactly once)"
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
    newline trimmed. With ``allowed`` given (STRICT mode — the product
    loaders), anything the lenient parse would silently lose is a typed
    error: non-blank content before the first header raises
    :class:`StrayContentError`, a repeated header raises
    :class:`DuplicateSectionError`, and a parsed header outside the set
    raises :class:`HeaderCollisionError`. Without ``allowed`` (the
    normalization / benchmarks-delegation mode), parsing stays permissive —
    a leading preamble is dropped and duplicates last-copy-win, because the
    optimizer firewall feeds arbitrary LLM output through it and needs a
    violations tuple back, never a raise.

    Example:
        >>> parse_sections("=== SERVER_INSTRUCTIONS ===\\nhi\\n")
        {'SERVER_INSTRUCTIONS': 'hi'}
    """
    sections = _parse_lines(text, strict=allowed is not None)
    if allowed is not None:
        permitted = tuple(allowed)
        violations = find_header_collisions(sections, allowed=permitted)
        if violations:
            raise HeaderCollisionError(violations, allowed=permitted)
    return sections


def _parse_lines(text: str, *, strict: bool) -> dict[str, str]:
    sections: dict[str, str] = {}
    key: str | None = None
    lines: list[str] = []
    for line in text.split("\n"):
        match = _HEADER_RE.match(line)
        if match is None:
            _consume_body_line(line, key=key, lines=lines, strict=strict)
            continue
        if key is not None:
            sections[key] = _join(lines)
        key, lines = match.group(1), []
        # The earlier same-key section was flushed above (by an intermediate
        # header or by this very match), so membership IS the duplicate probe.
        if strict and key in sections:
            raise DuplicateSectionError(key=key)
    if key is not None:
        sections[key] = _join(lines)
    return sections


def _consume_body_line(line: str, *, key: str | None, lines: list[str], strict: bool) -> None:
    if key is not None:
        lines.append(line)
        return
    if strict and line.strip():
        raise StrayContentError(line=line)


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
    # SESSION_START_PREAMBLE are outside the §D13 per-tool/total lint (the product
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


# --- Packaged document loading, override, and artifact hash (ADR 0006) ---

# WHY: the Phase 0 TOOL_DOCS literals were triple-quoted blocks whose bytes
# end in a newline, and the R6 byte-parity guarantee pins the attribute view
# to those exact bytes — but the grammar's canonical form cannot carry a bare
# trailing newline mid-document (normalize strips it). The terminator is
# re-attached in exactly one place (attribute_views) when a document is
# projected onto the tool_docs module attributes.
_TOOL_DOC_TERMINATOR = "\n"


def attribute_views(sections: Mapping[str, str]) -> tuple[str, dict[str, str], str]:
    """Project a validated section mapping onto the ``tool_docs`` attribute shapes.

    Returns ``(SERVER_INSTRUCTIONS, TOOL_DOCS, SESSION_START_PREAMBLE)``. Both binding
    paths (import-time packaged load and ``apply_source``) go through this one
    projection so the terminator rule above cannot drift between them.

    Example:
        >>> instructions, docs, preamble = attribute_views(load_packaged())  # doctest: +SKIP
    """
    tool_view = {
        name: sections[tool_section_header(name)] + _TOOL_DOC_TERMINATOR
        for name in FROZEN_TOOL_NAMES
    }
    return (
        sections[SERVER_INSTRUCTIONS_HEADER],
        tool_view,
        sections[SESSION_START_PREAMBLE_HEADER],
    )


def load_packaged() -> dict[str, str]:
    """Parse + validate the packaged ``defaults/descriptions.md``.

    The single source ``application/tool_docs.py`` populates its module
    attributes from at import. A failure here is a packaging bug: it raises
    at import of ``tool_docs`` (loud, pre-release, CI-pinned) rather than
    serving a partial surface.

    Example:
        >>> sections = load_packaged()  # doctest: +SKIP
        >>> sections["TOOL: grep"]  # doctest: +SKIP
    """
    text = resources.files(_PACKAGED_PACKAGE).joinpath(_PACKAGED_FILENAME).read_text("utf-8")
    return _parse_and_validate(text, origin=f"packaged {_PACKAGED_FILENAME}")


def apply_source(path: Path) -> str:
    """Load an override document and rebind the live ``tool_docs`` attributes.

    Read → parse → validate → rebind, in that order: validation is a hard
    error (ADR 0006 §4 universal strictness — an explicitly named source must
    never silently degrade to the packaged default) and it happens BEFORE any
    rebinding so a bad document can never half-apply. Returns the new
    :func:`current_artifact_hash`.

    Example:
        >>> apply_source(Path("candidate_descriptions.md"))  # doctest: +SKIP
        'e3b0c44298fc...'
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DescriptionSourceError(
            f"description source {str(path)!r} could not be read: {exc}"
        ) from exc
    sections = _parse_and_validate(text, origin=str(path))

    # WHY function-local: tool_docs imports this module at its own import
    # time (for load_packaged); importing it back at module level would cycle.
    from pydocs_mcp.application import tool_docs

    instructions, tool_view, preamble = attribute_views(sections)
    tool_docs.SERVER_INSTRUCTIONS = instructions
    tool_docs.SESSION_START_PREAMBLE = preamble
    # In-place per-key update: consumers hold references to the TOOL_DOCS dict
    # object (registration reads TOOL_DOCS[name]); rebinding a fresh dict would
    # strand them on the pre-override mapping.
    for name, doc in tool_view.items():
        tool_docs.TOOL_DOCS[name] = doc
    return current_artifact_hash()


def current_artifact_hash() -> str:
    """SHA-256 fingerprint of the description surface actually being served.

    Hashes ``normalize(render_sections(live module attributes))`` plus
    :data:`RENDERER_VERSION` — computed on demand from whatever is bound, so
    it stays truthful under BOTH writers (``apply_source`` and the legacy
    benchmarks wrapper that rebinds the attributes directly). Per the
    one-normalization-pass rule, only the normalized surface is hashed.

    Example:
        >>> current_artifact_hash()  # doctest: +SKIP
        '4f9a1c0d8be2...'
    """
    from pydocs_mcp.application import tool_docs

    return _artifact_hash(
        instructions=tool_docs.SERVER_INSTRUCTIONS,
        tool_view=tool_docs.TOOL_DOCS,
        preamble=tool_docs.SESSION_START_PREAMBLE,
    )


def packaged_artifact_hash() -> str:
    """Fingerprint the PACKAGED document would serve — live attributes untouched.

    Computes over the same attribute projection ``current_artifact_hash``
    uses, so the two are equal exactly when the live surface IS the packaged
    document. ``server.py`` compares them on the no-override startup branch
    to distinguish a genuine packaged serve from a surface some earlier
    caller pre-applied (e.g. the benchmarks overlay wrapper rebinding through
    ``apply_source`` before ``server.run``).

    Example:
        >>> packaged_artifact_hash() == current_artifact_hash()  # doctest: +SKIP
        True
    """
    instructions, tool_view, preamble = attribute_views(load_packaged())
    return _artifact_hash(instructions=instructions, tool_view=tool_view, preamble=preamble)


def _artifact_hash(*, instructions: str, tool_view: Mapping[str, str], preamble: str) -> str:
    sections = {
        SERVER_INSTRUCTIONS_HEADER: instructions,
        **{tool_section_header(name): tool_view[name] for name in FROZEN_TOOL_NAMES},
        SESSION_START_PREAMBLE_HEADER: preamble,
    }
    surface = normalize(render_sections(sections))
    payload = f"renderer:v{RENDERER_VERSION}\n{surface}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_and_validate(text: str, *, origin: str) -> dict[str, str]:
    try:
        sections = parse_sections(text, allowed=CANONICAL_HEADERS)
        validate_sections(sections)
    except DescriptionSourceError as exc:
        exc.add_note(f"description source: {origin}")
        raise
    return sections
