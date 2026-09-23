"""The P1 ``git:`` sub-models (spec §6.9, ticket #308): defaults, bounds, env, forbid extras."""

from __future__ import annotations

import re
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.git_models import (
    ALL_LOCAL_TRACK_ENTRY,
    AUTO_BASE_ENTRY,
    CHECKED_OUT_TRACK_ENTRY,
    AutoFetchConfig,
    BranchRetentionConfig,
    GitBranchesConfig,
    GitConfig,
    MergeDetectionConfig,
    RefWatchConfig,
    RemoteConfig,
)


def _overlay(tmp_path: Path, body: str) -> AppConfig:
    path = tmp_path / "pydocs-mcp.yaml"
    path.write_text(body, encoding="utf-8")
    return AppConfig.load(explicit_path=path)


# ── the ratified defaults (spec §11) ───────────────────────────────────────


def test_shipped_yaml_loads_the_ratified_defaults() -> None:
    git = AppConfig.load().git
    assert git.branches.track == [CHECKED_OUT_TRACK_ENTRY]  # O4
    assert git.branches.base == AUTO_BASE_ENTRY
    assert git.branches.retention.retain_recent == 8  # O4
    assert git.branches.retention.grace_days == 7  # O12
    assert git.branches.retention.auto_retire_merged is True
    assert git.branches.retention.auto_retire_deleted is True
    assert git.branches.merge_detection.lookback_landings == 200  # O16
    assert git.ref_watch.enabled is True
    assert git.ref_watch.debounce_ms == 1000
    assert git.ref_watch.reconcile_seconds == 60
    assert git.remote.name == "origin"
    assert git.remote.behind_hint is True
    assert git.remote.track_refs == []
    assert git.remote.auto_fetch.enabled is False  # O14
    assert git.remote.auto_fetch.interval_seconds == 60
    assert git.remote.auto_fetch.ls_remote_timeout_seconds == 10.0
    assert git.remote.auto_fetch.backoff_max_seconds == 1800
    assert git.remote.fast_forward_branches_without_worktree is False


def test_shipped_yaml_restates_the_python_defaults() -> None:
    """``default_config.yaml`` restates the module constants; the two must not drift."""
    assert AppConfig.load().git == GitConfig()


def _model_key_tree(model: type[BaseModel]) -> dict[str, Any]:
    return {
        name: _model_key_tree(field.annotation)
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
        else None
        for name, field in model.model_fields.items()
    }


def _yaml_key_tree(block: dict[str, Any]) -> dict[str, Any]:
    return {k: _yaml_key_tree(v) if isinstance(v, dict) else None for k, v in block.items()}


def test_shipped_yaml_states_every_git_key() -> None:
    """The shipped YAML is the canonical reference of every tunable (CLAUDE.md)."""
    shipped = files("pydocs_mcp.defaults").joinpath("default_config.yaml")
    block = yaml.safe_load(shipped.read_text(encoding="utf-8"))["git"]
    assert _yaml_key_tree(block) == _model_key_tree(GitConfig)


def test_default_track_list_is_not_shared_between_instances() -> None:
    first = GitBranchesConfig()
    first.track.append("main")
    assert GitBranchesConfig().track == [CHECKED_OUT_TRACK_ENTRY]


def test_reserved_words_are_the_spec_spellings() -> None:
    assert (CHECKED_OUT_TRACK_ENTRY, ALL_LOCAL_TRACK_ENTRY) == ("checked_out", "all_local")
    assert AUTO_BASE_ENTRY == "auto"


# ── track entries ──────────────────────────────────────────────────────────


def test_track_entries_accept_names_globs_and_the_two_reserved_words() -> None:
    entries = [CHECKED_OUT_TRACK_ENTRY, "release/*", ALL_LOCAL_TRACK_ENTRY, "main"]
    assert GitBranchesConfig(track=entries).track == entries


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_blank_track_entry_is_rejected_with_the_offending_list(blank: str) -> None:
    entries = ["main", blank]
    offending = re.escape(repr(entries))
    with pytest.raises(ValidationError, match=rf"git\.branches\.track: got .* in {offending}"):
        GitBranchesConfig(track=entries)


