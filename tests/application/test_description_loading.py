"""ADR 0005/0006 — packaged description source: loader, override, hash.

Pins the Task-2 contract of the Phase 1 plan:

- **Migration parity** — the rewired ``tool_docs`` module attributes are
  byte-identical to the Phase 0 literals (golden captured from the pre-rewire
  module at f4a8f2e; ``tests/fixtures/goldens/tool_docs_phase0_baseline.json``).
- **Packaged loading** — ``load_packaged()`` parses + validates
  ``defaults/descriptions.md`` and is the single source the module attributes
  are populated from at import.
- **Override** — ``apply_source(path)`` validates BEFORE rebinding (an invalid
  source must never half-apply) and hard-errors on missing/invalid files.
- **Hash truthfulness** — ``current_artifact_hash()`` changes iff the
  normalized source surface or ``RENDERER_VERSION`` changes, and reflects
  whatever is actually bound (including legacy attribute rebinding).
- **Packaging** — the ``.md`` ships exactly like ``default_config.yaml``
  (maturin ``include`` glob + importlib.resources visibility).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

from pydocs_mcp.application import description_source as ds
from pydocs_mcp.application import tool_docs

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN = _REPO_ROOT / "tests" / "fixtures" / "goldens" / "tool_docs_phase0_baseline.json"


@pytest.fixture
def restore_tool_docs():
    """Snapshot + restore the module attributes around rebinding tests.

    ``apply_source`` mutates process-global state (``TOOL_DOCS`` in place,
    ``SERVER_INSTRUCTIONS`` / ``SESSION_START_PREAMBLE`` by rebinding); without this
    fixture one test's overlay would leak into every later test in the run.
    """
    saved_docs = dict(tool_docs.TOOL_DOCS)
    saved_instructions = tool_docs.SERVER_INSTRUCTIONS
    saved_preamble = tool_docs.SESSION_START_PREAMBLE
    yield
    tool_docs.TOOL_DOCS.clear()
    tool_docs.TOOL_DOCS.update(saved_docs)
    tool_docs.SERVER_INSTRUCTIONS = saved_instructions
    tool_docs.SESSION_START_PREAMBLE = saved_preamble


def _packaged_sections() -> dict[str, str]:
    return ds.load_packaged()


def _write_document(path: Path, sections: dict[str, str]) -> Path:
    path.write_text(ds.render_sections(sections), encoding="utf-8")
    return path


# ── migration parity (one-time Phase 0 → Phase 1 pin) ─────────────────────────

# The golden stays the Phase 0 capture. Deliberate, owner-approved description
# edits made since are replayed onto it here, so every other byte stays pinned:
#
# 1. the get_references syntactic hedge (owner, 2026-09-10), inserted after its
#    "When NOT to use" line;
# 2. the get_references module-target clause (2026-09-11), inserted after that
#    hedge, documenting the import-graph answers module targets now get;
# 3. the grep glob-anchoring sentence (2026-09-11), inserted after its "Corpus"
#    line — grep's glob follows `rg --glob`, which the shipped `glob="*.py"`
#    example does not convey on its own;
# 4. the get_context example (2026-09-11), retargeted from a module (which
#    get_context rejects) to a class that resolves.
_GET_REFERENCES_HEDGE_ANCHOR = (
    "When NOT to use: you want source or docs (get_symbol / get_context).\n"
)
_GET_REFERENCES_HEDGE = (
    "Edges are syntactic — matched by name and import alias, not scope-resolved; "
    'meta.resolution reports the level per target ("unavailable" when the '
    "target's language has no working analyzer).\n"
)
_GET_REFERENCES_MODULE_CLAUSE = (
    "A module target answers its import graph: callers = modules importing it or its "
    "members, callees = its imports, impact = transitive callers of it and its members "
    "(its own internals excluded), governed_by = decisions on it; inherits needs a class.\n"
)
_GREP_CORPUS_ANCHOR = (
    "Corpus: the same file set the indexer sees (its discovery scope: exclusion floor + "
    "configured excludes + extension allowlist), served from live disk; .gitignore is NOT "
    'honored. scope="project" (default) | "deps" | "all".\n'
)
_GREP_GLOB_ANCHORING = (
    'glob: a pattern without "/" matches file names at any depth (like rg --glob); one '
    'with "/" matches the root-relative path; a leading "/" anchors at the root; a '
    'trailing "/" matches everything under that directory.\n'
)
_GET_CONTEXT_MODULE_EXAMPLE = 'get_context(targets=["pydocs_mcp.retrieval.pipeline"])\n'
_GET_CONTEXT_CLASS_EXAMPLE = (
    'get_context(targets=["pydocs_mcp.retrieval.pipeline.base.RetrieverPipeline"])\n'
)


def _replay_edit(doc: str, anchor: str, replacement: str) -> str:
    assert doc.count(anchor) == 1, f"replay anchor is not unique: {anchor!r}"
    return doc.replace(anchor, replacement)


def _phase0_docs_with_deliberate_edits(phase0_docs: dict[str, str]) -> dict[str, str]:
    docs = dict(phase0_docs)
    docs["get_references"] = _replay_edit(
        docs["get_references"],
        _GET_REFERENCES_HEDGE_ANCHOR,
        _GET_REFERENCES_HEDGE_ANCHOR + _GET_REFERENCES_HEDGE + _GET_REFERENCES_MODULE_CLAUSE,
    )
    docs["grep"] = _replay_edit(
        docs["grep"], _GREP_CORPUS_ANCHOR, _GREP_CORPUS_ANCHOR + _GREP_GLOB_ANCHORING
    )
    docs["get_context"] = _replay_edit(
        docs["get_context"], _GET_CONTEXT_MODULE_EXAMPLE, _GET_CONTEXT_CLASS_EXAMPLE
    )
    return docs


def test_tool_docs_byte_identical_to_phase0_literals_plus_deliberate_edits() -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    expected = _phase0_docs_with_deliberate_edits(golden["tool_docs"])
    assert dict(tool_docs.TOOL_DOCS) == expected
    assert golden["server_instructions"] == tool_docs.SERVER_INSTRUCTIONS


def test_tool_docs_iteration_order_is_contract_order() -> None:
    # test_mcp_surface_freeze pins this too; asserted here so a loader change
    # that scrambles dict insertion order fails next to its cause.
    assert tuple(tool_docs.TOOL_DOCS) == ds.FROZEN_TOOL_NAMES


# ── packaged loading ──────────────────────────────────────────────────────────


def test_load_packaged_returns_all_canonical_sections() -> None:
    sections = _packaged_sections()
    assert tuple(sections) == ds.CANONICAL_HEADERS


def test_module_attributes_are_populated_from_packaged_document() -> None:
    # attribute_views is the ONE projection both binding paths share; the
    # tool entries carry the re-attached trailing-newline terminator (the
    # Phase 0 literal bytes), which the canonical document form cannot hold.
    instructions, tool_view, preamble = ds.attribute_views(_packaged_sections())
    assert instructions == tool_docs.SERVER_INSTRUCTIONS
    assert preamble == tool_docs.SESSION_START_PREAMBLE
    assert dict(tool_docs.TOOL_DOCS) == tool_view
    for name in ds.FROZEN_TOOL_NAMES:
        assert tool_docs.TOOL_DOCS[name].endswith("\n")


def test_session_start_preamble_is_honest_framing_prose() -> None:
    # ADR 0008: the preamble frames an injected orientation block; it must be
    # substantive prose (not a stub) and must not claim tool-call provenance.
    assert isinstance(tool_docs.SESSION_START_PREAMBLE, str)
    assert len(tool_docs.SESSION_START_PREAMBLE) > 100
    assert "pydocs-mcp" in tool_docs.SESSION_START_PREAMBLE


def test_packaged_document_is_normalized() -> None:
    # The shipped file must already be the canonical byte surface, otherwise
    # the first normalize() pass would change what fingerprints hash.
    text = ds.resources.files("pydocs_mcp.defaults").joinpath("descriptions.md").read_text("utf-8")
    assert ds.normalize(text) == text


# ── apply_source ──────────────────────────────────────────────────────────────


def test_apply_source_rebinds_all_attributes(tmp_path: Path, restore_tool_docs) -> None:
    sections = _packaged_sections()
    sections[ds.tool_section_header("grep")] += "\nOverlay marker sentence."
    sections[ds.SERVER_INSTRUCTIONS_HEADER] = "Overlaid instructions."
    sections[ds.SESSION_START_PREAMBLE_HEADER] = "Overlaid preamble."
    path = _write_document(tmp_path / "overlay.md", sections)

    returned_hash = ds.apply_source(path)

    # The projected attribute carries the terminator newline (see
    # attribute_views); the overlay sentence sits right before it.
    assert tool_docs.TOOL_DOCS["grep"].endswith("Overlay marker sentence.\n")
    assert tool_docs.SERVER_INSTRUCTIONS == "Overlaid instructions."
    assert tool_docs.SESSION_START_PREAMBLE == "Overlaid preamble."
    assert returned_hash == ds.current_artifact_hash()


def test_apply_source_of_packaged_bytes_is_hash_neutral(tmp_path: Path, restore_tool_docs) -> None:
    baseline = ds.current_artifact_hash()
    packaged = (
        ds.resources.files("pydocs_mcp.defaults").joinpath("descriptions.md").read_text("utf-8")
    )
    path = tmp_path / "same.md"
    path.write_text(packaged, encoding="utf-8")
    assert ds.apply_source(path) == baseline


def test_apply_source_missing_path_is_hard_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope.md"
    with pytest.raises(ds.DescriptionSourceError, match="nope.md"):
        ds.apply_source(missing)


def test_apply_source_validates_before_rebinding(tmp_path: Path, restore_tool_docs) -> None:
    sections = _packaged_sections()
    del sections[ds.tool_section_header("glob")]
    path = _write_document(tmp_path / "invalid.md", sections)
    before = dict(tool_docs.TOOL_DOCS)

    with pytest.raises(ds.MissingSectionError):
        ds.apply_source(path)

    # Hard error must leave the live attributes untouched — no half-applied doc.
    assert dict(tool_docs.TOOL_DOCS) == before


def test_apply_source_rejects_marker_violations(tmp_path: Path, restore_tool_docs) -> None:
    sections = _packaged_sections()
    sections[ds.tool_section_header("grep")] = "Marker-free description."
    path = _write_document(tmp_path / "no_markers.md", sections)
    with pytest.raises(ds.MissingMarkerError):
        ds.apply_source(path)


# ── artifact hash ─────────────────────────────────────────────────────────────


def test_hash_is_deterministic_sha256_hex() -> None:
    first = ds.current_artifact_hash()
    assert first == ds.current_artifact_hash()
    assert len(first) == 64
    int(first, 16)  # raises unless hex


def test_hash_changes_when_a_section_changes(restore_tool_docs) -> None:
    baseline = ds.current_artifact_hash()
    tool_docs.TOOL_DOCS["grep"] += " drift"
    assert ds.current_artifact_hash() != baseline


def test_hash_restores_with_content(restore_tool_docs) -> None:
    baseline = ds.current_artifact_hash()
    saved = tool_docs.TOOL_DOCS["grep"]
    tool_docs.TOOL_DOCS["grep"] += " drift"
    tool_docs.TOOL_DOCS["grep"] = saved
    assert ds.current_artifact_hash() == baseline


def test_hash_changes_with_renderer_version(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = ds.current_artifact_hash()
    monkeypatch.setattr(ds, "RENDERER_VERSION", ds.RENDERER_VERSION + 1)
    assert ds.current_artifact_hash() != baseline


def test_hash_sees_legacy_attribute_rebinding(restore_tool_docs) -> None:
    # ADR 0006 §6: the hash must report whatever is actually bound, including
    # text injected by the benchmarks overlay wrapper (which rebinds the
    # module attributes directly, bypassing apply_source).
    baseline = ds.current_artifact_hash()
    tool_docs.SERVER_INSTRUCTIONS = "Legacy-wrapper overlay."
    assert ds.current_artifact_hash() != baseline


# ── packaging ─────────────────────────────────────────────────────────────────


def test_descriptions_md_ships_like_default_config_yaml() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = pyproject["tool"]["maturin"]["include"]
    assert "python/pydocs_mcp/defaults/*.yaml" in include  # the precedent
    assert "python/pydocs_mcp/defaults/*.md" in include


def test_descriptions_md_visible_via_importlib_resources() -> None:
    resource = ds.resources.files("pydocs_mcp.defaults").joinpath("descriptions.md")
    assert resource.is_file()
