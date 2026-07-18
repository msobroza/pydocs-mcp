"""The ask_prompt text artifact — markers, budgets, tool names, seed parity (AC-3, AC-4)."""

from __future__ import annotations

from importlib.resources import files

from pydocs_mcp.application.tool_docs import TOOL_DOCS
from pydocs_mcp.ask_your_docs.prompts import SYSTEM_PROMPT, render_shared

from pydocs_eval.optimize.artifacts._delimited import parse_delimited, render_delimited
from pydocs_eval.optimize.artifacts.ask_prompt import (
    _REWRITE_KEY,
    _SYSTEM_KEY,
    AskPromptArtifact,
)
from pydocs_eval.optimize.registries import artifact_registry


def _doc(system: str | None = None, rewrite: str = "Rewrite {history} {question}") -> str:
    if system is None:
        system = "Use " + ", ".join(TOOL_DOCS) + "."
    return render_delimited({_SYSTEM_KEY: system, _REWRITE_KEY: rewrite})


class TestSeed:
    def test_registered(self) -> None:
        assert "ask_prompt" in artifact_registry.names()

    def test_unseeded_render_is_the_live_product_prompts(self) -> None:
        # The delimited grammar normalizes a mid-document section's trailing
        # newline away on parse (the documented round-trip semantics) — every
        # candidate INCLUDING the seed rides the same normalized injection
        # path, so the system pin is the one-newline-normalized constant; the
        # rewrite (last section, EOF-terminated) round-trips verbatim.
        sections = parse_delimited(AskPromptArtifact().render())
        assert sections[_SYSTEM_KEY] == SYSTEM_PROMPT.removesuffix("\n")
        assert sections[_REWRITE_KEY] == render_shared(
            "rewrite_v1", history="{history}", question="{question}"
        )

    def test_seed_file_parity_with_live_render(self) -> None:
        # AC-4 regeneration test: the committed package-data seed equals the
        # live render byte-for-byte — drift between the product prompts and
        # the shipped seed fails CI.
        seed = files("pydocs_eval.optimize.artifacts").joinpath("ask_prompt_seed.md")
        assert seed.read_text(encoding="utf-8") == AskPromptArtifact().render()

    def test_seed_passes_its_own_firewall(self) -> None:
        assert AskPromptArtifact().validate() == ()

    def test_fingerprint_is_sha256_of_render(self) -> None:
        art = AskPromptArtifact()
        assert len(art.fingerprint) == 64
        assert art.fingerprint == art.with_content(art.render()).fingerprint


class TestValidate:
    def test_missing_section_is_a_distinct_violation(self) -> None:
        doc = render_delimited({_SYSTEM_KEY: "Use " + ", ".join(TOOL_DOCS)})
        violations = AskPromptArtifact().with_content(doc).validate()
        assert any(_REWRITE_KEY in v and "missing" in v for v in violations)

    def test_duplicated_section_is_a_distinct_violation(self) -> None:
        doc = _doc() + _doc()
        violations = AskPromptArtifact().with_content(doc).validate()
        assert any("once" in v or "duplicate" in v.lower() for v in violations)

    def test_sections_out_of_order_flagged(self) -> None:
        doc = render_delimited(
            {_REWRITE_KEY: "Rewrite {history} {question}", _SYSTEM_KEY: ", ".join(TOOL_DOCS)}
        )
        violations = AskPromptArtifact().with_content(doc).validate()
        assert any("order" in v for v in violations)

    def test_over_budget_system_section_flagged(self) -> None:
        bloated = ("Use " + ", ".join(TOOL_DOCS) + ". ") + "x" * 60_000
        violations = AskPromptArtifact().with_content(_doc(system=bloated)).validate()
        assert any("tokens" in v and _SYSTEM_KEY in v for v in violations)

    def test_over_budget_rewrite_section_flagged(self) -> None:
        violations = (
            AskPromptArtifact()
            .with_content(_doc(rewrite="{history} {question} " + "y" * 20_000))
            .validate()
        )
        assert any("tokens" in v and _REWRITE_KEY in v for v in violations)

    def test_system_must_name_every_live_tool(self) -> None:
        # Iterated from TOOL_DOCS keys — never a hard-coded name list.
        partial = ", ".join(list(TOOL_DOCS)[:-1])
        violations = AskPromptArtifact().with_content(_doc(system=partial)).validate()
        missing_tool = list(TOOL_DOCS)[-1]
        assert any(missing_tool in v for v in violations)

    def test_empty_section_flagged(self) -> None:
        violations = AskPromptArtifact().with_content(_doc(rewrite="")).validate()
        assert any("empty" in v and _REWRITE_KEY in v for v in violations)

    def test_valid_candidate_passes(self) -> None:
        assert AskPromptArtifact().with_content(_doc()).validate() == ()


class TestAccessors:
    def test_section_accessors_feed_the_runner_factory(self) -> None:
        art = AskPromptArtifact().with_content(_doc(system="SYS " + ", ".join(TOOL_DOCS)))
        assert art.system_prompt().startswith("SYS ")
        assert "{history}" in art.rewrite_template()
