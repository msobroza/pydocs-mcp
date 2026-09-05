"""Tests for application.formatting.format_references — spec §5.7 + appendix §A.1.

The 7 tests below pin the §A.1 markdown shape: empty / single-row / multi-row
grouped output, resolved-first sort with ⚠ prefix on unresolved rows, resolved
vs unresolved counts, and H1 wording per ``show`` (callers / callees / inherits).
"""

from __future__ import annotations

from pydocs_mcp.application.formatting import format_references
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference


def _ref(
    *,
    from_package: str,
    from_node_id: str,
    to_name: str,
    to_node_id: str | None,
    kind: ReferenceKind = ReferenceKind.CALLS,
) -> NodeReference:
    return NodeReference(
        from_package=from_package,
        from_node_id=from_node_id,
        to_name=to_name,
        to_node_id=to_node_id,
        kind=kind,
    )


def test_format_references_empty_returns_no_references_message():
    """Empty rows path produces clean H1 + ``No callers found.`` body (§A.1).

    The function must still emit the H1 so downstream parsers see a
    consistent shape regardless of zero-vs-many rows.
    """
    out = format_references(
        (),
        target="pkg.helpers.compute",
        show="callers",
        limit=50,
    )
    assert out.startswith("# Callers of `pkg.helpers.compute`\n"), (
        f"H1 missing or wrong: {out[:60]!r}"
    )
    assert "No callers found." in out, f"empty body wrong: {out!r}"
    assert out.endswith("\n"), f"trailing newline stripped: {out[-10:]!r}"


def test_format_references_single_resolved_row():
    """One resolved row → H1, lead summary, single group, one bullet."""
    rows = (
        _ref(
            from_package="pkg",
            from_node_id="pkg.cli.main",
            to_name="pkg.helpers.compute",
            to_node_id="pkg.helpers.compute",
        ),
    )
    out = format_references(rows, target="pkg.helpers.compute", show="callers", limit=50)
    assert out.startswith("# Callers of `pkg.helpers.compute`\n")
    assert "1 references found (1 resolved, 0 unresolved)." in out, out
    assert "## from `pkg` (1 caller)" in out, out
    assert "- `pkg.cli.main` → `pkg.helpers.compute`\n" in out, out


def test_format_references_groups_by_from_package_and_shows_count():
    """Rows are grouped under H2 per ``from_package`` (first-seen order)
    with a parenthesized count + singular/plural noun per spec §A.1."""
    rows = (
        _ref(
            from_package="pkg",
            from_node_id="pkg.utils.runner.run_pipeline",
            to_name="pkg.helpers.compute",
            to_node_id="pkg.helpers.compute",
        ),
        _ref(
            from_package="pkg",
            from_node_id="pkg.cli.main",
            to_name="pkg.helpers.compute",
            to_node_id="pkg.helpers.compute",
        ),
        _ref(
            from_package="acme-tools",
            from_node_id="acme_tools.analytics.aggregate.summarize",
            to_name="pkg.helpers.compute",
            to_node_id="pkg.helpers.compute",
        ),
    )
    out = format_references(rows, target="pkg.helpers.compute", show="callers", limit=50)
    # Both group H2s with plural/singular noun + count
    assert "## from `pkg` (2 callers)" in out, out
    assert "## from `acme-tools` (1 caller)" in out, out
    # Insertion order preserved: ``pkg`` block comes before ``acme-tools``
    assert out.index("## from `pkg`") < out.index("## from `acme-tools`"), out


def test_format_references_resolved_first_within_group_with_warning_prefix():
    """Within a group: resolved rows render first; unresolved get the ⚠
    prefix and the standard reason suffix (§A.1)."""
    rows = (
        # Mixed-order input: unresolved first, then resolved — output must
        # flip them so the resolved row appears first.
        _ref(
            from_package="acme-tools",
            from_node_id="acme_tools.legacy._old_runner",
            to_name="compute",
            to_node_id=None,
        ),
        _ref(
            from_package="acme-tools",
            from_node_id="acme_tools.analytics.aggregate.summarize",
            to_name="pkg.helpers.compute",
            to_node_id="pkg.helpers.compute",
        ),
    )
    out = format_references(rows, target="pkg.helpers.compute", show="callers", limit=50)
    resolved_idx = out.index("- `acme_tools.analytics.aggregate.summarize` → `pkg.helpers.compute`")
    unresolved_idx = out.index("- ⚠ `acme_tools.legacy._old_runner`")
    assert resolved_idx < unresolved_idx, f"resolved-first sort broke: {out!r}"
    assert "*(unresolved — to_name didn't match any indexed qname)*" in out, out


