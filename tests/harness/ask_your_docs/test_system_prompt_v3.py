"""The v3 system prompt AS THE MODEL RECEIVES IT (issue #379, turn-efficiency step 5).

The seam is the agent-turn boundary: a real ReAct graph over a fake chat model,
so every assertion here is on the system message a model was actually served —
never on the template file, which a future architecture override could shadow.

What v3 changes: it removes only the rules that induced extra calls — the
Example chain that sent the model to get_symbol for a signature, the "issue all
of them" obligation, the card-first rule — and says when get_overview, windowed
reads and the context-plus-uniqueness pair are worth a call. It adds no stop
rule and no completeness definition. It also carries #323's branch rule, gated
on the server advertising ``branch`` and inert until then.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from langchain_core.messages import HumanMessage

from pydocs_mcp.application.description_source import CHARS_PER_TOKEN
from pydocs_mcp.harness.ask_your_docs.agent import _assemble_prompt
from pydocs_mcp.harness.ask_your_docs.architectures import AgentBuildContext, agent_registry
from pydocs_mcp.harness.ask_your_docs.multimodal import ModelCapabilities
from pydocs_mcp.harness.ask_your_docs.prompts import SYSTEM_PROMPT
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities
from pydocs_mcp.harness.core.prompt_surfaces import ACTIVE_SYSTEM_PROMPT_TEMPLATE
from pydocs_mcp.harness.core.prompts import render_core_prompt
from pydocs_mcp.retrieval.config.ask_your_docs_models import AskYourDocsConfig

from ._agent_fakes import FakeLlm

_CATALOG = {"demo": ["pkg_a", "pkg_b"]}
# The tools v3's rules route work to. get_overview joined the five v2 routed
# when rule 14 started naming its two triggers (spec step 5, edit (i)); every
# OTHER tool still reaches the model through its schema alone.
_ROUTED_TOOLS = (
    "search_codebase",
    "get_symbol",
    "get_context",
    "grep",
    "read_file",
    "get_overview",
)
_UNROUTED_TOOLS = ("get_references", "get_why", "glob")
_GOLDENS = Path(__file__).resolve().parents[2] / "fixtures" / "goldens"
_V3_GOLDEN = _GOLDENS / "ask_your_docs_system_v3.txt"
_BRANCH_ADVERTISED = ScopeCapabilities(branch_selector=True, changed_slice=False, diff_slice=False)
# The ask_prompt artifact's system-section budget (benchmarks
# optimize/artifacts/ask_prompt.py ``_ASK_SYSTEM_TOKEN_BUDGET``) restated as a
# product-side pin, because the eval suite is a local-only gate that the
# product CI job never installs — so it cannot be imported here.
_SYSTEM_TOKEN_BUDGET = 1200

# #323's rule (branch-scope UI spec §6.6), numbered after v3's last rule.
_BRANCH_RULE = (
    '16. Every tool takes a "branch" argument; the indexed-projects list below\n'
    '    names each project\'s branches and marks the default. Leave "branch" empty\n'
    "    to answer from the default branch, or from the pinned branches when the\n"
    "    question carries a pin (the app applies the pin for you). Under a pin with\n"
    '    several branches, an empty "branch" returns one labeled result per branch\n'
    "    — best when the user is comparing branches; pass branch=<name> to read one\n"
    "    branch at a time when only one is relevant or the output is long — under\n"
    "    a pin, <name> must be one of the pinned branches; any other name is\n"
    "    answered from all pinned branches. A pinned-scope note may also list\n"
    "    branches and a slice; the app applies those too. Always say which branch\n"
    "    each claim comes from.\n"
)


def served_system_prompt(architecture: str = "text_react", *, branch: bool = False) -> str:
    """The system message one agent turn actually hands the model."""
    fake = FakeLlm()
    capabilities = {"scope_capabilities": _BRANCH_ADVERTISED} if branch else {}
    context = AgentBuildContext(
        llm=fake,
        tools=(),
        prompt=_assemble_prompt(architecture, _CATALOG, None, **capabilities),
        capabilities=ModelCapabilities(multimodal=False, source="default"),
        config=AskYourDocsConfig(),
    )
    graph = agent_registry.get(architecture)().build(context)
    asyncio.run(graph.ainvoke({"messages": [HumanMessage("how does routing work?")]}))
    return str(fake.calls[0][0].content)


def _flat(text: str) -> str:
    """Collapse the template's line wrapping so a sentence matches across lines."""
    return re.sub(r"\s+", " ", text)


