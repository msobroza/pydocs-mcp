"""The grammar loadability probe covers the analyzers' reference queries.

``_import_language`` compiles the chunker's top-level query AND every
reference query the language's analyzer registered, so a grammar that rejects
any of them counts as unloadable everywhere the one memoized verdict is read:
the chunker degrades to text windows, capabilities report "unavailable", and
``loadable_grammar_fingerprint`` drops the extension — the salt flips once the
grammar is fixed, instead of stranding a partial graph (ADR 0022).
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterator
from types import ModuleType

import pytest

pytest.importorskip("tree_sitter")

import tree_sitter

from pydocs_mcp.extraction.strategies.analyzers import (
    analyzer_registry,
    c_lang,
    java,
    javascript,
    rust,
    typescript,
)
from pydocs_mcp.extraction.strategies.analyzers._treesitter import (
    TREESITTER_DEGRADED_CAPABILITIES,
    capabilities_for,
)
from pydocs_mcp.extraction.strategies.chunkers import multilang_treesitter as mlt
from pydocs_mcp.extraction.strategies.references import ReferenceCollector
from tests.extraction._analyzer_fixtures import capture_with_analyzer

_LANGUAGE_MODULES = (c_lang, java, javascript, rust, typescript)


@pytest.fixture(autouse=True)
def _clean_caches() -> Iterator[None]:
    mlt._reset_multilang_caches()
    yield
    mlt._reset_multilang_caches()


def _module_query_sources(module: ModuleType) -> set[str]:
    """Every module-level ``_<LANG>_<ROLE>_QUERY`` constant — the queries the
    module's analyzer runs through ``CaptureSession.matches``."""
    return {
        value
        for name, value in vars(module).items()
        if re.fullmatch(r"_[A-Z]+_[A-Z]+_QUERY", name) and value.strip()
    }


def _module_extensions(module: ModuleType) -> list[str]:
    return sorted(
        ext
        for ext, analyzer in analyzer_registry.items()
        if type(analyzer).__module__ == module.__name__
    )


@pytest.mark.parametrize("module", _LANGUAGE_MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[1])
def test_the_probe_compiles_every_reference_query_the_analyzer_runs(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Also the drift pin: a query constant added to a language module without
    registering it with the probe fails here."""
    compiled: list[str] = []
    real_query = tree_sitter.Query

    def _recording_query(language: object, source: str) -> object:
        compiled.append(source)
        return real_query(language, source)

    monkeypatch.setattr(tree_sitter, "Query", _recording_query)
    for ext in _module_extensions(module):
        mlt._reset_multilang_caches()
        compiled.clear()
        assert mlt._load_language(ext) is not None
        missing = _module_query_sources(module) - set(compiled)
        assert not missing, f"{ext}: the probe did not compile {sorted(missing)}"


def test_a_reference_query_the_grammar_rejects_degrades_the_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grammar release that renames a node type only the Rust CALLS query
    names (simulated: ``tree_sitter.Query`` rejects that one source). Before:
    capabilities stayed "syntactic", the fingerprint kept ``.rs``, and capture
    raised ``QueryError`` in the calls pass after the imports pass had emitted
    rows — a partial graph whose salt never flips."""
    pytest.importorskip("tree_sitter_rust")
    real_query = tree_sitter.Query

    def _rejecting_query(language: object, source: str) -> object:
        if source == rust._RUST_CALLS_QUERY:
            raise tree_sitter.QueryError("Invalid node type call_expression")
        return real_query(language, source)

    monkeypatch.setattr(tree_sitter, "Query", _rejecting_query)
    mlt._reset_multilang_caches()
    assert capabilities_for(".rs") is TREESITTER_DEGRADED_CAPABILITIES
    fingerprint = mlt.loadable_grammar_fingerprint().split(",")
    assert ".rs" not in fingerprint
    assert ".c" in fingerprint  # one rejected query degrades ONE extension only
    collector = ReferenceCollector()
    capture_with_analyzer("pkg/x.rs", "use a::B;\nfn f() { g(); }\n", collector)
    assert collector.refs == []  # no partial graph: the analyzer no-ops
    monkeypatch.setattr(tree_sitter, "Query", real_query)
    mlt._reset_multilang_caches()
    assert ".rs" in mlt.loadable_grammar_fingerprint().split(",")  # fixed → salt flips


def test_registering_after_the_verdict_is_memoized_fails_loudly() -> None:
    """A late registration would leave the memoized verdict blind to the new
    query — the stranded state the probe exists to prevent."""
    pytest.importorskip("tree_sitter_rust")
    assert mlt._load_language(".rs") is not None
    with pytest.raises(RuntimeError, match=r"'\.rs'"):
        mlt._register_probe_queries(".rs", ("(identifier) @x",))


def test_registering_for_an_extension_without_a_grammar_spec_is_rejected() -> None:
    with pytest.raises(ValueError, match=r"got '\.py', expected one of"):
        mlt._register_probe_queries(".py", ("(identifier) @x",))


def test_importing_the_chunker_alone_registers_every_analyzer_query() -> None:
    """Import order is structural: the chunker's parent package imports the
    analyzers, so no process can memoize a verdict before they register (the
    index path reaches the fingerprint before it ever imports an analyzer)."""
    code = (
        "import pydocs_mcp.extraction.pipeline.stages.content_hash\n"
        "import pydocs_mcp.extraction.strategies.chunkers.multilang_treesitter as m\n"
        "print(','.join(sorted(m._REFERENCE_PROBE_QUERIES)))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=120
    )
    assert result.stdout.strip() == ",".join(sorted(mlt.MULTILANG_EXTENSIONS))
