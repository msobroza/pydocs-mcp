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

from pydocs_eval.campaign import before_after_command, before_after_compare_command
from pydocs_eval.campaign.before_after_corpora import IndexIdentity

from ._fakes import COMPARE_TASK_IDS, FakeArmRun, FakeSplitTasks
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
    monkeypatch.setattr(before_after_command, "_run_one_arm", fake)
    # The two plan inputs that read the serving YAML; the arm block has its own
    # tests (test_before_after_llm_block.py) and no bearing on the spend gate.
    monkeypatch.setattr(before_after_command, "_arm_llm_block", lambda args: None)
    monkeypatch.setattr(
        before_after_command,
        "_serving_settings",
        lambda args, block: before_after_command.ServingSettings(
            endpoint="http://e",
            max_agent_turns=4,
            identity=IndexIdentity(embedder_model="m", embedder_dim=8, scope_id="scope"),
        ),
    )
    monkeypatch.setattr(
        before_after_command, "_description_token_counter", lambda model: lambda text: 10
    )
    return fake


@pytest.fixture
def compare_split(monkeypatch: pytest.MonkeyPatch) -> FakeSplitTasks:
    """The split ``before-after-compare`` scores answers against: the needle file as gold."""
    split = FakeSplitTasks(dict.fromkeys(COMPARE_TASK_IDS, (GOLD,)))
    monkeypatch.setattr(before_after_compare_command, "load_split_tasks", split)
    return split
