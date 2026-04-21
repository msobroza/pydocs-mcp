"""Shared formatting helpers — single source of truth (spec §5.4, AC #6).

These helpers are the canonical rendering code for pydocs-mcp search output.
They are called from:

- ``retrieval.stages.TokenBudgetFormatterStage`` — wraps result as a
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

# Approximate characters per token (conservative estimate for English text).
# This module is the single source of truth for the ratio — ``TokenBudgetFormatterStage``
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
