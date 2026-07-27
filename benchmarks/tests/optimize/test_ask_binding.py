"""Eval-side ask binding — registry, extras guard, contract conformance, fakes.

Covers the stage-3 adoption of the product run contract (run-contract design
§2/§7): the runner the fitness drives is the PRODUCT harness runner wrapped
with the campaign's per-task timeout, candidates travel as
``guidance_sections``, and the binding identity that folds into the objective
hash is derived, never hard-coded.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_eval.optimize import ask_binding
from pydocs_eval.optimize.ask_binding import (
    _DEFAULT_ASK_ARCHITECTURE,
    DEFAULT_ASK_TRACE_ROOT,
    FakeAskRunner,
    TimeoutBoundedAskRunner,
    ask_architecture_registry,
    ask_binding_identity,
    build_ask_harness_runner,
    guidance_sections_for_candidate,
)
from pydocs_mcp.harness.core.run_contract import (
    HarnessRunner,
    TurnBudgetExceededError,
)
from tests.optimize._trajectories import make_trajectory

# The four product agent_registry names, bridged one-to-one (spec §7-Q1).
_PRODUCT_NAMES = ("auto", "inline", "text_react", "vision_subagent")

_SAMPLE = {"record_id": "q1", "task_name": "sweqapro", "rendered_prompt": "p", "gold": None}


@dataclass(frozen=True, slots=True)
class _TextArtifact:
    """Minimal artifact double: ``render()`` is all the projection reads."""

    text: str
    name: str = "probe"

    def render(self) -> str:
        return self.text


class TestRegistry:
    def test_registry_bridges_every_product_architecture(self) -> None:
        assert ask_architecture_registry.names() == _PRODUCT_NAMES

    def test_default_architecture_is_text_react(self) -> None:
        # §7-Q1's rename rule applied at birth: the default is the product
        # name "text_react"; a benchmarks-only "react" alias never existed.
        assert _DEFAULT_ASK_ARCHITECTURE == "text_react"

    def test_registry_names_match_the_product_registry(self) -> None:
        pytest.importorskip("langgraph")
        product = pytest.importorskip("pydocs_mcp.harness.ask_your_docs.architectures")
        assert ask_architecture_registry.names() == product.agent_registry.names()


class TestExtrasGuard:
    def test_missing_extra_raises_actionable_error(self, monkeypatch) -> None:
        # AC-18: the error names the exact install command.
        monkeypatch.setattr(ask_binding, "_ask_extra_missing_module", lambda: "langgraph")
        with pytest.raises(RuntimeError, match=r'pip install "pydocs-mcp-eval\[ask\]"'):
            ask_binding._require_ask_extra()

    def test_runner_construction_is_guarded(self, monkeypatch) -> None:
        monkeypatch.setattr(ask_binding, "_ask_extra_missing_module", lambda: "langgraph")
        with pytest.raises(RuntimeError, match=r"pydocs-mcp-eval\[ask\]"):
            build_ask_harness_runner(
                workspace=Path("~/pydocs-index"),
                model="m",
                architecture="text_react",
                max_agent_turns=12,
            )

    def test_present_extra_passes_the_guard(self, monkeypatch) -> None:
        monkeypatch.setattr(ask_binding, "_ask_extra_missing_module", lambda: None)
        ask_binding._require_ask_extra()  # must not raise

    def test_missing_module_probe_uses_find_spec(self, monkeypatch) -> None:
        # The guard's REAL logic: the first find_spec miss is named.
        import importlib.util

        real_find_spec = importlib.util.find_spec

        def _fake_find_spec(name, *args, **kwargs):
            if name == "langgraph":
                return None
            return real_find_spec(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec)
        assert ask_binding._ask_extra_missing_module() == "langgraph"

    def test_missing_module_probe_returns_none_when_complete(self, monkeypatch) -> None:
        import importlib.util

        monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: object())
        assert ask_binding._ask_extra_missing_module() is None


class TestHarnessRunnerFactory:
    """The eval side maps its settings onto the PRODUCT factory (design §11)."""

    def _capture_settings(self, monkeypatch) -> dict[str, object]:
        captured: dict[str, object] = {}
        product = pytest.importorskip("pydocs_mcp.harness.ask_your_docs.binding")

        def _fake_make_harness_runner(settings):
            captured.update(settings)
            return object()

        monkeypatch.setattr(product, "make_harness_runner", _fake_make_harness_runner)
        monkeypatch.setattr(ask_binding, "_require_ask_extra", lambda: None)
        return captured

    def test_settings_mapping_carries_every_bound_key(self, monkeypatch) -> None:
        captured = self._capture_settings(monkeypatch)
        runner = build_ask_harness_runner(
            workspace=Path("/index"),
            model="qwen3-4b",
            architecture="inline",
            max_agent_turns=7,
            base_url="http://localhost:9999/v1",
            pydocs_config=Path("/tmp/overlay.yaml"),
            trace_root="/traces",
            task_timeout_seconds=42.0,
        )
        assert captured == {
            "workspace": "/index",
            "model": "qwen3-4b",
            "base_url": "http://localhost:9999/v1",
            "pydocs_config": "/tmp/overlay.yaml",
            "architecture": "inline",
            "max_agent_turns": 7,
            "trace_root": "/traces",
        }
        assert runner.task_timeout_seconds == 42.0 and runner.max_agent_turns == 7

    def test_optional_paths_are_none_not_the_string_none(self, monkeypatch) -> None:
        captured = self._capture_settings(monkeypatch)
        build_ask_harness_runner(
            workspace=Path("/index"), model="m", architecture="text_react", max_agent_turns=12
        )
        assert captured["pydocs_config"] is None and captured["base_url"] is None
        assert captured["trace_root"] == DEFAULT_ASK_TRACE_ROOT


class TestTimeoutBoundedRunner:
    """One bad candidate costs its own sample, never the whole campaign."""

    @dataclass(slots=True)
    class _Raising:
        error: BaseException

        async def run(self, sample, guidance_sections):
            raise self.error

    async def test_turn_budget_error_becomes_a_failing_sentinel(self) -> None:
        runner = TimeoutBoundedAskRunner(
            inner=self._Raising(TurnBudgetExceededError(turn_limit=12)),
            task_timeout_seconds=60.0,
            max_agent_turns=12,
        )
        trajectory = await runner.run(_SAMPLE, {})
        # turns = cap + 1 fails max_turns; the empty answer fails min_answer_chars.
        assert trajectory.turns == 13 and trajectory.answer == ""
        assert trajectory.tool_calls == () and trajectory.cost_usd == 0.0

    async def test_task_timeout_becomes_a_failing_sentinel(self) -> None:
        import asyncio

        @dataclass(slots=True)
        class _Hanging:
            async def run(self, sample, guidance_sections):
                await asyncio.sleep(10)

        runner = TimeoutBoundedAskRunner(
            inner=_Hanging(), task_timeout_seconds=0.01, max_agent_turns=4
        )
        trajectory = await runner.run(_SAMPLE, {})
        assert trajectory.turns == 5 and trajectory.answer == ""

    async def test_a_healthy_run_passes_straight_through(self) -> None:
        expected = make_trajectory(answer="fine")
        seen: dict[str, object] = {}

        @dataclass(slots=True)
        class _Echo:
            async def run(self, sample, guidance_sections):
                seen.update(sample=sample, guidance=guidance_sections)
                return expected

        runner = TimeoutBoundedAskRunner(
            inner=_Echo(), task_timeout_seconds=60.0, max_agent_turns=12
        )
        assert await runner.run(_SAMPLE, {"SYSTEM_PROMPT": "s"}) is expected
        assert seen["sample"] is _SAMPLE and seen["guidance"] == {"SYSTEM_PROMPT": "s"}

    def test_wrapper_satisfies_the_contract_protocol(self) -> None:
        runner = TimeoutBoundedAskRunner(
            inner=self._Raising(RuntimeError("x")), task_timeout_seconds=1.0, max_agent_turns=1
        )
        assert isinstance(runner, HarnessRunner)


class TestGuidanceProjection:
    """Design §4: sections are the slots; non-sectioned families deliver nothing."""

    def test_delimited_candidate_projects_to_named_sections(self) -> None:
        artifact = _TextArtifact("=== SYSTEM_PROMPT ===\nbe terse\n=== REWRITE_PROMPT ===\nrw\n")
        assert guidance_sections_for_candidate(artifact) == {
            "SYSTEM_PROMPT": "be terse",
            "REWRITE_PROMPT": "rw",
        }

    def test_free_form_skill_delivers_no_sections(self) -> None:
        # usage_skill has no internal structure — its effect rides the agent
        # track's prompt, not the ask harness's channels.
        assert (
            guidance_sections_for_candidate(_TextArtifact("# Skill\nUse get_symbol first.")) == {}
        )

    def test_yaml_cell_delivers_no_sections(self) -> None:
        # ask_architecture / retrieval_config ride settings + serve overlay.
        yaml_cell = "architecture: text_react\nmax_agent_turns: 12\nretrieval_config: ''\n"
        assert guidance_sections_for_candidate(_TextArtifact(yaml_cell)) == {}


class TestBindingIdentity:
    def test_identity_names_the_three_verdict_moving_facts(self) -> None:
        identity = ask_binding_identity()
        assert set(identity) == {"scaffold", "delivery_map", "gates_source"}
        assert identity["scaffold"] == "task_scaffold_v1"
        assert identity["gates_source"] == "server_trace"

    def test_delivery_map_digest_is_derived_from_the_product(self) -> None:
        # Never a copied literal: a delivery-map change must move this value.
        from pydocs_mcp.harness.ask_your_docs.binding import delivery_map_digest

        assert ask_binding_identity()["delivery_map"] == delivery_map_digest()


class TestFakeAskRunner:
    async def test_scripted_trajectory_and_call_count(self) -> None:
        fake = FakeAskRunner(scripted={"q1": make_trajectory(answer="scripted answer")})
        trajectory = await fake.run(_SAMPLE, {})
        assert trajectory.answer == "scripted answer"
        assert fake.calls == 1

    async def test_unscripted_sample_returns_the_empty_trajectory(self) -> None:
        fake = FakeAskRunner(scripted={})
        trajectory = await fake.run({**_SAMPLE, "record_id": "q?"}, {})
        assert trajectory.answer == "" and trajectory.tool_calls == ()

    async def test_delivered_guidance_is_recorded_for_assertions(self) -> None:
        fake = FakeAskRunner(scripted={})
        await fake.run(_SAMPLE, {"SYSTEM_PROMPT": "s"})
        assert fake.seen_guidance_sections == [{"SYSTEM_PROMPT": "s"}]

    def test_fake_satisfies_the_contract_protocol(self) -> None:
        assert isinstance(FakeAskRunner(scripted={}), HarnessRunner)
