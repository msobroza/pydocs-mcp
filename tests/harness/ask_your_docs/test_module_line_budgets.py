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
    _HARNESS / "scope_pin.py": 200,
    _HARNESS / "scope_pickers.py": 200,
    _HARNESS / "serve_session.py": 300,
    _HARNESS / "page_agent.py": 300,
    _HARNESS / "reasoning_capture.py": 250,
    _ROOT / "python/pydocs_mcp/retrieval/config/ask_your_docs_models.py": 200,
    _ROOT / "python/pydocs_mcp/retrieval/config/ask_your_docs_image_models.py": 200,
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
