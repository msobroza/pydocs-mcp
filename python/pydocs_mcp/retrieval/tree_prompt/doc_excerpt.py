"""Docstring-excerpt extraction for the LLM-visible project tree.

Pure Google / NumPy / Sphinx docstring parsing with a hard char cap.
Extracted from the ``llm_tree_reasoning`` step so the docstring parser
(the most likely home of future format bugs) has its own file and reason
to change. The step imports the ``DEFAULT_*`` constants for its field
defaults / YAML codec, preserving the single source of truth.
"""

from __future__ import annotations

from pydocs_mcp.extraction.strategies.chunkers._shared import _collapse_ws

# Docstring excerpt depth fed to the LLM per node. "sections" = first line +
# Args/Returns/Raises blocks (best discriminator-per-token); "full" = whole
# docstring (bounded); "off" = no doc field. YAML-tunable per deployment.
DEFAULT_DOC_EXCERPT = "sections"
DEFAULT_DOC_EXCERPT_MAX_CHARS = 240
DOC_EXCERPT_MODES = ("sections", "full", "off")
# Section markers the "sections" doc excerpt recognizes (Google + NumPy
# headers, matched case-insensitively).
DOC_SECTION_HEADERS = frozenset(
    {
        "args",
        "arguments",
        "parameters",
        "params",
        "returns",
        "return",
        "yields",
        "yield",
        "raises",
        "raise",
    }
)
# Sphinx / reST field-list prefixes (one field per line) the excerpt keeps.
SPHINX_FIELD_PREFIXES = (
    ":param",
    ":parameter",
    ":returns",
    ":return",
    ":rtype",
    ":raises",
    ":raise",
    ":yields",
    ":yield",
    ":type",
)


def doc_sections(text: str) -> str:
    """First line + parameter / return / raise blocks (Google/NumPy/Sphinx)."""
    lines = text.splitlines()
    kept: list[str] = []
    first = lines[0].strip()
    if first:
        kept.append(first)
    in_section = False
    i = 1
    while i < len(lines):
        stripped = lines[i].strip()
        head_word = stripped.rstrip(":").strip().lower()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        next_is_underline = set(nxt) == {"-"} and len(nxt) >= 3
        is_underline = set(stripped) == {"-"} and len(stripped) >= 3
        is_google_header = head_word in DOC_SECTION_HEADERS and stripped.endswith(":")
        is_numpy_header = head_word in DOC_SECTION_HEADERS and next_is_underline
        is_sphinx = any(stripped.lower().startswith(p) for p in SPHINX_FIELD_PREFIXES)
        if is_underline:
            # Dashes belong to the header on the preceding line. A RECOGNIZED
            # NumPy header already toggled capture via is_numpy_header; an
            # UNRECOGNIZED one (Notes / Examples / See Also / a bare rule) must
            # NOT turn capture on, or its low-signal body leaks in. So just
            # skip the underline either way — never toggle, never append.
            i += 1
            continue
        if is_google_header or is_numpy_header:
            in_section = True
            kept.append(stripped)
        elif is_sphinx:
            kept.append(stripped)
            in_section = False
        elif in_section:
            if not stripped:
                in_section = False
            else:
                kept.append(stripped)
        i += 1
    return " ".join(kept)


def doc_excerpt(docstring: str, mode: str, max_chars: int) -> str:
    """Bounded docstring excerpt for the LLM-visible node.

    ``"off"`` → empty. ``"full"`` → the whole docstring, whitespace-
    collapsed. ``"sections"`` (and any unknown mode) → the first line plus
    the Args/Parameters/Returns/Yields/Raises blocks (Google + NumPy headers
    and Sphinx ``:param:``-style field lists) — the author's own words about
    inputs/outputs beyond the 140-char summary. Always capped at
    ``max_chars``.
    """
    excerpt, _ = doc_excerpt_with_flag(docstring, mode, max_chars)
    return excerpt


def doc_excerpt_with_flag(docstring: str, mode: str, max_chars: int) -> tuple[str, bool]:
    """Like :func:`doc_excerpt`, but also report whether the cap truncated it.

    The boolean lets the renderer surface one aggregated warning per query
    when emitted excerpts were cut — mirroring the ``max_tree_tokens``
    over-budget warning — instead of silently dropping docstring content.
    """
    if not docstring or mode == "off":
        return "", False
    text = docstring.strip()
    if not text:
        return "", False
    # Clamp so a non-positive cap can't become a tail-dropping negative slice;
    # the "always bounded (0 -> '')" contract holds for any caller.
    cap = max(0, max_chars)
    full = _collapse_ws(text) if mode == "full" else _collapse_ws(doc_sections(text))
    return full[:cap], len(full) > cap


__all__ = (
    "DEFAULT_DOC_EXCERPT",
    "DEFAULT_DOC_EXCERPT_MAX_CHARS",
    "DOC_EXCERPT_MODES",
    "DOC_SECTION_HEADERS",
    "SPHINX_FIELD_PREFIXES",
    "doc_excerpt",
    "doc_excerpt_with_flag",
    "doc_sections",
)
