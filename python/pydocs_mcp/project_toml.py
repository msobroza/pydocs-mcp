"""Owner of the indexed project's ``[tool.pydocs-mcp]`` pyproject schema.

A peer of :mod:`pydocs_mcp.deps` / :mod:`pydocs_mcp.multirepo`. Reads
per-project directory exclusions (``[tool.pydocs-mcp] exclude_dirs``) and
ships the shared entry normalizer used by BOTH configuration surfaces —
the TOML loader here and the YAML ``exclude_dirs`` field validator on
:class:`~pydocs_mcp.extraction.config.DiscoveryScopeConfig` funnel through
:func:`split_exclude_entries`, so the two surfaces can never drift
(design decision D5).

Import discipline: stdlib + :mod:`pydocs_mcp.exceptions` ONLY. In
particular nothing from ``extraction/`` — ``extraction/config.py``
imports this module, and any back-import would cycle.

Usage::

    excludes = load_project_excludes(Path("/repo"))
    excludes.matches("docs/generated")   # True if excluded
"""

from __future__ import annotations

import logging
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.exceptions import PydocsMCPError

logger = logging.getLogger(__name__)

_EXCLUDE_KEY = "exclude_dirs"
_TOOL_TABLE = "pydocs-mcp"


class ProjectExcludeConfigError(PydocsMCPError, ValueError):
    """A declared exclude list is malformed (wrong type, escaping entry).

    Raised — never swallowed — because a declared-but-broken config is
    user intent gone wrong (spec §8): silently ignoring it would index
    everything the user asked to exclude. ValueError lineage preserves
    stdlib isinstance checks, per the exceptions.py precedent.
    """


@dataclass(frozen=True, slots=True)
class ProjectExcludes:
    """User-declared exclusion entries, pre-classified (spec §4).

    ``names`` are bare directory names matched against ANY path component
    at any depth; ``anchored`` are normalized POSIX relpaths matched as
    walk-root-anchored subtrees.
    """

    names: frozenset[str]
    anchored: frozenset[str]

    def matches(self, relpath: str) -> bool:
        """True iff ``relpath`` falls under any entry.

        ``relpath`` is walk-root-relative with POSIX separators. The
        directories-only rule of spec §4 is the callers' contract — walkers
        test a file's parent directory, not the file itself — but the
        predicate is deliberately agnostic: any relpath that falls beneath
        an excluded directory matches, including a file path under one.
        Anchored entries match the directory itself and anything beneath
        it; names match any path component. Byte-wise case-sensitive on
        every platform (spec §4) — no casefolding, ever.
        """
        path = relpath.replace("\\", "/")
        if any(part in self.names for part in path.split("/")):
            return True
        return any(path == entry or path.startswith(entry + "/") for entry in self.anchored)


EMPTY_PROJECT_EXCLUDES = ProjectExcludes(frozenset(), frozenset())
"""The no-excludes value — default for every optional excludes parameter."""


def split_exclude_entries(
    entries: Sequence[str],
) -> tuple[frozenset[str], frozenset[str]]:
    """Normalize + classify exclude entries into ``(names, anchored)``.

    The shared validator for BOTH surfaces (decision D5). Normalization
    order is load-bearing (spec §4): backslashes -> ``/`` FIRST, trailing
    ``/`` stripped SECOND, classification by remaining ``/`` LAST — so
    ``"fixtures/"`` and ``"fixtures\\"`` are bare names, never anchored.

    Raises :class:`ProjectExcludeConfigError` for non-string, absolute,
    or empty entries, and for entries with an empty / ``.`` / ``..``
    segment — each either escapes the walk root or could never match a
    real path, and the message carries the offending value.

    Example::

        split_exclude_entries(["fixtures", "docs/generated/"])
        # (frozenset({"fixtures"}), frozenset({"docs/generated"}))
    """
    names: set[str] = set()
    anchored: set[str] = set()
    for raw in entries:
        if not isinstance(raw, str):
            raise ProjectExcludeConfigError(
                f"exclude_dirs entries must be strings; got {raw!r} "
                f"({type(raw).__name__}) — expected a directory name like "
                f'"fixtures" or an anchored path like "docs/generated"'
            )
        entry = raw.replace("\\", "/")
        if entry.startswith("/"):
            raise ProjectExcludeConfigError(
                f"exclude_dirs entry {raw!r} is absolute; entries must be "
                f"walk-root-relative directory names or paths"
            )
        entry = entry.rstrip("/")
        if not entry:
            raise ProjectExcludeConfigError(
                f"exclude_dirs entry {raw!r} is empty after normalization; "
                f"entries must name a directory"
            )
        if any(segment in {"", ".", ".."} for segment in entry.split("/")):
            raise ProjectExcludeConfigError(
                f"exclude_dirs entry {raw!r} contains an empty, '.', or '..' "
                f"path segment; '.'/'..' escape the walk root and an empty "
                f"segment ('//') can never match a real path"
            )
        if "/" in entry:
            anchored.add(entry)
        else:
            names.add(entry)
    return frozenset(names), frozenset(anchored)


