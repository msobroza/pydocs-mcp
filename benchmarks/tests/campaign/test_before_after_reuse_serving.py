"""``--reuse-baseline`` also compares what the arm was SERVED under (owner, 2026-11-08).

The settings digest says what the agent ran under. A baseline served under
another retrieval YAML, endpoint or index — or one that recorded no serving
fingerprint at all — still measured a different experiment, so its reuse is
refused key by key. ``--allow-serving-mismatch`` forces it, and a forced reuse
is never silent: one structured log line, the plan text, the
``serving_override.json`` beside the candidate's ``arm.json`` (written before
the candidate runs) and the report all name the keys it forced past.
"""

from __future__ import annotations

import contextlib
import json
import logging
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_eval.campaign import before_after_arm_launch, before_after_command
from pydocs_eval.campaign.__main__ import main
from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_arm import read_arm_summary
from pydocs_eval.campaign.before_after_reuse import (
    SERVING_MISMATCH_FORCED_EVENT,
    BaselineIdentity,
    reusable_baseline,
)
from pydocs_eval.campaign.before_after_serving import (
    SERVING_FINGERPRINT_FILENAME,
    read_serving_fingerprint,
    write_serving_fingerprint,
)

from ._fakes import (
    STUB_SERVING,
    FakeArmRun,
    before_after_argv,
    git_repo_with_two_descriptions,
    no_worktree,
    stub_serving_settings,
    write_finished_summary_from_settings,
)
from .test_before_after_reuse_baseline import _default_digest, write_finished_baseline

_FLAG = "--allow-serving-mismatch"
_MODEL = "test-model"
_SERVING_TURNS = 4


def _sha(repo: Path, ref: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref], capture_output=True, text=True, check=True
    )
    return completed.stdout.strip()


def _reused(tmp_path: Path, repo: Path, **serving: object) -> Path:
    """A finished baseline whose serving fingerprint is the plan's, overridden by ``serving``."""
    arm = write_finished_baseline(tmp_path / "earlier" / "baseline", commit=_sha(repo, "HEAD~1"))
    fingerprint = STUB_SERVING.fingerprint(None)
    write_serving_fingerprint(arm, type(fingerprint)(values={**fingerprint.values, **serving}))
    return arm


def _unrecorded(tmp_path: Path, repo: Path) -> Path:
    arm = _reused(tmp_path, repo)
    (arm / SERVING_FINGERPRINT_FILENAME).unlink()
    return arm


def _argv(tmp_path: Path, repo: Path, reused: Path, *extra: str) -> list[str]:
    return before_after_argv(tmp_path, repo, "--reuse-baseline", str(reused), *extra)


# --- match ------------------------------------------------------------------


