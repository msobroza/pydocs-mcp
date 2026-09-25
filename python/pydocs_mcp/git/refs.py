"""Git plumbing-file readers (spec §6.2) — no subprocess, safe on the request path.

Moved here from ``application/freshness.py`` so the git package owns every
git-format concern. Handles a ``.git`` directory, a worktree gitfile
(``gitdir:`` pointer + ``commondir`` delegation), loose refs, ``packed-refs``,
and detached HEAD. Any I/O error or unrecognized layout degrades to ``None``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

# The local-branch namespace; the one spelling every git-package reader and
# the subprocess adapter share.
HEADS_PREFIX = "refs/heads/"
# The remote-tracking namespace: ``refs/remotes/<remote>/<branch>`` (#318).
REMOTES_PREFIX = "refs/remotes/"
# What starts a symbolic ref's file (``HEAD``, ``refs/remotes/<remote>/HEAD``).
SYMREF_PREFIX = "ref:"


def _packed_ref_entries(text: str) -> Iterator[tuple[str, str]]:
    """``(ref, sha)`` of every entry line of a ``packed-refs`` file."""
    for line in text.splitlines():
        line = line.strip()
        # '#' = header, '^' = peeled-tag annotation for the line above.
        if not line or line.startswith(("#", "^")):
            continue
        sha, _, name = line.partition(" ")
        yield name, sha


def read_packed_refs(packed: Path, ref: str) -> str | None:
    for name, sha in _packed_ref_entries(packed.read_text(encoding="utf-8")):
        if name == ref:
            return sha
    return None


def locate_gitdir(project_root: Path) -> Path | None:
    """Resolve ``.git`` to a gitdir — a directory, or a worktree gitfile pointer.

    Degrades to ``None`` on any I/O error, per this module's contract: a ``.git``
    FILE (what every worktree and submodule uses) that is unreadable raises
    ``PermissionError`` and a non-UTF-8 one raises ``UnicodeDecodeError``. Either
    would otherwise escape ``git_repository_factory`` — called OUTSIDE
    ``WorkingTreeManifestBuilder.build``'s try — and abort the whole index pass,
    against spec R8 / §6.14 item 7.
    """
    try:
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
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError on a non-UTF-8 gitfile.
        return None


def refs_home(gitdir: Path) -> Path:
    """Worktree gitdirs keep only HEAD locally; refs live under ``commondir``."""
    commondir_file = gitdir / "commondir"
    if not commondir_file.is_file():
        return gitdir
    common = Path(commondir_file.read_text(encoding="utf-8").strip())
    return common if common.is_absolute() else (gitdir / common).resolve()


def _read_loose_ref(gitdir: Path, ref: str) -> str | None:
    """Stripped content of the first loose ref file (gitdir, then refs home); ``None`` if absent."""
    for candidate in (gitdir / ref, refs_home(gitdir) / ref):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip()
    return None


def resolve_ref(gitdir: Path, ref: str) -> str | None:
    """Loose file first, then the refs home, then ``packed-refs``."""
    loose = _read_loose_ref(gitdir, ref)
    if loose is not None:
        return loose or None
    packed = refs_home(gitdir) / "packed-refs"
    if packed.is_file():
        return read_packed_refs(packed, ref)
    return None


def resolve_symref(gitdir: Path, ref: str) -> str | None:
    """Commit sha behind ``ref`` after at most ONE ``ref:`` indirection; never raises.

    ``resolve_ref`` alone hands back the literal ``ref: …`` line of a symref
    file such as ``refs/remotes/origin/HEAD`` (spec R14). A plain ref (a sha
    file or a packed entry) resolves as usual. An unset symref, a symref whose
    target is missing, a chained symref and unreadable plumbing are ``None``:
    this runs on the request path, where a raise would fail a tool call.
    """
    try:
        loose = _read_loose_ref(gitdir, ref)
        if loose is not None and loose.startswith(SYMREF_PREFIX):
            target = loose.split(":", 1)[1].strip()
            return _sha_or_none(resolve_ref(gitdir, target)) if target else None
        return _sha_or_none(resolve_ref(gitdir, ref))
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError on a corrupted plumbing file.
        return None


def _sha_or_none(value: str | None) -> str | None:
    """Drop a second ``ref:`` indirection rather than return its text as a sha."""
    return None if value is None or value.startswith(SYMREF_PREFIX) else value


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
    if not head.startswith(SYMREF_PREFIX):
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
    return local_branch_of_head(head)


def read_head(gitdir: Path) -> str:
    """The raw ``HEAD`` line of ``gitdir`` (``ref: refs/heads/x`` or a sha); ``""``
    when unreadable. The ref watcher's snapshot reads it on every wake-up (#317)."""
    try:
        return (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        # ValueError covers UnicodeDecodeError on a corrupted plumbing file.
        return ""


def list_refs(gitdir: Path, prefix: str) -> dict[str, str]:
    """Every ref under ``prefix`` (``refs/heads/``) mapped to its file's content.

    ``packed-refs`` entries first, then the loose files, which win: git writes a
    moved ref loose and leaves the stale packed line behind. Read from the refs
    home, so a worktree lists its common dir's refs. The ref watcher's snapshot
    (#317) runs this on every wake-up, off the request path but inside a
    long-lived loop, so every read error degrades to "no such ref", never a raise.
    """
    try:
        home = refs_home(gitdir)
    except (OSError, ValueError):
        return {}
    return {**_packed_refs_under(home, prefix), **_loose_refs_under(home, prefix)}


def _packed_refs_under(home: Path, prefix: str) -> dict[str, str]:
    try:
        text = (home / "packed-refs").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    return {name: sha for name, sha in _packed_ref_entries(text) if name.startswith(prefix)}


def _loose_refs_under(home: Path, prefix: str) -> dict[str, str]:
    root = home / prefix
    try:
        # A ``.lock`` is git's in-flight write, renamed over the ref when done.
        files = sorted(p for p in root.rglob("*") if p.is_file() and not p.name.endswith(".lock"))
    except OSError:
        return {}
    refs = ((prefix + path.relative_to(root).as_posix(), _read_ref_file(path)) for path in files)
    return {name: value for name, value in refs if value}


def _read_ref_file(path: Path) -> str:
    """The stripped content; ``""`` when the file vanished or is unreadable (a
    ref deleted between the directory walk and the read)."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return ""


def local_branch_of_head(head: str) -> str | None:
    """The local branch a stripped ``HEAD`` line names; ``None`` when detached."""
    if not head.startswith(SYMREF_PREFIX):
        return None  # detached HEAD carries a raw sha, not a branch
    ref = head.split(":", 1)[1].strip()
    # A symbolic HEAD outside refs/heads/ (a remote-tracking or tag ref, which
    # `git switch --detach` and some tooling leave behind) names no local
    # branch. Returning the full ref string instead would hand callers a value
    # that looks like a branch name and matches no `branches` row.
    return ref.removeprefix(HEADS_PREFIX) if ref.startswith(HEADS_PREFIX) else None


# A worktree checkout: its root directory and the branch it has checked out
# (``None`` when detached).
WorktreeCheckout = tuple[Path, str | None]


def read_worktree_checkouts(project_root: Path) -> tuple[WorktreeCheckout, ...]:
    """Every worktree of ``project_root``'s repository with its checked-out branch.

    The plumbing twin of ``git worktree list`` (#314): the file tools find a
    selected branch's live checkout on the request path without spawning git
    (spec §6.6, AC-31). The main worktree comes first, then the linked ones by
    admin-directory name. A worktree whose directory is gone is left out, an
    unreadable admin entry costs only that entry, and a layout this reader does
    not recognize degrades to ``()``.
    """
    try:
        gitdir = locate_gitdir(project_root)
        if gitdir is None:
            return ()
        common = refs_home(gitdir)
        return (*_main_worktree(common), *_linked_worktrees(common))
    except (OSError, ValueError):
        return ()


def _checked_out_branch(admin_dir: Path) -> str | None:
    """The local branch the ``HEAD`` in ``admin_dir`` names; ``None`` when detached."""
    try:
        head = (admin_dir / "HEAD").read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None
    return local_branch_of_head(head)


def _main_worktree(common: Path) -> tuple[WorktreeCheckout, ...]:
    """The directory whose ``.git`` IS the common dir; none for a bare repository
    or a separated git dir, which have no main checkout to serve."""
    top = common.parent
    top_gitdir = locate_gitdir(top)
    if top_gitdir is None or top_gitdir.resolve() != common.resolve():
        return ()
    return ((top.resolve(), _checked_out_branch(common)),)


def _linked_worktrees(common: Path) -> tuple[WorktreeCheckout, ...]:
    admin = common / "worktrees"
    if not admin.is_dir():
        return ()
    rows = ((_linked_worktree_root(entry), entry) for entry in sorted(admin.iterdir()))
    return tuple((root, _checked_out_branch(entry)) for root, entry in rows if root is not None)


def _linked_worktree_root(entry: Path) -> Path | None:
    """The root a linked worktree's ``gitdir`` pointer names, when it still exists.

    The pointer holds the worktree's ``.git`` file, absolute by default and
    relative to ``entry`` under ``worktree.useRelativePaths``.
    """
    try:
        pointer = Path((entry / "gitdir").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    root = (pointer if pointer.is_absolute() else entry / pointer).parent.resolve()
    return root if root.is_dir() else None


# #318: per-worktree files naming the branch an operation in progress returns
# to while that worktree's HEAD is detached — a merge or interactive rebase and
# an am-style rebase (``head-name``: the full ref, or ``detached HEAD``), a
# bisect (``BISECT_START``: the short name, or the sha a detached start left,
# which names no branch). ``git worktree list`` reports such a worktree as
# detached.
_REBASE_HEAD_NAME_FILES = ("rebase-merge/head-name", "rebase-apply/head-name")
_BISECT_START_FILE = "BISECT_START"


def read_branches_held_by_worktrees(gitdir: Path) -> dict[str, Path]:
    """Every local branch some worktree is working on → that worktree's admin dir.

    Held: checked out (its ``HEAD`` names it), or the branch a rebase or a bisect
    in progress there returns to. git ends a rebase with a compare-and-swap on
    that branch, so moving it meanwhile strands the rebased work on a detached
    HEAD (#318, spec §6.8b layer 4). A worktree whose directory is gone still
    holds its branch, as git counts it until pruned. Plumbing only: an
    unreadable entry holds nothing, an unrecognized layout reads as ``{}``.
    """
    try:
        common = refs_home(gitdir)
        admins = (*_main_admin_dir(common), *_linked_admin_dirs(common))
        return {name: admin.resolve() for admin in admins for name in _branches_held_in(admin)}
    except (OSError, ValueError):
        return {}


def _main_admin_dir(common: Path) -> tuple[Path, ...]:
    """The common dir when it is a main worktree's own ``.git`` (not a bare repository)."""
    return (common,) if _main_worktree(common) else ()


def _linked_admin_dirs(common: Path) -> tuple[Path, ...]:
    admin = common / "worktrees"
    return tuple(sorted(admin.iterdir())) if admin.is_dir() else ()


def _branches_held_in(admin: Path) -> tuple[str, ...]:
    rebases = (_rebased_branch(admin / name) for name in _REBASE_HEAD_NAME_FILES)
    names = (_checked_out_branch(admin), *rebases, _read_ref_file(admin / _BISECT_START_FILE))
    return tuple(name for name in names if name)


def _rebased_branch(head_name: Path) -> str | None:
    ref = _read_ref_file(head_name)
    return ref.removeprefix(HEADS_PREFIX) if ref.startswith(HEADS_PREFIX) else None


_FETCH_HEAD = "FETCH_HEAD"


def read_last_fetch_time(gitdir: Path) -> float | None:
    """The newest ``FETCH_HEAD`` mtime (epoch seconds), ``None`` when nothing was
    ever fetched (#318: the behind-upstream signal's fetch age).

    A pseudo-ref, so per worktree: a linked worktree's gitdir and the common dir
    each hold the one their own fetches wrote. A ``stat``, never a subprocess.
    """
    try:
        homes = {gitdir, refs_home(gitdir)}
    except (OSError, ValueError):
        homes = {gitdir}
    times = [t for t in (_mtime(home / _FETCH_HEAD) for home in homes) if t is not None]
    return max(times, default=None)


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


__all__ = (
    "HEADS_PREFIX",
    "REMOTES_PREFIX",
    "SYMREF_PREFIX",
    "WorktreeCheckout",
    "list_refs",
    "local_branch_of_head",
    "locate_gitdir",
    "read_branches_held_by_worktrees",
    "read_head",
    "read_last_fetch_time",
    "read_packed_refs",
    "read_worktree_checkouts",
    "refs_home",
    "resolve_git_branch",
    "resolve_git_head",
    "resolve_ref",
    "resolve_symref",
)
