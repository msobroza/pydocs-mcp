"""GEPA-view candidate bridge + serve-truthful hash parity (ADR 0017 R3)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pydocs_eval.optimize.candidates.candidate import CANONICAL_SECTION_KEYS, Candidate

ds = pytest.importorskip("pydocs_mcp.application.description_source")


@pytest.fixture
def restore_live_surface():
    """Snapshot + restore the product ``tool_docs`` attributes around ``apply_source``.

    ``apply_source`` rebinds the live module attributes as a side effect; a test
    that exercises the real serve path must not leak the mutated surface into
    other tests sharing the process.
    """
    from pydocs_mcp.application import tool_docs

    saved = (
        tool_docs.SERVER_INSTRUCTIONS,
        dict(tool_docs.TOOL_DOCS),
        tool_docs.SESSION_START_PREAMBLE,
    )
    yield
    tool_docs.SERVER_INSTRUCTIONS, restored_docs, tool_docs.SESSION_START_PREAMBLE = saved
    tool_docs.TOOL_DOCS.clear()
    tool_docs.TOOL_DOCS.update(restored_docs)


def test_seed_is_the_eleven_canonical_sections() -> None:
    seed = Candidate.seed()
    assert tuple(seed.sections) == CANONICAL_SECTION_KEYS
    assert len(CANONICAL_SECTION_KEYS) == 11


def test_gepa_view_round_trips() -> None:
    seed = Candidate.seed()
    assert Candidate.from_gepa(seed.to_gepa()).sections == seed.sections
    assert seed.to_gepa() is not seed.sections  # a fresh copy, not an alias


def test_from_document_render_round_trip_is_idempotent() -> None:
    once = Candidate.from_document(Candidate.seed().render()).normalized()
    twice = Candidate.from_document(once).normalized()
    assert once == twice


def test_candidate_hash_is_stable_64_hex() -> None:
    a, b = Candidate.seed(), Candidate.seed()
    assert a.candidate_hash == b.candidate_hash
    assert len(a.candidate_hash) == 64
    int(a.candidate_hash, 16)  # hex-parseable


def test_seed_hash_equals_product_packaged_hash() -> None:
    # The seed IS the packaged document, so its serve-truthful hash must equal
    # the product's own packaged_artifact_hash (no drift between the bridge's
    # public payload replication and the product's private hasher).
    assert Candidate.seed().candidate_hash == ds.packaged_artifact_hash()


def test_mutated_candidate_hash_equals_serve_apply_source(restore_live_surface) -> None:
    # THE PARITY that matters: the ledger's candidate_hash must equal the
    # artifact_hash a real serve stamps into the trace header — i.e. the value
    # apply_source returns after loading this candidate. Proven end-to-end here.
    sections = dict(Candidate.seed().sections)
    sections["TOOL: grep"] = sections["TOOL: grep"] + "\nExtra optimization note."
    candidate = Candidate(sections=sections)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "candidate.md"
        path.write_text(candidate.render(), encoding="utf-8")
        served_hash = ds.apply_source(path)
    assert candidate.candidate_hash == served_hash


def test_from_gepa_accepts_a_partial_component_edit() -> None:
    # GEPA hands back a full dict[str, str]; a single-component edit is just a
    # new dict. The bridge does not validate (the firewall owns that).
    components = Candidate.seed().to_gepa()
    components["SESSION_START_PREAMBLE"] = "mutated preamble text"
    candidate = Candidate.from_gepa(components)
    assert candidate.sections["SESSION_START_PREAMBLE"] == "mutated preamble text"
    assert "mutated preamble text" in candidate.render()