def load_project_excludes(project_root: Path) -> ProjectExcludes:
    """Read ``[tool.pydocs-mcp] exclude_dirs`` from the project's pyproject.

    Error posture (spec §8): missing file / missing table / missing key ->
    empty and SILENT (the normal case for virtually every project);
    unreadable, undecodable, or unparseable file (``OSError`` /
    ``UnicodeDecodeError`` / ``TOMLDecodeError`` — a half-saved TOML mid
    ``--watch`` can surface any of the three) -> loud warning, empty
    result (the floor still protects the dangerous directories, and an
    index run must not die on it); declared-but-wrong-typed
    ``exclude_dirs`` -> :class:`ProjectExcludeConfigError`.

    Example::

        load_project_excludes(Path("/repo")).matches("fixtures")
    """
    pyproject = project_root / "pyproject.toml"
    if not pyproject.is_file():
        return EMPTY_PROJECT_EXCLUDES
    try:
        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        # tomllib decodes the raw bytes as UTF-8 BEFORE parsing and does
        # not wrap UnicodeDecodeError; OSError covers permissions and the
        # is_file()/open() TOCTOU window. All three are the same half-saved
        # or unreadable-file scenario, so they share one degradation path.
        logger.warning(
            "could not read/parse %s: project excludes NOT applied "
            "(hardcoded floor + YAML excludes remain in effect)",
            pyproject,
        )
        return EMPTY_PROJECT_EXCLUDES
    tool = data.get("tool")
    table = tool.get(_TOOL_TABLE) if isinstance(tool, dict) else None
    if table is None:
        return EMPTY_PROJECT_EXCLUDES
    if not isinstance(table, dict):
        raise ProjectExcludeConfigError(
            f"[tool.{_TOOL_TABLE}] in {pyproject} must be a table; got {table!r}"
        )
    entries = table.get(_EXCLUDE_KEY)
    if entries is None:
        return EMPTY_PROJECT_EXCLUDES
    if not isinstance(entries, list):
        raise ProjectExcludeConfigError(
            f"[tool.{_TOOL_TABLE}] {_EXCLUDE_KEY} in {pyproject} must be a "
            f"list of strings; got {entries!r}"
        )
    names, anchored = split_exclude_entries(entries)
    return ProjectExcludes(names=names, anchored=anchored)


def merge_excludes(
    floor: frozenset[str],
    scope_entries: Sequence[str],
    loaded: ProjectExcludes,
) -> ProjectExcludes:
    """Pure union of the three sources (spec §3.3) — nothing subtracts.

    ``floor`` is the hardcoded ``_EXCLUDED_DIRS`` set (callers pass it in;
    this module never imports extraction/), ``scope_entries`` the raw YAML
    list for the walk's scope, ``loaded`` the pre-classified TOML result.
    The returned ``names`` always includes the floor.

    Example::

        merge_excludes(_EXCLUDED_DIRS, cfg.exclude_dirs, loader(root))
    """
    scope_names, scope_anchored = split_exclude_entries(scope_entries)
    return ProjectExcludes(
        names=floor | scope_names | loaded.names,
        anchored=scope_anchored | loaded.anchored,
    )


def exclusion_fingerprint(excludes: ProjectExcludes, floor: frozenset[str]) -> str | None:
    """Normalized fingerprint of the effective set for the content-hash fold.

    ``None`` iff the effective set equals the bare floor (no user excludes,
    no anchored entries) — the conditional no-fold case of spec §9.2 that
    keeps every pre-upgrade stored hash valid. Otherwise a deterministic
    string: all bare names sorted and tagged ``n:``, then all anchored
    paths sorted and tagged ``a:``, NUL-joined — the kind tag prevents the
    same string appearing in both sets from colliding.

    Example::

        exclusion_fingerprint(effective, _EXCLUDED_DIRS)  # None or "a:...\\x00n:..."
    """
    if excludes.names == floor and not excludes.anchored:
        return None
    tagged = [f"n:{name}" for name in sorted(excludes.names)]
    tagged += [f"a:{path}" for path in sorted(excludes.anchored)]
    return "\0".join(tagged)
