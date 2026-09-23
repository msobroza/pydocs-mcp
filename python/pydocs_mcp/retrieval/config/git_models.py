"""``git:`` configuration (spec §6.9): enablement, binary, timeout, and the P1
``branches`` / ``ref_watch`` / ``remote`` blocks (ticket #308).

Deployment knobs, never MCP tool params (CLAUDE.md §"MCP API surface vs YAML
configuration"). Every default is a module constant (the single-source rule);
the owner-ratified ones carry their spec §11 decision id, so an override is a
one-line change. P2 adds ``changed_scope`` / ``diff_chunks``; until then those
keys fail the load like any other unknown key.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pydocs_mcp.retrieval.config.models import _MAX_WATCH_DEBOUNCE_MS

_DEFAULT_GIT_BINARY = "git"
_DEFAULT_GIT_TIMEOUT_SECONDS = 30.0

# Reserved words (spec §6.9). A ``branches.track`` entry other than the two
# reserved ones is a branch name or an fnmatch glob over the local branch
# names: git refuses ``*``, ``?`` and ``[`` in a ref name, so a glob never
# shadows a branch. A ``branches.base`` other than ``auto`` names the base.
CHECKED_OUT_TRACK_ENTRY = "checked_out"
ALL_LOCAL_TRACK_ENTRY = "all_local"
AUTO_BASE_ENTRY = "auto"

_DEFAULT_TRACK = (CHECKED_OUT_TRACK_ENTRY,)  # O4
_DEFAULT_BASE = AUTO_BASE_ENTRY
_DEFAULT_RETAIN_RECENT = 8  # O4
_DEFAULT_GRACE_DAYS = 7  # O12
_DEFAULT_AUTO_RETIRE_MERGED = True
_DEFAULT_AUTO_RETIRE_DELETED = True
_DEFAULT_LOOKBACK_LANDINGS = 200  # O16

_DEFAULT_REF_WATCH_ENABLED = True
_DEFAULT_REF_WATCH_DEBOUNCE_MS = 1000
_DEFAULT_RECONCILE_SECONDS = 60

_DEFAULT_REMOTE_NAME = "origin"
_DEFAULT_BEHIND_HINT = True
_DEFAULT_AUTO_FETCH_ENABLED = False  # O14
_DEFAULT_AUTO_FETCH_INTERVAL_SECONDS = 60
_DEFAULT_LS_REMOTE_TIMEOUT_SECONDS = 10.0
_DEFAULT_BACKOFF_MAX_SECONDS = 1800
_DEFAULT_FAST_FORWARD_BRANCHES_WITHOUT_WORKTREE = False


def _reject_blank_name(key: str, value: str) -> str:
    # git refuses whitespace in a ref or remote name, so a blank value matches
    # nothing: it would silently resolve no base or watch no ref (#308).
    if not value.strip():
        raise ValueError(f"{key}: got {value!r}; expected a name that is not blank")
    return value


class GitEnablement(StrEnum):
    """``auto``: on when a git binary and a repository are found; ``on`` / ``off``."""

    AUTO = "auto"
    ON = "on"
    OFF = "off"


class BranchRetentionConfig(BaseModel):
    """How long indexed branches stay (spec §6.8a): LRU size and the retired-row grace."""

    model_config = ConfigDict(extra="forbid")

    retain_recent: int = Field(default=_DEFAULT_RETAIN_RECENT, ge=1)
    # 0 purges a retired branch's rows on the next maintenance pass.
    grace_days: int = Field(default=_DEFAULT_GRACE_DAYS, ge=0)
    auto_retire_merged: bool = _DEFAULT_AUTO_RETIRE_MERGED
    auto_retire_deleted: bool = _DEFAULT_AUTO_RETIRE_DELETED


class MergeDetectionConfig(BaseModel):
    """Squash and rebase-merge detection bounds (spec §6.8a)."""

    model_config = ConfigDict(extra="forbid")

    # Base first-parent steps whose patch-ids are compared with a branch's
    # merge-base diff; independent of the diff retention window (O16).
    lookback_landings: int = Field(default=_DEFAULT_LOOKBACK_LANDINGS, ge=1)


class GitBranchesConfig(BaseModel):
    """Which branches are indexed and against which base (spec §6.5, §6.9)."""

    model_config = ConfigDict(extra="forbid")

    track: list[str] = Field(default_factory=lambda: list(_DEFAULT_TRACK))
    base: str = Field(default=_DEFAULT_BASE, min_length=1)
    retention: BranchRetentionConfig = Field(default_factory=BranchRetentionConfig)
    merge_detection: MergeDetectionConfig = Field(default_factory=MergeDetectionConfig)

    @field_validator("track")
    @classmethod
    def _track_entries_are_not_blank(cls, entries: list[str]) -> list[str]:
        # A blank entry matches no branch: it would silently track nothing.
        if any(not entry.strip() for entry in entries):
            raise ValueError(
                f"git.branches.track: got a blank entry in {entries!r}; expected "
                f"{CHECKED_OUT_TRACK_ENTRY!r}, {ALL_LOCAL_TRACK_ENTRY!r}, a branch name or a glob"
            )
        return entries

    @field_validator("base")
    @classmethod
    def _base_is_not_blank(cls, base: str) -> str:
        return _reject_blank_name("git.branches.base", base)


class RefWatchConfig(BaseModel):
    """Ref-driven refresh under ``serve`` / ``watch`` (spec §6.8); on by default."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = _DEFAULT_REF_WATCH_ENABLED
    # serve.watch's ceiling, exclusive as there (spec §6.8: the WatchConfig
    # bound pattern): one constant, so the two debounce knobs cannot drift.
    debounce_ms: int = Field(
        default=_DEFAULT_REF_WATCH_DEBOUNCE_MS, ge=1, lt=_MAX_WATCH_DEBOUNCE_MS
    )
    # Re-snapshot the refs without an event: the inotify-overflow safety net.
    reconcile_seconds: int = Field(default=_DEFAULT_RECONCILE_SECONDS, ge=1)


