"""Session-start context pack builder (ADR 0008): marker + preamble + card + inventory.

Pins the ADR's composition order, the wire-format marker constant, the
card-before-inventory trim order under budget pressure, determinism under
fixed inputs, and the never-trimmed marker/preamble floor.
"""

from __future__ import annotations

import asyncio

from pydocs_mcp.application import session_start_context, tool_docs
from pydocs_mcp.application.overview_service import OverviewService
from pydocs_mcp.application.session_start_context import (
    CARD_TRUNCATED_NOTE,
    INJECTED_CONTEXT_MARKER,
    INVENTORY_TRUNCATED_NOTE,
    build_session_start_context,
)
from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.retrieval.llm_clients.model_budget import count_tokens
from tests._fakes import (
    InMemoryDocumentTreeStore,
    InMemoryPackageStore,
    make_fake_uow_factory,
)

_PKG = "__project__"
_INVENTORY_HEADING = "## Installed packages"


def _package(name: str, version: str, origin: PackageOrigin) -> Package:
    return Package(
        name=name,
        version=version,
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=origin,
    )


def _module_node(qname: str) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=NodeKind.MODULE,
        source_path=qname.replace(".", "/") + ".py",
        start_line=1,
        end_line=10,
        text=f"Documentation prose for the {qname} module of the demo project.",
        content_hash="h",
    )


def _build_fixture(
    *,
    dependency_versions: dict[str, str] | None = None,
    module_count: int = 3,
):
    """A shared fake uow_factory + OverviewService seeded with a small corpus."""
    deps = (
        dependency_versions
        if dependency_versions is not None
        else {
            "numpy": "1.26.4",
            "fastapi": "0.111.0",
        }
    )
    packages = InMemoryPackageStore()
    packages.items[_PKG] = _package(_PKG, "0.1.0", PackageOrigin.PROJECT)
    for name, version in deps.items():
        packages.items[name] = _package(name, version, PackageOrigin.DEPENDENCY)
    trees = InMemoryDocumentTreeStore()
    trees.by_package[_PKG] = [
        _module_node(f"demo.subsystem_{i:02d}.component_module") for i in range(module_count)
    ]
    factory = make_fake_uow_factory(packages=packages, trees=trees)
    overview = OverviewService(uow_factory=factory, scripts={})
    return factory, overview


def _build_pack(budget_tokens: int, **fixture_kwargs) -> str:
    factory, overview = _build_fixture(**fixture_kwargs)
    return asyncio.run(
        build_session_start_context(
            uow_factory=factory,
            overview=overview,
            budget_tokens=budget_tokens,
        )
    )


def _pack_tokens(pack: str) -> int:
    # Empty model name -> tiktoken's o200k_base fallback, the encoding the
    # builder's budget enforcement uses.
    return count_tokens(pack, "")


class TestMarker:
    def test_marker_constant_is_pinned_wire_format(self) -> None:
        """ADR 0008: the Phase 2 attribution matcher does an EXACT match on
        this constant — rewording it is a cross-phase breaking change."""
        assert INJECTED_CONTEXT_MARKER == (
            "[pydocs-mcp session-start-context: harness-injected at session start; "
            "not model-retrieved]"
        )

    def test_pack_first_line_is_the_marker_byte_for_byte(self) -> None:
        pack = _build_pack(budget_tokens=10_000)
        assert pack.splitlines()[0] == INJECTED_CONTEXT_MARKER

    def test_floor_pack_first_line_is_still_the_marker(self) -> None:
        pack = _build_pack(budget_tokens=1)
        assert pack.splitlines()[0] == INJECTED_CONTEXT_MARKER