def test_a_matching_serving_fingerprint_lets_the_reuse_proceed(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _reused(tmp_path, repo)

    assert main(_argv(tmp_path, repo, reused)) == 0
    printed = capsys.readouterr().out
    assert "serving:    matches the reused baseline on all 5 key(s)" in printed

    assert main(_argv(tmp_path, repo, reused, "--confirm-spend")) == 0
    assert stub_command.roles == ["candidate"]


# --- mismatch ---------------------------------------------------------------


def test_a_serving_mismatch_is_refused_naming_each_differing_key(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _reused(tmp_path, repo, endpoint="http://other", **{"index.embedder_dim": 16})

    assert main(_argv(tmp_path, repo, reused, "--confirm-spend")) == 2

    error = capsys.readouterr().err
    assert "endpoint: recorded 'http://other', the plan's baseline 'http://e'" in error
    assert "index.embedder_dim: recorded 16, the plan's baseline 8" in error
    assert "serving_config" not in error, "a matching key is not named"
    assert _FLAG in error, "the refusal says how to force it"
    assert stub_command.roles == []


def test_an_arm_without_a_serving_fingerprint_is_refused_and_says_so(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _unrecorded(tmp_path, repo)

    assert main(_argv(tmp_path, repo, reused, "--confirm-spend")) == 2

    error = capsys.readouterr().err
    assert f"no {SERVING_FINGERPRINT_FILENAME}" in error and "not recorded" in error
    assert "endpoint" in error and "index.scope_id" in error, "each unverified key is named"
    assert _FLAG in error
    assert stub_command.roles == []


def test_the_override_never_forces_past_the_settings_digest(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """The digest gate is #385's own and stays strict: the flag forces SERVING keys only."""
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = write_finished_baseline(
        tmp_path / "earlier" / "baseline", commit=_sha(repo, "HEAD~1"), settings_digest="e" * 64
    )
    write_serving_fingerprint(reused, STUB_SERVING.fingerprint(None))

    assert main(_argv(tmp_path, repo, reused, _FLAG, "--confirm-spend")) == 2

    assert "settings_digest: recorded" in capsys.readouterr().err
    assert stub_command.roles == []


def test_the_override_without_a_reused_baseline_is_refused(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)

    assert main(before_after_argv(tmp_path, repo, _FLAG)) == 2

    assert "--reuse-baseline" in capsys.readouterr().err


# --- override ---------------------------------------------------------------


def _forced_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    events = [json.loads(record.getMessage()) for record in caplog.records]
    return [event for event in events if event.get("event") == SERVING_MISMATCH_FORCED_EVENT]


def test_the_override_forces_the_reuse_and_logs_the_differing_keys_once(
    tmp_path: Path,
    stub_command: FakeArmRun,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _reused(tmp_path, repo, endpoint="http://other", serving_config="x")
    caplog.set_level(logging.WARNING)

    assert main(_argv(tmp_path, repo, reused, _FLAG)) == 0

    assert _forced_events(caplog) == [
        {
            "event": SERVING_MISMATCH_FORCED_EVENT,
            "flag": _FLAG,
            "reused_baseline": str(reused),
            "fingerprint_recorded": True,
            "differing_keys": ["endpoint", "serving_config"],
            # A plan print spends nothing: the event says so, so an audit can tell it apart.
            "confirm_spend": False,
            "report_only": False,
        }
    ]
    printed = capsys.readouterr().out
    assert (
        f"serving:    MISMATCH on 2 key(s), reuse forced by {_FLAG}: endpoint, serving_config"
        in printed
    )


def test_the_override_forces_an_unrecorded_arm_and_names_every_unverified_key(
    tmp_path: Path,
    stub_command: FakeArmRun,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _unrecorded(tmp_path, repo)
    caplog.set_level(logging.WARNING)

    assert main(_argv(tmp_path, repo, reused, _FLAG, "--confirm-spend")) == 0

    (event,) = _forced_events(caplog)
    assert event["fingerprint_recorded"] is False
    assert event["differing_keys"] == sorted(STUB_SERVING.fingerprint(None).values)
    assert stub_command.roles == ["candidate"]
    assert "NOT RECORDED by the reused arm" in (tmp_path / "out" / "plan.txt").read_text()


def test_an_override_with_nothing_to_force_says_so_and_logs_nothing(
    tmp_path: Path,
    stub_command: FakeArmRun,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    caplog.set_level(logging.WARNING)

    assert main(_argv(tmp_path, repo, _reused(tmp_path, repo), _FLAG)) == 0

    assert _forced_events(caplog) == []
    assert f"{_FLAG} had nothing to force" in capsys.readouterr().out


# --- the gate on its own ---------------------------------------------------


def _expected_identity() -> BaselineIdentity:
    return BaselineIdentity(
        commit="a" * 40,
        model=_MODEL,
        max_agent_turns=_SERVING_TURNS,
        settings_digest=_default_digest(),
    )


def test_the_gate_returns_the_keys_it_forced(tmp_path: Path) -> None:
    arm = write_finished_baseline(tmp_path / "arm", commit="a" * 40)
    write_serving_fingerprint(arm, STUB_SERVING.fingerprint({"provider": "openrouter"}))

    reuse = reusable_baseline(
        arm,
        _expected_identity(),
        task_ids=(),
        serving=STUB_SERVING.fingerprint({"provider": "other"}),
        allow_serving_mismatch=True,
    )

    assert reuse.differing_keys == ("llm.provider",)
    assert reuse.fingerprint_recorded
    record = reuse.override_record()
    assert record is not None and record.differing_keys == ["llm.provider"]


def test_the_gate_records_no_override_for_a_match(tmp_path: Path) -> None:
    arm = write_finished_baseline(tmp_path / "arm", commit="a" * 40)
    write_serving_fingerprint(arm, STUB_SERVING.fingerprint(None))

    reuse = reusable_baseline(
        arm, _expected_identity(), task_ids=(), serving=STUB_SERVING.fingerprint(None)
    )

    assert reuse.differing_keys == () and reuse.override_record() is None


# --- what the run writes: each arm's fingerprint, the candidate's override ----


def test_every_arm_records_its_serving_fingerprint(tmp_path: Path, real_launch: None) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)

    assert main(before_after_argv(tmp_path, repo, "--confirm-spend")) == 0

    for role in ("baseline", "candidate"):
        recorded = read_serving_fingerprint(tmp_path / "out" / role)
        assert recorded == STUB_SERVING.fingerprint(None)


def test_the_baseline_a_run_wrote_qualifies_for_the_next_run(
    tmp_path: Path, real_launch: None
) -> None:
    """The ladder: step N's arm stands in for step N+1's baseline, gated on serving too."""
    repo = git_repo_with_two_descriptions(tmp_path)
    first = before_after_argv(tmp_path, repo, "--confirm-spend")
    first[first.index("--out") + 1] = str(tmp_path / "first")
    assert main(first) == 0

    reused = tmp_path / "first" / "baseline"
    assert main(_argv(tmp_path, repo, reused, "--confirm-spend")) == 0

    assert (tmp_path / "out" / "baseline").resolve() == reused.resolve()


def test_a_forced_reuse_is_recorded_in_the_candidates_arm_and_the_report(
    tmp_path: Path, real_launch: None
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _reused(tmp_path, repo, endpoint="http://other")

    assert main(_argv(tmp_path, repo, reused, _FLAG, "--confirm-spend")) == 0

    override = read_arm_summary(tmp_path / "out" / "candidate").serving_override
    assert override is not None
    assert override.differing_keys == ["endpoint"]
    assert override.reused_baseline == str(reused)
    report = (tmp_path / "out" / "before_after.md").read_text(encoding="utf-8")
    assert f"- serving, candidate: reuse of `{reused}` forced by `{_FLAG}`" in report
    assert "`endpoint`" in report
    assert _FLAG in (tmp_path / "out" / "plan.txt").read_text(encoding="utf-8")


# --- review fixes: the hint, and resuming under another serving setup -------


def test_the_hint_is_left_out_once_the_flag_is_given(
    tmp_path: Path, stub_command: FakeArmRun, capsys: pytest.CaptureFixture[str]
) -> None:
    """The flag cannot force a commit mismatch, so the refusal must not suggest it again."""
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _reused(tmp_path, repo, endpoint="http://other")
    argv = _argv(tmp_path, repo, reused, _FLAG, "--baseline", "HEAD", "--allow-identical")

    assert main(argv) == 2

    error = capsys.readouterr().err
    assert "commit: recorded" in error and _FLAG not in error


def test_an_arm_dir_served_differently_before_is_not_resumed(
    tmp_path: Path,
    real_launch: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Its finished rows would be kept, and its serving.json rewritten to the new setup."""
    repo = git_repo_with_two_descriptions(tmp_path)
    assert main(before_after_argv(tmp_path, repo, "--confirm-spend")) == 0
    before = (tmp_path / "out" / "baseline" / SERVING_FINGERPRINT_FILENAME).read_text()
    other = replace(stub_serving_settings(4), basis=replace(STUB_SERVING, config_digest="x"))
    monkeypatch.setattr(before_after_command, "_serving_settings", lambda args, block: other)

    assert main(before_after_argv(tmp_path, repo, "--confirm-spend")) == 2

    error = capsys.readouterr().err
    assert "serving_config: recorded 'stub-serving-config'" in error and "--out" in error
    assert (tmp_path / "out" / "baseline" / SERVING_FINGERPRINT_FILENAME).read_text() == before


def test_an_arm_dir_from_before_fingerprints_is_resumed_but_stays_unrecorded(
    tmp_path: Path, real_launch: None
) -> None:
    """Its earlier rows were served under an unknown setup: claiming this one would lie."""
    repo = git_repo_with_two_descriptions(tmp_path)
    assert main(before_after_argv(tmp_path, repo, "--confirm-spend")) == 0
    for role in ("baseline", "candidate"):
        (tmp_path / "out" / role / SERVING_FINGERPRINT_FILENAME).unlink()

    assert main(before_after_argv(tmp_path, repo, "--confirm-spend")) == 0

    for role in ("baseline", "candidate"):
        assert read_serving_fingerprint(tmp_path / "out" / role) is None


# --- review fix: the forced-reuse record survives a parent that dies after the arm ---


class ParentKilled(BaseException):
    """A Ctrl-C or an OOM kill of the parent: nothing after it gets to run."""


@contextlib.contextmanager
def _dying_worktree(repo: Path, sha: str, root: Path) -> Iterator[Path]:
    """The worktree's cleanup is where the parent dies, after the child's last write."""
    yield root / sha[:12]
    raise ParentKilled(sha)


def test_a_forced_reuse_is_recorded_even_if_the_parent_dies_after_the_arm(
    tmp_path: Path,
    real_launch: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = git_repo_with_two_descriptions(tmp_path)
    reused = _reused(tmp_path, repo, endpoint="http://other")
    monkeypatch.setattr(before_after_arm_launch, "product_worktree", _dying_worktree)
    with pytest.raises(ParentKilled):
        main(_argv(tmp_path, repo, reused, _FLAG, "--confirm-spend"))

    assert main(_argv(tmp_path, repo, reused, _FLAG, "--report-only")) == 0

    report = (tmp_path / "out" / "before_after.md").read_text(encoding="utf-8")
    assert f"- serving, candidate: reuse of `{reused}` forced by `{_FLAG}`" in report


def test_a_later_run_that_forces_nothing_drops_the_forced_reuse_record(
    tmp_path: Path, real_launch: None
) -> None:
    """The record is about the comparison: a resume against a matching baseline forced nothing."""
    repo = git_repo_with_two_descriptions(tmp_path)
    forced = _reused(tmp_path, repo, endpoint="http://other")
    assert main(_argv(tmp_path, repo, forced, _FLAG, "--confirm-spend")) == 0
    write_serving_fingerprint(forced, STUB_SERVING.fingerprint(None))

    assert main(_argv(tmp_path, repo, forced, "--confirm-spend")) == 0

    assert read_arm_summary(tmp_path / "out" / "candidate").serving_override is None
