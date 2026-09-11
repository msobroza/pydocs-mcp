"""ToolRouter ``meta.resolution`` — the honest per-extension reference-graph
state on ``get_references`` (ADR 0021 Decision 6, multilang-analyzers §7).

Split out of test_tool_router.py to keep both files under the 500-line cap."""

import asyncio
import dataclasses
from collections.abc import Iterator

import pytest

from pydocs_mcp.application.mcp_inputs import ReferencesInput, SymbolInput
from pydocs_mcp.application.multi_project_search import (
    MultiProjectLookup,
    MultiProjectSearch,
)
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.extraction.strategies.chunkers.multilang_queries import MULTILANG_EXTENSIONS
from pydocs_mcp.storage.index_metadata import format_grammar_stamp

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


def _router_over(services) -> ToolRouter:
    """A router serving exactly these fake projects, in this order."""
    return ToolRouter(
        services=services,
        envelope=make_envelope(),
        search_router=MultiProjectSearch(services=services),
        lookup_router=MultiProjectLookup(services=services),
    )


def _router_with_lookup(lookup: object, *, loadable_grammars: str = "") -> ToolRouter:
    # The default fake project with ONLY the lookup swapped. ProjectServices is
    # a frozen dataclass, so replace() builds a new instance rather than
    # mutating the shared one; it does NOT re-validate (no __post_init__), so
    # the fake lookup is taken as-is. ``loadable_grammars`` is the bundle's
    # index-time grammar stamp; "" is the unstamped (pre-stamp bundle) shape.
    service = make_service(loadable_grammars=loadable_grammars)
    return _router_over((dataclasses.replace(service, lookup=lookup),))


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
    # has left this list: .ts/.tsx were the last, and now answer from the
    # bundle's grammar stamp — "syntactic" when the bundle was indexed with the
    # grammar loaded, "unavailable" otherwise (the stamp pins below).
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
    `declared_reference_resolution` re-reads `capabilities` per call, so a
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


# Every pin below parametrizes over the chunker's own extension set — the
# stamp's vocabulary — so an eighth language cannot escape them.
_EVERY_GRAMMAR = format_grammar_stamp(MULTILANG_EXTENSIONS)


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


@pytest.mark.parametrize("ext", MULTILANG_EXTENSIONS)
def test_ac10_treesitter_target_is_syntactic_when_the_bundle_stamps_its_grammar(
    ext: str,
) -> None:
    assert _resolution_for(ext, loadable_grammars=_EVERY_GRAMMAR) == "syntactic"


@pytest.mark.parametrize("ext", MULTILANG_EXTENSIONS)
def test_ac11_treesitter_target_is_unavailable_when_the_bundle_does_not_stamp_it(
    ext: str,
) -> None:
    # Stamped, but not for this language: the graph never captured it.
    others = ",".join(e for e in MULTILANG_EXTENSIONS if e != ext)
    assert _resolution_for(ext, loadable_grammars=others) == "unavailable"


@pytest.mark.parametrize("ext", MULTILANG_EXTENSIONS)
def test_an_unstamped_bundle_reports_unavailable_for_code_targets(ext: str) -> None:
    """Owner ruling (2026-09-11): a bundle with no stamp cannot vouch for a
    code-language graph, so it declines rather than borrowing the serving
    process's verdict. Accurate for every RELEASED bundle it hits — they
    predate the analyzers and hold no such graph; a bundle built from `main`
    between the analyzers and the stamp holds one it cannot vouch for until
    it is re-indexed."""
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


# --- the stamp is the ANSWERING bundle's, read as it is on disk now ---------


def _with_lookup(service, ext: str):
    return dataclasses.replace(service, lookup=_FakeLookupWithExt(ext))


def test_multi_repo_resolution_comes_from_the_bundle_that_answered() -> None:
    """With no `project=` selector a lookup resolves by recency, and the
    newest bundle may not be the first-loaded one. Reading the first-loaded
    bundle's stamp then vouched for a graph a DIFFERENT bundle served —
    `syntactic` over beta's empty graph because alpha happened to be stamped."""
    alpha = _with_lookup(
        make_service("alpha", indexed_at=1.0, loadable_grammars=_EVERY_GRAMMAR), ".rs"
    )
    beta = _with_lookup(make_service("beta", indexed_at=2.0, loadable_grammars=""), ".rs")
    router = _router_over((alpha, beta))

    unselected = asyncio.run(router.get_references(ReferencesInput(target="pkg.mod.f")))
    assert unselected.meta["resolution"] == "unavailable"  # beta answered
    # An explicit selector names the answering bundle outright — both ways,
    # so a router that always read `services[0]` cannot pass.
    explicit = asyncio.run(
        router.get_references(ReferencesInput(target="pkg.mod.f", project="alpha"))
    )
    assert explicit.meta["resolution"] == "syntactic"
    explicit = asyncio.run(
        router.get_references(ReferencesInput(target="pkg.mod.f", project="beta"))
    )
    assert explicit.meta["resolution"] == "unavailable"


def test_the_answering_project_channel_never_reaches_the_wire() -> None:
    from pydocs_mcp.application.multi_project_search import ANSWERING_PROJECT_EXTRA

    alpha = _with_lookup(make_service("alpha", loadable_grammars=_EVERY_GRAMMAR), ".rs")
    router = _router_over((alpha,))
    refs = asyncio.run(router.get_references(ReferencesInput(target="pkg.mod.f"))).meta
    assert ANSWERING_PROJECT_EXTRA not in refs
    symbol = asyncio.run(router.get_symbol(SymbolInput(target="pkg.mod.f"))).meta
    assert ANSWERING_PROJECT_EXTRA not in symbol


def test_resolution_follows_a_stamp_rewritten_on_disk_after_load(tmp_path) -> None:
    """A separate `pydocs-mcp index` / `watch` process can re-stamp the bundle
    a running server loaded. The freshness header already re-reads the row per
    response; the stamp must too, or the envelope reports the new pass's
    commit beside the old pass's resolution."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.multirepo import LoadedProject
    from pydocs_mcp.storage.index_metadata import IndexMetadata, write_index_metadata

    db = tmp_path / "solo.db"
    at_load = IndexMetadata(
        project_name="solo",
        project_root="",
        embedding_provider="fastembed",
        embedding_model="bge",
        embedding_dim=384,
        pipeline_hash="h",
        indexed_at=1.0,
        loadable_grammars=_EVERY_GRAMMAR,
    )
    conn = open_index_database(db)
    write_index_metadata(conn, at_load)
    conn.close()
    service = dataclasses.replace(
        _with_lookup(make_service("solo"), ".rs"),
        project=LoadedProject(name="solo", db_path=db, metadata=at_load),
    )
    router = _router_over((service,))
    assert (
        asyncio.run(router.get_references(ReferencesInput(target="pkg.mod.f"))).meta["resolution"]
        == "syntactic"
    )

    # Another process re-indexes with no grammar loadable: the graph is gone.
    conn = open_index_database(db)
    write_index_metadata(conn, dataclasses.replace(at_load, loadable_grammars="", indexed_at=2.0))
    conn.close()
    assert (
        asyncio.run(router.get_references(ReferencesInput(target="pkg.mod.f"))).meta["resolution"]
        == "unavailable"
    )
