"""Preflight enumeration for the agent track (spec §D15).

The preflight verifies the environment contract (CLI present, JSON output
fields, MCP server importable + boots, disk headroom) BEFORE any paid run. The
enumeration itself must be pure and offline — building the check list spends
nothing and touches no subprocess. The expensive checks (a one-token
``claude -p`` for the JSON contract, booting the MCP server) only run when a
check is *invoked*, never when the list is *built* — so this suite asserts the
enumerated names/order and that construction is side-effect-free.
"""

from __future__ import annotations

from pathlib import Path

from benchmarks.eval.agent_track.__main__ import PreflightCheck, preflight_checks


def test_preflight_checks_are_enumerated() -> None:
    checks = preflight_checks(python=Path("/venv/bin/python"))
    names = [c.name for c in checks]
    assert names == [
        "claude-cli-present",
        "claude-json-contract",
        "pydocs-mcp-importable",
        "mcp-config-boots",
        "disk-headroom",
    ]


def test_preflight_checks_are_check_objects() -> None:
    checks = preflight_checks(python=Path("/venv/bin/python"))
    assert all(isinstance(c, PreflightCheck) for c in checks)
    # Every check carries a callable ``run`` — the enumeration builds them but
    # spends nothing until a check is invoked.
    assert all(callable(c.run) for c in checks)


def test_preflight_enumeration_is_offline(monkeypatch) -> None:
    # Building the check list must never shell out. Poison subprocess so a
    # regression that eagerly runs a check (e.g. the paid claude probe) fails
    # loudly here rather than spending money in CI.
    import subprocess

    def _forbidden(*args, **kwargs):  # pragma: no cover - only fires on regression
        raise AssertionError("preflight enumeration must not spawn a subprocess")

    monkeypatch.setattr(subprocess, "run", _forbidden)
    monkeypatch.setattr(subprocess, "Popen", _forbidden)
    checks = preflight_checks(python=Path("/venv/bin/python"))
    assert len(checks) == 5