@pytest.mark.parametrize("blank", ["   ", "\t"])
def test_a_blank_base_is_rejected_with_the_offending_value(blank: str) -> None:
    # git refuses whitespace in a ref name: a blank base would resolve no base.
    with pytest.raises(
        ValidationError, match=rf"git\.branches\.base: got {re.escape(repr(blank))}"
    ):
        GitBranchesConfig(base=blank)


@pytest.mark.parametrize("blank", ["   ", "\t"])
def test_a_blank_remote_name_is_rejected_with_the_offending_value(blank: str) -> None:
    # git refuses whitespace in a remote name: a blank one would watch no ref.
    with pytest.raises(ValidationError, match=rf"git\.remote\.name: got {re.escape(repr(blank))}"):
        RemoteConfig(name=blank)


@pytest.mark.parametrize("blank", ["", "\t"])
def test_a_blank_remote_track_ref_is_rejected(blank: str) -> None:
    with pytest.raises(ValidationError, match=r"git\.remote\.track_refs"):
        RemoteConfig(track_refs=["origin/main", blank])


def test_remote_track_refs_accept_remote_tracking_names() -> None:
    assert RemoteConfig(track_refs=["origin/main"]).track_refs == ["origin/main"]
    renamed = RemoteConfig(name="upstream", track_refs=["upstream/release/1.x"])
    assert renamed.track_refs == ["upstream/release/1.x"]


@pytest.mark.parametrize("stray", ["upstream/main", "main", "origin/", "origin"])
def test_a_track_ref_outside_the_configured_remote_is_rejected(stray: str) -> None:
    # Only refs/remotes/<name>/ is watched and fetched: another remote's ref
    # would be indexed once and then never refreshed.
    with pytest.raises(ValidationError, match=r"git\.remote\.track_refs.*'origin/<branch>'"):
        RemoteConfig(track_refs=[stray])


# ── bounds ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("model", "kwargs", "error_type"),
    [
        (BranchRetentionConfig, {"retain_recent": 0}, "greater_than_equal"),
        (BranchRetentionConfig, {"grace_days": -1}, "greater_than_equal"),
        (MergeDetectionConfig, {"lookback_landings": 0}, "greater_than_equal"),
        (GitBranchesConfig, {"base": ""}, "string_too_short"),
        (RefWatchConfig, {"debounce_ms": 0}, "greater_than_equal"),
        # serve.watch's exclusive ceiling (spec §6.8: the WatchConfig bound pattern).
        (RefWatchConfig, {"debounce_ms": 60_000}, "less_than"),
        (RefWatchConfig, {"reconcile_seconds": 0}, "greater_than_equal"),
        (AutoFetchConfig, {"interval_seconds": 0}, "greater_than_equal"),
        (AutoFetchConfig, {"ls_remote_timeout_seconds": 0}, "greater_than"),
        # The field bound itself, not the backoff-vs-interval model validator.
        (AutoFetchConfig, {"backoff_max_seconds": 0}, "greater_than_equal"),
        (RemoteConfig, {"name": ""}, "string_too_short"),
    ],
)
def test_out_of_bound_values_are_rejected(
    model: type[BaseModel], kwargs: dict[str, Any], error_type: str
) -> None:
    (field,) = kwargs
    with pytest.raises(ValidationError, match=rf"\n{field}\n  [^\n]*\[type={error_type},"):
        model(**kwargs)


def test_the_edge_values_inside_the_bounds_load() -> None:
    assert BranchRetentionConfig(grace_days=0).grace_days == 0  # immediate purge
    assert RefWatchConfig(debounce_ms=59_999).debounce_ms == 59_999
    assert RefWatchConfig(debounce_ms=1).debounce_ms == 1


def test_a_backoff_ceiling_below_the_check_interval_is_rejected() -> None:
    with pytest.raises(ValidationError, match=r"backoff_max_seconds.*interval_seconds"):
        AutoFetchConfig(interval_seconds=120, backoff_max_seconds=60)
    assert AutoFetchConfig(interval_seconds=60, backoff_max_seconds=60).backoff_max_seconds == 60