class TestRemovedCallInducingRules:
    """Each removed sentence is gone from what the model is served."""

    def test_no_signature_detour_through_get_symbol(self) -> None:
        assert "when you need the exact signature" not in _flat(served_system_prompt())

    def test_no_obligation_to_issue_every_together_call(self) -> None:
        assert "issue all of them in ONE turn" not in _flat(served_system_prompt())

    def test_no_card_first_rule(self) -> None:
        served = _flat(served_system_prompt())
        assert "Look at the symbol card first" not in served
        assert "only when the card does not answer" not in served


class TestEdits:
    """One test per edit letter of the step-5 spec; each pins the PR's sentence."""

    def test_a_defines_what_a_result_already_showed(self) -> None:
        served = _flat(served_system_prompt())
        assert "Never read or fetch content a result already showed you." in served
        assert (
            'A hit whose follow-up calls offer no source call (no depth="source", no '
            "read_file) has already shown its whole span." in served
        )
        assert 'When a view says lines are "not in the index", read exactly those lines, once.' in (
            served
        )

    def test_d_follow_ups_are_permission_batched_when_several_are_taken(self) -> None:
        served = _flat(served_system_prompt())
        assert "They are offers, not orders: take only the ones the question needs." in served
        assert (
            'The calls on a "Together:" line are independent of each other, so when you '
            "take several, issue them in one turn." in served
        )
        assert '"Then:" line need the earlier results first' in served

    def test_f_the_example_comes_only_from_what_was_shown(self) -> None:
        served = _flat(served_system_prompt())
        assert (
            "assembled strictly from a signature or usage the results already showed; "
            "when they showed neither, leave the Example out." in served
        )
        assert "Never invent parameters, defaults, or return shapes" in served

    def test_g_one_source_view_and_the_card_only_for_members(self) -> None:
        served = _flat(served_system_prompt())
        assert (
            'To see a symbol\'s code, make one get_symbol call with depth="source" or one '
            "windowed read_file call." in served
        )
        assert (
            "Ask for the symbol card (get_symbol's default depth) only when the question "
            "needs its member list." in served
        )

    def test_h_read_file_is_windowed_around_cited_or_missing_lines(self) -> None:
        served = _flat(served_system_prompt())
        assert (
            "Give read_file an offset and limit around the lines a result cited or "
            "reported as missing; widen the window only when it shows a reference you "
            "need to follow." in served
        )

    def test_i_names_exactly_two_get_overview_triggers(self) -> None:
        served = _flat(served_system_prompt())
        rule = (
            "Call get_overview only after a search misses, or for a question about the "
            "project's structure (which packages and modules exist, how the repo is laid "
            "out) — never alongside the first search."
        )
        assert rule in served
        # The one rule that names it, so no third trigger hides elsewhere.
        assert served.count("get_overview") == 1

    def test_j_context_and_uniqueness_checks_share_one_turn(self) -> None:
        served = _flat(served_system_prompt())
        assert (
            "When the answer needs get_context on a symbol you located and a grep "
            "confirming its name has no second site, issue both in the same turn." in served
        )


