"""
Pure Python fallbacks for Rust functions.
Used when the Rust extension is not installed (pip install without Rust toolchain).
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.constants import (
    DOCSTRING_LOOKAHEAD,
    FUNC_DOCSTRING_MAX,
    MODULE_DOCSTRING_MAX,
)

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".tox", ".eggs", "build", "dist", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "htmlcov", ".nox",
}


def walk_py_files(root: str) -> list[str]:
    """Find all .py files, skipping excluded directories."""
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for f in filenames:
            if f.endswith(".py"):
                result.append(os.path.join(dirpath, f))
    result.sort()
    return result


def hash_files(paths: list[str]) -> str:
    """Hash file paths + mtimes to detect changes."""
    h = hashlib.md5()
    for p in paths:
        h.update(p.encode())
        try:
            h.update(str(os.stat(p).st_mtime_ns).encode())
        except OSError:
            pass
    return h.hexdigest()[:16]


def split_into_chunks(text: str, max_chars: int = 4000) -> list[tuple[str, str]]:
    """Split text into (heading, body) tuples at heading boundaries."""
    heading, buf, results = "Overview", [], []
    buf_len = 0

    def flush():
        body = "\n".join(buf).strip()
        if len(body) > 30:
            results.append((heading, body[:max_chars]))

    for line in text.splitlines():
        m = re.match(r"^(#{1,4})\s+(.+)", line)
        if m:
            flush()
            heading, buf = m.group(2).strip(), []
            buf_len = 0
            continue
        buf.append(line)
        buf_len += len(line) + 1
        if buf_len > max_chars * 2:
            flush()
            buf = []
            buf_len = 0
    flush()
    return results


# plain @dataclass (not frozen+slots) to mirror Rust #[pyclass] ParsedMember,
# which exposes read-only getters but isn't truly frozen on the Python side.
@dataclass
class ParsedMember:
    name: str
    kind: str
    signature: str
    docstring: str


def parse_py_file(source: str) -> list[ParsedMember]:
    """Extract top-level functions and classes using regex."""
    def_re = re.compile(
        r'^(async\s+def|def|class)\s+([A-Za-z_]\w*)\s*\(([^)]*)\)\s*(?:->[\s\w\[\],.|]*)?:',
        re.MULTILINE,
    )
    doc_re = re.compile(r'(?s)^(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')')

    members = []
    for m in def_re.finditer(source):
        kind, name, sig = m.group(1), m.group(2), m.group(3)
        if name.startswith("_"):
            continue

        # Look for docstring immediately after the definition (colon consumed by regex).
        rest = source[m.end():][:DOCSTRING_LOOKAHEAD].lstrip()
        docstring = ""
        doc_match = doc_re.match(rest)
        if doc_match:
            docstring = (doc_match.group(1) or doc_match.group(2) or "").strip()[:FUNC_DOCSTRING_MAX]

        members.append(ParsedMember(name, kind, f"({sig.strip()})", docstring))
    return members


def extract_module_doc(source: str) -> str:
    """Extract module-level docstring."""
    doc_re = re.compile(r'(?s)^(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')')
    m = doc_re.match(source.lstrip())
    if m:
        return (m.group(1) or m.group(2) or "").strip()[:MODULE_DOCSTRING_MAX]
    return ""


def read_file(path: str) -> str:
    """Read a file, return empty string on error."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def read_files_parallel(paths: list[str]) -> list[tuple[str, str]]:
    """Read files (no parallelism in pure Python fallback)."""
    return [(p, read_file(p)) for p in paths]
