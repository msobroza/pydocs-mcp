"""Single source of the dotted Python module-id rule.

Two root kinds, two rules:

- :func:`package_rooted_module_id` is for roots that are NOT ``sys.path``
  entries, such as an indexed project directory. A file inside a real
  package is re-rooted at the parent of its topmost consecutive
  ``__init__.py`` directory, so ``/p/src/needle/a.py`` (root ``/p``) is
  ``needle.a``, the name ``import needle.a`` uses. Chunk ids, document-tree
  ids, reference-graph node ids and project member ids all come from it.
- :func:`import_root_module_id` is for ``sys.path``-entry roots such as
  site-packages, where the root-relative path IS the import path:
  ``/sp/google/cloud/x.py`` (root ``/sp``) is ``google.cloud.x``, namespace
  packages included. Dependency member ids come from it.

WHY one module: the member extractor used to carry its own relpath-only copy
of the rule, which skipped the package-root walk. Project members then read
``src.needle.x`` / ``python.pkg.x`` while chunks and trees read ``needle.x`` /
``pkg.x``, and get_symbol could not resolve the member ids that search
published (spec 2026-09-10-member-module-ids-design §1).

Collisions (owner decision OD-A): package rooting can map two files to one
bare id, e.g. ``examples/a/app/main.py`` and ``examples/b/app/main.py`` both
become ``app.main``. Chunks and trees already collide the same way, so
members now match them instead of holding unique ids nothing can resolve.

Stdlib imports only: ``chunkers/ast_python.py`` imports this module while the
``extraction.strategies`` package is still initialising, so importing
anything under ``chunkers`` from here would create a cycle.
"""

from __future__ import annotations

import os
from pathlib import Path

# Upgrade token for the member module-id rule, and its only definition.
# WHY: the project cache skip runs before member extraction, so a rule change
# never reaches an existing index on its own. ContentHashStage folds this
# token into the ``__project__`` package hash only, so bumping it forces ONE
# project re-extraction: chunk hashes are unchanged (nothing re-embeds) and
# dependency hashes are untouched (spec 2026-09-10-member-module-ids-design
# §4). A future ``file_extractions.members_json`` cache (multi-branch P1)
# must fold it too, or cached member rows would survive a rule change (§9).
MODULE_ID_RULE_VERSION = "package-root/1"


def relative_module_parts(path: str, root: Path) -> tuple[list[str], Path]:
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

    Example: ``relative_module_parts("/p/pkg/mod.py", Path("/p"))`` returns
    ``(["pkg", "mod"], Path("/p/pkg/mod.py"))``.
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


def python_package_root(source_file: Path) -> Path:
    """Find the parent directory of the topmost ``__init__.py`` ancestor.

    Walks upward from ``source_file`` collecting consecutive directories
    that contain ``__init__.py``. The PARENT of the topmost such dir is
    the right "root" for computing a dotted module qname — it matches
    what Python's import machinery uses when ``source_file``'s package
    is added to ``sys.path``.

    Why: project-source qnames used to come out as ``python.pydocs_mcp.X``
    because the indexing ``root`` was the project directory and the
    filesystem walked through ``python/``. With this helper the root
    becomes the parent of the topmost ``__init__.py``-containing dir
    (typically ``project/python/``), so the qname matches
    ``import pydocs_mcp.X``.

    Falls back to ``source_file.parent`` when no ``__init__.py`` is
    found anywhere up the chain — handles loose scripts / scratch files.

    Example: with ``/p/src/needle/__init__.py`` present,
    ``python_package_root(Path("/p/src/needle/a.py"))`` is ``Path("/p/src")``.
    """
    p = source_file.resolve() if source_file.is_absolute() else (Path.cwd() / source_file).resolve()
    cur = p.parent
    topmost_pkg: Path | None = None
    while (cur / "__init__.py").exists():
        topmost_pkg = cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return topmost_pkg.parent if topmost_pkg is not None else p.parent


def package_rooted_module_id(path: str, root: Path) -> str:
    """Dotted module id for a file under a non-``sys.path`` root (a project dir).

    Strips suffix, drops a trailing ``__init__``, joins ``/`` → ``.`` —
    matches Python's import machinery.

    If the file lives inside a real Python package (its parent directory
    has ``__init__.py``), the package root discovered by
    :func:`python_package_root` is the effective root, so the resulting
    qname matches ``import pkg.mod`` (not ``python.pkg.mod``). When the
    file's parent is NOT a package — e.g. synthetic paths used in unit
    tests, or loose ``.py`` scripts — the passed-in ``root`` is honored
    unchanged so ``tmp_path/pkg/mod.py`` without ``__init__.py`` still
    gets a ``pkg.mod`` qname relative to the caller's root.

    Examples (root ``/p``): ``/p/src/needle/a.py`` with ``src/needle/__init__.py``
    → ``needle.a``; ``/p/src/app.py`` with no ``__init__.py`` → ``src.app``;
    ``/p/__init__.py`` → ``p`` (the root directory's own name).
    """
    p = Path(path)
    is_in_package = p.parent.is_dir() and (p.parent / "__init__.py").exists()
    effective_root = python_package_root(p) if is_in_package else root
    parts, _p2 = relative_module_parts(path, effective_root)
    return _join_module_parts(parts) or p.stem


def import_root_module_id(path: str, import_root: Path) -> str:
    """Dotted module id for a file under a ``sys.path``-entry root (site-packages).

    The root-relative path is the import path, so namespace packages keep
    every segment: ``/sp/google/cloud/x.py`` (root ``/sp``) → ``google.cloud.x``;
    ``/sp/pkg/__init__.py`` → ``pkg``; ``/sp/six.py`` → ``six``.

    Raises ``ValueError`` when ``os.path.relpath`` cannot relate the two
    paths (a Windows cross-drive path); the member extractor skips the file.
    """
    rel = os.path.relpath(path, str(import_root))
    return _join_module_parts(rel.replace(os.sep, ".").removesuffix(".py").split("."))


def _join_module_parts(parts: list[str]) -> str:
    """Join module path segments, dropping a trailing ``__init__`` SEGMENT.

    Segment-wise, never a substring replace: ``pkg/__init__x.py`` stays
    ``pkg.__init__x`` rather than gluing into ``pkgx``.
    """
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


__all__ = (
    "MODULE_ID_RULE_VERSION",
    "import_root_module_id",
    "package_rooted_module_id",
    "python_package_root",
    "relative_module_parts",
)
