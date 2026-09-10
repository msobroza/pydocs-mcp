"""ToolRouter ``meta.resolution`` — the honest per-extension reference-graph
state on ``get_references`` (ADR 0021 Decision 6, multilang-analyzers §7).

Split out of test_tool_router.py to keep both files under the 500-line cap."""

import asyncio
import dataclasses
from collections.abc import Iterator

import pytest

from pydocs_mcp.application.mcp_inputs import ReferencesInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
)
from pydocs_mcp.application.tool_router import ToolRouter

from ._router_fakes import make_envelope, make_service


class _FakeLookupWithExt:
    """A lookup body that echoes a chosen target file extension in the
    reference-branch extras (``TARGET_EXTENSION_EXTRA``) — enough to drive
    ToolRouter's honest ``meta.resolution`` mapping (ADR 0021 Decision 6)
    without the real reference graph or a persisted tree."""

    context_token_budget = 2048

    def __init__(self, ext: str | None) -> None:
        self._ext = ext

    async def lookup_with_items(self, payload):
        from pydocs_mcp.application.lookup_service import TARGET_EXTENSION_EXTRA

        return f"refs for {payload.target}", (), {TARGET_EXTENSION_EXTRA: self._ext}


def _router_with_lookup(lookup: object) -> ToolRouter:
    # The default fake project with ONLY the lookup swapped. ProjectServices is
    # a frozen dataclass, so replace() builds a new instance rather than
    # mutating the shared one; it does NOT re-validate (no __post_init__), so
    # the fake lookup is taken as-is.
    services = (dataclasses.replace(make_service(), lookup=lookup),)
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def _resolution_for(ext: str | None) -> str:
    router = _router_with_lookup(_FakeLookupWithExt(ext))
    resp = asyncio.run(router.get_references(ReferencesInput(target="pkg.mod.f")))
    return resp.meta["resolution"]


@pytest.mark.parametrize("ext", [".py", ".md"])
def test_references_resolution_analyzed_target_is_syntactic(ext: str) -> None:
    # .py / .md carry a registered analyzer → its declared references flag.
    assert _resolution_for(ext) == "syntactic"


@pytest.mark.parametrize("ext", [".toml", ".yaml", ".yml", ".cfg", ".ini", ".rst", ".txt", ".json"])
def test_references_resolution_non_python_target_is_unavailable(ext: str) -> None:
    # The T2 text/config extensions carry no analyzer → the honest value never
    # overstates Python's graph (ADR 0021 Decision 6). Every T3 code extension
    # has left this list: .ts/.tsx were the last, and now report the analyzer's
    # declared state — "syntactic" where the grammar loads, "unavailable" in a
    # degraded deployment (the two-state pins below).
    assert _resolution_for(ext) == "unavailable"


def test_references_resolution_unresolvable_extension_is_unavailable() -> None:
    # A node with no resolvable source extension degrades honestly, not to
    # a Python default.
    assert _resolution_for(None) == "unavailable"


def test_references_resolution_channel_key_stripped_from_meta() -> None:
    # The extension channel is internal: only the declared `resolution` field
    # may reach the wire meta (§2.2), never the TARGET_EXTENSION_EXTRA key.
    from pydocs_mcp.application.lookup_service import TARGET_EXTENSION_EXTRA

    router = _router_with_lookup(_FakeLookupWithExt(".toml"))
    meta = asyncio.run(router.get_references(ReferencesInput(target="pkg.mod.f"))).meta
    assert TARGET_EXTENSION_EXTRA not in meta
    assert meta["resolution"] == "unavailable"


class _TwoStateAnalyzer:
    """Property-shaped fake proving the router is data-driven (spec §7.3):
    `_resolution_for_ext` re-reads `capabilities` per call, so a
    deployment-state flip changes `meta.resolution` with ZERO router code."""

    def __init__(self) -> None:
        self.active = True

    @property
    def capabilities(self) -> dict[str, str]:
        if self.active:
            return {"outline": "available", "definitions": "available", "references": "syntactic"}
        return {"outline": "available", "definitions": "unavailable", "references": "unavailable"}

    def capture(self, source, *, path, root, from_package, allowed, collector) -> None:
        return None


def test_references_resolution_follows_property_backed_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydocs_mcp.extraction.strategies.analyzers import (
        LanguageAnalyzer,
        analyzer_registry,
    )

    fake = _TwoStateAnalyzer()
    assert isinstance(fake, LanguageAnalyzer)  # property-shaped Protocol (D7)
    monkeypatch.setitem(analyzer_registry, ".zz", fake)
    assert _resolution_for(".zz") == "syntactic"
    fake.active = False
    assert _resolution_for(".zz") == "unavailable"


_GRAMMAR_MODULES = {
    ".rs": "tree_sitter_rust",
    ".c": "tree_sitter_c",
    ".h": "tree_sitter_c",
    ".js": "tree_sitter_javascript",
    ".ts": "tree_sitter_typescript",
    ".tsx": "tree_sitter_typescript",
    ".java": "tree_sitter_java",
}


@pytest.fixture
def _fresh_grammar_caches() -> Iterator[None]:
    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        _reset_multilang_caches,
    )

    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


@pytest.mark.parametrize("ext", sorted(_GRAMMAR_MODULES))
def test_ac10_references_resolution_treesitter_target_is_syntactic(
    ext: str, _fresh_grammar_caches: None
) -> None:
    pytest.importorskip("tree_sitter")
    pytest.importorskip(_GRAMMAR_MODULES[ext])
    assert _resolution_for(ext) == "syntactic"


def test_ac11_references_resolution_degrades_to_unavailable(
    monkeypatch: pytest.MonkeyPatch, _fresh_grammar_caches: None
) -> None:
    import sys

    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        _reset_multilang_caches,
    )

    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    # §7.2 invariant end-to-end: a structurally empty graph never claims
    # "syntactic".
    assert _resolution_for(".rs") == "unavailable"
