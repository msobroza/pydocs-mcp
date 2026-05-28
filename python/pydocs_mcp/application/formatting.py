"""Shared formatting helpers — single source of truth (spec §5.4, AC #6).

These helpers are the canonical rendering code for pydocs-mcp search output.
They are called from:

- ``retrieval.steps.TokenBudgetStep`` — wraps result as a
  composite ``Chunk`` with ``ChunkOrigin.COMPOSITE_OUTPUT`` origin.
- MCP handler fallback paths in ``server.py`` — when the pipeline config
  omits the formatter stage, the handler renders the raw result itself.
- CLI ``query`` / ``api`` subcommands in ``__main__.py`` — stdout rendering
  (via the composite chunk text produced by the formatter stage).

Byte-parity contract (sub-PR #2 AC #21, sub-PR #4 AC #6):
  - Each block is ``"## {title}\\n{body}\\n"`` with a SINGLE ``\\n`` between
    heading and body (NO blank line after the heading).
  - Blocks are joined with ``"\\n"`` so CONSECUTIVE blocks are separated by
    a blank line: ``"## A\\nbody\\n\\n## B\\nbody\\n"``.
  - The trailing ``\\n`` of the last block is preserved — NO ``rstrip()``
    anywhere in this module.
  - The 100-char remainder gate: if the next piece does not fit but
    ``max_chars - total > 100`` chars remain, the piece is truncated and
    appended; otherwise nothing extra is emitted.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydocs_mcp.constants import (
    LIST_PACKAGES_MAX,
    PACKAGE_DOC_LINE_MAX,
    PACKAGE_DOC_MAX,
    REQUIREMENTS_DISPLAY,
)
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageDoc,
)

if TYPE_CHECKING:
    from pydocs_mcp.models import SearchResponse
    from pydocs_mcp.storage.node_reference import NodeReference

# Approximate characters per token (conservative estimate for English text).
# This module is the single source of truth for the ratio — ``TokenBudgetStep``
# and the pre-sub-PR-2 ``search.format_within_budget`` both used the same value (4).
_CHARS_PER_TOKEN = 4

# Truncation gate: if fewer chars than this remain in the budget, we do NOT
# emit a partial piece at all (the old ``format_within_budget`` behaviour).
_TRUNCATION_MIN_REMAINDER = 100


def format_chunks_markdown_within_budget(
    chunks: tuple[Chunk, ...],
    budget_tokens: int,
) -> str:
    """Render chunks as ``## {title}\\n{text}\\n`` blocks within a char budget.

    The byte layout is identical to the pre-sub-PR-2 ``format_within_budget``
    in ``search.py``: pieces are joined with ``"\\n"``, so between consecutive
    blocks there is a blank line. Trailing newline is preserved.

    Args:
        chunks: Ordered chunks (best first).
        budget_tokens: Rough budget; multiplied by 4 to get a char cap.

    Returns:
        Concatenated markdown. Empty string when ``chunks`` is empty.
    """
    max_chars = budget_tokens * _CHARS_PER_TOKEN
    parts: list[str] = []
    total = 0
    for chunk in chunks:
        title = chunk.metadata.get(ChunkFilterField.TITLE.value, "") or ""
        text = chunk.text or ""
        piece = f"## {title}\n{text}\n"
        if total + len(piece) > max_chars:
            remaining = max_chars - total
            if remaining > _TRUNCATION_MIN_REMAINDER:
                parts.append(piece[:remaining])
            break
        parts.append(piece)
        total += len(piece)
    return "\n".join(parts)


def format_packages_list(packages: tuple[Package, ...]) -> str:
    """Render a sorted bullet list ``- name version — summary``.

    Byte-parity with pre-#6 ``server.py::list_packages`` (sub-PR #4 §5.1).
    Cap at ``LIST_PACKAGES_MAX`` packages.
    """
    sorted_pkgs = sorted(packages[:LIST_PACKAGES_MAX], key=lambda p: p.name)
    return "\n".join(
        f"- {p.name} {p.version} — {p.summary}" for p in sorted_pkgs
    )


def format_package_doc(doc: PackageDoc) -> str:
    """Render a ``PackageDoc`` as the pre-#6 ``get_package_doc`` markdown.

    Byte-parity with sub-PR #4 ``server.py::_render_package_doc`` (AC #6):
    blocks joined with ``"\\n\\n"``, capped at ``PACKAGE_DOC_MAX`` chars.
    """
    pkg = doc.package
    parts = [f"# {pkg.name} {pkg.version}\n{pkg.summary}"]
    if pkg.homepage:
        parts.append(f"Homepage: {pkg.homepage}")
    if pkg.dependencies:
        parts.append("Deps: " + ", ".join(pkg.dependencies[:REQUIREMENTS_DISPLAY]))

    for c in doc.chunks:
        title = c.metadata.get(ChunkFilterField.TITLE.value, "")
        parts.append(f"## {title}\n{c.text}")

    if doc.members:
        rendered: list[str] = []
        for m in doc.members:
            md = m.metadata
            kind = md.get(ModuleMemberFilterField.KIND.value, "")
            name = md.get(ModuleMemberFilterField.NAME.value, "")
            signature = md.get("signature", "")
            docstring = str(md.get("docstring", "") or "")
            first_line = docstring.split("\n")[0][:PACKAGE_DOC_LINE_MAX]
            rendered.append(f"- `{kind} {name}{signature}` — {first_line}")
        parts.append("## API\n" + "\n".join(rendered))
    return "\n\n".join(parts)[:PACKAGE_DOC_MAX]


def format_members_markdown_within_budget(
    members: tuple[ModuleMember, ...],
    budget_tokens: int,
) -> str:
    """Render module members as ``**[pkg] mod.name{sig}** ({kind})\\n{doc}\\n``
    within a char budget.

    Same byte-parity contract as :func:`format_chunks_markdown_within_budget`:
    pieces are ``"\\n".join``-ed, so between blocks there is a blank line.
    """
    max_chars = budget_tokens * _CHARS_PER_TOKEN
    parts: list[str] = []
    total = 0
    for member in members:
        md = member.metadata
        pkg = md.get(ModuleMemberFilterField.PACKAGE.value, "") or ""
        module = md.get(ModuleMemberFilterField.MODULE.value, "") or ""
        name = md.get(ModuleMemberFilterField.NAME.value, "") or ""
        kind = md.get(ModuleMemberFilterField.KIND.value, "") or ""
        signature = md.get("signature", "") or ""
        docstring = md.get("docstring", "") or ""
        header = f"**[{pkg}] {module}.{name}{signature}** ({kind})"
        piece = f"{header}\n{docstring}\n"
        if total + len(piece) > max_chars:
            remaining = max_chars - total
            if remaining > _TRUNCATION_MIN_REMAINDER:
                parts.append(piece[:remaining])
            break
        parts.append(piece)
        total += len(piece)
    return "\n".join(parts)


# Per-``show`` rendering vocabulary (spec §5.7, appendix §A.1):
#   - H1 phrasing differs per question ("Callers/Callees of X" / "Bases of X").
#   - Group-header noun gets singular/plural ("caller" vs "callers").
# Keeping these as a single table avoids ad-hoc conditionals at three
# call sites and makes the §A.1 shape one edit away if the vocabulary
# changes (e.g., MENTIONS → "Mentions of X").
_SHOW_VOCAB: dict[str, tuple[str, str]] = {
    "callers":  ("Callers of", "caller"),
    "callees":  ("Callees of", "callee"),
    "inherits": ("Bases of",   "base"),
}


def format_references(
    rows: tuple[NodeReference, ...],
    *,
    target: str,
    show: Literal["callers", "callees", "inherits"],
    limit: int,
) -> str:
    """Render reference rows as markdown for the ``lookup`` MCP tool.

    Spec §5.7 + appendix §A.1. Single source of truth for callers/callees/
    inherits rendering; the MCP handler and CLI both delegate here.

    Shape:
      - H1 = ``# {Callers|Callees|Bases} of `target` ``
      - Lead summary: ``N references found (R resolved, U unresolved).``
      - H2 groups by ``from_package`` in first-seen order
      - Within each group: resolved rows first (``to_node_id is not None``)
      - Row format: ``- `from_node_id` → `to_node_id` `` for resolved,
        ``- ⚠ `from_node_id` → `to_name` *(unresolved — to_name didn't
        match any indexed qname)*`` for unresolved
      - Empty rows → header + ``No {caller|callee|base}s found.``

    Args:
        rows: Reference rows for the target (already filtered to this
              ``show`` direction by ``ReferenceService``).
        target: Display name (the qualified name asked about).
        show: ``"callers"`` / ``"callees"`` / ``"inherits"`` — controls
              H1 wording and the singular/plural noun in group headers.
        limit: The limit value used; rendered in lead only when truncation
               is detectable from ``len(rows) == limit``. The argument is
               accepted for API symmetry with the service (caller passes
               whatever bound came from MCP); we do NOT re-truncate here.

    Returns:
        UTF-8 markdown string. Always ends with a single trailing ``\\n``.
    """
    title_verb, noun = _SHOW_VOCAB[show]
    h1 = f"# {title_verb} `{target}`\n"

    if not rows:
        # Empty path: still emit the H1 + body so downstream parsers see
        # a consistent shape. The body sentence pluralizes the noun.
        return f"{h1}\nNo {noun}s found.\n"

    resolved_count = sum(1 for r in rows if r.to_node_id is not None)
    unresolved_count = len(rows) - resolved_count
    lead = (
        f"{len(rows)} references found "
        f"({resolved_count} resolved, {unresolved_count} unresolved).\n"
    )

    # Group by from_package preserving FIRST-SEEN order — appendix §A.1's
    # example renders packages in the order they appear in ``rows``.
    groups: dict[str, list[NodeReference]] = {}
    for r in rows:
        groups.setdefault(r.from_package, []).append(r)

    blocks: list[str] = [h1, lead]
    for pkg, refs in groups.items():
        # Resolved-first within each group; stable on from_node_id for
        # deterministic output across runs.
        refs_sorted = sorted(
            refs,
            key=lambda r: (0 if r.to_node_id is not None else 1, r.from_node_id),
        )
        count = len(refs_sorted)
        plural = "" if count == 1 else "s"
        blocks.append(f"\n## from `{pkg}` ({count} {noun}{plural})\n\n")
        for r in refs_sorted:
            if r.to_node_id is not None:
                blocks.append(f"- `{r.from_node_id}` → `{r.to_node_id}`\n")
            else:
                blocks.append(
                    f"- ⚠ `{r.from_node_id}` → `{r.to_name}` "
                    f"*(unresolved — to_name didn't match any indexed qname)*\n"
                )
    return "".join(blocks)


# Default empty-state message for ``render_top_composite``. Single source of
# truth so both server.py (kind='docs', kind='api') and __main__.py share
# the same wording when no override is supplied.
_DEFAULT_EMPTY_MSG = "No results."


def render_top_composite(
    response: SearchResponse,
    empty_msg: str = _DEFAULT_EMPTY_MSG,
) -> str:
    """Collapse a :class:`SearchResponse` to a single rendered string.

    The retrieval pipeline's ``TokenBudgetStep`` wraps the final output as a
    single composite chunk at ``items[0]``, so reading its ``.text`` is the
    contract for "the rendered body". Both the MCP server (``server.py``) and
    the CLI (``__main__.py``) need that collapse on every search; this helper
    is the single source of truth.

    Args:
        response: ``SearchResponse`` from a chunk or member pipeline. When
            ``response.result`` is ``None`` or its ``items`` tuple is empty,
            the pipeline produced nothing renderable.
        empty_msg: Returned verbatim when the response is empty. Callers
            customize this for the MCP surface (``"No matches found."`` /
            ``"No symbols found."``) or pass the empty string when joining
            multiple responses (the ``kind="any"`` search path).

    Returns:
        ``response.result.items[0].text`` if a composite is present,
        otherwise ``empty_msg``.
    """
    result = response.result
    if result is None or not result.items:
        return empty_msg
    return result.items[0].text
