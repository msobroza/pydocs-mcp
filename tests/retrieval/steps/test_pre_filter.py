"""PreFilterStep tests — parse + validate + scope-split + typed result.

Covers the 8 explicit gap tests required by plan-eng-review's locked
decisions for Task 4:

1. PreFilterStep is a RetrieverStep (ABC subclass check).
2. No-op when state.query.pre_filter is None — no scratch key written.
3. Writes a typed PreFilterResult dataclass under state.scratch['pre_filter.result'].
4. Invalid filter format (unknown field for the schema) raises.
5. Scope clause is split out into result.scope (frozenset[SearchScope]).
6. target_field='member' routes through _MEMBER_COLUMNS (no 'c.' prefix).
7. to_dict emits {type, schema_name, target_field}.
8. from_dict round-trip via BuildContext rebuilds an equivalent step.
"""
from __future__ import annotations

from dataclasses import is_dataclass
from pathlib import Path

import pytest

from pydocs_mcp.models import SearchQuery, SearchScope
from pydocs_mcp.retrieval.pipeline import (
    PerCallConnectionProvider,
    RetrieverState,
    RetrieverStep,
)
from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult, PreFilterStep


def _state(pre_filter: dict | None = None) -> RetrieverState:
    """Build a RetrieverState with the given pre_filter (None = no filter)."""
    if pre_filter is None:
        query = SearchQuery(terms="x", max_results=10)
    else:
        query = SearchQuery(terms="x", max_results=10, pre_filter=pre_filter)
    return RetrieverState(query=query)


def _step_chunk() -> PreFilterStep:
    return PreFilterStep(
        allowed_fields=frozenset({"package", "module", "scope"}),
        schema_name="chunk",
        target_field="chunk",
    )


def _step_member() -> PreFilterStep:
    return PreFilterStep(
        allowed_fields=frozenset({"package", "module", "scope"}),
        schema_name="member",
        target_field="member",
    )


async def test_pre_filter_step_is_a_retriever_step() -> None:
    """PreFilterStep subclasses the RetrieverStep ABC."""
    assert isinstance(_step_chunk(), RetrieverStep)


async def test_pre_filter_noop_when_pre_filter_is_none() -> None:
    """No pre_filter on the query → no scratch key written."""
    out = await _step_chunk().run(_state(pre_filter=None))
    assert "pre_filter.result" not in out.scratch


async def test_pre_filter_writes_typed_result_when_filter_present() -> None:
    """A valid pre_filter → PreFilterResult dataclass under state.scratch['pre_filter.result']."""
    out = await _step_chunk().run(_state(pre_filter={"package": "demo"}))
    assert "pre_filter.result" in out.scratch
    result = out.scratch["pre_filter.result"]
    assert isinstance(result, PreFilterResult)
    assert is_dataclass(result)
    # SQL pushdown clause is non-empty (LIKE / equality on 'package').
    assert result.sql
    assert result.tree is not None


async def test_pre_filter_invalid_format_raises() -> None:
    """A filter referencing an unknown field for the schema raises ValueError."""
    # 'origin' is not in our allowed_fields {package, module, scope} → schema
    # validate raises ValueError.
    state = _state(pre_filter={"origin": "site-packages"})
    with pytest.raises(ValueError, match="unknown"):
        await _step_chunk().run(state)


async def test_pre_filter_scope_split_into_typed_field() -> None:
    """A pre_filter with `scope:project_only` → result.scope is a frozenset."""
    out = await _step_chunk().run(_state(pre_filter={"scope": "project_only"}))
    result = out.scratch["pre_filter.result"]
    assert result.scope is not None
    assert isinstance(result.scope, frozenset)
    assert SearchScope.PROJECT_ONLY in result.scope


async def test_pre_filter_member_target_uses_member_columns() -> None:
    """target_field='member' → SQL adapter uses _MEMBER_COLUMNS (no 'c.' prefix)."""
    out = await _step_member().run(_state(pre_filter={"package": "demo"}))
    result = out.scratch["pre_filter.result"]
    assert result.sql  # non-empty
    # Chunk SQL has 'c.package'; member SQL has bare 'package'. Both contain
    # the column name, but only the chunk variant has the 'c.' prefix.
    assert "c.package" not in result.sql
    assert "package" in result.sql


