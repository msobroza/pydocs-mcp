"""Arm-B overlay server wrapper: fail-closed tool_docs injection (plan Task 12,
spec §D6).

The wrapper re-binds ``pydocs_mcp.application.tool_docs`` module attributes
BEFORE delegating to ``pydocs_mcp.server.run`` — the spec's recorded §D6
alternative to an ``AppConfig.tool_docs_overlay_path`` field, so the product
footprint stays at the §D2b lint-constant refactor only. These tests mutate
those module attributes; the autouse fixture snapshots and restores them so no
test leaks a re-bound surface into another.

Fully offline (slice-6 contract): ``pydocs_mcp.server.run`` is monkeypatched to
a recording double, so no MCP server ever boots, no subprocess, no socket.
"""

from __future__ import annotations

import pytest

import pydocs_mcp.application.tool_docs as td
from pydocs_eval.optimize._overlay_server import (
    OverlayValidationError,
    serve_with_overlay,
)
from pydocs_eval.optimize.artifacts._delimited import parse_delimited, render_delimited
from pydocs_eval.optimize.artifacts.tool_docs import ToolDocsArtifact


@pytest.fixture(autouse=True)
def _restore_tool_docs():
    """Snapshot + restore the mutated module attributes around each test.

    ``serve_with_overlay`` re-binds ``td.TOOL_DOCS`` entries,
    ``td.SERVER_INSTRUCTIONS``, and (through the product ``apply_source``
    loader) ``td.TURN0_PREAMBLE`` in place; without this, a valid-overlay
    test would leak its injected text into the byte-identical-noop test.
    """
    before = (dict(td.TOOL_DOCS), td.SERVER_INSTRUCTIONS, td.TURN0_PREAMBLE)
    yield
    td.TOOL_DOCS.clear()
    td.TOOL_DOCS.update(before[0])
    td.SERVER_INSTRUCTIONS = before[1]
    td.TURN0_PREAMBLE = before[2]


def _valid_overlay_with(*, get_why: str) -> str:
    """A valid delimited overlay: the seed surface with ``get_why`` replaced.

    Keeping every §D13 marker (the seed already carries them) and only swapping
    the leading sentence keeps the candidate under budget and structurally
    valid, so ``ToolDocsArtifact().validate()`` returns no violations.
    """
    sections = parse_delimited(ToolDocsArtifact().render())
    original = sections["TOOL: get_why"]
    # Replace only the first line (the human-readable summary) so all §D13
    # markers below it survive; the caller's text becomes the new summary.
    _, _, tail = original.partition("\n")
    sections["TOOL: get_why"] = f"{get_why}\n{tail}"
    return render_delimited(sections)


def _overlay_blowing_the_token_budget() -> str:
    """A delimited overlay whose ``get_symbol`` section blows the per-tool cap."""
    sections = parse_delimited(ToolDocsArtifact().render())
    sections["TOOL: get_symbol"] = "x" * (500 * 4 + 40)  # > 500-token/tool budget
    return render_delimited(sections)


def test_valid_overlay_rebinds_module_attrs_then_delegates(monkeypatch, tmp_path) -> None:
    calls = {}
    monkeypatch.setattr("pydocs_mcp.server.run", lambda **kw: calls.setdefault("run", kw))
    overlay = tmp_path / "overlay.txt"
    overlay.write_text(_valid_overlay_with(get_why="Explains rationale. USE WHEN ..."))
    serve_with_overlay(project=tmp_path, overlay=overlay)
    assert "Explains rationale" in td.TOOL_DOCS["get_why"] and "run" in calls


