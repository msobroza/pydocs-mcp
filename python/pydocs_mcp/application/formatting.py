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
    appended; otherwise nothing extra is emitted. ``format_context``
    historically used ``>=`` (partial IS emitted at exactly 100 chars
    remaining); ``_take_within_budget`` preserves both gates verbatim via
    ``inclusive_gate`` — pinned by boundary tests, to be unified only by a
    deliberate decision.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from math import ceil
from typing import TYPE_CHECKING, Literal

from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger
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
from pydocs_mcp.retrieval.config.models import _DEFAULT_SKELETON_BODY_RATIO

if TYPE_CHECKING:
    from pydocs_mcp.application.overview_service import OverviewCard
    from pydocs_mcp.application.reference_service import ContextNode, ImpactNode
    from pydocs_mcp.models import SearchResponse
    from pydocs_mcp.storage.node_reference import NodeReference

# Approximate characters per token (conservative estimate for English text).
# This module is the single source of truth for the ratio — ``TokenBudgetStep``
# and the pre-sub-PR-2 ``search.format_within_budget`` both used the same value (4).
_CHARS_PER_TOKEN = 4

# Truncation gate: if fewer chars than this remain in the budget, we do NOT
# emit a partial piece at all (the old ``format_within_budget`` behaviour).
_TRUNCATION_MIN_REMAINDER = 100

# Next-step pointers (spec §D5). Renderers emit surface-NEUTRAL tokens —
# the pipeline that renders hits cannot know whether the response will leave
# via MCP or the CLI, so the ResponseEnvelope resolves tokens at the router
# layer. Token payloads are dotted names / show-mode words (no ':' or ']]'),
# which keeps the grammar regex-parsable.
#
# The ``overview`` action is the zero-hit-search recovery step (spec §D1 empty
# contract): it takes an EMPTY target (``[[next:overview:]]``) because
# get_overview scopes to a package, not a symbol — the target group is ``*``
# (not ``+``) so the empty payload parses.
_POINTER_RE = re.compile(
    r"\[\[next:(lookup|lookup-show|search|overview):([^:\]]*)(?::([^:\]]+))?\]\]"
)

# show-mode → (mcp renderer, cli renderer). context maps to a one-element
# get_context batch; tree/default stay on get_symbol via depth.
_SHOW_TO_TOOL: dict[str, tuple[str, str]] = {
    "callers": (
        'get_references(target="{t}", direction="callers")',
        "pydocs-mcp refs {t} --direction callers",
    ),
    "callees": (
        'get_references(target="{t}", direction="callees")',
        "pydocs-mcp refs {t} --direction callees",
    ),
    "inherits": (
        'get_references(target="{t}", direction="inherits")',
        "pydocs-mcp refs {t} --direction inherits",
    ),
    "impact": (
        'get_references(target="{t}", direction="impact")',
        "pydocs-mcp refs {t} --direction impact",
    ),
    "context": ('get_context(targets=["{t}"])', "pydocs-mcp context {t}"),
    "tree": ('get_symbol(target="{t}", depth="tree")', "pydocs-mcp symbol {t} --depth tree"),
    "source": ('get_symbol(target="{t}", depth="source")', "pydocs-mcp symbol {t} --depth source"),
}


def pointer_token(action: str, target: str, show: str = "") -> str:
    """Build a surface-neutral next-step token. ``show`` only for lookup-show."""
    if action == "lookup-show":
        return f"[[next:lookup-show:{target}:{show}]]"
    return f"[[next:{action}:{target}]]"


def _render_pointer(match: re.Match[str], surface: str) -> str:
    action, target, show = match.group(1), match.group(2), match.group(3)
    if action == "overview":
        # Empty-target action: get_overview scopes to a package, so the
        # zero-hit-search recovery pointer takes no argument.
        return "→ pydocs-mcp overview" if surface == "cli" else "→ get_overview()"
    if action == "search":
        if surface == "cli":
            return f'→ pydocs-mcp search "{target}"'
        return f'→ search_codebase(query="{target}")'
    if action == "lookup-show":
        mcp_fmt, cli_fmt = _SHOW_TO_TOOL[show]
        fmt = cli_fmt if surface == "cli" else mcp_fmt
        return "→ " + fmt.format(t=target)
    if surface == "cli":
        return f"→ pydocs-mcp symbol {target}"
    return f'→ get_symbol(target="{target}")'


