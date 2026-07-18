"""ADR 0008 channel gate: ``turn0_context_for_workspace`` (core-only imports).

The helper is deliberately langgraph-free so the flag gate + pack build are
testable without the ``[ask-your-docs]`` extra; ``agent.build_agent`` calls it
at the single prompt-assembly site (covered langgraph-gated in
``test_prompt_seam.py``).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from pydocs_mcp.ask_your_docs.turn0 import turn0_context_for_workspace


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    yield


def _enabled_config(tmp_path: Path, budget_tokens: int = 777) -> str:
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text(
        f"serve:\n  turn0_context:\n    enabled: true\n    budget_tokens: {budget_tokens}\n"
    )
    return str(overlay)


def test_flag_off_returns_none_without_touching_the_workspace(tmp_path: Path) -> None:
    """Default (disabled) short-circuits BEFORE workspace discovery — a
    nonexistent workspace must not raise, proving byte-identical assembly
    costs nothing when off."""
    missing = tmp_path / "no-such-workspace"
    assert asyncio.run(turn0_context_for_workspace(str(missing), None)) is None


def test_flag_on_builds_the_pack_for_the_first_bundle(tmp_path: Path, monkeypatch) -> None:
    """Enabled: the pack is built over the FIRST workspace bundle (the same
    services[0] default-project rule the MCP server uses) with the YAML
    budget threaded through."""
    first = SimpleNamespace(
        db_path=Path("/bundles/alpha.db"),
        metadata=SimpleNamespace(project_root="/repos/alpha"),
    )
    second = SimpleNamespace(
        db_path=Path("/bundles/beta.db"),
        metadata=SimpleNamespace(project_root="/repos/beta"),
    )
    sentinel_overview = object()
    sentinel_factory = object()
    captured: dict[str, object] = {}

    monkeypatch.setattr("pydocs_mcp.multirepo.discover_workspace", lambda ws: [first, second])

    def _fake_overview(db_path, *, project_root, config):
        captured["overview_db"] = db_path
        captured["project_root"] = project_root
        return sentinel_overview

    def _fake_uow_factory(db_path):
        captured["uow_db"] = db_path
        return sentinel_factory

    async def _fake_build(*, uow_factory, overview, budget_tokens, package=""):
        captured["uow_factory"] = uow_factory
        captured["overview"] = overview
        captured["budget_tokens"] = budget_tokens
        return "PACK"

    monkeypatch.setattr(
        "pydocs_mcp.storage.factories.build_sqlite_overview_service", _fake_overview
    )
    monkeypatch.setattr("pydocs_mcp.storage.factories.build_sqlite_uow_factory", _fake_uow_factory)
    monkeypatch.setattr("pydocs_mcp.application.turn0_context.build_turn0_context", _fake_build)

    result = asyncio.run(turn0_context_for_workspace("/any/workspace", _enabled_config(tmp_path)))
    assert result == "PACK"
    assert captured["overview_db"] == first.db_path
    assert captured["uow_db"] == first.db_path
    assert captured["project_root"] == Path("/repos/alpha")
    assert captured["overview"] is sentinel_overview
    assert captured["uow_factory"] is sentinel_factory
    assert captured["budget_tokens"] == 777


def test_flag_on_with_missing_workspace_fails_loudly(tmp_path: Path) -> None:
    """Enabled + broken workspace propagates the discovery error — the serve
    subprocess would fail on the same workspace, and a silent skip would
    contaminate the ablation arms with an unmarked control."""
    missing = tmp_path / "no-such-workspace"
    with pytest.raises(FileNotFoundError):
        asyncio.run(turn0_context_for_workspace(str(missing), _enabled_config(tmp_path)))