# ── unknown keys ───────────────────────────────────────────────────────────


def _nest(path: tuple[str, ...], leaf: dict[str, Any]) -> dict[str, Any]:
    for key in reversed(path):
        leaf = {key: leaf}
    return leaf


@pytest.mark.parametrize(
    "path",
    [
        ("branches",),
        ("branches", "retention"),
        ("branches", "merge_detection"),
        ("ref_watch",),
        ("remote",),
        ("remote", "auto_fetch"),
    ],
)
def test_an_unknown_key_is_rejected_at_every_level(path: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="unknown_key"):
        GitConfig.model_validate(_nest(path, {"unknown_key": 1}))


@pytest.mark.parametrize("p2_key", ["changed_scope", "diff_chunks"])
def test_the_p2_keys_are_not_accepted_yet(tmp_path: Path, p2_key: str) -> None:
    with pytest.raises(ValidationError, match=p2_key):
        _overlay(tmp_path, f"git:\n  {p2_key}:\n    enabled: true\n")


def test_a_yaml_typo_fails_the_load(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="retain_recnt"):
        _overlay(tmp_path, "git:\n  branches:\n    retention:\n      retain_recnt: 3\n")


# ── overlays and env ───────────────────────────────────────────────────────


def test_a_partial_yaml_overlay_keeps_the_other_defaults(tmp_path: Path) -> None:
    config = _overlay(
        tmp_path,
        "git:\n"
        "  branches:\n"
        "    track: [checked_out, 'release/*']\n"
        "    retention:\n"
        "      grace_days: 0\n"
        "  remote:\n"
        "    auto_fetch:\n"
        "      enabled: true\n",
    )
    git = config.git
    assert git.branches.track == [CHECKED_OUT_TRACK_ENTRY, "release/*"]
    assert git.branches.retention.grace_days == 0
    assert git.branches.retention.retain_recent == 8
    assert git.remote.auto_fetch.enabled is True
    assert git.remote.auto_fetch.interval_seconds == 60
    assert git.timeout_seconds == 30.0


def test_env_override_reaches_a_nested_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDOCS_GIT__BRANCHES__RETENTION__GRACE_DAYS", "3")
    monkeypatch.setenv("PYDOCS_GIT__REMOTE__AUTO_FETCH__ENABLED", "true")
    monkeypatch.setenv("PYDOCS_GIT__REF_WATCH__ENABLED", "false")
    monkeypatch.setenv("PYDOCS_GIT__REMOTE__NAME", "upstream")
    git = AppConfig.load().git
    assert git.branches.retention.grace_days == 3
    assert git.branches.retention.retain_recent == 8
    assert git.remote.auto_fetch.enabled is True
    assert git.ref_watch.enabled is False
    assert git.remote.name == "upstream"


def test_env_override_sets_the_track_list_as_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDOCS_GIT__BRANCHES__TRACK", '["checked_out", "release/*"]')
    assert AppConfig.load().git.branches.track == [CHECKED_OUT_TRACK_ENTRY, "release/*"]


def test_an_out_of_bound_env_value_fails_the_load(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYDOCS_GIT__REF_WATCH__DEBOUNCE_MS", "0")
    with pytest.raises(ValidationError, match="debounce_ms"):
        AppConfig.load()


# ── cache identity ─────────────────────────────────────────────────────────


def test_git_keys_do_not_move_the_ingestion_pipeline_hash(tmp_path: Path) -> None:
    """Tracking, watching and remote knobs choose WHICH refs are indexed, not how a
    file is chunked or embedded: changing them must not re-embed anything."""
    stock = AppConfig.load().ingestion_pipeline_hash
    tuned = _overlay(
        tmp_path,
        "git:\n"
        "  branches:\n"
        "    track: [all_local]\n"
        "    base: develop\n"
        "    retention: {retain_recent: 3, grace_days: 0}\n"
        "    merge_detection: {lookback_landings: 10}\n"
        "  ref_watch: {enabled: false, debounce_ms: 50}\n"
        "  remote: {name: upstream, track_refs: [upstream/main]}\n",
    )
    assert tuned.ingestion_pipeline_hash == stock
