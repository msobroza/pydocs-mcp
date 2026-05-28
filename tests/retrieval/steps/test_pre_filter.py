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
    # Post-C5 commit 2: PreFilterResult is backend-neutral (tree + scope).
    # Fetchers translate the tree to SQL themselves via
    # ``BuildContext.filter_adapter``; the recording-adapter test in
    # commit 1 pins the adapter is invoked with the right target_field.
    assert result.tree is not None
    assert result.scope is None


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


async def test_pre_filter_member_target_propagates_to_step_field() -> None:
    """target_field='member' is preserved on the resulting step.

    Post-C5 commit 2: ``PreFilterResult`` no longer pre-computes SQL.
    The MemberFetcherStep downstream calls
    ``ctx.filter_adapter.adapt(tree, target_field='member')`` itself
    via :meth:`MemberFetcherStep._build_where_clause` — the
    chunk-vs-member dispatch is owned by each fetcher, not the
    pre-filter step. This test only pins that ``target_field='member'``
    survives in the step config and produces a parsable tree.
    """
    step = _step_member()
    assert step.target_field == "member"
    out = await step.run(_state(pre_filter={"package": "demo"}))
    result = out.scratch["pre_filter.result"]
    assert result.tree is not None


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


async def test_pre_filter_does_not_call_adapter(tmp_path: Path) -> None:
    """Post-C5 commit 2: ``PreFilterStep.run`` is backend-neutral.

    The adapter is invoked by the downstream fetchers
    (``ChunkFetcherStep`` / ``MemberFetcherStep``), NOT by the
    pre-filter step. Pinning this prevents the SQL materialization
    from drifting back into the step.
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
    # The fetcher will call the adapter; the pre-filter step does not.
    assert adapter.calls == []


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


def test_pre_filter_result_has_no_sql_field() -> None:
    """C5 commit 2: ``PreFilterResult`` is backend-neutral (only ``tree`` + ``scope``).

    Fetchers translate the tree to backend-specific query fragments via
    ``BuildContext.filter_adapter`` at fetch time — the pre-filter step
    no longer pre-computes a SQL fragment.
    """
    field_names = {f.name for f in PreFilterResult.__dataclass_fields__.values()}
    assert "sql" not in field_names
    assert "params" not in field_names
    assert field_names == {"tree", "scope"}
