"""Fixtures every ``before-after`` CLI test shares.

One fixture, and it exists because the command's plan inputs are the expensive
part: reading a serving YAML, counting description tokens under a real model
encoding, loading a split, and running each arm in a git worktree. A test about
what the CLI DECIDES needs none of that, so the seams are replaced here, once,
with the named doubles from ``_fakes`` — never re-monkeypatched per module.
"""

from __future__ import annotations

import pytest

from pydocs_eval.campaign import before_after_command
from pydocs_eval.campaign.before_after_corpora import IndexIdentity
from pydocs_eval.datasets.base_dataset import EvalTask

from ._fakes import FakeArmRun, eval_task


@pytest.fixture
def stub_command(monkeypatch: pytest.MonkeyPatch) -> FakeArmRun:
    """Plan inputs resolved offline; arms replaced by a recorder."""
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

    async def _tasks(split: str, *, limit: int | None = None) -> tuple[EvalTask, ...]:
        return (eval_task("t1"), eval_task("t2"))[: limit or 2]

    monkeypatch.setattr(before_after_command, "load_split_tasks", _tasks)
    return fake
