"""The shipped gate config must be WINNABLE against the real vendored gold (H5).

The existing combined-config test asserts the gate is *configured*, using
synthetic gold — which is how an unsatisfiable production config shipped green.
`gold_substring_all` requires EVERY gold candidate verbatim in the answer, and
the vendored gold takes the raw union of each contributing commit's
`files_changed`: 10 of 25 records carry more than five gold files and one
carries 128. No answer can name 128 paths, so the gate failed for every
candidate, `fail_fast` short-circuited the judge, and every ccv sample scored
0.0 — the slice contributed no optimization gradient at all.

The invariant this pins is simply: *a gate must be winnable.*
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import yaml

from pydocs_eval.datasets.crosscommitvuln import CrossCommitVulnDataset
from pydocs_eval.optimize.rubric.gates import _all_gate_candidates, evaluate_gate
from pydocs_eval.optimize.rubric.model import GateCheck

_CONFIG = "benchmarks/src/pydocs_eval/optimize/configs/optimize_ask_prompt_combined.yaml"


@dataclass(frozen=True, slots=True)
class _Transcript:
    answer: str
    tool_calls: tuple[()] = ()
    turns: int = 3
    wall_seconds: float = 10.0
    cost_usd: float = 0.0


def _shipped_tasks():
    import asyncio

    async def collect():
        return [t async for t in CrossCommitVulnDataset().tasks()]

    return asyncio.run(collect())


def _exact_id_gate() -> GateCheck:
    """The `exact_id` gate exactly as the shipped campaign config declares it."""
    from pathlib import Path

    cfg = yaml.safe_load(Path(_CONFIG).read_text())
    raw = next(g for g in cfg["ask_rubric"]["gates"] if g["name"] == "exact_id")
    return GateCheck(name=raw["name"], kind=raw["kind"], params=raw.get("params") or {})


@pytest.mark.parametrize("task", _shipped_tasks(), ids=lambda t: t.task_id)
def test_exact_id_gate_is_winnable_for_every_shipped_record(task) -> None:
    """Best possible answer — every gold candidate verbatim — must pass the gate."""
    gate = _exact_id_gate()
    best_possible = " ".join(_all_gate_candidates(task, gate.params.get("keys")))
    assert evaluate_gate(gate, task, _Transcript(answer=best_possible)) is True


@pytest.mark.parametrize("task", _shipped_tasks(), ids=lambda t: t.task_id)
def test_exact_id_gate_needs_a_realistic_number_of_tokens(task) -> None:
    """A winnable-in-principle gate is still useless if it needs 128 paths.

    Caps what the gate may demand so the raw-union gold shape cannot return.
    """
    gate = _exact_id_gate()
    candidates = _all_gate_candidates(task, gate.params.get("keys"))
    assert len(candidates) <= 5, (
        f"{task.task_id}: exact_id demands {len(candidates)} verbatim tokens; "
        "no answer names that many, so the gate is unwinnable in practice"
    )


def test_exact_id_gate_still_rejects_a_wrong_answer() -> None:
    """The complement: narrowing the gate must not make it vacuous."""
    gate = _exact_id_gate()
    task = _shipped_tasks()[0]
    assert evaluate_gate(gate, task, _Transcript(answer="I could not find anything.")) is False
