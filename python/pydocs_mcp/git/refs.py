"""Git plumbing-file readers (spec §6.2) — no subprocess, safe on the request path.

Moved here from ``application/freshness.py`` so the git package owns every
git-format concern. Handles a ``.git`` directory, a worktree gitfile
(``gitdir:`` pointer + ``commondir`` delegation), loose refs, ``packed-refs``,
and detached HEAD. Any I/O error or unrecognized layout degrades to ``None``.
"""

from __future__ import annotations

from pathlib import Path

_HEADS_PREFIX = "refs/heads/"


def read_packed_refs(packed: Path, ref: str) -> str | None:
    for line in packed.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        # '#' = header, '^' = peeled-tag annotation for the line above.
        if not line or line.startswith(("#", "^")):
            continue
        sha, _, name = line.partition(" ")
        if name == ref:
            return sha
    return None


def locate_gitdir(project_root: Path) -> Path | None:
    """Resolve ``.git`` to a gitdir — a directory, or a worktree gitfile pointer."""
    git = project_root / ".git"
    if git.is_dir():
        return git
    if not git.is_file():
        return None
    content = git.read_text(encoding="utf-8").strip()
    if not content.startswith("gitdir:"):
        return None
    gitdir = Path(content.split(":", 1)[1].strip())
    return gitdir if gitdir.is_absolute() else (project_root / gitdir).resolve()


def refs_home(gitdir: Path) -> Path:
    """Worktree gitdirs keep only HEAD locally; refs live under ``commondir``."""
    commondir_file = gitdir / "commondir"
    if not commondir_file.is_file():
        return gitdir
    common = Path(commondir_file.read_text(encoding="utf-8").strip())
    return common if common.is_absolute() else (gitdir / common).resolve()


def resolve_ref(gitdir: Path, ref: str) -> str | None:
    """Loose file first, then the refs home, then ``packed-refs``."""
    for candidate in (gitdir / ref, refs_home(gitdir) / ref):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip() or None
    packed = refs_home(gitdir) / "packed-refs"
    if packed.is_file():
        return read_packed_refs(packed, ref)
    return None


def _gitdir_and_head(project_root: Path) -> tuple[Path, str] | None:
    """The gitdir plus its raw ``HEAD`` line, or ``None`` for a non-repo / unreadable layout.

    Returns both so a symbolic HEAD resolves against the gitdir already located
    here instead of walking ``.git`` a second time.
    """
    try:
        gitdir = locate_gitdir(project_root)
        if gitdir is None:
            return None
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        return (gitdir, head) if head else None
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError on a corrupted plumbing file.
        return None


def resolve_git_head(project_root: Path) -> str | None:
    """Commit sha ``HEAD`` points at, or ``None`` when unresolvable."""
    located = _gitdir_and_head(project_root)
    if located is None:
        return None
    gitdir, head = located
    if not head.startswith("ref:"):
        return head  # detached HEAD stores the raw sha
    try:
        return resolve_ref(gitdir, head.split(":", 1)[1].strip())
    except (OSError, ValueError):
        return None


def resolve_git_branch(project_root: Path) -> str | None:
    """Short branch name ``HEAD`` points at; ``None`` when detached or unresolvable."""
    located = _gitdir_and_head(project_root)
    if located is None:
        return None
    _, head = located
    if not head.startswith("ref:"):
        return None  # detached HEAD carries a raw sha, not a branch
    return head.split(":", 1)[1].strip().removeprefix(_HEADS_PREFIX)


__all__ = (
    "locate_gitdir",
    "read_packed_refs",
    "refs_home",
    "resolve_git_branch",
    "resolve_git_head",
    "resolve_ref",
)
