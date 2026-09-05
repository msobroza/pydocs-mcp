"""Tests for the deterministic decision-mining sources (spec §D8).

Two sources here: ``inline_markers`` mines the already-extracted
:class:`DocumentNode` trees for ``# WHY:`` / ``# DECISION:`` /
``# TRADEOFF:`` / ``# RATIONALE:`` / ``# REJECTED:`` / ``# WORKAROUND:``
markers (no file re-reads — nodes carry ``text``/``source_path``/``start_line``);
``adr_files`` globs the project's ADR directories and parses the heading /
Status: / Date: headers. Both satisfy the :class:`DecisionSource` Protocol and
emit pre-merge :class:`RawDecision` records with verbatim evidence spans.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.decisions import (
    CaptureContext,
    decision_source_registry,
)
from pydocs_mcp.extraction.decisions.sources import AdrFilesSource, InlineMarkersSource
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.project_toml import ProjectExcludes
from pydocs_mcp.retrieval.config.models import DecisionCaptureConfig


def _cfg() -> DecisionCaptureConfig:
    return DecisionCaptureConfig()


def _module_node(qname: str, source_path: str, text: str, start: int = 1) -> DocumentNode:
    """Mirror existing tree-test helpers — one MODULE node carrying ``text``."""
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=NodeKind.MODULE,
        source_path=source_path,
        start_line=start,
        end_line=start + text.count("\n"),
        text=text,
        content_hash="deadbeef",
        summary="",
        extra_metadata={},
        parent_id=None,
        children=(),
    )


# ── inline_markers ────────────────────────────────────────────────────────


async def test_inline_marker_yields_raw_decision_with_context_window() -> None:
    text = (
        "def f():\n"
        "    pass\n"
        "\n"
        "# DECISION: vectors live in a .tq sidecar\n"
        "# keeps SQLite rows slim\n"
        "def g():\n"
        "    pass\n"
    )
    node = _module_node("proj.storage", "proj/storage.py", text)
    ctx = CaptureContext(project_root=Path("/x"), trees=(node,), config=_cfg())
    raws = await InlineMarkersSource().mine(ctx)
    assert (
        raws[0].title.startswith("vectors live in a .tq sidecar"[:20]) or "sidecar" in raws[0].title
    )
    assert raws[0].status == "active" and raws[0].confidence == 0.95
    assert raws[0].affected_files == ("proj/storage.py",)
    assert raws[0].affected_qnames == ("proj.storage",)
    assert "# DECISION:" in raws[0].evidence[0].text  # verbatim window
    assert raws[0].evidence[0].locator.startswith("proj/storage.py:")


async def test_rejected_marker_gets_rejected_status() -> None:
    text = "# REJECTED: redis cache — extra infra for no win\n"
    node = _module_node("proj.cache", "proj/cache.py", text)
    ctx = CaptureContext(project_root=Path("/x"), trees=(node,), config=_cfg())
    raws = await InlineMarkersSource().mine(ctx)
    assert raws[0].status == "rejected"
    assert "redis cache" in raws[0].title


async def test_all_six_markers_detected_and_non_markers_ignored() -> None:
    text = (
        "# WHY: we chose porter stemming\n"
        "# DECISION: FTS5 for BM25\n"
        "# TRADEOFF: inspect mode risks import side effects\n"
        "# RATIONALE: RRF fuses without tuned weights\n"
        "# REJECTED: a single combined engine\n"
        "# WORKAROUND: pin turbovec below 1.0\n"
        "# NOTE: this is not a decision marker\n"
    )
    node = _module_node("proj.mod", "proj/mod.py", text)
    ctx = CaptureContext(project_root=Path("/x"), trees=(node,), config=_cfg())
    raws = await InlineMarkersSource().mine(ctx)
    assert len(raws) == 6
    titles = " ".join(r.title for r in raws)
    assert "not a decision marker" not in titles


# ── adr_files ─────────────────────────────────────────────────────────────


async def test_adr_file_parsed_with_status_mapping(tmp_path) -> None:
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-use-sqlite.md").write_text(
        "# 1. Use SQLite for the index\n\nStatus: Accepted\nDate: 2026-05-01\n\n"
        "## Context\nWe need local persistence in pkg/db.py.\n\n## Decision\nSQLite with FTS5.\n"
    )
    raws = await AdrFilesSource().mine(
        CaptureContext(project_root=tmp_path, trees=(), config=_cfg())
    )
    assert raws[0].title == "Use SQLite for the index"
    assert raws[0].status == "active" and raws[0].confidence == 1.0
    assert raws[0].evidence_date is not None  # from the Date: header


async def test_adr_unknown_status_maps_to_proposed(tmp_path) -> None:
    adr = tmp_path / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0002-mystery.md").write_text(
        "# 2. A mystery decision\n\nStatus: Ruminating\n\n## Decision\nUnclear.\n"
    )
    raws = await AdrFilesSource().mine(
        CaptureContext(project_root=tmp_path, trees=(), config=_cfg())
    )
    assert raws[0].title == "A mystery decision"
    assert raws[0].status == "proposed"


async def test_source_registry_lists_both() -> None:
    assert {"inline_markers", "adr_files"} <= set(decision_source_registry.names())


# ── adr_files × effective excludes (spec 7.8, AC-21) ─────────────────────────


def _two_adr_dirs(tmp_path) -> None:
    """One ADR under docs/adr/ and one under the root-level adr/ convention."""
    docs_adr = tmp_path / "docs" / "adr"
    docs_adr.mkdir(parents=True)
    (docs_adr / "0001-docs-side.md").write_text(
        "# 1. Docs-side decision\n\nStatus: Accepted\n\n## Decision\nSQLite.\n"
    )
    root_adr = tmp_path / "adr"
    root_adr.mkdir()
    (root_adr / "0001-root-side.md").write_text(
        "# 1. Root-side decision\n\nStatus: Accepted\n\n## Decision\nFTS5.\n"
    )


async def test_adr_source_skips_excluded_parent_dirs(tmp_path) -> None:
    _two_adr_dirs(tmp_path)
    excluded = ProjectExcludes(names=frozenset({"docs"}), anchored=frozenset())
    raws = await AdrFilesSource().mine(
        CaptureContext(project_root=tmp_path, trees=(), config=_cfg(), excluded=excluded)
    )
    # bare "docs" silences docs/adr; the root-level adr/ fixture still mines.
    assert [r.title for r in raws] == ["Root-side decision"]


async def test_adr_source_anchored_entry_leaves_other_candidates(tmp_path) -> None:
    _two_adr_dirs(tmp_path)
    excluded = ProjectExcludes(names=frozenset(), anchored=frozenset({"docs/generated"}))
    raws = await AdrFilesSource().mine(
        CaptureContext(project_root=tmp_path, trees=(), config=_cfg(), excluded=excluded)
    )
    # anchored "docs/generated" matches neither docs/adr nor adr — all mine.
    assert sorted(r.title for r in raws) == ["Docs-side decision", "Root-side decision"]


async def test_adr_source_default_excluded_is_identity(tmp_path) -> None:
    _two_adr_dirs(tmp_path)
    raws = await AdrFilesSource().mine(
        CaptureContext(project_root=tmp_path, trees=(), config=_cfg())
    )
    # A directly-constructed context (no excluded kwarg) behaves exactly as today.
    assert sorted(r.title for r in raws) == ["Docs-side decision", "Root-side decision"]