class TestWhatV3DoesNotAdd:
    def test_no_stop_rule_and_no_completeness_definition(self) -> None:
        served = served_system_prompt().lower()
        assert re.search(r"\bstop", served) is None
        assert "complete" not in served
        assert "enough to answer" not in served

    def test_only_the_routed_tools_are_named(self) -> None:
        served = served_system_prompt()
        assert all(tool in served for tool in _ROUTED_TOOLS)
        assert not any(tool in served for tool in _UNROUTED_TOOLS)

    def test_the_kept_rules_are_still_served(self) -> None:
        served = _flat(served_system_prompt())
        for kept in (
            "search UNSCOPED first",
            "Rewrite follow-up questions into self-contained queries",
            "ONE short clarifying question instead of guessing",
            "Cite the project and package.module for every claim",
            'end with a SHORT "Example" snippet',
            '"[pinned scope: ...]" note set by the app',
            "Call the follow-up a result offers instead of searching again",
            "One call with many targets beats several calls of the same tool",
            "ranked or conceptual question goes to search_codebase",
        ):
            assert kept in served

    def test_both_gate_renderings_fit_the_artifact_budget(self) -> None:
        advertised = render_core_prompt("system_v3", branch_selector_advertised=True)
        assert len(SYSTEM_PROMPT) // CHARS_PER_TOKEN <= _SYSTEM_TOKEN_BUDGET
        assert len(advertised) // CHARS_PER_TOKEN <= _SYSTEM_TOKEN_BUDGET


class TestNumbering:
    def test_rules_are_numbered_one_through_fifteen(self) -> None:
        numbered = re.findall(r"^\s*(\d+)\. ", served_system_prompt(), re.MULTILINE)
        assert numbered == [str(n) for n in range(1, 16)]

    def test_the_branch_rule_takes_the_next_number_and_comes_last(self) -> None:
        served = served_system_prompt(branch=True)
        numbered = re.findall(r"^\s*(\d+)\. ", served, re.MULTILINE)
        assert numbered == [str(n) for n in range(1, 17)]
        system = render_core_prompt(ACTIVE_SYSTEM_PROMPT_TEMPLATE, branch_selector_advertised=True)
        assert system.endswith(_BRANCH_RULE)


class TestBranchGate:
    """#323's two renderings: inert without the capability, one block with it."""

    def test_the_active_template_is_v3(self) -> None:
        assert ACTIVE_SYSTEM_PROMPT_TEMPLATE == "system_v3"

    def test_no_variable_render_equals_the_golden(self) -> None:
        golden = _V3_GOLDEN.read_bytes().decode("utf-8")
        assert render_core_prompt("system_v3") == golden
        assert golden == SYSTEM_PROMPT

    def test_an_advertised_branch_appends_exactly_the_rule_block(self) -> None:
        golden = _V3_GOLDEN.read_bytes().decode("utf-8")
        assert render_core_prompt("system_v3", branch_selector_advertised=True) == (
            golden + _BRANCH_RULE
        )

    def test_a_false_flag_renders_the_golden(self) -> None:
        golden = _V3_GOLDEN.read_bytes().decode("utf-8")
        assert render_core_prompt("system_v3", branch_selector_advertised=False) == golden

    def test_the_branch_rule_reaches_the_model_only_when_advertised(self) -> None:
        assert 'Every tool takes a "branch" argument' not in served_system_prompt()
        assert 'Every tool takes a "branch" argument' in served_system_prompt(branch=True)


class TestActiveTemplateResolution:
    def test_the_served_prompt_is_the_active_core_template(self) -> None:
        """The render site reads the active name off the surface record, so
        flipping that one name flips what every architecture is served."""
        active = render_core_prompt(ACTIVE_SYSTEM_PROMPT_TEMPLATE)
        assert active == SYSTEM_PROMPT
        assert served_system_prompt().startswith(active)

    def test_no_architecture_overrides_the_active_template_today(self) -> None:
        active = render_core_prompt(ACTIVE_SYSTEM_PROMPT_TEMPLATE)
        for name in agent_registry.names():
            assert _assemble_prompt(name, _CATALOG, None).startswith(active)
