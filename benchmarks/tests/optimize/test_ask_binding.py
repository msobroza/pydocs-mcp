"""Eval-side ask binding — registry, extras guard, contract conformance, fakes.

Covers the stage-3 adoption of the product run contract (run-contract design
§2/§7): the runner the fitness drives is the PRODUCT harness runner wrapped
with the campaign's per-task timeout, candidates travel as
``guidance_sections``, and the binding identity that folds into the objective
hash is derived, never hard-coded.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

import pydocs_eval
import pydocs_mcp

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
from pydocs_mcp.harness.platform.contract import (
    HarnessRunner,
    TurnBudgetExceededError,
)
from tests.optimize._trajectories import make_trajectory

# The four product agent_registry names, bridged one-to-one (spec §7-Q1).
_PRODUCT_NAMES = ("auto", "inline", "text_react", "vision_subagent")

# The roots a probe subprocess needs on PYTHONPATH — derived from the imported
# packages so the pin works regardless of how pytest was invoked.
_PROBE_PATH = (
    str(Path(pydocs_eval.__file__).resolve().parents[1]),
    str(Path(pydocs_mcp.__file__).resolve().parents[1]),
)

_SAMPLE = {"record_id": "q1", "task_name": "repo_qa", "rendered_prompt": "p", "gold": None}


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
        product = pytest.importorskip("pydocs_mcp.harness.builtin.ask_your_docs.architectures")
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
        product = pytest.importorskip("pydocs_mcp.harness.builtin.ask_your_docs.binding")

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
        # An unmetered harness reports no price, so the sentinel stays 0.0.
        assert trajectory.tool_calls == () and trajectory.cost_usd == 0.0

    async def test_a_metered_harnesss_capped_spend_reaches_the_ledger(self) -> None:
        # The external CLI engine METERS its runs, and turn-capping is a common
        # failure mode on a long-horizon arm. Recording those rollouts as $0.00
        # would enforce budget.max_usd against a number below actual spend.
        runner = TimeoutBoundedAskRunner(
            inner=self._Raising(TurnBudgetExceededError(turn_limit=40, cost_usd=3.10)),
            task_timeout_seconds=60.0,
            max_agent_turns=40,
        )
        trajectory = await runner.run(_SAMPLE, {})
        assert trajectory.cost_usd == 3.10 and trajectory.turns == 41

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
        from pydocs_mcp.harness.builtin.ask_your_docs.binding import delivery_map_digest

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


class TestHarnessBridges:
    """``_PRODUCT_BRIDGES`` widened to harness level — lazy rows (design §6/§7)."""

    def test_lookup_is_a_name_check_and_imports_nothing(self) -> None:
        # Proved by ABSENCE in a FRESH interpreter's ``sys.modules``: patching
        # ``importlib.import_module`` would not see an ``import x.y`` statement
        # (that routes through ``builtins.__import__``), and this session has
        # already imported the harness anyway.
        probe = textwrap.dedent(
            """
            import sys
            from pydocs_eval.optimize import ask_binding

            bridge = ask_binding.harness_bridge_for(
                "pydocs_mcp.harness.builtin.ask_your_docs.binding:make_harness_runner"
            )
            assert bridge.extra == "ask"
            resident = [
                name
                for name in sys.modules
                if name == "langgraph"
                or name.startswith("langgraph.")
                or name.startswith("pydocs_mcp.harness.builtin.ask_your_docs.binding")
            ]
            assert not resident, f"bridge lookup imported the harness: {resident}"
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(_PROBE_PATH)},
        )
        assert completed.returncode == 0, completed.stderr

    def test_an_unregistered_runner_names_the_known_rows(self) -> None:
        with pytest.raises(KeyError, match="some.other.harness"):
            ask_binding.harness_bridge_for("some.other.harness:make_runner")

    def test_resolution_is_guarded_by_the_bridges_extra(self, monkeypatch) -> None:
        monkeypatch.setattr(ask_binding, "_missing_module_for", lambda modules: "langgraph")
        with pytest.raises(RuntimeError, match=r'pip install "pydocs-mcp-eval\[ask\]"'):
            ask_binding.resolve_harness_runner_factory(
                "pydocs_mcp.harness.builtin.ask_your_docs.binding:make_harness_runner"
            )

    def test_resolution_returns_the_product_factory(self) -> None:
        pytest.importorskip("langgraph")
        product = pytest.importorskip("pydocs_mcp.harness.builtin.ask_your_docs.binding")
        resolved = ask_binding.resolve_harness_runner_factory(
            "pydocs_mcp.harness.builtin.ask_your_docs.binding:make_harness_runner"
        )
        assert resolved is product.make_harness_runner

    def test_delivery_map_hash_matches_the_products_own_digest(self) -> None:
        product = pytest.importorskip("pydocs_mcp.harness.builtin.ask_your_docs.binding")
        assert (
            ask_binding.harness_delivery_map_hash(
                "pydocs_mcp.harness.builtin.ask_your_docs.binding:make_harness_runner"
            )
            == product.delivery_map_digest()
        )

    def test_known_task_names_come_from_the_product_loader(self) -> None:
        loader = pytest.importorskip("pydocs_mcp.harness.platform.skill_artifact")
        assert ask_binding.known_task_names() == loader.TASK_NAMES


