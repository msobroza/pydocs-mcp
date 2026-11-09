"""Eval-side ask binding — how ``build_ask_harness_runner`` maps an arm's settings.

Split out of ``test_ask_binding.py`` (the file-size ceiling): the eval side maps
its settings onto the PRODUCT factory (design §11), including the arm's llm
block under ``harness.llm`` and an overlay's other keys beside it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.optimize import ask_binding
from pydocs_eval.optimize.ask_binding import DEFAULT_ASK_TRACE_ROOT, build_ask_harness_runner


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

    def test_an_arm_llm_block_lands_under_harness_llm(self, monkeypatch) -> None:
        # The ONE sanctioned channel for model settings: the binding takes
        # ``harness.llm`` as-is and refuses a block read from pydocs_config.
        captured = self._capture_settings(monkeypatch)
        block = {"provider": "openrouter", "params": {"temperature": 1.0}}

        build_ask_harness_runner(
            workspace=Path("/index"),
            model="m",
            architecture="text_react",
            max_agent_turns=12,
            harness_llm=block,
        )

        assert captured["harness"] == {"llm": block}

    def test_an_arm_harness_block_merges_beside_harness_llm(self, monkeypatch) -> None:
        # An arm overlay's other ask_your_docs keys share the ``harness`` mapping.
        captured = self._capture_settings(monkeypatch)
        block = {"provider": "openrouter"}

        build_ask_harness_runner(
            workspace=Path("/index"),
            model="m",
            architecture="auto",
            max_agent_turns=12,
            harness_llm=block,
            harness_block={"seed_search_with_question": True},
        )

        assert captured["harness"] == {"seed_search_with_question": True, "llm": block}

    def test_a_harness_block_alone_needs_no_llm_block(self, monkeypatch) -> None:
        captured = self._capture_settings(monkeypatch)

        build_ask_harness_runner(
            workspace=Path("/index"),
            model="m",
            architecture="text_react",
            max_agent_turns=12,
            harness_block={"seed_search_with_question": True},
        )

        assert captured["harness"] == {"seed_search_with_question": True}

    def test_an_empty_harness_block_adds_nothing(self, monkeypatch) -> None:
        captured = self._capture_settings(monkeypatch)

        build_ask_harness_runner(
            workspace=Path("/index"),
            model="m",
            architecture="text_react",
            max_agent_turns=12,
            harness_block={},
        )

        assert "harness" not in captured

    def test_no_block_leaves_the_settings_mapping_exactly_as_it_was(self, monkeypatch) -> None:
        # Byte identity for every caller that pins no block: no arm hash moves.
        captured = self._capture_settings(monkeypatch)

        build_ask_harness_runner(
            workspace=Path("/index"), model="m", architecture="text_react", max_agent_turns=12
        )

        assert "harness" not in captured

    def test_optional_paths_are_none_not_the_string_none(self, monkeypatch) -> None:
        captured = self._capture_settings(monkeypatch)
        build_ask_harness_runner(
            workspace=Path("/index"), model="m", architecture="text_react", max_agent_turns=12
        )
        assert captured["pydocs_config"] is None and captured["base_url"] is None
        assert captured["trace_root"] == DEFAULT_ASK_TRACE_ROOT