def resolve_pointers(text: str, surface: str) -> str:
    """Rewrite every pointer token to ``surface`` syntax ("mcp" | "cli")."""
    return _POINTER_RE.sub(lambda m: _render_pointer(m, surface), text)


def strip_pointers(text: str) -> str:
    """Remove pointer tokens AND their line ending — restores pre-§D5 bytes."""
    return re.sub(r"\[\[next:[^\]]*\]\]\n?", "", text)


def _take_within_budget(
    pieces: Iterable[str],
    max_chars: int,
    *,
    start_total: int = 0,
    inclusive_gate: bool = False,
    on_elide: Callable[[int], TruncationEntry | None] | None = None,
) -> list[str]:
    """Accumulate ``pieces`` until ``max_chars``; truncate the overflow piece.

    Single source of truth for the budget loop the module header pins.
    Joining stays with the caller — and so does whether separators count
    toward the budget: the chunk/member formatters join with ``"\\n"``
    WITHOUT charging the separators, ``format_context`` joins with ``""``
    and pre-charges its header via ``start_total``. Both quirks are part
    of the byte-parity contract.

    ``inclusive_gate`` preserves ``format_context``'s historical ``>=``
    truncation gate (partial emitted at exactly
    ``_TRUNCATION_MIN_REMAINDER`` chars remaining) versus the strict ``>``
    of the other two callers. The divergence predates this helper; it is
    pinned by regression tests rather than silently unified.
    """
    parts: list[str] = []
    total = start_total
    elided = 0
    iterator = iter(pieces)
    for piece in iterator:
        if total + len(piece) > max_chars:
            remaining = max_chars - total
            emit_partial = (
                remaining >= _TRUNCATION_MIN_REMAINDER
                if inclusive_gate
                else remaining > _TRUNCATION_MIN_REMAINDER
            )
            if emit_partial:
                parts.append(piece[:remaining])
            # A partially-emitted piece still counts as elided — its tail was
            # dropped. Draining the iterator counts what never rendered.
            elided = 1 + sum(1 for _ in iterator)  # this piece + the rest
            break
        parts.append(piece)
        total += len(piece)
    if elided and on_elide is not None:
        ledger = get_active_ledger()
        entry = on_elide(elided)
        if ledger is not None and entry is not None:
            ledger.record(entry)
    return parts


def _chunk_piece(chunk: Chunk) -> str:
    title = chunk.metadata.get(ChunkFilterField.TITLE.value, "") or ""
    text = chunk.text or ""
    # Code-backed hits carry the v7 ``qualified_name`` column back through
    # metadata (storage/sqlite/row_mappers.row_to_chunk); those point at
    # ``lookup``. Prose hits (no qname) get no pointer in slice 1.
    qname = str(chunk.metadata.get("qualified_name") or "")
    if qname:
        return f"## {title}\n{text}\n{pointer_token('lookup', qname)}\n"
    return f"## {title}\n{text}\n"


def _member_piece(member: ModuleMember) -> str:
    md = member.metadata
    pkg = md.get(ModuleMemberFilterField.PACKAGE.value, "") or ""
    module = md.get(ModuleMemberFilterField.MODULE.value, "") or ""
    name = md.get(ModuleMemberFilterField.NAME.value, "") or ""
    kind = md.get(ModuleMemberFilterField.KIND.value, "") or ""
    signature = md.get("signature", "") or ""
    docstring = md.get("docstring", "") or ""
    header = f"**[{pkg}] {module}.{name}{signature}** ({kind})"
    body = f"{header}\n{docstring}\n"
    # Members are always code-backed: ``module.name`` IS their lookup target.
    if module and name:
        body += f"{pointer_token('lookup', f'{module}.{name}')}\n"
    return body


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

    def _entry(count: int) -> TruncationEntry:
        first_qname = next((str(c.metadata.get("qualified_name") or "") for c in chunks), "")
        return TruncationEntry(
            description=f"{count} result(s) elided by the token budget",
            recovery=pointer_token("lookup", first_qname) if first_qname else "",
        )

    return "\n".join(
        _take_within_budget(
            (_chunk_piece(c) for c in chunks),
            budget_tokens * _CHARS_PER_TOKEN,
            on_elide=_entry,
        )
    )