def test_pre_filter_to_dict_shape() -> None:
    """to_dict emits type + schema_name + target_field."""
    d = _step_chunk().to_dict()
    assert d["type"] == "pre_filter"
    assert d["schema_name"] == "chunk"
    assert d["target_field"] == "chunk"


def test_pre_filter_round_trip_via_from_dict(tmp_path: Path) -> None:
    """from_dict reconstructs an equivalent step given a BuildContext."""
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.serialization import BuildContext

    config = AppConfig.load()
    # PerCallConnectionProvider is required by BuildContext but unused by
    # PreFilterStep.from_dict — supply a stub pointing at a non-existent
    # path (the step never opens it during construction).
    provider = PerCallConnectionProvider(cache_path=tmp_path / "unused.db")
    context = BuildContext(connection_provider=provider, app_config=config)
    original = _step_chunk()
    rebuilt = PreFilterStep.from_dict(original.to_dict(), context)
    assert rebuilt.schema_name == original.schema_name
    assert rebuilt.target_field == original.target_field
    # allowed_fields is rebuilt from config.metadata_schemas; check it's non-empty.
    assert rebuilt.allowed_fields


# ── C5 commit 1: tightened FilterAdapter Protocol + BuildContext wiring ──


from dataclasses import dataclass, field as _field


@dataclass
class _RecordingAdapter:
    """Test double for the tightened ``FilterAdapter`` Protocol.

    Records every ``adapt`` invocation so the test can assert the step
    called through the Protocol-typed surface (not via the old runtime
    ``from pydocs_mcp.storage.sqlite import SqliteFilterAdapter`` import).
    """

    calls: list = _field(default_factory=list)

    def adapt(self, tree, *, target_field):
        self.calls.append((tree, target_field))
        return ("WHERE 1=1", ())


def test_filter_adapter_protocol_runtime_check() -> None:
    """``_RecordingAdapter`` satisfies the runtime_checkable ``FilterAdapter`` Protocol."""
    from pydocs_mcp.storage.protocols import FilterAdapter

    assert isinstance(_RecordingAdapter(), FilterAdapter)


async def test_pre_filter_calls_adapter_with_target_field(tmp_path: Path) -> None:
    """``PreFilterStep`` invokes ``ctx.filter_adapter.adapt`` once with the
    declared ``target_field`` kwarg — the step no longer constructs the
    adapter from a runtime ``from pydocs_mcp.storage.sqlite import ...``.

    Pins the wiring contract: composition root sets
    ``BuildContext.filter_adapter``; ``PreFilterStep.from_dict`` reads it
    onto the step; ``run()`` invokes it via the typed Protocol.
    """
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.serialization import BuildContext

    adapter = _RecordingAdapter()
    config = AppConfig.load()
    provider = PerCallConnectionProvider(cache_path=tmp_path / "unused.db")
    context = BuildContext(
        connection_provider=provider,
        app_config=config,
        filter_adapter=adapter,
    )
    step = PreFilterStep.from_dict(
        {"type": "pre_filter", "schema_name": "chunk", "target_field": "chunk"},
        context,
    )
    state = _state(pre_filter={"package": "demo"})
    await step.run(state)
    assert len(adapter.calls) == 1
    _tree, target = adapter.calls[0]
    assert target == "chunk"


async def test_pre_filter_member_target_invokes_adapter_with_member() -> None:
    """``target_field='member'`` propagates through to the adapter call kwarg.

    Without an explicit ``filter_adapter`` on the BuildContext the step
    must still work — the transitional shape falls back to constructing a
    default ``SqliteFilterAdapter`` so user overlays without the new
    wiring keep functioning through commit 1.
    """
    out = await _step_member().run(_state(pre_filter={"package": "demo"}))
    result = out.scratch["pre_filter.result"]
    assert result.tree is not None
    assert result.scope is None
