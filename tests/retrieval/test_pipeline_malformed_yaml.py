"""Regression tests: ``CodeRetrieverPipeline.from_dict`` on malformed YAML shapes.

``PipelineLoadError``'s own docstring promises "clear migration diagnostics
rather than a confusing low-level KeyError" for schema violations. Four
shapes a hand-edited overlay easily produces were leaking raw
``TypeError`` / ``AttributeError`` / ``KeyError`` instead of
``PipelineLoadError``:

- ``steps:`` with an empty YAML block (parses to ``None``)
- ``steps:`` as a mapping, or containing a bare-string entry
- a step entry missing ``type:``
- the top-level pipeline missing ``name:``

See ``python/pydocs_mcp/retrieval/pipeline/code_pipeline.py``.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.retrieval.pipeline import (
    CodeRetrieverPipeline,
    PerCallConnectionProvider,
    PipelineLoadError,
)
from pydocs_mcp.retrieval.serialization import BuildContext


def _context(tmp_path) -> BuildContext:
    return BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"name": "p", "steps": None}, id="steps-none"),
        pytest.param({"name": "p", "steps": {"a": 1}}, id="steps-mapping"),
    ],
)
def test_from_dict_rejects_non_list_steps(tmp_path, data) -> None:
    """``steps:`` must be a list, not a YAML-empty-block None or a mapping."""
    with pytest.raises(PipelineLoadError, match="pipeline 'p'.*'steps:' must be a list"):
        CodeRetrieverPipeline.from_dict(data, _context(tmp_path))


def test_from_dict_rejects_bare_string_step_entry(tmp_path) -> None:
    """A ``steps:`` list entry that is a bare string (e.g. ``- limit``), not a mapping."""
    data = {"name": "p", "steps": ["limit"]}
    with pytest.raises(PipelineLoadError, match="step #0.*must be a mapping"):
        CodeRetrieverPipeline.from_dict(data, _context(tmp_path))


def test_from_dict_rejects_step_entry_missing_type(tmp_path) -> None:
    """A ``steps:`` entry missing ``type:`` must raise PipelineLoadError, not KeyError."""
    data = {"name": "p", "steps": [{"name": "trim"}]}
    with pytest.raises(PipelineLoadError, match="step #0.*trim.*missing required 'type:'"):
        CodeRetrieverPipeline.from_dict(data, _context(tmp_path))


def test_from_dict_rejects_missing_top_level_name(tmp_path) -> None:
    """A pipeline dict missing the top-level ``name:`` must raise PipelineLoadError."""
    data = {"steps": []}
    with pytest.raises(PipelineLoadError, match="missing required 'name:'"):
        CodeRetrieverPipeline.from_dict(data, _context(tmp_path))