def format_packages_list(packages: tuple[Package, ...]) -> str:
    """Render a sorted bullet list ``- name version — summary``.

    Byte-parity with pre-#6 ``server.py::list_packages`` (sub-PR #4 §5.1).
    Cap at ``LIST_PACKAGES_MAX`` packages.
    """
    sorted_pkgs = sorted(packages[:LIST_PACKAGES_MAX], key=lambda p: p.name)
    return "\n".join(f"- {p.name} {p.version} — {p.summary}" for p in sorted_pkgs)


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

    def _entry(count: int) -> TruncationEntry:
        target = ""
        for m in members:
            module = m.metadata.get(ModuleMemberFilterField.MODULE.value, "") or ""
            name = m.metadata.get(ModuleMemberFilterField.NAME.value, "") or ""
            if module and name:
                target = f"{module}.{name}"
                break
        return TruncationEntry(
            description=f"{count} result(s) elided by the token budget",
            recovery=pointer_token("lookup", target) if target else "",
        )

    return "\n".join(
        _take_within_budget(
            (_member_piece(m) for m in members),
            budget_tokens * _CHARS_PER_TOKEN,
            on_elide=_entry,
        )
    )


# Per-``show`` rendering vocabulary (spec §5.7, appendix §A.1):
#   - H1 phrasing differs per question ("Callers/Callees of X" / "Bases of X").
#   - Group-header noun gets singular/plural ("caller" vs "callers").
# Keeping these as a single table avoids ad-hoc conditionals at three
# call sites and makes the §A.1 shape one edit away if the vocabulary
# changes (e.g., MENTIONS → "Mentions of X").
_SHOW_VOCAB: dict[str, tuple[str, str]] = {
    "callers": ("Callers of", "caller"),
    "callees": ("Callees of", "callee"),
    "inherits": ("Bases of", "base"),
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

    # A full page (``len(rows) == limit``) can't distinguish "exactly this many"
    # from "the limit clipped more" — record the elision so the envelope surfaces
    # the recovery pointer (spec §D7).
    if len(rows) == limit:
        ledger = get_active_ledger()
        if ledger is not None:
            ledger.record(
                TruncationEntry(
                    description=(
                        f"exactly {limit} rows returned — possibly more exist; "
                        "raise reference_graph.output.default_limit to see them"
                    ),
                    recovery=pointer_token("lookup-show", target, show),
                )
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


def format_impact(
    rows: tuple[ImpactNode, ...],
    *,
    target: str,
    limit: int,
) -> str:
    """Render a ranked blast-radius (``lookup(show="impact")``) as markdown.

    ``rows`` are the ``ImpactNode``s the service already ranked and sliced;
    this only renders them (grouped into concentric hop rings so "what breaks
    first" reads top-down). The ranking-mode line tells the user whether the
    order came from PageRank (``node_scores`` enabled) or the fan-in fallback.

    Shape:
      - H1 = ``# Impact of `target` — what transitively calls it``
      - Empty rows → H1 + ``Nothing transitively calls `target`.``
      - Lead: ``N transitive caller(s) found (max depth D).`` + ranking-mode note
      - ``## hop N`` ring per distance (``hop 1`` labelled "direct callers")
      - Row: ``- `qname` — PageRank P, in-degree D`` (or just in-degree when
        ``node_scores`` is disabled)

    ``limit`` is accepted for API symmetry with the service (which already
    sliced); it is NOT re-applied here. Always ends with a single ``\\n``.
    """
    h1 = f"# Impact of `{target}` — what transitively calls it\n"
    if not rows:
        return f"{h1}\nNothing transitively calls `{target}`.\n"

    max_hop = max(n.hop for n in rows)
    has_scores = any(n.has_scores for n in rows)
    mode = (
        "Ranked by PageRank centrality.\n"
        if has_scores
        else "Ranked by fan-in (in-degree); enable reference_graph.node_scores "
        "for PageRank ranking.\n"
    )
    plural = "" if len(rows) == 1 else "s"
    lead = f"{len(rows)} transitive caller{plural} found (max depth {max_hop}). {mode}"

    # Group into hop rings preserving the service's rank order within each ring.
    rings: dict[int, list[ImpactNode]] = {}
    for n in rows:
        rings.setdefault(n.hop, []).append(n)

    blocks: list[str] = [h1, lead]
    for hop in sorted(rings):
        label = " (direct callers)" if hop == 1 else ""
        blocks.append(f"\n## hop {hop}{label}\n\n")
        for n in rings[hop]:
            if n.has_scores:
                blocks.append(
                    f"- `{n.qualified_name}` — PageRank {n.pagerank:.4f}, in-degree {n.in_degree}\n"
                )
            else:
                blocks.append(f"- `{n.qualified_name}` — in-degree {n.in_degree}\n")
    return "".join(blocks)


def _render_context_node(node: ContextNode) -> str:
    """Render one node at the fidelity its hop distance earns.

    Derives each tier from the persisted source text: full source (focus),
    signature = first source line (ring), name only (outline).
    """
    if node.hop == 0:  # focus tier — full source
        body = node.source_text or "# (source unavailable)"
        return f"\n## Focus — `{node.qualified_name}`\n\n```python\n{body}\n```\n"
    if node.hop == 1:  # ring tier — signature (the source's first line)
        sig = node.source_text.split("\n", 1)[0].strip() or f"# `{node.qualified_name}`"
        return f"\n## `{node.qualified_name}` — signature\n\n```python\n{sig}\n```\n"
    # outline tier — one line
    return f"- `{node.qualified_name}` (hop {node.hop})\n"


def _context_signature_lines(node: ContextNode) -> str:
    """Signature line, plus the first docstring line when one follows it.

    The persisted ``source_text`` starts with the ``def``/``class`` header; a
    module/class-level docstring — when present — is the SECOND source line.
    We surface it because a one-line summary is the highest-value byte per
    token in a skeleton (spec §D6). No docstring → signature only.
    """
    lines = node.source_text.split("\n")
    sig = lines[0].strip() or f"# `{node.qualified_name}`"
    second = lines[1].strip() if len(lines) > 1 else ""
    if second.startswith(('"""', "'''")):
        return f"{sig}\n{second}"
    return sig


def _rank_context_nodes(nodes: tuple[ContextNode, ...]) -> tuple[ContextNode, ...]:
    """Order nodes most-central first for skeleton body allocation.

    Key is ``(pagerank if any node carries pagerank else in_degree, -hop)``
    descending: PageRank when the graph was scored, structural fan-in as the
    tie-breaking fallback, closer hops winning ties. Pure — does not mutate.
    """
    use_pagerank = any(n.pagerank for n in nodes)
    return tuple(
        sorted(
            nodes,
            key=lambda n: (n.pagerank if use_pagerank else n.in_degree, -n.hop),
            reverse=True,
        )
    )


def _select_body_qnames(
    nodes: tuple[ContextNode, ...],
    *,
    body_budget_chars: int,
    max_bodies: int,
) -> frozenset[str]:
    """Qnames that earn a FULL body — the most-central nodes, doubly bounded.

    Walks the centrality-ranked order (:func:`_rank_context_nodes`) admitting a
    node while BOTH bounds hold: its source keeps cumulative body length within
    ``body_budget_chars`` (the char cap the spec fraction sets) AND the admitted
    count stays within ``max_bodies`` (a fraction of node count, so a tiny
    corpus of tiny bodies still reserves most nodes for signature-only). Pure —
    returns the admitted qname set so the renderer decides per node in input
    order.
    """
    admitted: set[str] = set()
    spent = 0
    for node in _rank_context_nodes(nodes):
        if len(admitted) >= max_bodies:
            break
        cost = len(node.source_text)
        if spent + cost > body_budget_chars:
            continue
        admitted.add(node.qualified_name)
        spent += cost
    return frozenset(admitted)


def _skeleton_block(node: ContextNode, *, with_body: bool) -> str:
    """One skeleton card block — full body for central nodes, signature else.

    Non-body nodes keep a ``lookup-show:<qname>:source`` recovery pointer so
    the elided body is one hop away (resolves to ``get_symbol(..., "source")``).
    """
    header = f"\n## `{node.qualified_name}`\n\n"
    if with_body:
        body = node.source_text or "# (source unavailable)"
        return f"{header}```python\n{body}\n```\n"
    sig = _context_signature_lines(node)
    pointer = pointer_token("lookup-show", node.qualified_name, "source")
    return f"{header}```python\n{sig}\n```\n{pointer}\n"


def _render_context_skeleton(
    nodes: tuple[ContextNode, ...],
    *,
    token_budget: int,
    body_ratio: float,
) -> list[str]:
    """Skeleton blocks in input order — bodies to the most-central nodes.

    ``body_ratio`` governs both bounds :func:`_select_body_qnames` applies: the
    char budget (``body_ratio`` of the total char budget) and the body count
    (``ceil(body_ratio * n)``, so the same fraction of nodes stays signature-
    only even when every body is small). Blocks stay in the service's input
    order so callee proximity reads top-down.
    """
    body_budget = int(body_ratio * token_budget * _CHARS_PER_TOKEN)
    max_bodies = max(1, ceil(body_ratio * len(nodes)))
    with_body = _select_body_qnames(nodes, body_budget_chars=body_budget, max_bodies=max_bodies)
    return [_skeleton_block(node, with_body=node.qualified_name in with_body) for node in nodes]


def format_context(
    nodes: tuple[ContextNode, ...],
    *,
    target: str,
    token_budget: int,
    render: str = "full",
    body_ratio: float = _DEFAULT_SKELETON_BODY_RATIO,
) -> str:
    """Render a smart-context pack (``lookup(show="context")``) under a budget.

    ``nodes`` are already ranked by the service (seed first, then callees by
    proximity × centrality). Two render modes:

    - ``render="full"`` (default for direct/test construction): graded fidelity
      by hop — focus (hop 0) = full source, ring (hop 1) = signature, rest
      (hop ≥2) = one-line outline.
    - ``render="skeleton"`` (spec §D6, the shipped YAML default): every node
      renders its signature line (+ first docstring line); full bodies are
      appended only to the most-central nodes — ranked by ``(pagerank else
      in_degree, -hop)`` — while their cumulative length stays within
      ``body_ratio * token_budget * _CHARS_PER_TOKEN`` chars. Each elided body
      keeps a ``lookup-show:<qname>:source`` recovery pointer.

    In both modes blocks are appended until the token budget
    (``token_budget * _CHARS_PER_TOKEN`` chars) is hit; the overflow piece is
    truncated when ≥ ``_TRUNCATION_MIN_REMAINDER`` chars remain, else dropped.
    Always ends with a single ``\\n``.
    """
    h1 = f"# Context for `{target}` — its dependency closure\n"
    if not nodes:
        return f"{h1}\nNo dependency context available for `{target}`.\n"

    max_hop = max(n.hop for n in nodes)

    def _context_entry(count: int) -> TruncationEntry:
        # ``"context"`` is not in ``_SHOW_VOCAB`` — it doesn't need to be; the
        # pointer token's show-word round-trips verbatim through resolve_pointers.
        return TruncationEntry(
            description=f"{count} closure symbol(s) elided by the context budget",
            recovery=pointer_token("lookup-show", target, "context"),
        )

    if render == "skeleton":
        lead = (
            f"{len(nodes)} nodes in the closure (max depth {max_hop}). Skeleton "
            "fidelity: signatures for all, full source for the most-central.\n"
        )
        pieces = _render_context_skeleton(nodes, token_budget=token_budget, body_ratio=body_ratio)
    else:
        lead = (
            f"{len(nodes)} symbols in the closure (max depth {max_hop}). Graded fidelity: "
            "focus = full source, ring = signature, rest = outline.\n"
        )
        pieces = [_render_context_node(node) for node in nodes]

    blocks = [h1, lead]
    blocks.extend(
        _take_within_budget(
            pieces,
            token_budget * _CHARS_PER_TOKEN,
            start_total=len(h1) + len(lead),
            inclusive_gate=True,
            on_elide=_context_entry,
        )
    )
    out = "".join(blocks)
    return out if out.endswith("\n") else out + "\n"


# The communities block degrades to this hint when node_scores is off — the
# community partition is derived from PageRank/Leiden, so with scores disabled
# there is nothing to show. Anchored on the YAML knob so the agent's fix is
# one config edit away (spec §D17 block 5).
_COMMUNITIES_DISABLED_HINT = (
    "Community structure is unavailable — enable reference_graph.node_scores to see it.\n"
)


def _overview_stats_line(card: OverviewCard) -> str:
    """One-line corpus census — ``doc_coverage`` rendered as an integer percent."""
    pct = round(card.doc_coverage * 100)
    return (
        f"[{card.package_count} packages · {card.module_count} modules · "
        f"{card.symbol_count} symbols · {pct}% documented]\n"
    )


def _overview_module_block(card: OverviewCard) -> str:
    """Centrality-ranked module map — each line points at ``get_context`` via
    the ``lookup-show:<module>:context`` token (resolved per surface)."""
    lines = [
        f"- `{m.qualified_name}` — {m.first_doc_line} "
        f"{pointer_token('lookup-show', m.qualified_name, 'context')}\n"
        for m in card.modules
    ]
    return "## Module map\n" + "".join(lines)


def _overview_entry_points_block(card: OverviewCard) -> str:
    """Entry-point union (scripts / __main__ / graph roots), each pointing at
    ``get_symbol`` via a plain ``lookup`` token."""
    lines = [
        f"- `{e.name}` ({e.kind}) {pointer_token('lookup', e.name)}\n" for e in card.entry_points
    ]
    return "## Entry points\n" + "".join(lines)


def _overview_communities_block(card: OverviewCard) -> str:
    """Structure communities with cohesion, OR the enablement hint when
    ``node_scores`` is off (nothing to partition without centrality)."""
    if not card.node_scores_available:
        return "## Structure communities\n" + _COMMUNITIES_DISABLED_HINT
    lines = [
        f"- {c.label} — {c.size} members, cohesion {c.cohesion:.2f}, top `{c.top_member}`\n"
        for c in card.communities
    ]
    return "## Structure communities\n" + "".join(lines)


def _overview_dependency_block(card: OverviewCard) -> str:
    """External dependency profile by import count — each points at
    ``get_symbol`` for the package via a ``lookup`` token."""
    lines = [
        f"- {pkg} ({count} imports) {pointer_token('lookup', pkg)}\n"
        for pkg, count in card.dependency_profile
    ]
    return "## Dependency profile\n" + "".join(lines)


def format_overview_card(card: OverviewCard) -> str:
    """Render an :class:`OverviewCard` as the §D17 structural orientation card.

    Pure rendering (no I/O): H1 + one stats line, then the four §D17 H2 blocks
    in order — Module map, Entry points, Structure communities, Dependency
    profile. Each block obeys the module byte-parity contract (``## {title}\\n``
    then body lines, blocks joined with ``"\\n"`` so a blank line separates
    them). The communities block degrades to an enablement hint when
    ``node_scores`` is disabled. Always ends with a single trailing ``\\n``.
    """
    h1 = f"# Overview — {card.package}\n"
    header = h1 + _overview_stats_line(card)
    blocks = [
        header,
        _overview_module_block(card),
        _overview_entry_points_block(card),
        _overview_communities_block(card),
        _overview_dependency_block(card),
    ]
    out = "\n".join(blocks)
    return out if out.endswith("\n") else out + "\n"


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