def test_invalid_overlay_refuses_to_serve(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("pydocs_mcp.server.run", lambda **kw: pytest.fail("must not serve"))
    overlay = tmp_path / "overlay.txt"
    overlay.write_text(_overlay_blowing_the_token_budget())
    with pytest.raises(OverlayValidationError, match="get_symbol"):
        serve_with_overlay(project=tmp_path, overlay=overlay)  # fail-closed (spec §D6)


def test_no_overlay_is_byte_identical_noop(monkeypatch, tmp_path) -> None:
    before = (dict(td.TOOL_DOCS), td.SERVER_INSTRUCTIONS)
    monkeypatch.setattr("pydocs_mcp.server.run", lambda **kw: None)
    serve_with_overlay(project=tmp_path, overlay=None)
    assert (dict(td.TOOL_DOCS), td.SERVER_INSTRUCTIONS) == before


def test_valid_overlay_rebinds_server_instructions(monkeypatch, tmp_path) -> None:
    # SERVER_INSTRUCTIONS is the other re-bound attribute; a candidate that edits
    # it must reach the product module too (server.py:183 reads it at call time).
    monkeypatch.setattr("pydocs_mcp.server.run", lambda **kw: None)
    sections = parse_delimited(ToolDocsArtifact().render())
    sections["SERVER_INSTRUCTIONS"] = sections["SERVER_INSTRUCTIONS"] + " OVERLAY-MARKER"
    overlay = tmp_path / "overlay.txt"
    overlay.write_text(render_delimited(sections))
    serve_with_overlay(project=tmp_path, overlay=overlay)
    assert "OVERLAY-MARKER" in td.SERVER_INSTRUCTIONS


def test_delegates_through_shared_db_resolution(monkeypatch, tmp_path) -> None:
    # The wrapper must resolve the corpus path through the SAME helper the CLI
    # serve command uses so the per-project path-hash cache never forks. Assert
    # the db_path handed to server.run matches cache_path_for_project(project).
    from pydocs_mcp.db import cache_path_for_project

    captured = {}
    monkeypatch.setattr("pydocs_mcp.server.run", lambda **kw: captured.update(kw))
    serve_with_overlay(project=tmp_path, overlay=None)
    expected = cache_path_for_project(tmp_path.resolve())
    assert captured["db_path"] == expected


def test_overlay_preserves_live_turn0_preamble(monkeypatch, tmp_path) -> None:
    # The overlay optimizes only the tool_docs surface, but the product
    # loader (apply_source) validates the FULL canonical document — the
    # wrapper must carry the live TURN0_PREAMBLE through unchanged rather
    # than fail on the missing section or blank it.
    monkeypatch.setattr("pydocs_mcp.server.run", lambda **kw: None)
    before = td.TURN0_PREAMBLE
    overlay = tmp_path / "overlay.txt"
    overlay.write_text(_valid_overlay_with(get_why="Explains rationale. USE WHEN ..."))
    serve_with_overlay(project=tmp_path, overlay=overlay)
    assert before == td.TURN0_PREAMBLE


def test_overlay_tool_docs_end_with_exactly_one_newline(monkeypatch, tmp_path) -> None:
    # Rebinding goes through the product loader, which re-attaches the
    # tool-doc terminator: a candidate section WITHOUT a trailing newline is
    # normalized to the product invariant (exactly one), and a terminated
    # one is never double-terminated.
    monkeypatch.setattr("pydocs_mcp.server.run", lambda **kw: None)
    before_grep = td.TOOL_DOCS["grep"]
    sections = parse_delimited(ToolDocsArtifact().render())
    sections["TOOL: get_why"] = sections["TOOL: get_why"].rstrip("\n")
    overlay = tmp_path / "overlay.txt"
    overlay.write_text(render_delimited(sections))
    serve_with_overlay(project=tmp_path, overlay=overlay)
    assert td.TOOL_DOCS["get_why"].endswith("\n")
    assert not td.TOOL_DOCS["get_why"].endswith("\n\n")
    # An untouched section round-trips byte-identically to the live value
    # (the artifact render → parse pass eats a middle section's trailing
    # newline; the loader's terminator re-attachment restores it).
    assert td.TOOL_DOCS["grep"] == before_grep


def test_overlay_updates_current_artifact_hash(monkeypatch, tmp_path) -> None:
    # ADR 0006: the served-surface fingerprint is computed from the LIVE
    # module attributes, so an overlay run reports a hash reflecting the
    # overlay (truthful), never the packaged document's.
    from pydocs_mcp.application.description_source import current_artifact_hash

    monkeypatch.setattr("pydocs_mcp.server.run", lambda **kw: None)
    packaged_hash = current_artifact_hash()
    overlay = tmp_path / "overlay.txt"
    overlay.write_text(_valid_overlay_with(get_why="Explains rationale. USE WHEN ..."))
    serve_with_overlay(project=tmp_path, overlay=overlay)
    assert current_artifact_hash() != packaged_hash


def test_main_parses_project_and_overlay(monkeypatch, tmp_path) -> None:
    calls = {}
    monkeypatch.setattr(
        "pydocs_eval.optimize._overlay_server.serve_with_overlay",
        lambda *, project, overlay: calls.setdefault("call", (project, overlay)),
    )
    from pydocs_eval.optimize._overlay_server import main

    overlay = tmp_path / "o.txt"
    overlay.write_text("x")
    main([str(tmp_path), "--overlay", str(overlay)])
    project, got_overlay = calls["call"]
    assert project == tmp_path and got_overlay == overlay
