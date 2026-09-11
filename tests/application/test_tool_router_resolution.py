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


def _router_with_lookup(lookup: object, *, loadable_grammars: str = "") -> ToolRouter:
    # The default fake project with ONLY the lookup swapped. ProjectServices is
    # a frozen dataclass, so replace() builds a new instance rather than
    # mutating the shared one; it does NOT re-validate (no __post_init__), so
    # the fake lookup is taken as-is. ``loadable_grammars`` is the bundle's
    # index-time grammar stamp; "" is the unstamped (pre-stamp bundle) shape.
    services = (
        dataclasses.replace(make_service(loadable_grammars=loadable_grammars), lookup=lookup),
    )
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def _resolution_for(ext: str | None, *, loadable_grammars: str = "") -> str:
    router = _router_with_lookup(_FakeLookupWithExt(ext), loadable_grammars=loadable_grammars)
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


_TREESITTER_EXTENSIONS = (".c", ".h", ".java", ".js", ".rs", ".ts", ".tsx")
_EVERY_GRAMMAR = ",".join(_TREESITTER_EXTENSIONS)


@pytest.fixture
def _fresh_grammar_caches() -> Iterator[None]:
    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        _reset_multilang_caches,
    )

    _reset_multilang_caches()
    yield
    _reset_multilang_caches()


# For the tree-sitter languages `meta.resolution` describes the INDEX: the
# bundle's `loadable_grammars` stamp says which grammars loaded when it was
# built, i.e. which languages' reference graph it can actually contain. The
# serving process's own grammar state is irrelevant to a READ — rows were
# written at index time or never (ADR 0022 follow-up, issue #246 item 3).


@pytest.mark.parametrize("ext", _TREESITTER_EXTENSIONS)
def test_ac10_treesitter_target_is_syntactic_when_the_bundle_stamps_its_grammar(
    ext: str,
) -> None:
    assert _resolution_for(ext, loadable_grammars=_EVERY_GRAMMAR) == "syntactic"


@pytest.mark.parametrize("ext", _TREESITTER_EXTENSIONS)
def test_ac11_treesitter_target_is_unavailable_when_the_bundle_does_not_stamp_it(
    ext: str,
) -> None:
    # Stamped, but not for this language: the graph never captured it.
    others = ",".join(e for e in _TREESITTER_EXTENSIONS if e != ext)
    assert _resolution_for(ext, loadable_grammars=others) == "unavailable"


@pytest.mark.parametrize("ext", _TREESITTER_EXTENSIONS)
def test_an_unstamped_bundle_reports_unavailable_for_code_targets(ext: str) -> None:
    """Owner ruling (2026-09-11): a bundle with no stamp cannot vouch for a
    code-language graph, so it declines rather than borrowing the serving
    process's verdict. This is also accurate for the population it hits — an
    unstamped read-only bundle predates the analyzers and holds no such graph."""
    assert _resolution_for(ext, loadable_grammars="") == "unavailable"


def test_resolution_describes_the_index_not_the_serving_process(
    monkeypatch: pytest.MonkeyPatch, _fresh_grammar_caches: None
) -> None:
    """The item-3 defect, both directions. A process that CAN load grammars
    serving a bundle built while they could not must not claim `syntactic`
    over an empty graph; and a process that CANNOT load them serving a bundle
    that was built with them still serves the rows that exist."""
    import sys

    from pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter import (
        _reset_multilang_caches,
    )

    # Live grammars available (the default test environment), stamp absent.
    assert _resolution_for(".rs", loadable_grammars="") == "unavailable"
    # Live grammars unavailable, stamp present: the graph is in the bundle.
    monkeypatch.setitem(sys.modules, "tree_sitter", None)
    _reset_multilang_caches()
    assert _resolution_for(".rs", loadable_grammars=".rs") == "syntactic"


@pytest.mark.parametrize("ext", [".py", ".md"])
def test_python_and_markdown_ignore_the_stamp(ext: str) -> None:
    # Their analyzers were never grammar-gated, so the stamp — which records
    # only the tree-sitter extensions — says nothing about them.
    assert _resolution_for(ext, loadable_grammars="") == "syntactic"
    assert _resolution_for(ext, loadable_grammars=_EVERY_GRAMMAR) == "syntactic"
