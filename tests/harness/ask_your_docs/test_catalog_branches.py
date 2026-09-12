"""WorkspaceBranchListing + render_catalog(branches=) — AC-13, AC-14b."""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.bundle import IndexedBranch
from pydocs_mcp.harness.ask_your_docs.catalog import (
    EMPTY_BRANCH_LISTING,
    CatalogService,
    WorkspaceBranchListing,
    render_catalog,
)
from pydocs_mcp.models import BranchStatus

_SHA = "3e1a9c2" + "0" * 33


def _row(name, *, default=False, base=None, status=BranchStatus.ACTIVE, merged_into=None):
    return IndexedBranch(
        name=name,
        head_sha="a" * 40,
        base_name=base,
        is_default=default,
        status=status,
        merged_into=merged_into,
        landing_kind=None,
        indexed_at=1.0,
    )


_LISTING = WorkspaceBranchListing(
    projects={
        "backend": (
            _row("feature/retry", default=True, base="main"),
            _row("main"),
            _row("feature/old", base="main", status=BranchStatus.MERGED, merged_into=_SHA),
            _row(_SHA, base="main"),
        ),
        "tooling": (_row("main", default=True),),
    },
    bundle_stems=frozenset({"backend_0123456789", "tooling_abcdefabcd"}),
)
_CATALOG = {"backend": ["fastapi", "pydantic"], "tooling": []}


def test_pickable_excludes_landing_units_and_tombstones():
    assert [r.name for r in _LISTING.pickable("backend")] == ["feature/retry", "main"]
    assert [r.name for r in _LISTING.merged("backend")] == ["feature/old"]


def test_knows_project_accepts_names_and_bundle_stems():
    assert _LISTING.knows_project("backend")
    assert _LISTING.knows_project("backend_0123456789")
    assert not _LISTING.knows_project("frontend")


def test_default_row_and_lookups():
    assert _LISTING.default_row("backend").name == "feature/retry"
    assert _LISTING.has_branch("backend", "main")
    assert not _LISTING.has_branch("tooling", "feature/retry")
    assert _LISTING.has_branch("", "feature/retry")  # union: any project
    assert _LISTING.head_sha("backend", "main") == "a" * 40
    assert _LISTING.head_sha("backend", "nope") == ""


def test_render_catalog_without_branches_is_byte_identical():
    """AC-13."""
    assert render_catalog(_CATALOG) == render_catalog(_CATALOG, branches=None)
    assert render_catalog(_CATALOG) == (
        "- backend — dependency packages: fastapi, pydantic\n"
        "- tooling — own code only (no dependency packages indexed)"
    )


def test_render_catalog_with_branches_lists_pickable_rows_only():
    assert render_catalog(_CATALOG, branches=_LISTING) == (
        "- backend — branches: feature/retry (default), main — dependency packages: fastapi, pydantic\n"
        "- tooling — branches: main (default) — own code only (no dependency packages indexed)"
    )


def test_render_catalog_show_merged_appends_the_tombstone_marker():
    rendered = render_catalog(_CATALOG, branches=_LISTING, show_merged=True)
    assert (
        "branches: feature/retry (default), main, feature/old (merged into main @3e1a9c2) — "
        "dependency packages" in rendered
    )
    assert _SHA not in rendered


def test_empty_listing_renders_no_branch_segment():
    assert render_catalog(_CATALOG, branches=EMPTY_BRANCH_LISTING) == render_catalog(_CATALOG)


class _FakeReader:
    def __init__(self, db: Path) -> None:
        self._stem = db.stem

    def project_name(self) -> str:
        return self._stem.rsplit("_", 1)[0]

    def indexed_at(self) -> float:
        return 2.0 if self._stem.endswith("new") else 1.0

    def packages(self) -> list[str]:
        return []

    def branches(self) -> tuple[IndexedBranch, ...]:
        return (_row("main", default=True),) if self._stem.endswith("new") else (_row("old"),)


def test_branch_listing_newest_bundle_wins_and_collects_stems(tmp_path):
    # WHY these stems: `_bundles()` scans sorted by name, so the STALE bundle
    # must sort first. With the newest also scanned first, "newest wins" and
    # "first wins" agree and the rule this test is named for goes unpinned.
    (tmp_path / "backend_astale").with_suffix(".db").write_bytes(b"")
    (tmp_path / "backend_bnew").with_suffix(".db").write_bytes(b"")
    listing = CatalogService(str(tmp_path), reader_factory=_FakeReader).branch_listing()
    assert [r.name for r in listing.rows("backend")] == ["main"]
    assert listing.bundle_stems == frozenset({"backend_astale", "backend_bnew"})
