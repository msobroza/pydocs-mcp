"""Base-branch resolution, tracking selection, and LRU eviction (spec §6.5, §6.9, R14).

Pure functions over the git port and the YAML config: nothing here opens a
database or spawns anything the port does not. ``plumbing_base_tip`` and
``snapshot_base_tip_ref`` are the entry points the watcher and the request path
may use — they read the plumbing files (or a snapshot of them) only (spec §6.5c
read-time rules).
"""

from __future__ import annotations

import fnmatch
import json
import logging
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.refs import HEADS_PREFIX, resolve_symref
from pydocs_mcp.models import BranchStatus
from pydocs_mcp.retrieval.config.git_models import (
    ALL_LOCAL_TRACK_ENTRY,
    AUTO_BASE_ENTRY,
    CHECKED_OUT_TRACK_ENTRY,
    GitBranchesConfig,
    GitConfig,
)
from pydocs_mcp.storage.branch_records import BranchRecord

log = logging.getLogger("pydocs-mcp")

# R14: after the remote HEAD symref, these names in this order.
_BASE_NAME_CANDIDATES = ("main", "master")
_REMOTES_PREFIX = "refs/remotes/"
_REMOTE_HEAD = "HEAD"
_SYMREF_PREFIX = "ref:"
_EVICTABLE = frozenset({BranchStatus.ACTIVE, BranchStatus.INACTIVE})


@dataclass(frozen=True, slots=True)
class BaseBranch:
    """The base branch and the tip a diff is anchored against (spec §6.5).

    ``tracking_ref`` is ``refs/remotes/<remote>/<name>`` when the tip came from
    the remote-tracking ref, else ``None`` (the tip is the local branch's).
    """

    name: str
    tip_sha: str
    tracking_ref: str | None


def _remote_tracking_ref(remote: str, name: str) -> str:
    return f"{_REMOTES_PREFIX}{remote}/{name}"


def _tracking_ref(config: GitConfig, name: str) -> str:
    return _remote_tracking_ref(config.remote.name, name)


def _base_name_in_remote(target: str | None, remote: str) -> str | None:
    """The branch a ``refs/remotes/<remote>/HEAD`` symref target names.

    The whole remote prefix is stripped (not the last path segment), so a base
    such as ``release/1.0`` keeps its name; a target outside the remote's
    namespace names no base of that remote and is ignored.
    """
    prefix = _remote_tracking_ref(remote, "")
    if not target or not target.startswith(prefix):
        return None
    return target.removeprefix(prefix) or None


def _remote_head_name(git: GitRepository, config: GitConfig) -> str | None:
    """The branch ``refs/remotes/<remote>/HEAD`` names, read as a symref only.

    R14: never ``--abbrev-ref``, which echoes the literal name when the symref
    is unset.
    """
    target = git.symbolic_ref(_tracking_ref(config, _REMOTE_HEAD))
    return _base_name_in_remote(target, config.remote.name)


def _auto_candidates(remote_head: str | None) -> tuple[str, ...]:
    """R14's chain: the remote HEAD's branch, then ``main``, then ``master``."""
    chain = (remote_head, *_BASE_NAME_CANDIDATES) if remote_head else _BASE_NAME_CANDIDATES
    return tuple(dict.fromkeys(chain))


def _candidate_names(git: GitRepository, config: GitConfig) -> tuple[str, ...]:
    """An explicit ``git.branches.base`` is the only candidate; ``auto`` is R14's chain."""
    if config.branches.base != AUTO_BASE_ENTRY:
        return (config.branches.base,)
    return _auto_candidates(_remote_head_name(git, config))


def _base_with_tip(git: GitRepository, config: GitConfig, name: str) -> BaseBranch | None:
    """Spec §6.5 tip rule: the remote-tracking ref when it exists, else the local branch."""
    tracking = _tracking_ref(config, name)
    remote_tip = git.head_sha(tracking)
    if remote_tip:
        return BaseBranch(name, remote_tip, tracking)
    local_tip = git.head_sha(f"{HEADS_PREFIX}{name}")
    return BaseBranch(name, local_tip, None) if local_tip else None


def _log_base_unresolved(config: GitConfig, tried: Sequence[str]) -> None:
    """#308: no base is a logged outcome, never an error. An explicit base the
    operator asked for warns; ``auto`` finding nothing only informs (the
    ``git_unavailable`` precedent in ``git/factory.py``)."""
    explicit = config.branches.base != AUTO_BASE_ENTRY
    payload = {
        "event": "base_branch_unresolved",
        "configured_base": config.branches.base,
        "remote": config.remote.name,
        "tried": list(tried),
    }
    log.log(logging.WARNING if explicit else logging.INFO, json.dumps(payload))


