"""Helpers shared across two or more concrete chunkers.

Anything that exactly one chunker uses lives next to its chunker
implementation; this module is reserved for utilities multiple chunkers
genuinely depend on (CommonMark fence regex, path/qname helpers, hash
+ summary primitives, fallback MODULE node).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

from pydocs_mcp.extraction.model import DocumentNode, NodeKind

log = logging.getLogger("pydocs-mcp")


# CommonMark §4.5 fenced code blocks. Pre-fix this only matched
# triple-backtick fences with ``\w*`` info strings — phantom headings
# slipped through 4+ backtick fences (required when body contains
# triple-backticks), tilde fences (``~~~python``), and hyphenated lang
# tags like ``c++`` or ``text/plain``. Adversarial review F16 catch.
#
# Opener length ≥ 3, closer must MATCH the opener exactly (kind +
# length) via the ``fence`` backreference. The info string accepts any
# non-newline char. Named groups so call sites read structurally
# (``lang`` / ``body``) instead of positional ``group(1)`` / ``group(2)``.
_FENCED_RE = re.compile(
    r"^(?P<fence>`{3,}|~{3,})(?P<lang>[^\n]*)\n(?P<body>.*?)\n(?P=fence)",
    re.MULTILINE | re.DOTALL,
)

# Matches ANY fence opener line, closed or not — used only to detect an
# unclosed trailing fence (see ``_unclosed_fence_start``). ``_FENCED_RE``
# can't see this case because it requires a matching closer.
_FENCE_OPENER_RE = re.compile(r"^(?P<fence>`{3,}|~{3,})[^\n]*$", re.MULTILINE)


def _unclosed_fence_start(content: str) -> int | None:
    """Return the start offset of an unclosed trailing fence, or ``None``.

    CommonMark §4.5: a fence opener with no matching closer runs to
    end-of-document — the remainder is still code. ``_FENCED_RE`` only
    matches opener+closer PAIRS, so a truncated / author-error file (final
    fence never closed) leaves that trailing region invisible to masking
    logic built solely on ``_FENCED_RE`` matches. Callers that mask fenced
    regions from heading/text scanning must also mask
    ``[start, len(content))`` when this returns non-``None``.

    Finds the first fence-opener line not already covered by a matched
    (closed) fence range; that opener has no closer of its own kind, so it
    masks everything after it, per CommonMark.
    """
    closed_ranges = [(m.start(), m.end()) for m in _FENCED_RE.finditer(content)]
    for m in _FENCE_OPENER_RE.finditer(content):
        if any(start <= m.start() < end for start, end in closed_ranges):
            continue
        return m.start()
    return None


def _relative_module_parts(path: str, root: Path) -> tuple[list[str], Path]:
    """Return ``(parts_without_suffix, Path(path))`` relative to ``root``.

    Shared by ``_module_from_path`` (.py) and ``_module_from_doc_path``
    (.md / .ipynb) — only the post-processing (``__init__`` stripping)
    differs. Paths outside ``root`` fall back to the basename stem so
    tests using fake paths and vendored files still produce a stable
    module id.

    Uses ``os.path.abspath`` (normalizes ``.``/``..``, does NOT follow
    symlinks) rather than ``Path.resolve()`` (follows symlinks). A monorepo
    file symlinked from inside ``root`` to a target outside it must keep its
    IN-TREE location as its identity — resolving the symlink target made
    ``relative_to(root)`` raise on paths that are legitimately inside the
    indexed tree, falling back to the bare basename stem and colliding two
    same-named symlinks from different packages on the module qname.
    """
    p = Path(path)
    # WORKAROUND: os.path.abspath (not Path.resolve()) — resolve() follows
    # symlinks, which is exactly what must NOT happen here (see docstring).
    p_abs = Path(os.path.abspath(path))  # noqa: PTH100
    root_abs = Path(os.path.abspath(root))  # noqa: PTH100
    try:
        rel = p_abs.relative_to(root_abs)
    except ValueError:
        rel = Path(p.name)
    return list(rel.with_suffix("").parts), p


def _module_from_doc_path(path: str, root: Path) -> str:
    """Doc-file module id = relative path with ``/`` → ``.``, suffix preserved
    as a trailing dotted segment.

    Pre-fix this stripped the suffix entirely, which made ``pkg/foo.md`` and
    ``pkg/foo.ipynb`` produce the same module name (``pkg.foo``) — the SAME
    name that ``pkg/foo.py`` produces. DocumentTreeStore PK is
    ``(package, module)`` so all three writes collided and the last writer
    won, silently dropping the other two trees.

    Keeping the suffix in the qualified_name (``pkg.foo.md``,
    ``pkg.foo.ipynb``) makes the identity per-file unique while staying
    human-readable.
    """
    parts, p = _relative_module_parts(path, root)
    base = ".".join(parts) or p.stem
    suffix = p.suffix.lstrip(".").lower()
    return f"{base}.{suffix}" if suffix else base


def _relpath(path: str, root: Path) -> str:
    """Relative source path from the indexing root; opaque strings pass
    through unchanged (satisfies spec §4.3 ``source_path`` contract).

    ``os.path.abspath`` (not ``Path.resolve()``) so a symlinked file keeps
    its in-tree location as ``source_path`` instead of leaking the resolved
    target's absolute filesystem path — see ``_relative_module_parts`` for
    the identical symlink-preserving rationale.
    """
    try:
        # WORKAROUND: os.path.abspath, not Path.resolve() — see
        # _relative_module_parts docstring for the symlink rationale.
        return str(Path(os.path.abspath(path)).relative_to(os.path.abspath(root)))  # noqa: PTH100
    except ValueError:
        return path


def _slice_lines(lines: list[str], start: int, end: int) -> str:
    """1-indexed inclusive line slice. Returns joined text (no trailing
    newline). Clamps negative / zero starts to line 1."""
    s = max(1, start) - 1
    e = max(s, end)
    return "\n".join(lines[s:e])


def _content_hash(text: str, kind: NodeKind, title: str) -> str:
    """Stable 12-char MD5 prefix over ``(kind, title, text)``. Used for
    incremental re-index — identical inputs produce identical hashes
    across processes (hashlib is deterministic unlike Python ``hash()``)."""
    # md5 used as a non-cryptographic content fingerprint, not a security hash.
    h = hashlib.md5(f"{kind.value}:{title}:{text}".encode(), usedforsecurity=False).hexdigest()
    return h[:12]


# Bound the char scan that reconstructs a def/class header from node text, so
# a pathological body (no paren-depth-0 ``:`` near the top) can't run the
# scanner over the whole function source.
_HEADER_SCAN_LIMIT = 2000


def _collapse_ws(text: str) -> str:
    """Collapse every run of whitespace (incl. newlines) to single spaces."""
    return " ".join(text.split())


def _header_from_text(text: str, *, max_chars: int | None = None) -> str:
    """Reconstruct a ``def`` / ``class`` header from a node's source text.

    Scans to the first paren-depth-0 ``:`` (so annotation / slice colons,
    which live inside ``()`` / ``[]``, don't terminate the header) and
    collapses whitespace, so a multi-line signature becomes one tidy line.
    Best-effort: a ``):`` inside a string default can truncate early, which
    only degrades the derived label, never crashes. ``max_chars`` (when given)
    bounds the result; ``None`` means unbounded (the chunker stores the full
    signature, the tree-reasoning render side passes its own title cap).
    """
    if not text:
        return ""
    depth = 0
    chars: list[str] = []
    for ch in text[:_HEADER_SCAN_LIMIT]:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == ":" and depth == 0:
            break
        chars.append(ch)
    header = _collapse_ws("".join(chars)).replace("( ", "(").replace(" )", ")")
    return header if max_chars is None else header[:max_chars]


def _docstring_summary(doc: str) -> str:
    """First line of the docstring, truncated to 140 chars. Empty
    docstring → empty summary (never raises, never returns ``None``)."""
    if not doc:
        return ""
    first = doc.strip().split("\n", 1)[0]
    return first[:140]


def _fallback_module_node(
    module: str,
    path: str,
    content: str,
    root: Path,
) -> DocumentNode:
    """Single-node MODULE tree for unparsable files — the file still gets
    one chunk keyed by its full source. Used by AstPythonChunker on
    ``SyntaxError`` and NotebookChunker on malformed JSON."""
    end = max(len(content.splitlines()), 1)
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path=_relpath(path, root),
        start_line=1,
        end_line=end,
        text=content,
        content_hash=_content_hash(content, NodeKind.MODULE, module),
    )


def _code_example_node(
    code: str,
    lang: str,
    index: int,
    parent_qname: str,
    rel: str,
    *,
    start_line: int = 1,
    end_line: int = 1,
) -> DocumentNode:
    """Shared CODE_EXAMPLE constructor for the .py and .md chunkers.

    The qname scheme (``{parent}.__example_{i}__``) and the
    ``_content_hash`` recipe are IDENTITY-BEARING — ``chunks.content_hash``
    (incremental re-index) and tree lookups key off them, so the two
    chunkers must never drift; this helper is the single owner.
    ``start_line``/``end_line`` default to the docstring-stub policy
    (fence offsets inside a docstring don't map back to source lines);
    the markdown chunker passes its real heading span.
    """
    title = f"example {index}"
    qname = f"{parent_qname}.__example_{index}__"
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=title,
        kind=NodeKind.CODE_EXAMPLE,
        source_path=rel,
        start_line=start_line,
        end_line=end_line,
        text=code,
        content_hash=_content_hash(code, NodeKind.CODE_EXAMPLE, title),
        extra_metadata={"language": lang},
        parent_id=parent_qname,
    )


__all__ = (
    "_FENCED_RE",
    "_HEADER_SCAN_LIMIT",
    "_code_example_node",
    "_collapse_ws",
    "_content_hash",
    "_docstring_summary",
    "_fallback_module_node",
    "_header_from_text",
    "_module_from_doc_path",
    "_relative_module_parts",
    "_relpath",
    "_slice_lines",
    "_unclosed_fence_start",
)
