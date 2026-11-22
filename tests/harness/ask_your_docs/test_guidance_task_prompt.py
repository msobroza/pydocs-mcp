"""``ask_your_docs.guidance_task`` AS THE MODEL RECEIVES IT (issue #391, spec
2026-11-22-guidance-task-experiment-design "The knob" + "The served-prompt seam").

The seam is the system message a real build serves: ``build_agent_with_scope_capabilities``
over no MCP tools, a real ReAct graph, and a recording fake chat model. Under
``none`` (and with the key absent) the message is today's bytes, compared against
the COMMITTED ``system_v3`` golden — not the active-template constant — so the
test keeps meaning "today's prompt" after a win. Any other value folds the
packaged seed's three sections for that task after the catalog and the pack.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import HumanMessage

from pydocs_mcp.application.description_source import TokenBudgetExceededError
from pydocs_mcp.harness.ask_your_docs.agent import build_agent_with_scope_capabilities
from pydocs_mcp.harness.ask_your_docs.catalog import render_catalog
from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities
from pydocs_mcp.harness.core.skill_artifact_loader import load_packaged_skill
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig, GuidanceTask

from ._guidance_task_fakes import (
    FakeChatModelFactory,
    FakeOverCapPackagedSeed,
    FakeSessionStartPack,
    FakeTracedServeSession,
    packaged_seed_sections,
)

_CATALOG = {"demo": ["pkg_a", "pkg_b"]}
_PACK = "SESSION-START-PACK"
_V3_GOLDEN = Path(__file__).resolve().parents[2] / "fixtures" / "goldens"
_V3_GOLDEN = _V3_GOLDEN / "ask_your_docs_system_v3.txt"
_SYNTHETIC_KEY = "sk-test-synthetic"


@pytest.fixture
def chat_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeChatModelFactory:
    """A network-free real build: recording chat model, fixed pack, no cwd config."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", _SYNTHETIC_KEY)
    FakeSessionStartPack(_PACK).install(monkeypatch)
    return FakeChatModelFactory().install(monkeypatch)


def _served(
    chat_model: FakeChatModelFactory,
    config: AskYourDocsConfig | None,
    *,
    task_name: str | None = None,
    workspace: str = "/tmp/ws",
    catalog: dict[str, list[str]] | None = _CATALOG,
) -> str:
    """The system message one agent turn of a real build hands the model."""

    async def _build_and_ask() -> None:
        built = await build_agent_with_scope_capabilities(
            workspace,
            "m",
            catalog=catalog,
            architecture="text_react",
            config=config,
            capabilities=ModelCapabilities(multimodal=False, source="override"),
            mcp_tools=[],
            task_name=task_name,
        )
        await built.graph.ainvoke({"messages": [HumanMessage("how does routing work?")]})

    asyncio.run(_build_and_ask())
    return chat_model.served_system_message()


def _todays_prompt() -> str:
    golden = _V3_GOLDEN.read_bytes().decode("utf-8")
    return f"{golden}\nIndexed projects and packages:\n{render_catalog(_CATALOG)}\n{_PACK}"


def _folded(task: str) -> str:
    artifact = load_packaged_skill()
    return "\n".join(
        (
            artifact.backbone,
            artifact.task_head(task),
            artifact.harness_task_head("ask_your_docs", task),
        )
    )


class TestNoneIsTodaysPrompt:
    def test_the_knob_at_none_serves_the_committed_v3_golden(self, chat_model) -> None:
        config = AskYourDocsConfig(guidance_task=GuidanceTask.NONE)
        assert _served(chat_model, config) == _todays_prompt()

    def test_the_key_absent_serves_the_committed_v3_golden(self, chat_model) -> None:
        assert _served(chat_model, AskYourDocsConfig()) == _todays_prompt()
        assert _served(chat_model, None) == _todays_prompt()


class TestATaskFoldsThePackagedSeed:
    @pytest.mark.parametrize(
        "task", [GuidanceTask.REPO_QA, GuidanceTask.VULN, GuidanceTask.BUG_LOC]
    )
    def test_the_three_sections_follow_the_catalog_and_the_pack(self, chat_model, task) -> None:
        served = _served(chat_model, AskYourDocsConfig(guidance_task=task))
        assert served == f"{_todays_prompt()}\n{_folded(task.value)}"

    def test_an_explicit_task_name_wins_over_the_knob(self, chat_model) -> None:
        config = AskYourDocsConfig(guidance_task=GuidanceTask.VULN)
        served = _served(chat_model, config, task_name="repo_qa")
        assert served == f"{_todays_prompt()}\n{_folded('repo_qa')}"

    def test_an_over_cap_packaged_seed_stops_the_build(
        self, chat_model, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """No fallback to an unguided prompt: the loader's typed error, nothing served."""
        FakeOverCapPackagedSeed(tmp_path / "seed").install(monkeypatch)
        with pytest.raises(TokenBudgetExceededError) as excinfo:
            _served(chat_model, AskYourDocsConfig(guidance_task=GuidanceTask.REPO_QA))
        assert "BACKBONE" in str(excinfo.value)
        assert chat_model.llm.calls == []


class TestTheKnobServesWhatTheEvalArmMeasured:
    async def test_repo_qa_under_the_knob_equals_the_eval_bindings_repo_qa_task(
        self, chat_model, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """For one guidance document (the packaged seed), the knob's prompt and the
        prompt the eval binding serves a ``repo_qa`` task carrying its sections are
        the same bytes — the measured state is the shipped state."""
        from pydocs_mcp.harness.ask_your_docs import binding

        from tests.harness.core._runner_contract import conformant_sample

        from ._binding_fakes import binding_settings

        FakeTracedServeSession().install(monkeypatch)
        settings = binding.AskYourDocsRunnerSettings.model_validate(
            {**binding_settings(tmp_path), "architecture": "text_react"}
        )
        sample = {**conformant_sample(), "task_name": "repo_qa"}
        await binding.run_task(sample, packaged_seed_sections(), settings)
        eval_served = chat_model.served_system_message()

        knob = FakeChatModelFactory().install(monkeypatch)
        config = AskYourDocsConfig(guidance_task=GuidanceTask.REPO_QA)
        # Same workspace, catalog scan and pack as the eval build: only the
        # guidance route differs.
        knob_served = await asyncio.to_thread(
            _served, knob, config, workspace=settings.workspace, catalog=None
        )

        assert knob_served == eval_served
        assert knob_served.endswith(f"\n{_folded('repo_qa')}")
