"""LanguageAnalyzer seam — extension-keyed analyzer registry + capability flags.

Contract: docs/tool-contracts.md §5.1 + ADR 0004. The capability-flag
vocabulary is frozen as ``{outline, definitions, references} ×
{semantic | syntactic | unavailable}`` and Python declares
``references: syntactic``. The golden test pins the exact edge set the
pre-refactor ``ReferenceCaptureStage`` emitted on a mixed fixture so the
registry refactor is provably behavior-preserving.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import (
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages import ReferenceCaptureStage
from pydocs_mcp.extraction.pipeline.stages import reference_capture as stages_mod
from pydocs_mcp.extraction.strategies.analyzers import (
    PYTHON_CAPABILITIES,
    LanguageAnalyzer,
    analyzer_registry,
    language_capabilities,
    register_analyzer,
)
from pydocs_mcp.extraction.strategies.references import ReferenceCollector
from pydocs_mcp.retrieval.config import ReferenceCaptureConfig

_PY_SOURCE = (
    "from helpers import compute as do_it\n"
    "import os\n"
    "class Base:\n"
    "    pass\n"
    "class Child(Base):\n"
    "    def __init__(self):\n"
    "        self.helper = Helper()\n"
    "    def fn(self):\n"
    "        return self.helper.run() + do_it(1)\n"
    "async def runner():\n"
    "    return do_it(42)\n"
)
_MD_SOURCE = "# Docs\nSee `pkg.helpers.compute` and `pkg.utils.runner`.\n"


def _state(file_contents: tuple[tuple[str, str], ...]) -> IngestionState:
    return IngestionState(
        files=FileBundle(
            target=Path(),
            target_kind=TargetKind.PROJECT,
            package_name="pkg",
            root=Path(),
            file_contents=file_contents,
        ),
    )


# ---------------------------------------------------------------------------
# Golden: registry refactor must emit the identical edge set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_golden_edge_set_identical_pre_and_post_registry_refactor(monkeypatch):
    """Exact edge/alias/attr-type snapshot captured from the pre-refactor
    ``_capture_all`` (hardcoded ``.py``/``.md`` branches). The registry
    dispatch must reproduce it byte-for-byte — imports, calls (incl.
    ``self.X.Y``), inherits, mentions; broken ``.py`` contained per-file;
    unknown extensions skipped."""
    monkeypatch.setattr(
        stages_mod,
        "_CAPTURE_CONFIG",
        ReferenceCaptureConfig(
            enabled=True,
            kinds=["calls", "imports", "inherits", "mentions"],
        ),
    )
    state = _state(
        (
            ("pkg/mod.py", _PY_SOURCE),
            ("pkg/README.md", _MD_SOURCE),
            ("pkg/notes.txt", "not code `pkg.helpers.compute`\n"),
            ("pkg/broken.py", "def broken( syntax error\n"),
        )
    )
    new_state = await ReferenceCaptureStage().run(state)
    edges = sorted(
        (r.from_package, r.from_node_id, r.to_name, r.kind.value) for r in new_state.refs.references
    )
    assert edges == [
        ("pkg", "pkg.README.md", "pkg.helpers.compute", "mentions"),
        ("pkg", "pkg.README.md", "pkg.utils.runner", "mentions"),
        ("pkg", "pkg.mod", "helpers.compute", "imports"),
        ("pkg", "pkg.mod", "os", "imports"),
        ("pkg", "pkg.mod.Child", "Base", "inherits"),
        ("pkg", "pkg.mod.Child.__init__", "Helper", "calls"),
        ("pkg", "pkg.mod.Child.fn", "do_it", "calls"),
        ("pkg", "pkg.mod.Child.fn", "self.helper.run", "calls"),
        ("pkg", "pkg.mod.runner", "do_it", "calls"),
    ]
    assert new_state.refs.reference_aliases == {"pkg.mod": {"do_it": "helpers.compute"}}
    assert new_state.refs.class_attribute_types == {"pkg.mod.Child": {"helper": "Helper"}}


# ---------------------------------------------------------------------------
# Registry surface
# ---------------------------------------------------------------------------


def test_registry_has_python_and_markdown_analyzers():
    assert set(analyzer_registry) >= {".py", ".md"}


def test_registered_analyzers_satisfy_protocol():
    for ext, analyzer in analyzer_registry.items():
        assert isinstance(analyzer, LanguageAnalyzer), ext


def test_python_capabilities_declaration_is_the_frozen_contract_value():
    """docs/tool-contracts.md §5.1 — Python declares outline + definitions
    available, references syntactic. Byte-for-byte frozen vocabulary."""
    assert PYTHON_CAPABILITIES == {
        "outline": "available",
        "definitions": "available",
        "references": "syntactic",
    }


def test_language_capabilities_lookup():
    assert language_capabilities(".py") == PYTHON_CAPABILITIES
    assert language_capabilities(".py") is analyzer_registry[".py"].capabilities
    assert language_capabilities(".rs") is None


def test_duplicate_registration_raises_at_import_time():
    with pytest.raises(ValueError, match=r"\.py"):

        @register_analyzer(".py")
        class ShadowAnalyzer:
            capabilities = PYTHON_CAPABILITIES

            def capture(self, source, *, path, root, from_package, allowed, collector):
                raise NotImplementedError

    # The original analyzer survives the failed duplicate registration.
    assert language_capabilities(".py") == PYTHON_CAPABILITIES


# ---------------------------------------------------------------------------
# Analyzer behavior in isolation (no stage)
# ---------------------------------------------------------------------------


def test_python_analyzer_captures_directly_into_collector():
    collector = ReferenceCollector()
    analyzer_registry[".py"].capture(
        _PY_SOURCE,
        path="pkg/mod.py",
        root=Path(),
        from_package="pkg",
        allowed=frozenset({"calls", "imports", "inherits"}),
        collector=collector,
    )
    names = {r.to_name for r in collector.refs}
    assert {"helpers.compute", "do_it", "Base", "self.helper.run"} <= names
    assert collector.aliases == {"pkg.mod": {"do_it": "helpers.compute"}}


def test_markdown_analyzer_is_gated_on_mentions_kind():
    collector = ReferenceCollector()
    analyzer_registry[".md"].capture(
        _MD_SOURCE,
        path="pkg/README.md",
        root=Path(),
        from_package="pkg",
        allowed=frozenset({"calls", "imports", "inherits"}),
        collector=collector,
    )
    assert collector.refs == []


@pytest.mark.asyncio
async def test_stage_skips_extensions_without_a_registered_analyzer():
    """Unknown extension ⇒ skip (mirrors ChunkingStage's registry policy)."""
    state = _state((("pkg/data.toml", "[tool]\nname = 'x'\n"),))
    new_state = await ReferenceCaptureStage().run(state)
    assert new_state.refs.references == ()