def resolve_base_branch(git: GitRepository, config: GitConfig) -> BaseBranch | None:
    """The first R14 candidate whose tip resolves; ``None`` (logged) when none does."""
    candidates = _candidate_names(git, config)
    for name in candidates:
        base = _base_with_tip(git, config, name)
        if base is not None:
            return base
    _log_base_unresolved(config, candidates)
    return None


def plumbing_base_tip(gitdir: Path, base: BaseBranch) -> str | None:
    """The live base tip through the plumbing readers — no subprocess (spec §6.5c).

    ``resolve_symref`` rather than ``resolve_ref``: it degrades every read
    error to ``None``, which the request path needs (a raise would fail a tool
    call).
    """
    return resolve_symref(gitdir, base.tracking_ref or f"{HEADS_PREFIX}{base.name}")


def snapshot_base_tip_ref(
    heads: Mapping[str, str], remotes: Mapping[str, str], *, configured_base: str, remote: str
) -> str | None:
    """The full ref of the base tip in one ref snapshot — no subprocess (#317).

    The plumbing twin of :func:`resolve_base_branch`: the same R14 candidates
    (``configured_base`` is ``git.branches.base``), the same §6.5 tip rule (the
    remote-tracking ref when the snapshot holds it, else the local branch).
    ``heads`` / ``remotes`` map full ref names (``refs/remotes/<remote>/…``) to
    their file content, a symref's being its ``ref: …`` line. The ref watcher
    calls it on every snapshot, so a remote the first push adds, or the first
    commit of an unborn base, is followed without a restart.
    """
    for name in _snapshot_candidates(remotes, configured_base, remote):
        tracking, local = _remote_tracking_ref(remote, name), f"{HEADS_PREFIX}{name}"
        if tracking in remotes:
            return tracking
        if local in heads:
            return local
    return None


def _snapshot_candidates(
    remotes: Mapping[str, str], configured_base: str, remote: str
) -> tuple[str, ...]:
    if configured_base != AUTO_BASE_ENTRY:
        return (configured_base,)
    line = remotes.get(_remote_tracking_ref(remote, _REMOTE_HEAD), "")
    target = line.removeprefix(_SYMREF_PREFIX).strip() if line.startswith(_SYMREF_PREFIX) else None
    return _auto_candidates(_base_name_in_remote(target, remote))


def _expand_track_entry(entry: str, local: Sequence[str], checked_out: str | None) -> list[str]:
    if entry == CHECKED_OUT_TRACK_ENTRY:
        return [checked_out] if checked_out else []
    if entry == ALL_LOCAL_TRACK_ENTRY:
        return list(local)
    # Case-sensitive on every OS: git branch names are, and ``fnmatch.fnmatch``
    # would fold case on Windows.
    return [name for name in local if fnmatch.fnmatchcase(name, entry)]


def select_tracked_branches(
    config: GitBranchesConfig, local_branches: Sequence[str], checked_out: str | None
) -> tuple[str, ...]:
    """Expand ``branches.track`` in order, dropping duplicates (spec §6.9)."""
    chosen: dict[str, None] = {}
    for entry in config.track:
        chosen.update(dict.fromkeys(_expand_track_entry(entry, local_branches, checked_out)))
    return tuple(chosen)


def _is_evictable(record: BranchRecord, protected: Collection[str]) -> bool:
    return (
        record.status in _EVICTABLE
        and not record.pinned
        and not record.is_landing_unit
        and record.name not in protected
    )


def lru_evictions(
    records: Sequence[BranchRecord], retain_recent: int, *, protected: Collection[str]
) -> tuple[str, ...]:
    """Names beyond the ``retain_recent`` most recently used, never a pinned,
    protected, retired, or landing-unit row (spec §6.5b, §6.8a, D13)."""
    candidates = sorted(
        (r for r in records if _is_evictable(r, protected)),
        key=lambda r: r.last_used_at,
        reverse=True,
    )
    return tuple(r.name for r in candidates[retain_recent:])


__all__ = (
    "BaseBranch",
    "lru_evictions",
    "plumbing_base_tip",
    "resolve_base_branch",
    "select_tracked_branches",
    "snapshot_base_tip_ref",
)
