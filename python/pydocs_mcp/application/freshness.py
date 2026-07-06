"""Index-freshness probe — is the index current with the working tree? (spec §D4)

``resolve_git_head`` reads git plumbing files directly (``.git`` dir or
worktree gitfile → ``HEAD`` → loose ref / ``commondir`` / ``packed-refs``) —
no subprocess, so it is safe to call from a TTL-cached probe on every
response. Unresolvable layouts degrade to ``None`` (the envelope then
renders age-only, never a false stale warning).
"""

from __future__ import annotations

from pathlib import Path


def _read_packed_refs(packed: Path, ref: str) -> str | None:
    for line in packed.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        # '#' = header, '^' = peeled-tag annotation for the line above.
        if not line or line.startswith(("#", "^")):
            continue
        sha, _, name = line.partition(" ")
        if name == ref:
            return sha
    return None


def resolve_git_head(project_root: Path) -> str | None:
    """Return the commit sha ``HEAD`` points at, or ``None`` when unresolvable.

    Handles: regular ``.git`` directories, detached HEAD (raw sha), loose
    refs, worktree gitfiles (``gitdir:`` pointer + ``commondir`` delegation),
    and ``packed-refs``. Any I/O error or unrecognized layout → ``None``.
    """
    git = project_root / ".git"
    try:
        if git.is_file():
            content = git.read_text(encoding="utf-8").strip()
            if not content.startswith("gitdir:"):
                return None
            gitdir = Path(content.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = (project_root / gitdir).resolve()
        elif git.is_dir():
            gitdir = git
        else:
            return None

        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head or None  # detached HEAD stores the raw sha
        ref = head.split(":", 1)[1].strip()

        loose = gitdir / ref
        if loose.is_file():
            return loose.read_text(encoding="utf-8").strip() or None

        # Worktree gitdirs keep only HEAD locally; refs + packed-refs live in
        # the main repo's gitdir, reachable via the ``commondir`` pointer.
        commondir_file = gitdir / "commondir"
        if commondir_file.is_file():
            common = Path(commondir_file.read_text(encoding="utf-8").strip())
            if not common.is_absolute():
                common = (gitdir / common).resolve()
            loose = common / ref
            if loose.is_file():
                return loose.read_text(encoding="utf-8").strip() or None
            packed = common / "packed-refs"
        else:
            packed = gitdir / "packed-refs"

        if packed.is_file():
            return _read_packed_refs(packed, ref)
        return None
    except OSError:
        return None