class TestExternalHarnessBridge:
    """The composed CLI harness's row — one line, same lazy mechanism."""

    _RUNNER = "pydocs_mcp.harness.builtin.external.binding:make_harness_runner"

    def test_lookup_is_a_name_check_and_imports_nothing(self) -> None:
        # Same proof by ABSENCE as the ask row: a config that merely NAMES this
        # harness must not pay for importing it.
        probe = textwrap.dedent(
            f"""
            import sys
            from pydocs_eval.optimize import ask_binding

            bridge = ask_binding.harness_bridge_for({self._RUNNER!r})
            assert bridge.extra == "retrieval"
            assert bridge.required_modules == ("pydocs_mcp",)
            resident = [
                name for name in sys.modules
                if name.startswith("pydocs_mcp.harness.builtin.external")
            ]
            assert not resident, f"bridge lookup imported the harness: {{resident}}"
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(_PROBE_PATH)},
        )
        assert completed.returncode == 0, completed.stderr

    def test_resolution_is_guarded_by_the_product_library(self, monkeypatch) -> None:
        # No agent runtime is required — only the product itself, which the
        # [retrieval] extra ships, so the hint names a real extra.
        monkeypatch.setattr(ask_binding, "_missing_module_for", lambda modules: "pydocs_mcp")
        with pytest.raises(RuntimeError, match=r'pip install "pydocs-mcp-eval\[retrieval\]"'):
            ask_binding.resolve_harness_runner_factory(self._RUNNER)

    def test_resolution_returns_the_product_factory(self) -> None:
        product = pytest.importorskip("pydocs_mcp.harness.builtin.external.binding")
        assert ask_binding.resolve_harness_runner_factory(self._RUNNER) is (
            product.make_harness_runner
        )

    def test_delivery_map_hash_matches_the_products_own_digest(self) -> None:
        product = pytest.importorskip("pydocs_mcp.harness.builtin.external.binding")
        assert ask_binding.harness_delivery_map_hash(self._RUNNER) == product.delivery_map_digest()

    def test_the_two_harnesses_declare_different_delivery_maps(self) -> None:
        # Two harnesses delivering the same candidate through different channels
        # are different arms; their digests must not collide.
        ask = pytest.importorskip("pydocs_mcp.harness.builtin.ask_your_docs.binding")
        external = pytest.importorskip("pydocs_mcp.harness.builtin.external.binding")
        assert ask.delivery_map_digest() != external.delivery_map_digest()

    def test_a_runner_settings_mapping_travels_uninspected_to_the_product(
        self, tmp_path: Path
    ) -> None:
        # The construction site passes the arm's opaque settings straight
        # through; validation (and the engine lookup) happen product-side.
        pytest.importorskip("pydocs_mcp.harness.builtin.external.binding")
        runner = ask_binding.build_harness_runner(
            self._RUNNER,
            {
                "workspace": str(tmp_path),
                "model": "a-model",
                "trace_root": str(tmp_path / "traces"),
                "engine": "claude_code",
            },
            task_timeout_seconds=30.0,
            max_agent_turns=40,
        )
        assert isinstance(runner, TimeoutBoundedAskRunner)
        assert isinstance(runner.inner, HarnessRunner)
        assert runner.inner.settings.engine == "claude_code"

    def test_the_arms_platforms_tool_vocabulary_is_the_one_the_harness_accepts(
        self, tmp_path: Path
    ) -> None:
        # ArmCell only admits the bare INDEXED_TOOL_NAMES, and that is exactly
        # what arm_runner_settings stamps — so the composed harness must accept
        # the whole set. (The ENGINE namespaces them into the CLI grant; a
        # harness that took them verbatim would run a drop-one arm tool-less.)
        from pydocs_eval.optimize.rubric.gates import INDEXED_TOOL_NAMES

        pytest.importorskip("pydocs_mcp.harness.builtin.external.binding")
        runner = ask_binding.build_harness_runner(
            self._RUNNER,
            {
                "workspace": str(tmp_path),
                "model": "a-model",
                "trace_root": str(tmp_path / "traces"),
                "tool_names": sorted(INDEXED_TOOL_NAMES),
            },
            task_timeout_seconds=30.0,
            max_agent_turns=40,
        )
        assert set(runner.inner.settings.tool_names) == set(INDEXED_TOOL_NAMES)
