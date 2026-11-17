"""Fixtures the ``before-after`` and ``before-after-compare`` CLI tests share.

These fixtures exist because the commands' inputs are the expensive
part: reading a serving YAML, counting description tokens under a real model
encoding, loading a split, and running each arm in a git worktree. A test about
what a CLI DECIDES needs none of that, so the seams are replaced here, once,
with the named doubles from ``_fakes`` — never re-monkeypatched per module. A
test that needs a different split reshapes the one its fixture returns.
"""

from __future__ import annotations

import pytest

from pydocs_eval.campaign import (
    before_after_arm_launch,
    before_after_command,
    before_after_compare_command,
)

from ._fakes import (
    COMPARE_TASK_IDS,
    FakeArmBlockProbe,
    FakeArmRun,
    FakeSplitTasks,
    no_worktree,
    stub_serving_settings,
    write_finished_summary_from_settings,
)
from ._outcome_fixtures import GOLD


@pytest.fixture
def before_after_split(monkeypatch: pytest.MonkeyPatch) -> FakeSplitTasks:
    """The split ``before-after`` loads: two tasks with ``a.py`` as gold, for a test to reshape."""
    split = FakeSplitTasks({"t1": ("a.py",), "t2": ("a.py",)})
    monkeypatch.setattr(before_after_command, "load_split_tasks", split)
    return split


@pytest.fixture
def stub_command(monkeypatch: pytest.MonkeyPatch, before_after_split: FakeSplitTasks) -> FakeArmRun:
    """Plan inputs resolved offline, the split from ``before_after_split``; arms replaced by a recorder."""
    fake = FakeArmRun()
    monkeypatch.setattr(before_after_command, "run_one_arm", fake)
    # Overlays reach the probe; the live one checks each commit out (its own tests).
    monkeypatch.setattr(before_after_command, "probe_arm_block", FakeArmBlockProbe())
    # The two plan inputs that read the serving YAML; the arm block has its own
    # tests (test_before_after_llm_block.py) and no bearing on the spend gate.
    monkeypatch.setattr(before_after_command, "_arm_llm_block", lambda args: None)
    monkeypatch.setattr(
        before_after_command, "_serving_settings", lambda args, block: stub_serving_settings(4)
    )
    monkeypatch.setattr(
        before_after_command, "_description_token_counter", lambda model: lambda text: 10
    )
    return fake


@pytest.fixture
def real_launch(stub_command: FakeArmRun, monkeypatch: pytest.MonkeyPatch) -> None:
    """The real ``run_one_arm`` (settings, fingerprint, forced-reuse record) over a fake child."""
    monkeypatch.setattr(before_after_command, "run_one_arm", before_after_arm_launch.run_one_arm)
    monkeypatch.setattr(before_after_arm_launch, "product_worktree", no_worktree)
    monkeypatch.setattr(before_after_arm_launch, "_spawn_arm", write_finished_summary_from_settings)


@pytest.fixture
def compare_split(monkeypatch: pytest.MonkeyPatch) -> FakeSplitTasks:
    """The split ``before-after-compare`` scores answers against: the needle file as gold."""
    split = FakeSplitTasks(dict.fromkeys(COMPARE_TASK_IDS, (GOLD,)))
    monkeypatch.setattr(before_after_compare_command, "load_split_tasks", split)
    return split
