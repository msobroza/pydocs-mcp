"""DependencyFileDiscoverer — lists files shipped by an installed dependency.

Returns ``(paths, site_packages_root)``; a missing distribution
(declared-but-not-installed) returns ``([], Path("."))`` — the
:class:`~pydocs_mcp.application.ProjectIndexer` treats that as a
non-fatal skip. Applies the same extension + size filters as
:class:`ProjectFileDiscoverer`, plus the EFFECTIVE dependency-scope
exclusion set: the hardcoded ``_EXCLUDED_DIRS`` floor (a wheel can ship
bundled ``.git/`` / ``__pycache__`` / ``node_modules`` directories and
they must never leak into the FTS index) unioned with YAML
``extraction.discovery.dependency.exclude_dirs``.

A dependency's own ``pyproject.toml`` is NEVER consulted (spec D4 —
an untrusted-input channel pointed at index composition), so this
class has no ``excludes_loader`` field. User entries are
directories-only (§4): bare names match PARENT-directory components
(a bare entry colliding with a shipped file name, e.g. ``"conf.py"``,
excludes nothing); anchored YAML entries match each ``dist.files``
relpath with its FIRST path component stripped (§4) — one entry like
``"docs/examples"`` excludes ``<top-level>/docs/examples/**``
uniformly across distributions, and a flat single-component relpath
(``six.py``) never matches an anchored entry. The FLOOR keeps today's
full-relpath framing (byte-compat with the pre-feature walk).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies._dep_helpers import (
    find_installed_distribution,
    find_site_packages_root,
)
from pydocs_mcp.extraction.strategies.discovery._shared import (
    _in_excluded_dir,
    _within_size_budget,
)
from pydocs_mcp.project_toml import (
    EMPTY_PROJECT_EXCLUDES,
    ProjectExcludes,
    merge_excludes,
)


def _bare_dep_dir_match(rel_str: str, effective: ProjectExcludes) -> bool:
    """True iff a PARENT-directory component of ``rel_str`` is an
    effective bare-name entry.

    The file's own basename is stripped BEFORE matching (directories-only
    rule, §4): a bare user entry colliding with a shipped FILE name (e.g.
    ``"conf.py"``) excludes nothing. The floor keeps its legacy
    full-relpath framing in ``discover`` — this check carries only the
    user-visible semantics, so passing the union set (which includes the
    floor) is harmless: floor names on parent components were already
    caught by the floor check.
    """
    parent = "/".join(rel_str.replace("\\", "/").split("/")[:-1])
    return bool(parent) and _in_excluded_dir(parent, effective.names)


def _anchored_dep_match(rel_str: str, effective: ProjectExcludes) -> bool:
    """True iff the file's PARENT directory, after stripping the relpath's
    first component, falls under an anchored entry (§4).

    The strip is a relpath rule, not a package-directory rule: a
    distribution with several top-level components has the entry applied
    under each of them, and a single-component relpath (flat module a la
    ``six.py``) has nothing left after stripping and never matches. The
    parent directory — not the full file relpath — is matched, per the
    directories-only rule (§4): an entry colliding with a file name is a
    uniform no-op on both walks.
    """
    if not effective.anchored:
        return False
    parts = rel_str.replace("\\", "/").split("/")
    if len(parts) < 2:
        return False
    return effective.matches("/".join(parts[1:-1]))


@dataclass(frozen=True, slots=True)
class DependencyFileDiscoverer:
    scope: DiscoveryScopeConfig

    def discover(self, target: str) -> tuple[list[str], Path]:
        # Floor ∪ YAML dependency.exclude_dirs only — no TOML loader
        # (spec D4); EMPTY_PROJECT_EXCLUDES stands in for the absent
        # pyproject surface.
        effective = merge_excludes(
            _EXCLUDED_DIRS,
            self.scope.exclude_dirs,
            EMPTY_PROJECT_EXCLUDES,
        )
        dist = find_installed_distribution(target)
        if dist is None:
            return [], Path()
        paths: list[str] = []
        for f in dist.files or []:
            rel_str = str(f)
            # Floor: full-relpath framing, byte-identical to today's walk
            # when no user entries are configured (AC-11 regression).
            if _in_excluded_dir(rel_str):
                continue
            # User entries: directories-only (§4) — bare names match
            # parent-dir components, anchored entries match with the
            # first component stripped.
            if _bare_dep_dir_match(rel_str, effective):
                continue
            if _anchored_dep_match(rel_str, effective):
                continue
            ext = Path(rel_str).suffix.lower()
            if ext not in self.scope.include_extensions:
                continue
            full = str(dist.locate_file(f))
            if not _within_size_budget(full, self.scope.max_file_size_bytes):
                continue
            paths.append(full)
        paths.sort()
        root = Path(find_site_packages_root(paths[0])) if paths else Path()
        return paths, root


__all__ = ("DependencyFileDiscoverer",)