def test_format_references_counts_resolved_vs_unresolved():
    """Lead sentence reports total + resolved/unresolved split (§A.1)."""
    rows = (
        _ref(from_package="pkg", from_node_id="a", to_name="t", to_node_id="t"),
        _ref(from_package="pkg", from_node_id="b", to_name="t", to_node_id="t"),
        _ref(from_package="pkg", from_node_id="c", to_name="t", to_node_id="t"),
        _ref(from_package="pkg", from_node_id="d", to_name="t", to_node_id=None),
        _ref(from_package="pkg", from_node_id="e", to_name="t", to_node_id=None),
    )
    out = format_references(rows, target="t", show="callers", limit=50)
    assert "5 references found (3 resolved, 2 unresolved)." in out, out


def test_format_references_show_callees_header():
    """``show="callees"`` switches H1 to ``Callees of`` and the noun to
    ``callee``/``callees`` (§A.1 vocabulary table)."""
    rows = (
        _ref(
            from_package="pkg",
            from_node_id="pkg.helpers.compute",
            to_name="pkg.utils.add",
            to_node_id="pkg.utils.add",
        ),
    )
    out = format_references(rows, target="pkg.helpers.compute", show="callees", limit=50)
    assert out.startswith("# Callees of `pkg.helpers.compute`\n"), out
    assert "## from `pkg` (1 callee)" in out, out


def test_format_references_show_inherits_subclasses_section():
    """``show="inherits"`` rows whose from-side is NOT the target are the
    target's SUBCLASSES — rendered under "Subclasses of", never mislabeled
    "Bases of" (the pre-fix defect)."""
    rows = (
        _ref(
            from_package="pkg",
            from_node_id="pkg.api.Sub",
            to_name="pkg.api.Base",
            to_node_id="pkg.api.Base",
            kind=ReferenceKind.INHERITS,
        ),
        _ref(
            from_package="pkg",
            from_node_id="pkg.api.OtherSub",
            to_name="pkg.api.Base",
            to_node_id="pkg.api.Base",
            kind=ReferenceKind.INHERITS,
        ),
    )
    out = format_references(rows, target="pkg.api.Base", show="inherits", limit=50)
    assert out.startswith("# Inheritance of `pkg.api.Base`\n"), out
    assert "## Subclasses of `pkg.api.Base` (2 subclasses)" in out, out
    # Empty sense → its section is omitted entirely.
    assert "## Bases of" not in out, out


def test_format_references_show_inherits_bases_section():
    """Rows whose from-side IS the target are its BASES (from-side INHERITS
    edges) — one "Bases of" section, unresolved bases keep the ⚠ marker."""
    rows = (
        _ref(
            from_package="pkg",
            from_node_id="pkg.api.Child",
            to_name="pkg.core.Base",
            to_node_id="pkg.core.Base",
            kind=ReferenceKind.INHERITS,
        ),
        _ref(
            from_package="pkg",
            from_node_id="pkg.api.Child",
            to_name="ExternalMixin",
            to_node_id=None,
            kind=ReferenceKind.INHERITS,
        ),
    )
    out = format_references(rows, target="pkg.api.Child", show="inherits", limit=50)
    assert out.startswith("# Inheritance of `pkg.api.Child`\n"), out
    assert "## Bases of `pkg.api.Child` (2 bases)" in out, out
    assert "## Subclasses of" not in out, out
    assert "- `pkg.api.Child` → `pkg.core.Base`" in out, out
    assert "⚠ `pkg.api.Child` → `ExternalMixin`" in out, out


def test_format_references_show_inherits_both_sections_bases_first():
    """Both senses present → "Bases of" renders before "Subclasses of"."""
    rows = (
        _ref(
            from_package="pkg",
            from_node_id="pkg.api.Mid",
            to_name="pkg.core.Root",
            to_node_id="pkg.core.Root",
            kind=ReferenceKind.INHERITS,
        ),
        _ref(
            from_package="pkg",
            from_node_id="pkg.api.Leaf",
            to_name="pkg.api.Mid",
            to_node_id="pkg.api.Mid",
            kind=ReferenceKind.INHERITS,
        ),
    )
    out = format_references(rows, target="pkg.api.Mid", show="inherits", limit=50)
    bases_at = out.index("## Bases of `pkg.api.Mid` (1 base)")
    subs_at = out.index("## Subclasses of `pkg.api.Mid` (1 subclass)")
    assert bases_at < subs_at, out
    assert "2 references found (2 resolved, 0 unresolved)." in out, out


def test_format_references_show_inherits_empty_message():
    """Both senses empty → single "No inheritance edges found" line (the
    pre-fix ``No bases found.`` message implied only one sense existed)."""
    out = format_references((), target="pkg.api.Loner", show="inherits", limit=50)
    assert out.startswith("# Inheritance of `pkg.api.Loner`\n"), out
    assert "No inheritance edges found for `pkg.api.Loner`." in out, out
    assert out.endswith("\n"), out