class AutoFetchConfig(BaseModel):
    """Layer 3 of the remote lane (spec §6.8b): change-detect, then fetch; off (O14)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = _DEFAULT_AUTO_FETCH_ENABLED
    interval_seconds: int = Field(default=_DEFAULT_AUTO_FETCH_INTERVAL_SECONDS, ge=1)
    ls_remote_timeout_seconds: float = Field(default=_DEFAULT_LS_REMOTE_TIMEOUT_SECONDS, gt=0)
    backoff_max_seconds: int = Field(default=_DEFAULT_BACKOFF_MAX_SECONDS, ge=1)

    @model_validator(mode="after")
    def _backoff_ceiling_is_not_below_the_interval(self) -> AutoFetchConfig:
        # The backoff doubles the interval up to this ceiling; a lower ceiling
        # would probe an unreachable remote MORE often than a healthy one.
        if self.backoff_max_seconds < self.interval_seconds:
            raise ValueError(
                f"git.remote.auto_fetch: got backoff_max_seconds={self.backoff_max_seconds} "
                f"below interval_seconds={self.interval_seconds}; expected a ceiling "
                "at least as long as the check interval"
            )
        return self


class RemoteConfig(BaseModel):
    """The remote that counts and the four remote-sync layers (spec §6.8b)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default=_DEFAULT_REMOTE_NAME, min_length=1)
    behind_hint: bool = _DEFAULT_BEHIND_HINT
    track_refs: list[str] = Field(default_factory=list)
    auto_fetch: AutoFetchConfig = Field(default_factory=AutoFetchConfig)
    fast_forward_branches_without_worktree: bool = _DEFAULT_FAST_FORWARD_BRANCHES_WITHOUT_WORKTREE

    @field_validator("name")
    @classmethod
    def _name_is_not_blank(cls, name: str) -> str:
        return _reject_blank_name("git.remote.name", name)

    @model_validator(mode="after")
    def _track_refs_live_under_the_remote(self) -> RemoteConfig:
        # Only refs/remotes/<name>/ is watched and fetched (spec §6.8), so a
        # ref under another remote would be indexed once and then go stale.
        prefix = f"{self.name}/"
        stray = [ref for ref in self.track_refs if not ref.startswith(prefix) or ref == prefix]
        if stray:
            raise ValueError(
                f"git.remote.track_refs: got {stray!r}; expected '{prefix}<branch>' "
                f"entries (git.remote.name is {self.name!r})"
            )
        return self


class GitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: GitEnablement = GitEnablement.AUTO
    binary: str = Field(default=_DEFAULT_GIT_BINARY, min_length=1)
    timeout_seconds: float = Field(default=_DEFAULT_GIT_TIMEOUT_SECONDS, gt=0)
    branches: GitBranchesConfig = Field(default_factory=GitBranchesConfig)
    ref_watch: RefWatchConfig = Field(default_factory=RefWatchConfig)
    remote: RemoteConfig = Field(default_factory=RemoteConfig)
