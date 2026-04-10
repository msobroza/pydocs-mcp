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


def chunk_text(text: str, max_chars: int = 4000) -> list[tuple[str, str]]:
    """Split text into (heading, body) tuples at heading boundaries."""
    heading, buf, results = "Overview", [], []

    def flush():
        body = "\n".join(buf).strip()
        if len(body) > 30:
            results.append((heading, body[:max_chars]))

    for line in text.splitlines():
        m = re.match(r"^(#{1,4})\s+(.+)", line)
        if m:
            flush()
            heading, buf = m.group(2).strip(), []
            continue
        buf.append(line)
        if len("\n".join(buf)) > max_chars * 2:
            flush()
            buf = []
    flush()
    return results


@dataclass
class Symbol:
    name: str
    kind: str
    signature: str
    docstring: str


def parse_py_file(source: str) -> list[Symbol]:
    """Extract top-level functions and classes using regex."""
    def_re = re.compile(
        r'^(async\s+def|def|class)\s+([A-Za-z_]\w*)\s*\(([^)]*)\)',
        re.MULTILINE,
    )
    doc_re = re.compile(r'(?s)(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')')

    symbols = []
    for m in def_re.finditer(source):
        kind, name, sig = m.group(1), m.group(2), m.group(3)
        if name.startswith("_"):
            continue

        # Look for docstring right after the definition.
        rest = source[m.end():]
        docstring = ""
        doc_match = doc_re.search(rest.lstrip())
        if doc_match:
            docstring = (doc_match.group(1) or doc_match.group(2) or "").strip()[:3000]

        symbols.append(Symbol(name, kind, f"({sig.strip()})", docstring))
    return symbols


def extract_module_doc(source: str) -> str:
    """Extract module-level docstring."""
    doc_re = re.compile(r'(?s)^(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')')
    m = doc_re.match(source.lstrip())
    if m:
        return (m.group(1) or m.group(2) or "").strip()[:5000]
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
