"""The single path normalizer (ADR 0011 — "one path normalizer").

This is the ONLY code allowed to reconcile paths across trace sources so the
attributor can compare a tool-emitted path against a gold-patch path. It folds
the three verified conventions into one normal form:

- index-root-relative — index-backed tools
  (``extraction/strategies/chunkers/_shared.py`` ``_relpath``)
- project-root-relative POSIX — filesystem tools, project files
  (``application/file_tools.py`` module docstring)
- absolute — filesystem tools' dependency files and the loop's client-side
  Read tool

Normal form: **workspace-root-relative POSIX**. Index-root == project-root ==
the checked-out workspace root for a rollout, so both relative conventions are
already workspace-relative and only need POSIX cleanup. An absolute path under
the workspace is made relative; an absolute path OUTSIDE the workspace is a
dependency file (site-packages) — it stays absolute and is flagged
``gold_matchable=False``, because gold diffs are workspace-relative by
construction (``a/…``/``b/…``, ``-p1``) and a dependency path can never
legitimately match a gold file (ADR 0011). ``posixpath`` is used explicitly so
normalization is byte-identical on every platform (R6).
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass

# macOS firmlinks: the top-level dirs /var, /tmp, /etc are symlinks into
# /private, so /private/var/X and /var/X denote the SAME location. The loop's
# client-side Read tool canonicalizes temp workspaces to the /private form while
# the rollout driver records workspace_root under the bare firmlink alias (real
# bug: 3c63ee67's successful file_path Read was /private/var/... against a
# /var/... workspace_root and got mis-excluded as a dependency). Folded purely
# lexically — no filesystem access — so normalization stays byte-identical on
# every platform (R6): a non-macOS trace never carries a /private/{var,tmp,etc}
# absolute path, so the collapse is a no-op there.
_MACOS_FIRMLINK_PREFIXES = ("/private/var/", "/private/tmp/", "/private/etc/")


@dataclass(frozen=True, slots=True)
class NormalizedPath:
    """A path reduced to the workspace-relative POSIX normal form.

    ``value`` is workspace-root-relative POSIX when ``gold_matchable`` is True,
    or the cleaned absolute path when False (a dependency outside the workspace,
    excluded from gold matching).
    """

    value: str
    gold_matchable: bool


def normalize_path(raw: str, *, workspace_root: str) -> NormalizedPath:
    """Fold ``raw`` into the workspace-relative POSIX normal form (ADR 0011).

    ``workspace_root`` must be an absolute POSIX path. A relative ``raw`` is
    already workspace-relative and is only POSIX-normalized. An absolute ``raw``
    under ``workspace_root`` becomes relative; one outside stays absolute and is
    marked ``gold_matchable=False``.

    Example:
        >>> normalize_path("./src/a.py", workspace_root="/ws").value
        'src/a.py'
        >>> normalize_path("/venv/lib/dep.py", workspace_root="/ws").gold_matchable
        False
    """
    if not raw:
        raise ValueError(f"empty path: got {raw!r}, expected a non-empty path string")
    if not posixpath.isabs(workspace_root):
        raise ValueError(f"workspace_root must be absolute POSIX: got {workspace_root!r}")
    if not posixpath.isabs(raw):
        return NormalizedPath(posixpath.normpath(raw), gold_matchable=True)
    return _normalize_absolute(
        _fold_firmlink(posixpath.normpath(raw)),
        _fold_firmlink(posixpath.normpath(workspace_root)),
    )


def _fold_firmlink(norm: str) -> str:
    """Collapse a leading macOS firmlink prefix (/private/var → /var, …).

    Applied to both the raw path and workspace_root so the prefix comparison in
    :func:`_normalize_absolute` is symmetric regardless of which side recorded
    the /private form. A path outside the firmlink set is returned unchanged.
    """
    for prefix in _MACOS_FIRMLINK_PREFIXES:
        if norm.startswith(prefix):
            return norm[len("/private") :]
    return norm


def _normalize_absolute(norm: str, root: str) -> NormalizedPath:
    """Relativize an absolute path under ``root``; else flag it as a dependency."""
    if norm == root or norm.startswith(root + "/"):
        return NormalizedPath(posixpath.relpath(norm, root), gold_matchable=True)
    return NormalizedPath(norm, gold_matchable=False)
