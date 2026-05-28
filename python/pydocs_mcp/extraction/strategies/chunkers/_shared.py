"""Helpers shared across two or more concrete chunkers.

Anything that exactly one chunker uses lives next to its chunker
implementation; this module is reserved for utilities multiple chunkers
genuinely depend on (CommonMark fence regex, path/qname helpers, hash
+ summary primitives, fallback MODULE node).
"""
from __future__ import annotations

import hashlib
import logging
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


def _relative_module_parts(path: str, root: Path) -> tuple[list[str], Path]:
    """Return ``(parts_without_suffix, Path(path))`` relative to ``root``.

    Shared by ``_module_from_path`` (.py) and ``_module_from_doc_path``
    (.md / .ipynb) — only the post-processing (``__init__`` stripping)
    differs. Paths outside ``root`` fall back to the basename stem so
    tests using fake paths and vendored files still produce a stable
    module id.
    """
    p = Path(path)
    root_abs = root.resolve() if root.is_absolute() else Path.cwd() / root
    try:
        rel = p.resolve().relative_to(root_abs)
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
    through unchanged (satisfies spec §4.3 ``source_path`` contract)."""
    try:
        return str(Path(path).resolve().relative_to(root.resolve()))
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
    h = hashlib.md5(
        f"{kind.value}:{title}:{text}".encode(), usedforsecurity=False
    ).hexdigest()
    return h[:12]


def _docstring_summary(doc: str) -> str:
    """First line of the docstring, truncated to 140 chars. Empty
    docstring → empty summary (never raises, never returns ``None``)."""
    if not doc:
        return ""
    first = doc.strip().split("\n", 1)[0]
    return first[:140]


def _fallback_module_node(
    module: str, path: str, content: str, root: Path,
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


__all__ = (
    "_FENCED_RE",
    "_content_hash",
    "_docstring_summary",
    "_fallback_module_node",
    "_module_from_doc_path",
    "_relative_module_parts",
    "_relpath",
    "_slice_lines",
)
