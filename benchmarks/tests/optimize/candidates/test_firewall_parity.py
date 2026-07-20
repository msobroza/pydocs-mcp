"""The firewall-parity rule: firewall-accepts ⇒ product-accepts (ADR 0019 §6).

Battery of mutated candidates — header edits, marker deletions, budget
overflows, section reorders, duplicate/smuggled headers, and benign text edits
— asserted through BOTH the v2 firewall (``firewall_violations``) and the
product's own strict ``parse_sections`` + ``validate_sections`` (the exact
acceptance decision ``apply_source`` makes before it rebinds). Every
firewall-accepted document must pass the product path, and every
product-rejected document must have been firewall-rejected.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from pydocs_eval.optimize.candidates.candidate import Candidate
from pydocs_eval.optimize.candidates.firewall import firewall_violations, screen_candidate

ds = pytest.importorskip("pydocs_mcp.application.description_source")

_MARKER_DROP = ("TOOL: get_symbol", "Response contract")


def _render(sections: dict[str, str]) -> str:
    return ds.render_sections(sections)


def _pad_to_chars(text: str, n_chars: int) -> str:
    # Keep the existing markers, append filler to reach ``n_chars`` (token budget
    # probes). Filler is inert 'x' so no header-like line is introduced.
    return text if len(text) >= n_chars else text + "\n" + "x" * (n_chars - len(text) - 1)


def _battery() -> list[tuple[str, str, bool]]:
    """Return ``(label, document, expect_product_accepts)`` mutations of the seed."""
    seed = dict(Candidate.seed().sections)
    cases: list[tuple[str, str, bool]] = [("seed", _render(seed), True)]
    cases.append(_benign_tool_edit(seed))
    cases.append(_benign_server_edit(seed))
    cases.append(_benign_preamble_edit(seed))
    cases.append(_marker_deletion(seed))
    cases.append(_per_tool_overflow(seed))
    cases.append(_total_overflow(seed))
    cases.append(_section_reorder(seed))
    cases.append(_renamed_tool_header(seed))
    cases.append(_smuggled_header(seed))
    cases.append(_missing_tool_section(seed))
    cases.append(_duplicate_header(seed))
    cases.append(_oversized_server_instructions(seed))
    return cases


def _benign_tool_edit(seed: dict[str, str]) -> tuple[str, str, bool]:
    m = dict(seed)
    m["TOOL: grep"] = m["TOOL: grep"] + "\nAn extra clarifying sentence."
    return ("benign_tool_edit", _render(m), True)


def _benign_server_edit(seed: dict[str, str]) -> tuple[str, str, bool]:
    m = dict(seed)
    m["SERVER_INSTRUCTIONS"] = m["SERVER_INSTRUCTIONS"] + "\nAdded guidance."
    return ("benign_server_edit", _render(m), True)


def _benign_preamble_edit(seed: dict[str, str]) -> tuple[str, str, bool]:
    # The 10-vs-11 mismatch case: SESSION_START_PREAMBLE is a valid mutable
    # component; the v2 firewall must NOT flag a phantom header collision.
    m = dict(seed)
    m["SESSION_START_PREAMBLE"] = m["SESSION_START_PREAMBLE"] + "\nAdded preamble line."
    return ("benign_preamble_edit", _render(m), True)


def _marker_deletion(seed: dict[str, str]) -> tuple[str, str, bool]:
    section, marker = _MARKER_DROP
    m = dict(seed)
    m[section] = m[section].replace(marker, "REMOVED")
    return ("marker_deletion", _render(m), False)


def _per_tool_overflow(seed: dict[str, str]) -> tuple[str, str, bool]:
    m = dict(seed)
    m["TOOL: get_symbol"] = m["TOOL: get_symbol"] + "x" * (500 * 4 + 40)
    return ("per_tool_overflow", _render(m), False)


def _total_overflow(seed: dict[str, str]) -> tuple[str, str, bool]:
    # Pad each of the nine TOOL sections to ~420 tokens (< 500 per-tool) so only
    # the 3600 surface total is exceeded, never a per-tool cap.
    m = dict(seed)
    for name in ds.FROZEN_TOOL_NAMES:
        header = ds.tool_section_header(name)
        m[header] = _pad_to_chars(m[header], 420 * ds.CHARS_PER_TOKEN)
    return ("total_overflow", _render(m), False)


def _section_reorder(seed: dict[str, str]) -> tuple[str, str, bool]:
    # Product checks presence, NOT order → it ACCEPTS a reorder; the firewall's
    # extra order invariant REJECTS it (the safe, over-strict direction).
    items = list(seed.items())
    items[1], items[2] = items[2], items[1]
    return ("section_reorder", _render(dict(items)), True)


def _renamed_tool_header(seed: dict[str, str]) -> tuple[str, str, bool]:
    m = {("TOOL: grep_v2" if k == "TOOL: grep" else k): v for k, v in seed.items()}
    return ("renamed_tool_header", _render(m), False)


def _smuggled_header(seed: dict[str, str]) -> tuple[str, str, bool]:
    m = dict(seed)
    m["TOOL: get_why"] = m["TOOL: get_why"] + "\n=== TOOL: fake_tool ===\nsmuggled"
    return ("smuggled_header", _render(m), False)


def _missing_tool_section(seed: dict[str, str]) -> tuple[str, str, bool]:
    m = {k: v for k, v in seed.items() if k != "TOOL: read_file"}
    return ("missing_tool_section", _render(m), False)


def _duplicate_header(seed: dict[str, str]) -> tuple[str, str, bool]:
    # A repeated header — lenient parse is last-copy-wins (the old firewall would
    # MISS this), but the product strict parse raises DuplicateSectionError, so
    # the v2 firewall must reject it to keep parity.
    doc = _render(seed)
    doc = doc + "=== SERVER_INSTRUCTIONS ===\nduplicate copy\n"
    return ("duplicate_header", doc, False)


def _oversized_server_instructions(seed: dict[str, str]) -> tuple[str, str, bool]:
    # SERVER_INSTRUCTIONS is OUTSIDE the product token budget (TOOL sections
    # only), so a huge SERVER block is ACCEPTED by the product — and the v2
    # firewall's exact-parity budget must accept it too (the old ToolDocsArtifact
    # over-rejected here). Budget reconciliation, ADR 0019 §Decision 6a.
    m = dict(seed)
    m["SERVER_INSTRUCTIONS"] = "x" * (600 * ds.CHARS_PER_TOKEN)
    return ("oversized_server_instructions", _render(m), True)


def _product_accepts(document: str) -> bool:
    try:
        sections = ds.parse_sections(document, allowed=ds.CANONICAL_HEADERS)
        ds.validate_sections(sections)
    except ds.DescriptionSourceError:
        return False
    return True


@pytest.mark.parametrize(
    "label, document, expect_product", [(c[0], c[1], c[2]) for c in _battery()]
)
def test_firewall_accepts_implies_product_accepts(label, document, expect_product) -> None:
    firewall_ok = firewall_violations(document) == ()
    product_ok = _product_accepts(document)
    assert product_ok is expect_product, (
        f"{label}: product verdict drifted from battery expectation"
    )
    if firewall_ok:
        assert product_ok, (
            f"{label}: firewall ACCEPTED a document the product REJECTS (parity break)"
        )
    if not product_ok:
        assert not firewall_ok, (
            f"{label}: product REJECTED a document the firewall accepted (parity break)"
        )


def test_every_firewall_accepted_battery_doc_survives_apply_source() -> None:
    # The strongest form of the rule: a firewall-accepted document must load
    # through the REAL product serve path (apply_source) without raising.
    from pydocs_mcp.application import tool_docs

    saved = (
        tool_docs.SERVER_INSTRUCTIONS,
        dict(tool_docs.TOOL_DOCS),
        tool_docs.SESSION_START_PREAMBLE,
    )
    try:
        with tempfile.TemporaryDirectory() as d:
            for label, document, _ in _battery():
                if firewall_violations(document):
                    continue
                path = Path(d) / f"{label}.md"
                path.write_text(document, encoding="utf-8")
                ds.apply_source(path)  # must not raise for any firewall-accepted doc
    finally:
        tool_docs.SERVER_INSTRUCTIONS, restored, tool_docs.SESSION_START_PREAMBLE = saved
        tool_docs.TOOL_DOCS.clear()
        tool_docs.TOOL_DOCS.update(restored)


def test_reorder_is_firewall_rejected_but_product_accepted() -> None:
    # Pin the one over-strict direction explicitly: the firewall's order
    # invariant rejects a document the product would serve (search-space
    # shrink, never a wasted rollout).
    _, document, _ = _section_reorder(dict(Candidate.seed().sections))
    assert firewall_violations(document)  # firewall rejects
    assert _product_accepts(document)  # product accepts (no order check)


def test_screen_candidate_reports_verdict() -> None:
    valid = screen_candidate(Candidate.seed())
    assert valid.valid and valid.violations == ()
    broken_sections = dict(Candidate.seed().sections)
    section, marker = _MARKER_DROP
    broken_sections[section] = broken_sections[section].replace(marker, "REMOVED")
    verdict = screen_candidate(Candidate(sections=broken_sections))
    assert not verdict.valid and verdict.violations