class TestComposition:
    def test_sections_present_and_ordered(self) -> None:
        pack = _build_pack(budget_tokens=10_000)
        preamble_at = pack.index(tool_docs.SESSION_START_PREAMBLE)
        card_at = pack.index(f"# Overview — {_PKG}")
        inventory_at = pack.index(_INVENTORY_HEADING)
        assert pack.index(INJECTED_CONTEXT_MARKER) == 0
        assert preamble_at < card_at < inventory_at

    def test_inventory_rows_are_name_version_sorted_by_name(self) -> None:
        pack = _build_pack(budget_tokens=10_000)
        tail = pack[pack.index(_INVENTORY_HEADING) :].splitlines()
        assert tail[1:] == ["__project__ 0.1.0", "fastapi 0.111.0", "numpy 1.26.4"]

    def test_no_truncation_notes_within_budget(self) -> None:
        pack = _build_pack(budget_tokens=10_000)
        assert CARD_TRUNCATED_NOTE not in pack
        assert INVENTORY_TRUNCATED_NOTE not in pack

    def test_preamble_reads_the_live_tool_docs_attribute(self, monkeypatch) -> None:
        """An apply_source override rebinding SESSION_START_PREAMBLE (ADR 0006) must
        reach every later pack build — the builder reads the module attribute,
        never a from-import snapshot."""
        monkeypatch.setattr(tool_docs, "SESSION_START_PREAMBLE", "OVERRIDDEN PREAMBLE.")
        pack = _build_pack(budget_tokens=10_000)
        assert pack.splitlines()[1] == "OVERRIDDEN PREAMBLE."

    def test_deterministic_under_fixed_inputs(self) -> None:
        factory, overview = _build_fixture()

        async def _twice() -> tuple[str, str]:
            first = await build_session_start_context(
                uow_factory=factory, overview=overview, budget_tokens=500
            )
            second = await build_session_start_context(
                uow_factory=factory, overview=overview, budget_tokens=500
            )
            return first, second

        first, second = asyncio.run(_twice())
        assert first == second


class TestBudget:
    def test_card_is_trimmed_before_the_inventory(self) -> None:
        """ADR 0008 trim order: under mild pressure the card loses lines while
        the (distinctive, cheap) inventory stays complete."""
        fixture = {"module_count": 20}
        full = _build_pack(budget_tokens=100_000, **fixture)
        budget = _pack_tokens(full) - 20
        pack = _build_pack(budget_tokens=budget, **fixture)
        assert _pack_tokens(pack) <= budget
        assert CARD_TRUNCATED_NOTE in pack
        assert INVENTORY_TRUNCATED_NOTE not in pack
        tail = pack[pack.index(_INVENTORY_HEADING) :].splitlines()
        assert tail[1:] == ["__project__ 0.1.0", "fastapi 0.111.0", "numpy 1.26.4"]

    def test_inventory_is_trimmed_only_after_the_card_is_exhausted(self) -> None:
        deps = {f"pkg_{i:03d}": "1.0" for i in range(200)}
        pack = _build_pack(budget_tokens=400, dependency_versions=deps)
        assert _pack_tokens(pack) <= 400
        assert CARD_TRUNCATED_NOTE in pack
        assert INVENTORY_TRUNCATED_NOTE in pack
        # Deterministic prefix survives: earliest-sorted rows kept, latest cut.
        assert "pkg_000 1.0" in pack
        assert "pkg_199 1.0" not in pack

    def test_floor_never_drops_marker_preamble_or_notes(self) -> None:
        """A budget below the floor returns the floor (marker + preamble +
        both notes) rather than an unmarked fragment — the marker/preamble are
        machinery the attribution phase needs."""
        pack = _build_pack(budget_tokens=1)
        lines = pack.splitlines()
        assert lines[0] == INJECTED_CONTEXT_MARKER
        assert tool_docs.SESSION_START_PREAMBLE in pack
        assert CARD_TRUNCATED_NOTE in pack
        assert INVENTORY_TRUNCATED_NOTE in pack

    def test_trimmed_pack_respects_the_budget_exactly(self) -> None:
        full = _build_pack(budget_tokens=100_000)
        floor_tokens = _pack_tokens(_build_pack(budget_tokens=1))
        for budget in (_pack_tokens(full) - 5, floor_tokens + 50, floor_tokens + 10):
            pack = _build_pack(budget_tokens=budget)
            assert _pack_tokens(pack) <= budget, f"budget={budget} exceeded"


def test_module_reexports_public_surface() -> None:
    assert session_start_context.INJECTED_CONTEXT_MARKER is INJECTED_CONTEXT_MARKER
