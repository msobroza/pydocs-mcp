"""AC-29: every harness module stays readable in one tool call (CLAUDE.md §Code shape), and
reformulation lives in its own module. Core deps only — a line count, no imports."""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_HARNESS = _ROOT / "python/pydocs_mcp/harness/ask_your_docs"
_BUDGETS = {
    _HARNESS / "agent.py": 500,
    _HARNESS / "app.py": 500,
    _HARNESS / "binding.py": 500,
    _HARNESS / "serve_spawn.py": 500,
    _HARNESS / "llm_connection.py": 500,
    _HARNESS / "bearer_tokens.py": 500,
    _HARNESS / "model_listing.py": 500,
    _HARNESS / "connection_dialog.py": 500,
    _HARNESS / "reformulation.py": 500,
    _HARNESS / "first_turn.py": 200,
    _HARNESS / "prompt_assembly.py": 200,
    _HARNESS / "scope_pin.py": 200,
    # 300, not the 200 of the sibling pure modules: the token grammar carries its
    # refusal texts (spec §6.10a) and CLAUDE.md's 200-300 band covers it.
    _HARNESS / "scope_tokens.py": 300,
    # 300 like its pure sibling: the strip's value objects and its compiler, split out
    # of question_scope.py so neither module leaves CLAUDE.md's 200-300 band.
    _HARNESS / "strip_state.py": 300,
    # 420: the value object, its invariants, both resolution helpers, the four label
    # tables and the caption / note renderers live here (399 lines today); the strip
    # state that would have pushed it past 500 went to strip_state.py instead, and the
    # next growth (a second branch resolver) splits again rather than squeezing in.
    _HARNESS / "question_scope.py": 420,
    # 200: the panel, the pin popover and the pin chips left for the strip and the
    # picker; what stays (attachment chips, follow-up chips, the graph row, the branch
    # caption) fits the pure-module band, and the budget pins the shrink.
    _HARNESS / "scope_panel.py": 200,
    # 300 each: Streamlit fragments carry their labels and their callbacks; CLAUDE.md's
    # 200-300 band covers them.
    _HARNESS / "scope_strip.py": 300,
    _HARNESS / "scope_picker.py": 300,
    _HARNESS / "answer_footer.py": 500,
    _HARNESS / "page_scope.py": 200,
    _HARNESS / "transcript.py": 200,
    _HARNESS / "serve_session.py": 300,
    _HARNESS / "page_agent.py": 300,
    _HARNESS / "reasoning_capture.py": 250,
    _HARNESS / "reasoning_capability.py": 200,
    _HARNESS / "activity_events.py": 300,
    _HARNESS / "activity_labels.py": 300,
    _HARNESS / "activity_outcomes.py": 300,
    _HARNESS / "activity_trace.py": 300,
    _HARNESS / "activity_trace_builder.py": 300,
    _HARNESS / "activity_stream.py": 200,
    _HARNESS / "activity_redaction.py": 200,
    _HARNESS / "activity_view.py": 400,
    _HARNESS / "activity_markdown.py": 200,
    _HARNESS / "page_turn.py": 300,
    # 300 like page_turn.py, its sibling split out of app.py: the composer's send path
    # (the token parse, the pre-send refusals, the image policy, the one-shot pin)
    # carries its docstrings and WHY comments; CLAUDE.md's 200-300 band covers it.
    _HARNESS / "page_send.py": 300,
    # 320: the graph page script — the shared picker, the strip-fed branch row and
    # the canvas; the compare overlay of stage U1 splits it rather than growing it.
    _HARNESS / "pages" / "2_Graph.py": 320,
    _HARNESS / "reasoning_caption.py": 200,
    _HARNESS / "connection_test.py": 200,
    _HARNESS / "binding_llm_block.py": 200,
    _HARNESS / "binding_sent_settings.py": 200,
    _HARNESS / "page_connection_actions.py": 200,
    _HARNESS / "provider_profiles.py": 200,
    _HARNESS / "control_support.py": 200,
    _HARNESS / "param_rejections.py": 200,
    _HARNESS / "chat_wire.py": 200,
    _HARNESS / "connection_auth.py": 200,
    _HARNESS / "litellm_probe.py": 200,
    # Raised with the card-preset pre-fill (S7); _BUDGETS is a per-module knob and the
    # form still sits inside CLAUDE.md's 200-300 ideal band.
    _HARNESS / "model_settings_form.py": 250,
    _HARNESS / "param_feedback.py": 200,
    _HARNESS / "settings_view.py": 200,
    _HARNESS / "family_presets.py": 200,
    _ROOT / "python/pydocs_mcp/retrieval/llm_clients/reasoning_models.py": 200,
    _ROOT / "python/pydocs_mcp/retrieval/config/ask_your_docs_models.py": 200,
    # Split out of ask_your_docs_models.py (which the where-to-search block and the
    # seeded-search knob together pushed over 200) — the same free-the-budget move its
    # image / multimodal / params / scope / ui siblings already made.
    _ROOT / "python/pydocs_mcp/retrieval/config/ask_your_docs_llm_models.py": 200,
    _ROOT / "python/pydocs_mcp/retrieval/config/ask_your_docs_image_models.py": 200,
    _ROOT / "python/pydocs_mcp/retrieval/config/ask_your_docs_multimodal_models.py": 200,
    _ROOT / "python/pydocs_mcp/retrieval/config/ask_your_docs_ui_models.py": 200,
    _ROOT / "python/pydocs_mcp/retrieval/config/ask_your_docs_params_models.py": 200,
    _ROOT / "python/pydocs_mcp/retrieval/config/ask_your_docs_scope_models.py": 200,
}


@pytest.mark.parametrize(
    ("path", "budget"), sorted(_BUDGETS.items()), ids=lambda p: getattr(p, "name", p)
)
def test_module_line_budget(path: Path, budget: int) -> None:
    if not path.exists():
        pytest.skip(f"{path.name} lands in a later task")
    assert len(path.read_text(encoding="utf-8").splitlines()) < budget


def test_reformulation_moved_out_of_agent() -> None:
    agent_source = (_HARNESS / "agent.py").read_text(encoding="utf-8")
    reformulation_source = (_HARNESS / "reformulation.py").read_text(encoding="utf-8")
    assert "def reformulate(" not in agent_source and "def _history_line(" not in agent_source
    assert (
        "def reformulate(" in reformulation_source and "def _history_line(" in reformulation_source
    )
