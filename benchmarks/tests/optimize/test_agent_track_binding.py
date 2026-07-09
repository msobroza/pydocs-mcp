"""The binding is the ONLY place optimize/ imports agent-track code (spec §upstream contract)."""

import inspect
from pathlib import Path

from benchmarks.optimize import _agent_track_binding as b

# WHY: derive the package root from __file__, not a CWD-relative literal, so the
# guard resolves identically regardless of pytest's invocation directory (F.I.R.S.T.
# repeatability). From benchmarks/tests/optimize/test_*.py, parents[2] is benchmarks/.
_OPTIMIZE_PKG = Path(__file__).resolve().parents[2] / "src" / "benchmarks" / "optimize"


def test_binding_reexports_full_contract() -> None:
    for name in (
        "AgentTrackConfig",
        "run_agent_track",
        "PairResult",
        "RunMetrics",
        "JudgeScore",
        "AgentRunner",
        "FakeAgentRunner",
        "Judge",
        "FakeJudge",
        "task_prompt",
    ):
        assert hasattr(b, name), f"binding misses contract name {name!r}"


def test_task_prompt_has_kwonly_skill_with_empty_default() -> None:
    p = inspect.signature(b.task_prompt).parameters["skill"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default == ""


def test_track_config_carries_seed_and_guardrails() -> None:
    cfg = b.AgentTrackConfig()
    for field in (
        "rng_seed",
        "max_usd",
        "max_tasks",
        "task_timeout_seconds",
        "arms",
        "judge_model",
    ):
        assert hasattr(cfg, field)


def test_binding_is_the_only_agent_track_import() -> None:
    # Scan the optimize package source: no .py file other than the binding
    # imports benchmarks.eval.agent_track. Path.rglob over *.py (not a grep
    # subprocess) so the assertion is immune to compiled __pycache__ artifacts
    # and stays inside the fully-offline test convention (no subprocess).
    importers = sorted(
        py.relative_to(_OPTIMIZE_PKG).as_posix()
        for py in _OPTIMIZE_PKG.rglob("*.py")
        if "eval.agent_track" in py.read_text()
    )
    assert importers == ["_agent_track_binding.py"]
