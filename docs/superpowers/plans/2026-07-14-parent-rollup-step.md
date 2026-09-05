# ParentRollupStep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the `parent_rollup` retrieval step per the approved spec at `docs/superpowers/specs/2026-07-14-parent-rollup-retriever-step-design.md` — an opt-in rerank step that collapses co-retrieved sibling chunks into their `DocumentNode` parent's own indexed chunk, gated by per-kind coverage thresholds and a constant ≥2 sibling floor.

**Architecture:** One new frozen/slots `RetrieverStep` under `retrieval/steps/parent_rollup.py` that groups candidates by `(package, module)`, loads each group's persisted tree via one `uow.trees.load()` point lookup inside a single read-only UoW, finds triggered parents by post-order tree walk with atomic index claims, fetches each applied parent's chunk row by the `(package, module, qualified_name)` join key, and rebuilds the candidate list. Two prerequisite extensions land first: the generic YAML codec learns `default_factory` fields + Mapping emission, and the test fakes learn `module`/`qualified_name` chunk filters + real per-module tree loads.

**Tech Stack:** Python 3.11+, dataclasses (frozen/slots), pytest + pytest-asyncio, in-memory fakes from `tests/_fakes.py`, generic YAML codec from `retrieval/serialization.py`.

**Ground rules for every commit in this plan:**
- Author is the local git user; **NEVER add `Co-Authored-By:` trailers** (user authorship policy).
- Work happens in this worktree (branch `claude/retriever-parent-dedup-ef2c24`).
- All test commands run from the repo root. Async tests use `@pytest.mark.asyncio` (pytest-asyncio is already configured).
- The spec is the normative reference; section/AC numbers below (§3.2, AC7, …) refer to it.

---

## File map (what this plan touches)

| File | Change | Task |
|---|---|---|
| `python/pydocs_mcp/retrieval/serialization.py` | Extend `step_to_yaml_dict` / `yaml_kwargs`: `default_factory` resolution + Mapping→dict emission | 1 |
| `tests/retrieval/test_serialization_helpers.py` | Pin the codec extension (AC39) | 1 |
| `tests/_fakes.py` | `InMemoryChunkStore.list` metadata filters; `InMemoryDocumentTreeStore.load` real lookup | 2 |
| `tests/test_fakes.py` | Pin both fake extensions (AC22) | 2 |
| `python/pydocs_mcp/retrieval/steps/parent_rollup.py` | **New** — the step | 3 (skeleton), 4 (algorithm) |
| `python/pydocs_mcp/retrieval/steps/__init__.py` | Import + `__all__` entry | 3 |
| `tests/retrieval/steps/test_parent_rollup.py` | **New** — full per-step suite (AC1–AC20, AC24–AC38, AC40, AC34) | 3–9 |
| `tests/retrieval/steps/test_yaml_codec_parity.py` | `_YAML_KEYS` pin + emission tests (AC21) | 3 |
| `tests/retrieval/test_serialization.py` | Extend registry-population test (AC23) | 3 |
| `CLAUDE.md` | Append `parent_rollup` to the steps enumeration | 9 |
| `DOCUMENTATION.md` | Rerank-step paragraph + loader-valid YAML snippet | 9 |
| `benchmarks/configs/pipelines/exp_parent_rollup.yaml` + `exp_parent_rollup_baseline.yaml` | **New** — benchmark A/B configs | 10 |

---

### Task 1: Generic-codec extension (`default_factory` + Mapping emission)

The mapping field on the new step **cannot** carry a plain dataclass default (dataclasses rejects unhashable defaults — both `dict` and `MappingProxyType`), so it ships a `default_factory`. Today both codec helpers hard-fail on factory fields (`f.default is MISSING` → ValueError). Extend them minimally; the injected-dependency guard (neither default nor factory) must stay intact.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/serialization.py:185-242`
- Test: `tests/retrieval/test_serialization_helpers.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/retrieval/test_serialization_helpers.py` (extend the existing imports at the top of the file first):

```python
# --- add to the import block at the top of the file ---
from collections.abc import Mapping
from dataclasses import field
from types import MappingProxyType

import yaml
```

(The file already imports `dataclass`, `ClassVar`, `pytest`, and the two helpers.)

```python
# --- append at the end of the file ---
_DEFAULT_TABLE = {"class": 0.3}


@dataclass(frozen=True, slots=True)
class _MappedWidget:
    table: Mapping[str, float] = field(default_factory=lambda: dict(_DEFAULT_TABLE))
    knob: int = _DEFAULT_KNOB
    _YAML_KEYS: ClassVar[tuple[str, ...]] = ("table", "knob")


def test_to_dict_resolves_default_factory_and_omits_default_mapping() -> None:
    out = step_to_yaml_dict(_MappedWidget(), type_name="mw", keys=_MappedWidget._YAML_KEYS)
    assert out == {"type": "mw"}


def test_to_dict_emits_non_default_mapping_as_plain_dict() -> None:
    w = _MappedWidget(table=MappingProxyType({"class": 0.2}))
    out = step_to_yaml_dict(w, type_name="mw", keys=_MappedWidget._YAML_KEYS)
    assert out == {"type": "mw", "table": {"class": 0.2}}
    assert type(out["table"]) is dict
    # A raw mappingproxy in the output would raise yaml RepresenterError.
    yaml.safe_dump(out)


def test_yaml_kwargs_resolves_default_factory() -> None:
    kwargs = yaml_kwargs({}, _MappedWidget, _MappedWidget._YAML_KEYS)
    assert kwargs == {"table": {"class": 0.3}, "knob": _DEFAULT_KNOB}


def test_yaml_kwargs_passes_yaml_mapping_through_untouched() -> None:
    kwargs = yaml_kwargs({"table": {"module": 0.6}}, _MappedWidget, _MappedWidget._YAML_KEYS)
    assert kwargs["table"] == {"module": 0.6}


def test_helpers_still_reject_keys_with_neither_default_nor_factory() -> None:
    with pytest.raises(ValueError, match="dep"):
        step_to_yaml_dict(_NoDefault(dep=object()), type_name="x", keys=("dep",))
    with pytest.raises(ValueError, match="dep"):
        yaml_kwargs({}, _NoDefault, ("dep",))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/retrieval/test_serialization_helpers.py -q`
Expected: the 4 new factory/mapping tests FAIL with `ValueError: _MappedWidget.table has no dataclass default`; the reject-test and all pre-existing tests PASS.

- [ ] **Step 3: Implement the codec extension**

In `python/pydocs_mcp/retrieval/serialization.py`, add one module-level helper directly above `step_to_yaml_dict` (dataclasses is already imported):

```python
def _effective_default(f: dataclasses.Field) -> Any:
    """Resolve a field's effective default: plain default or default_factory product.

    Mapping-typed step fields (e.g. ParentRollupStep.min_coverage_by_kind)
    cannot carry a plain default — dataclasses rejects unhashable defaults —
    so they ship a ``default_factory``; the codec treats its product as the
    omit-when-default baseline. ``MISSING`` means the field has neither (an
    injected dependency), which stays a codec error at the call sites.
    """
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        return f.default_factory()
    return dataclasses.MISSING
```

In `step_to_yaml_dict`, change the defaults line and the emission line:

```python
    defaults = {f.name: _effective_default(f) for f in dataclasses.fields(step)}
```

and replace `out[key] = list(value) if isinstance(value, tuple) else value` with:

```python
        if isinstance(value, tuple):
            value = list(value)
        elif isinstance(value, Mapping):
            # YAML has no mappingproxy (safe_dump raises RepresenterError) —
            # emit a plain dict, mirroring the tuple→list rule above.
            value = dict(value)
        out[key] = value
```

(`Mapping` is already imported at the top of the module from `collections.abc`.)

In `yaml_kwargs`, replace the body of the per-key loop:

```python
    for key in keys:
        f = fields_by_name[key]
        default = _effective_default(f)
        if default is dataclasses.MISSING:
            raise ValueError(
                f"{cls.__name__}.{key} has no dataclass default; _YAML_KEYS "
                "may only list defaulted config fields, never injected dependencies."
            )
        value = data.get(key, default)
        if isinstance(default, tuple) and not isinstance(value, tuple):
            value = tuple(value)
        kwargs[key] = value
```

Also update both docstrings with one sentence: "`default_factory` fields resolve their effective default via the factory product; only fields with neither default nor factory are rejected."

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/retrieval/test_serialization_helpers.py tests/retrieval/steps/test_yaml_codec_parity.py tests/retrieval/test_serialization.py -q`
Expected: ALL PASS (the parity suite proves no existing step's round-trip changed).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/serialization.py tests/retrieval/test_serialization_helpers.py
git commit -m "feat(retrieval): generic YAML codec resolves default_factory fields, emits Mapping as dict"
```

---

### Task 2: Test-fake extensions (chunk metadata filters + real tree loads)

G5 honesty fixes: the fakes must match the real repositories on the paths the new step exercises. `InMemoryChunkStore.list` today honors only `package`; `InMemoryDocumentTreeStore.load` is a stub returning `None`.

**Files:**
- Modify: `tests/_fakes.py:98-99` (tree load), `tests/_fakes.py:198-210` (chunk list)
- Test: `tests/test_fakes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fakes.py`:

```python
@pytest.mark.asyncio
async def test_in_memory_chunk_store_list_filters_module_and_qualified_name():
    store = InMemoryChunkStore()
    await store.upsert(
        [
            Chunk(text="a", metadata={"package": "p", "module": "p.m", "qualified_name": "p.m.A"}),
            Chunk(text="b", metadata={"package": "p", "module": "p.m", "qualified_name": "p.m.B"}),
            Chunk(text="c", metadata={"package": "p", "module": "p.n", "qualified_name": "p.m.A"}),
        ]
    )
    # AND semantics across the CHUNK_COLUMNS-whitelisted keys the real
    # translator supports (storage/sqlite/filter_adapter.py CHUNK_COLUMNS).
    rows = await store.list(
        filter={"package": "p", "module": "p.m", "qualified_name": "p.m.A"}, limit=1
    )
    assert [c.text for c in rows] == ["a"]
    # package-only behavior unchanged.
    assert len(await store.list(filter={"package": "p"})) == 3


@pytest.mark.asyncio
async def test_in_memory_document_tree_store_load_serves_by_module_and_records_call():
    from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind

    store = InMemoryDocumentTreeStore()
    tree = DocumentNode(
        node_id="p.m",
        qualified_name="p.m",
        title="m",
        kind=NodeKind.MODULE,
        source_path="m.py",
        start_line=1,
        end_line=2,
        text="doc",
        content_hash="h",
    )
    await store.save_many([tree], package="p")
    # Mirrors SqliteDocumentTreeStore.load: the module argument equals the
    # tree root's qualified_name (the document_trees row key).
    assert await store.load("p", "p.m") is tree
    assert await store.load("p", "p.other") is None
    assert await store.load("other", "p.m") is None
    assert any(c.method == "load" and c.payload == ("p", "p.m") for c in store.calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fakes.py -q -k "filters_module or load_serves"`
Expected: both FAIL (chunk filter returns the wrong row set; `load` returns `None`).

- [ ] **Step 3: Implement the fake extensions**

In `tests/_fakes.py`, replace `InMemoryDocumentTreeStore.load` (lines 98-99):

```python
    async def load(self, package, module):
        # Mirrors SqliteDocumentTreeStore.load: a point lookup keyed by the
        # tree root's qualified_name (save_many writes t.qualified_name as
        # the row key). Recorded so read-side tests can pin call counts.
        self.calls.append(_Call("load", (package, module)))
        for tree in self.by_package.get(package, ()):
            if tree.qualified_name == module:
                return tree
        return None
```

Add a module-level constant directly above `class InMemoryChunkStore`:

```python
# Metadata keys InMemoryChunkStore.list AND-matches, mirroring the real
# CHUNK_COLUMNS whitelist (storage/sqlite/filter_adapter.py) minus
# "package" (which selects the bucket) and "id" (a dataclass field,
# not metadata).
_CHUNK_METADATA_FILTER_KEYS = ("module", "origin", "title", "qualified_name")
```

Replace `InMemoryChunkStore.list`:

```python
    async def list(
        self,
        filter: Any | None = None,
        limit: int | None = None,
    ) -> list[Chunk]:
        self.calls.append(_Call("list", {"filter": filter, "limit": limit}))
        if isinstance(filter, dict) and "package" in filter:
            rows = list(self.by_package.get(filter["package"], []))
        else:
            rows = [c for cs in self.by_package.values() for c in cs]
        if isinstance(filter, dict):
            for key in _CHUNK_METADATA_FILTER_KEYS:
                if key in filter:
                    rows = [c for c in rows if c.metadata.get(key) == filter[key]]
        if limit is not None:
            rows = rows[:limit]
        return rows
```

- [ ] **Step 4: Run the full suite to verify no fake consumer broke**

Run: `pytest tests/ --ignore=tests/test_parity.py -q`
Expected: ALL PASS (the extensions only narrow results for callers that pass the new keys; existing callers pass `package` only).

- [ ] **Step 5: Commit**

```bash
git add tests/_fakes.py tests/test_fakes.py
git commit -m "test(fakes): chunk-store metadata filters + real per-module tree loads"
```

---

### Task 3: Step module skeleton — constants, codec, validation, registration

Ships the complete step class with a guard-only `run()` (Phase 0 of §3.2); the algorithm lands in Task 4. Covers AC1–AC6, AC21, AC23, AC36, AC37.

**Files:**
- Create: `python/pydocs_mcp/retrieval/steps/parent_rollup.py`
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py`
- Test: `tests/retrieval/steps/test_parent_rollup.py` (new), `tests/retrieval/steps/test_yaml_codec_parity.py`, `tests/retrieval/test_serialization.py:135-146`

- [ ] **Step 1: Write the failing tests**

Create `tests/retrieval/steps/test_parent_rollup.py`:

```python
"""ParentRollupStep — kind-aware sibling→parent rollup (spec 2026-07-14)."""

from __future__ import annotations

import pytest
import yaml

from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind
from pydocs_mcp.models import Chunk, ChunkList, ModuleMemberList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps.parent_rollup import (
    _DEFAULT_MIN_COVERAGE,
    _DEFAULT_MIN_COVERAGE_BY_KIND,
    _MIN_SIBLINGS,
    ParentRollupStep,
)
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    make_fake_uow_factory,
)

_PKG = "pkg"
_MOD = "pkg.mod"


# ── fixtures ─────────────────────────────────────────────────────────────


def _node(
    qname: str,
    kind: NodeKind,
    *,
    text: str = "body",
    children: tuple = (),
) -> DocumentNode:
    return DocumentNode(
        node_id=qname,
        qualified_name=qname,
        title=qname.rsplit(".", 1)[-1],
        kind=kind,
        source_path="src.py",
        start_line=1,
        end_line=2,
        text=text,
        content_hash=f"hash-{qname}",
        children=tuple(children),
    )


def _class_tree(n_methods: int, *, root_text: str = "") -> DocumentNode:
    """MODULE root (empty text unless stated) → ClassA `_MOD + '.C'` → n METHODs."""
    methods = tuple(_node(f"{_MOD}.C.m{i}", NodeKind.METHOD) for i in range(n_methods))
    cls = _node(f"{_MOD}.C", NodeKind.CLASS, children=methods)
    return _node(_MOD, NodeKind.MODULE, text=root_text, children=(cls,))


def _chunk(
    qname: str,
    relevance: float | None = None,
    *,
    package: str = _PKG,
    module: str = _MOD,
    text: str | None = None,
) -> Chunk:
    return Chunk(
        text=text or f"text-{qname}",
        relevance=relevance,
        metadata={"package": package, "module": module, "qualified_name": qname},
    )


def _state(items: list[Chunk]) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="q"),
        candidates=ChunkList(items=tuple(items)),
        result=None,
        scratch={},
    )


async def _stores(
    trees: list[DocumentNode] = (),
    chunks: list[Chunk] = (),
    package: str = _PKG,
) -> tuple[InMemoryDocumentTreeStore, InMemoryChunkStore]:
    tree_store = InMemoryDocumentTreeStore()
    chunk_store = InMemoryChunkStore()
    if trees:
        await tree_store.save_many(list(trees), package=package)
    if chunks:
        await chunk_store.upsert(list(chunks))
    return tree_store, chunk_store


def _step(
    tree_store: InMemoryDocumentTreeStore | None = None,
    chunk_store: InMemoryChunkStore | None = None,
    **cfg,
) -> ParentRollupStep:
    return ParentRollupStep(
        uow_factory=make_fake_uow_factory(
            trees=tree_store or InMemoryDocumentTreeStore(),
            chunks=chunk_store or InMemoryChunkStore(),
        ),
        **cfg,
    )


def _qnames(state: RetrieverState) -> list[str]:
    return [c.metadata["qualified_name"] for c in state.candidates.items]


def _ctx() -> BuildContext:
    return BuildContext(uow_factory=make_fake_uow_factory())


# ── AC1–AC5, AC36, AC37: codec, constants, validation ────────────────────


def test_ac1_default_to_dict_is_bare_type() -> None:
    assert _step().to_dict() == {"type": "parent_rollup"}


def test_ac2_round_trip_via_registry_and_no_alias() -> None:
    original = _step(min_coverage=0.6)
    rebuilt = step_registry.build(original.to_dict(), _ctx())
    assert isinstance(rebuilt, ParentRollupStep)
    assert rebuilt.min_coverage == 0.6
    assert "parent_rollup" in step_registry.names()
    assert "rollup" not in step_registry.names()


def test_ac3_from_dict_requires_uow_factory() -> None:
    with pytest.raises(ValueError, match="uow_factory"):
        ParentRollupStep.from_dict({"type": "parent_rollup"}, BuildContext(uow_factory=None))


@pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
def test_ac4_min_coverage_domain_validated_pre_construction(bad: float) -> None:
    with pytest.raises(ValueError, match=repr(bad)):
        ParentRollupStep.from_dict({"type": "parent_rollup", "min_coverage": bad}, _ctx())


def test_ac5_constants_and_read_only_mapping() -> None:
    assert _DEFAULT_MIN_COVERAGE == 0.5
    assert dict(_DEFAULT_MIN_COVERAGE_BY_KIND) == {
        "class": 0.3,
        "module": 0.6,
        "markdown_heading": 0.5,
    }
    assert _MIN_SIBLINGS == 2
    step = _step()
    with pytest.raises(TypeError):
        step.min_coverage_by_kind["class"] = 0.1  # type: ignore[index]


def test_ac36_custom_mapping_round_trip_and_yaml_dumpable() -> None:
    original = _step(min_coverage_by_kind={"class": 0.2, "function": 0.4})
    data = original.to_dict()
    assert data == {
        "type": "parent_rollup",
        "min_coverage_by_kind": {"class": 0.2, "function": 0.4},
    }
    assert type(data["min_coverage_by_kind"]) is dict
    yaml.safe_dump(data)  # raw mappingproxy would raise RepresenterError
    rebuilt = step_registry.build(data, _ctx())
    assert rebuilt.min_coverage_by_kind == {"class": 0.2, "function": 0.4}
    # A mapping equal to the default table is omitted entirely.
    assert _step(min_coverage_by_kind=dict(_DEFAULT_MIN_COVERAGE_BY_KIND)).to_dict() == {
        "type": "parent_rollup"
    }


def test_ac37_mapping_validation_names_offender_pre_construction() -> None:
    with pytest.raises(ValueError, match="'klass'"):
        ParentRollupStep.from_dict(
            {"type": "parent_rollup", "min_coverage_by_kind": {"klass": 0.3}}, _ctx()
        )
    for bad_value in (1.5, "hot", True):
        with pytest.raises(ValueError, match="'class'"):
            ParentRollupStep.from_dict(
                {"type": "parent_rollup", "min_coverage_by_kind": {"class": bad_value}},
                _ctx(),
            )
    for non_mapping in (0.3, ["class"]):
        with pytest.raises(ValueError, match="must be a mapping"):
            ParentRollupStep.from_dict(
                {"type": "parent_rollup", "min_coverage_by_kind": non_mapping}, _ctx()
            )
    # 0.0 is allowed per-kind (explicit opt-in to maximum eagerness).
    step = ParentRollupStep.from_dict(
        {"type": "parent_rollup", "min_coverage_by_kind": {"class": 0.0}}, _ctx()
    )
    assert step.min_coverage_by_kind == {"class": 0.0}


# ── AC6: guards ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ac6_non_chunklist_and_empty_pass_through_as_identity() -> None:
    step = _step()
    for candidates in (
        None,
        ChunkList(items=()),
        ModuleMemberList(items=()),
    ):
        state = RetrieverState(
            query=SearchQuery(terms="q"), candidates=candidates, result=None, scratch={}
        )
        assert await step.run(state) is state
```

Add to `tests/retrieval/steps/test_yaml_codec_parity.py` (AC21) — extend the import block with `from pydocs_mcp.retrieval.steps.parent_rollup import ParentRollupStep` and append:

```python
def test_parent_rollup_declares_yaml_keys() -> None:
    assert ParentRollupStep._YAML_KEYS == ("min_coverage", "min_coverage_by_kind", "name")


def test_parent_rollup_default_emits_bare_type() -> None:
    step = ParentRollupStep(uow_factory=make_fake_uow_factory())
    assert step.to_dict() == {"type": "parent_rollup"}


def test_parent_rollup_emits_non_defaults_in_key_order_mapping_as_dict() -> None:
    step = ParentRollupStep(
        uow_factory=make_fake_uow_factory(),
        min_coverage=0.4,
        min_coverage_by_kind={"class": 0.25},
        name="pr",
    )
    out = step.to_dict()
    assert out == {
        "type": "parent_rollup",
        "min_coverage": 0.4,
        "min_coverage_by_kind": {"class": 0.25},
        "name": "pr",
    }
    assert list(out) == ["type", "min_coverage", "min_coverage_by_kind", "name"]
    assert type(out["min_coverage_by_kind"]) is dict
```

In `tests/retrieval/test_serialization.py`, extend `test_bare_retrieval_import_populates_registries` (AC23) — add one line at the end of the function body:

```python
    assert "parent_rollup" in step_registry.names()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/retrieval/steps/test_parent_rollup.py -q`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'pydocs_mcp.retrieval.steps.parent_rollup'`.

- [ ] **Step 3: Create the step module (skeleton)**

Create `python/pydocs_mcp/retrieval/steps/parent_rollup.py`:

```python
"""ParentRollupStep — collapse sibling results into their parent.

A rerank-only step: when enough children of one ``DocumentNode`` parent
are co-retrieved (>= ``_MIN_SIBLINGS`` sibling hits AND kind-resolved
coverage of the parent's chunk-emitting children), the siblings are
replaced by the parent's own indexed chunk at the group's best rank.
Replaces candidates only — adds nothing on failure paths and falls
through to the unchanged input on every data-shaped failure condition
(missing tree, missing parent chunk row, gates unmet, malformed
metadata). Reads ``document_trees`` via ``uow.trees`` and ``chunks`` via
``uow.chunks`` in one read-only UoW per call. Spec:
docs/superpowers/specs/2026-07-14-parent-rollup-retriever-step-design.md.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar

from pydocs_mcp.extraction.model.document_node import NodeKind
from pydocs_mcp.models import ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    step_registry,
    step_to_yaml_dict,
    yaml_kwargs,
)
from pydocs_mcp.storage.protocols import UnitOfWork

# WHY: per-kind coverage thresholds — see the spec's §3.6 table. Class
# rollup is eager (top-K caps the numerator hard for classes); a whole-
# module rollup swallows the most granularity, so it demands the
# strongest evidence; doc headings sit in between.
_DEFAULT_MIN_COVERAGE = 0.5
_DEFAULT_MIN_COVERAGE_BY_KIND: Mapping[str, float] = MappingProxyType(
    {"class": 0.3, "module": 0.6, "markdown_heading": 0.5}
)
# WHY: structural floor, not a tunable — collapsing a single retrieved
# child is pure information loss (same list length, less specific
# result), so no deployment wants 1. Not a dataclass field, never
# serialized, absent from _YAML_KEYS.
_MIN_SIBLINGS = 2
_DEFAULT_NAME = "parent_rollup"
_QNAME_KEY = "qualified_name"
_PACKAGE_KEY = "package"
_MODULE_KEY = "module"
_VALID_KIND_KEYS = frozenset(k.value for k in NodeKind)


def _validated_coverage_mapping(raw: object) -> dict[str, float]:
    """Validate a YAML-parsed ``min_coverage_by_kind`` value pre-construction."""
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"ParentRollupStep.min_coverage_by_kind must be a mapping of "
            f"NodeKind value -> float; got {raw!r}."
        )
    out: dict[str, float] = {}
    for key, value in raw.items():
        if key not in _VALID_KIND_KEYS:
            raise ValueError(
                f"ParentRollupStep.min_coverage_by_kind key {key!r} is not a "
                f"NodeKind value; valid keys: {sorted(_VALID_KIND_KEYS)}."
            )
        # bool is an int subclass, but `class: true` is a YAML typo, not a
        # threshold. 0.0 is allowed: explicit per-kind opt-in to maximum
        # eagerness (the sibling floor still gates).
        if isinstance(value, bool) or not isinstance(value, int | float) or not 0.0 <= value <= 1.0:
            raise ValueError(
                f"ParentRollupStep.min_coverage_by_kind[{key!r}] must be a "
                f"float in [0.0, 1.0]; got {value!r}."
            )
        out[key] = float(value)
    return out


@step_registry.register("parent_rollup")
@dataclass(frozen=True, slots=True)
class ParentRollupStep(RetrieverStep):
    """Collapse co-retrieved sibling chunks into their parent's chunk."""

    uow_factory: Callable[[], UnitOfWork] = field(kw_only=True)
    min_coverage: float = field(default=_DEFAULT_MIN_COVERAGE, kw_only=True)
    min_coverage_by_kind: Mapping[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_MIN_COVERAGE_BY_KIND),
        kw_only=True,
    )
    name: str = field(default=_DEFAULT_NAME, kw_only=True)
    _YAML_KEYS: ClassVar[tuple[str, ...]] = ("min_coverage", "min_coverage_by_kind", "name")

    def __post_init__(self) -> None:
        # Read-only normalization — the Chunk.metadata precedent
        # (models.py __post_init__): frozen+slots forbids assignment,
        # not object.__setattr__; dataclasses.replace re-runs this and
        # harmlessly re-wraps.
        object.__setattr__(
            self,
            "min_coverage_by_kind",
            MappingProxyType(dict(self.min_coverage_by_kind)),
        )

    async def run(self, state: RetrieverState) -> RetrieverState:
        candidates = state.candidates
        if not isinstance(candidates, ChunkList) or not candidates.items:
            return state
        # Phases 1-6 land in the core-algorithm task; guard-only until then.
        return state

    def to_dict(self) -> dict:
        return step_to_yaml_dict(self, type_name="parent_rollup", keys=self._YAML_KEYS)

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> ParentRollupStep:
        if context.uow_factory is None:
            raise ValueError(
                "ParentRollupStep requires BuildContext.uow_factory. "
                "Production wiring in __main__.py / server.py sets this.",
            )
        kwargs = yaml_kwargs(data, cls, cls._YAML_KEYS)
        if not 0.0 < kwargs["min_coverage"] <= 1.0:
            raise ValueError(
                f"ParentRollupStep.min_coverage must be in (0.0, 1.0]; "
                f"got {kwargs['min_coverage']!r}.",
            )
        kwargs["min_coverage_by_kind"] = _validated_coverage_mapping(
            kwargs["min_coverage_by_kind"]
        )
        return cls(uow_factory=context.uow_factory, **kwargs)


__all__ = ("ParentRollupStep",)
```

(The skeleton imports only what it uses — `ruff` F401 must stay clean at this commit. Task 4 extends the import block when the algorithm lands.)

In `python/pydocs_mcp/retrieval/steps/__init__.py`, add to the alphabetical import block (between `ParallelStep` and `PreFilterResult` lines):

```python
from pydocs_mcp.retrieval.steps.parent_rollup import ParentRollupStep
```

and to `__all__` (between `"ParallelStep"` and `"PreFilterResult"`):

```python
    "ParentRollupStep",
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/retrieval/steps/test_parent_rollup.py tests/retrieval/steps/test_yaml_codec_parity.py tests/retrieval/test_serialization.py -q`
Expected: ALL PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/parent_rollup.py python/pydocs_mcp/retrieval/steps/__init__.py tests/retrieval/steps/test_parent_rollup.py tests/retrieval/steps/test_yaml_codec_parity.py tests/retrieval/test_serialization.py
git commit -m "feat(retrieval): ParentRollupStep skeleton — codec, validation, registration"
```

---

### Task 4: Core rollup algorithm (§3.2 Phases 1–6)

The full algorithm: grouping, per-group tree loads, post-order trigger detection with atomic claims, fetch-at-apply with abandonment, rebuild with relevance folding and cross-group dedup. Covers AC7, AC8, AC9, AC24 now; later tasks pin the rest against this same implementation.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/steps/parent_rollup.py`
- Test: `tests/retrieval/steps/test_parent_rollup.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/retrieval/steps/test_parent_rollup.py`:

```python
# ── AC7–AC9, AC24: core gates + happy path ───────────────────────────────


@pytest.mark.asyncio
async def test_ac7_happy_path_collapses_siblings_into_class() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    items = [
        _chunk(f"{_MOD}.C.m0", 0.9),
        _chunk(f"{_MOD}.C.m2", 0.7),
        _chunk("pkg.other.x", 0.6, module="pkg.other"),
        _chunk(f"{_MOD}.C.m1", 0.5),
    ]
    out = await step.run(_state(items))
    # 3/4 hits >= class threshold 0.3, floor met -> parent at the lowest
    # group index (0), siblings gone, non-group candidate order preserved.
    assert _qnames(out) == [f"{_MOD}.C", "pkg.other.x"]
    assert out.candidates.items[0].relevance == 0.9
    # Parent chunk fetched via the three-key filter.
    fetch = next(c for c in cs.calls if c.method == "list")
    assert fetch.payload["filter"] == {
        "package": _PKG,
        "module": _MOD,
        "qualified_name": f"{_MOD}.C",
    }


@pytest.mark.asyncio
async def test_ac8_below_coverage_returns_identity() -> None:
    ts, cs = await _stores(trees=[_class_tree(8)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8)])
    # 2/8 = 0.25 < 0.3 (class threshold); floor satisfied -> coverage is
    # the failing gate; nothing else rolls up -> identity return.
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac9_sibling_floor_blocks_single_hit_and_is_not_configurable() -> None:
    ts, cs = await _stores(trees=[_class_tree(2)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state([_chunk(f"{_MOD}.C.m0", 0.9)])
    # 1 hit of a 2-method class: coverage 0.5 >= 0.3 but floor fails.
    assert await step.run(state) is state
    # The floor is not configuration: unknown params keys are ignored by
    # the codec, so behavior is unchanged.
    step2 = ParentRollupStep.from_dict(
        {"type": "parent_rollup", "min_children": 1, "min_siblings": 1}, _ctx()
    )
    assert step2.to_dict() == {"type": "parent_rollup"}


@pytest.mark.asyncio
async def test_ac24_coverage_boundary_equality_triggers() -> None:
    ts, cs = await _stores(trees=[_class_tree(10)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    items = [_chunk(f"{_MOD}.C.m{i}", 0.5) for i in (0, 1, 2)]
    out = await step.run(_state(items))
    # 3/10 == 0.30 satisfies >= against the class threshold; `>` fails this.
    assert _qnames(out) == [f"{_MOD}.C"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/retrieval/steps/test_parent_rollup.py -q -k "ac7 or ac8 or ac9 or ac24"`
Expected: `ac7` and `ac24` FAIL (guard-only run returns input unchanged); `ac8`/`ac9` PASS trivially — that is fine, they pin the failing gate once the algorithm lands.

- [ ] **Step 3: Implement the algorithm**

In `python/pydocs_mcp/retrieval/steps/parent_rollup.py`, first extend the import block (the algorithm consumes these):

```python
# change: from dataclasses import dataclass, field  ->
from dataclasses import dataclass, field, replace

# change: from pydocs_mcp.extraction.model.document_node import NodeKind  ->
from pydocs_mcp.extraction.model.document_node import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
)

# change: from pydocs_mcp.models import ChunkList  ->
from pydocs_mcp.models import Chunk, ChunkList
```

Then add module-level helpers between `_validated_coverage_mapping` and the class:

```python
@dataclass(slots=True)
class _Rollup:
    """One applied parent rollup — module-private mutable accumulator.

    ``claimed`` holds the ORIGINAL candidate indices this rollup consumed
    (hit children + same-qname self-folds). Mutable so an AST
    duplicate-parent merge can union claims into the first emission.
    """

    chunk: Chunk
    claimed: set[int]


def _candidate_key(chunk: Chunk) -> tuple[str, str, str] | None:
    """(package, module, qualified_name) — None when any is missing/blank."""
    package = chunk.metadata.get(_PACKAGE_KEY)
    module = chunk.metadata.get(_MODULE_KEY)
    qname = chunk.metadata.get(_QNAME_KEY)
    if not package or not module or not qname:
        return None
    return (str(package), str(module), str(qname))


def _group_candidates(
    items: tuple[Chunk, ...],
) -> dict[tuple[str, str], dict[str, list[int]]]:
    """Group candidate indices by (package, module), then by qualified_name."""
    groups: dict[tuple[str, str], dict[str, list[int]]] = {}
    for i, chunk in enumerate(items):
        key = _candidate_key(chunk)
        if key is None:
            continue
        package, module, qname = key
        groups.setdefault((package, module), {}).setdefault(qname, []).append(i)
    return groups


def _post_order(root: DocumentNode) -> list[DocumentNode]:
    """Post-order DFS: children before parent, document order among siblings.

    Iterative (deep subpackage chains must not hit the recursion limit).
    Guarantees deeper-before-shallower along any ancestor chain so the
    more specific collapse claims its indices first (spec §3.2 Phase 4).
    """
    out: list[DocumentNode] = []
    stack: list[tuple[DocumentNode, bool]] = [(root, False)]
    while stack:
        node, expanded = stack.pop()
        if expanded:
            out.append(node)
            continue
        stack.append((node, True))
        for child in reversed(node.children):
            stack.append((child, False))
    return out


def _emitting_children(node: DocumentNode) -> list[DocumentNode]:
    """Children that emit a chunk — byte-identical to tree_flatten._should_emit."""
    return [c for c in node.children if c.kind not in STRUCTURAL_ONLY_KINDS and c.text.strip()]


def _fold_relevance(rollup: _Rollup, items: tuple[Chunk, ...]) -> Chunk:
    """relevance = max over the group's non-None values; None if all None."""
    values = [items[i].relevance for i in rollup.claimed if items[i].relevance is not None]
    if not values:
        return rollup.chunk
    return replace(rollup.chunk, relevance=max(values))


def _rebuild(items: tuple[Chunk, ...], rollups: list[_Rollup]) -> list[Chunk]:
    """Emit each parent at its group's lowest index; drop other claimed indices."""
    emit_at = {min(r.claimed): r for r in rollups}
    claimed_all: set[int] = set().union(*(r.claimed for r in rollups))
    out: list[Chunk] = []
    for i, chunk in enumerate(items):
        rollup = emit_at.get(i)
        if rollup is not None:
            out.append(_fold_relevance(rollup, items))
        elif i not in claimed_all:
            out.append(chunk)
    return _dedup_against_parents(out, rollups)


def _dedup_against_parents(out: list[Chunk], rollups: list[_Rollup]) -> list[Chunk]:
    """Cross-group dedup (spec §3.2 Phase 5): a surviving candidate whose
    (qualified_name, content_hash) equals an emitted parent's is dropped,
    keeping the single lowest-index occurrence."""
    parent_keys = {(r.chunk.metadata.get(_QNAME_KEY), r.chunk.content_hash) for r in rollups}
    seen: set[tuple] = set()
    deduped: list[Chunk] = []
    for chunk in out:
        key = (chunk.metadata.get(_QNAME_KEY), chunk.content_hash)
        if key in parent_keys:
            if key in seen:
                continue
            seen.add(key)
        deduped.append(chunk)
    return deduped
```

Replace the guard-only `run()` and add the private methods inside `ParentRollupStep` (after `__post_init__`, before `to_dict`):

```python
    async def run(self, state: RetrieverState) -> RetrieverState:
        candidates = state.candidates
        if not isinstance(candidates, ChunkList) or not candidates.items:
            return state
        groups = _group_candidates(candidates.items)
        if not groups:
            return state
        rollups: list[_Rollup] = []
        claimed: set[int] = set()
        async with self.uow_factory() as uow:
            for (package, module), by_qname in groups.items():
                tree = await uow.trees.load(package, module)
                if tree is None:
                    continue
                rollups.extend(
                    await self._apply_group(
                        uow, tree, package, module, by_qname, candidates.items, claimed
                    )
                )
        if not rollups:
            return state
        rebuilt = _rebuild(candidates.items, rollups)
        return replace(state, candidates=ChunkList(items=tuple(rebuilt)))

    def _threshold(self, kind: NodeKind) -> float:
        # Kind-resolved from the loaded tree node — never chunk metadata.
        return self.min_coverage_by_kind.get(kind.value, self.min_coverage)

    def _hit_qnames(
        self,
        parent: DocumentNode,
        by_qname: dict[str, list[int]],
        claimed: set[int],
    ) -> set[str]:
        """Child qnames with >=1 unclaimed candidate index (set semantics —
        AST duplicate qnames count once)."""
        hits: set[str] = set()
        for child in _emitting_children(parent):
            indices = by_qname.get(child.qualified_name, ())
            if any(i not in claimed for i in indices):
                hits.add(child.qualified_name)
        return hits

    def _gates_pass(self, parent: DocumentNode, hits: set[str]) -> bool:
        if not parent.text.strip() or len(hits) < _MIN_SIBLINGS:
            return False
        emitting = _emitting_children(parent)
        if not emitting:
            return False
        # >= is normative: equality triggers (spec §3.2 Phase 3, AC24).
        return len(hits) / len(emitting) >= self._threshold(parent.kind)

    def _claim_for(
        self,
        parent: DocumentNode,
        hits: set[str],
        by_qname: dict[str, list[int]],
        claimed: set[int],
    ) -> set[int]:
        """Atomic claim: every unclaimed index of a hit child, PLUS the
        self-fold — same-group candidates bearing the parent's own qname."""
        claim: set[int] = set()
        for qname in hits:
            claim.update(i for i in by_qname.get(qname, ()) if i not in claimed)
        claim.update(i for i in by_qname.get(parent.qualified_name, ()) if i not in claimed)
        return claim

    @staticmethod
    def _reuse_in_list(
        parent: DocumentNode,
        claim: set[int],
        items: tuple[Chunk, ...],
    ) -> Chunk | None:
        """The self-folded in-list parent chunk, if the parent was a candidate."""
        for i in sorted(claim):
            if items[i].metadata.get(_QNAME_KEY) == parent.qualified_name:
                return items[i]
        return None

    async def _apply_group(
        self,
        uow: UnitOfWork,
        tree: DocumentNode,
        package: str,
        module: str,
        by_qname: dict[str, list[int]],
        items: tuple[Chunk, ...],
        claimed: set[int],
    ) -> list[_Rollup]:
        """Post-order application with atomic claims (spec §3.2 Phase 4).

        Gates evaluate against UNCLAIMED indices only, so a deeper rollup's
        claim is invisible to its ancestors (no cascade) and an abandoned
        fetch releases its claim implicitly (the claim is registered only
        after a successful fetch/reuse).
        """
        rollups: list[_Rollup] = []
        by_parent_qname: dict[str, _Rollup] = {}
        for parent in _post_order(tree):
            hits = self._hit_qnames(parent, by_qname, claimed)
            if not self._gates_pass(parent, hits):
                continue
            claim = self._claim_for(parent, hits, by_qname, claimed)
            existing = by_parent_qname.get(parent.qualified_name)
            if existing is not None:
                # AST duplicate parent qname: merge into the earlier rollup —
                # one emission at the lowest combined index, no second fetch.
                existing.claimed |= claim
                claimed |= claim
                continue
            parent_chunk = self._reuse_in_list(parent, claim, items)
            if parent_chunk is None:
                rows = await uow.chunks.list(
                    filter={
                        _PACKAGE_KEY: package,
                        _MODULE_KEY: module,
                        _QNAME_KEY: parent.qualified_name,
                    },
                    limit=1,
                )
                if not rows:
                    continue  # abandonment — claim never registered (§3.5)
                parent_chunk = rows[0]
            rollup = _Rollup(chunk=parent_chunk, claimed=claim)
            by_parent_qname[parent.qualified_name] = rollup
            rollups.append(rollup)
            claimed |= claim
        return rollups
```

- [ ] **Step 4: Run the step suite**

Run: `pytest tests/retrieval/steps/test_parent_rollup.py -q`
Expected: ALL PASS.

- [ ] **Step 5: Run the full suite**

Run: `pytest tests/ --ignore=tests/test_parity.py -q`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/parent_rollup.py tests/retrieval/steps/test_parent_rollup.py
git commit -m "feat(retrieval): parent_rollup core algorithm — post-order claims, kind-gated coverage"
```

---

### Task 5: Eligibility + fallback edges (AC11–AC13, AC15, AC18, AC20)

Pin tests against the Task 4 implementation. Each is expected to PASS; a failure means the algorithm diverges from the spec — fix `parent_rollup.py`, never weaken the test.

**Files:**
- Test: `tests/retrieval/steps/test_parent_rollup.py`
- Possibly fix: `python/pydocs_mcp/retrieval/steps/parent_rollup.py`

- [ ] **Step 1: Write the pin tests**

Append:

```python
# ── AC11–AC13, AC15, AC18, AC20: eligibility + fallbacks ─────────────────


@pytest.mark.asyncio
async def test_ac11_empty_text_parent_never_triggers() -> None:
    methods = tuple(_node(f"{_MOD}.C.m{i}", NodeKind.METHOD) for i in range(2))
    cls = _node(f"{_MOD}.C", NodeKind.CLASS, text="", children=methods)
    root = _node(_MOD, NodeKind.MODULE, text="", children=(cls,))
    ts, cs = await _stores(trees=[root])
    step = _step(ts, cs)
    state = _state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8)])
    # Class text="" fails the parent-must-emit gate; root text="" likewise.
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac12_missing_parent_chunk_row_abandons_rollup() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)])  # no chunk rows seeded
    step = _step(ts, cs)
    state = _state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8)])
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac13_missing_tree_skips_group_while_other_group_rolls_up() -> None:
    other_methods = tuple(_node(f"pkg.other.D.m{i}", NodeKind.METHOD) for i in range(2))
    other_cls = _node("pkg.other.D", NodeKind.CLASS, children=other_methods)
    other_root = _node("pkg.other", NodeKind.MODULE, text="", children=(other_cls,))
    ts, cs = await _stores(
        trees=[other_root], chunks=[_chunk("pkg.other.D", module="pkg.other")]
    )
    step = _step(ts, cs)
    items = [
        # Group (pkg, pkg.mod): no tree persisted -> skipped, kept verbatim.
        _chunk(f"{_MOD}.C.m0", 0.9),
        _chunk(f"{_MOD}.C.m1", 0.8),
        # Group (pkg, pkg.other): rolls up (2/2 = 1.0 >= 0.3).
        _chunk("pkg.other.D.m0", 0.7, module="pkg.other"),
        _chunk("pkg.other.D.m1", 0.6, module="pkg.other"),
    ]
    out = await step.run(_state(items))
    assert _qnames(out) == [f"{_MOD}.C.m0", f"{_MOD}.C.m1", "pkg.other.D"]


@pytest.mark.asyncio
async def test_ac13b_drifted_qname_contributes_nothing() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state(
        [
            _chunk(f"{_MOD}.C.m0", 0.9),
            _chunk(f"{_MOD}.stale_symbol", 0.8),  # matches no tree node
        ]
    )
    # Only 1 real hit -> floor fails; the drifted chunk never counts.
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac15_denominator_counts_emitting_children_only() -> None:
    emitting = tuple(_node(f"{_MOD}.C.m{i}", NodeKind.METHOD) for i in range(6))
    silent = tuple(
        _node(f"{_MOD}.C.s{i}", NodeKind.METHOD, text="") for i in range(2)
    )
    cls = _node(f"{_MOD}.C", NodeKind.CLASS, children=emitting + silent)
    root = _node(_MOD, NodeKind.MODULE, text="", children=(cls,))
    ts, cs = await _stores(trees=[root], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    out = await step.run(_state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8)]))
    # 2/6 = 0.33 >= 0.3 triggers; counting the empty-text children
    # (2/8 = 0.25) would not.
    assert _qnames(out) == [f"{_MOD}.C"]


@pytest.mark.asyncio
async def test_ac18_missing_metadata_passes_through_verbatim() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    no_qname = Chunk(text="n1", metadata={"package": _PKG, "module": _MOD})
    none_qname = Chunk(
        text="n2", metadata={"package": _PKG, "module": _MOD, "qualified_name": None}
    )
    blank_module = Chunk(
        text="n3", metadata={"package": _PKG, "module": "  ", "qualified_name": f"{_MOD}.C.m3"}
    )
    items = [
        _chunk(f"{_MOD}.C.m0", 0.9),
        no_qname,
        none_qname,
        blank_module,
        _chunk(f"{_MOD}.C.m1", 0.5),
    ]
    out = await step.run(_state(items))
    texts = [c.text for c in out.candidates.items]
    assert texts == [f"text-{_MOD}.C", "n1", "n2", "n3"]


@pytest.mark.asyncio
async def test_ac20_one_tree_load_per_fully_keyed_group() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    items = [
        _chunk(f"{_MOD}.C.m0", 0.9),
        _chunk(f"{_MOD}.C.m1", 0.8),
        _chunk("pkg.other.x", 0.7, module="pkg.other"),
        # Package X's only chunk lacks qualified_name -> no group, no load.
        Chunk(text="nx", metadata={"package": "x", "module": "x.m"}),
    ]
    await step.run(_state(items))
    loads = [c.payload for c in ts.calls if c.method == "load"]
    assert sorted(loads) == [(_PKG, _MOD), (_PKG, "pkg.other")]
```

- [ ] **Step 2: Run and fix divergences**

Run: `pytest tests/retrieval/steps/test_parent_rollup.py -q`
Expected: ALL PASS. If any fails, the implementation diverges from spec §3.5 — fix `parent_rollup.py` (do not weaken tests) and re-run.

- [ ] **Step 3: Commit**

```bash
git add tests/retrieval/steps/test_parent_rollup.py python/pydocs_mcp/retrieval/steps/parent_rollup.py
git commit -m "test(retrieval): parent_rollup eligibility gates + fallback matrix pins"
```

---

### Task 6: Per-kind thresholds — class vs module vs markdown (AC10, AC38, AC40)

**Files:**
- Test: `tests/retrieval/steps/test_parent_rollup.py`
- Possibly fix: `python/pydocs_mcp/retrieval/steps/parent_rollup.py`

- [ ] **Step 1: Write the pin tests**

Append:

```python
# ── AC10, AC38, AC40: kind-resolved thresholds ───────────────────────────


def _module_tree(n_functions: int, *, root_text: str = "module docstring") -> DocumentNode:
    functions = tuple(_node(f"{_MOD}.f{i}", NodeKind.FUNCTION) for i in range(n_functions))
    return _node(_MOD, NodeKind.MODULE, text=root_text, children=functions)


@pytest.mark.asyncio
async def test_ac10_class_and_module_diverge_at_identical_coverage() -> None:
    # (a) class tree: 3 of 10 methods -> collapses (0.3 >= class 0.3).
    ts, cs = await _stores(trees=[_class_tree(10)], chunks=[_chunk(f"{_MOD}.C")])
    out = await _step(ts, cs).run(
        _state([_chunk(f"{_MOD}.C.m{i}", 0.5) for i in (0, 1, 2)])
    )
    assert _qnames(out) == [f"{_MOD}.C"]

    # (b) module tree: 3 of 10 functions -> NO rollup (0.3 < module 0.6).
    ts2, cs2 = await _stores(trees=[_module_tree(10)], chunks=[_chunk(_MOD)])
    state = _state([_chunk(f"{_MOD}.f{i}", 0.5) for i in (0, 1, 2)])
    assert await _step(ts2, cs2).run(state) is state

    # (c) module tree: 6 of 10 -> collapses into the module's own chunk,
    # fetched via qualified_name == module (6/10 >= 0.6).
    ts3, cs3 = await _stores(trees=[_module_tree(10)], chunks=[_chunk(_MOD)])
    out3 = await _step(ts3, cs3).run(
        _state([_chunk(f"{_MOD}.f{i}", 0.5) for i in range(6)])
    )
    assert _qnames(out3) == [_MOD]
    fetch = next(c for c in cs3.calls if c.method == "list")
    assert fetch.payload["filter"]["qualified_name"] == _MOD


@pytest.mark.asyncio
async def test_ac38_fallback_for_unmapped_kinds_and_replace_wholesale() -> None:
    def _function_tree(n_examples: int) -> DocumentNode:
        examples = tuple(
            _node(f"{_MOD}.f#ex{i}", NodeKind.CODE_EXAMPLE) for i in range(n_examples)
        )
        fn = _node(f"{_MOD}.f", NodeKind.FUNCTION, children=examples)
        return _node(_MOD, NodeKind.MODULE, text="", children=(fn,))

    # (a) "function" absent from the default mapping -> fallback 0.5:
    # 2/4 = 0.5 >= 0.5 -> rollup.
    ts, cs = await _stores(trees=[_function_tree(4)], chunks=[_chunk(f"{_MOD}.f")])
    out = await _step(ts, cs).run(
        _state([_chunk(f"{_MOD}.f#ex0", 0.9), _chunk(f"{_MOD}.f#ex1", 0.8)])
    )
    assert _qnames(out) == [f"{_MOD}.f"]

    # (b) 2/5 = 0.4 < 0.5 -> no rollup.
    ts2, cs2 = await _stores(trees=[_function_tree(5)], chunks=[_chunk(f"{_MOD}.f")])
    state = _state([_chunk(f"{_MOD}.f#ex0", 0.9), _chunk(f"{_MOD}.f#ex1", 0.8)])
    assert await _step(ts2, cs2).run(state) is state

    # (c) an explicit {"function": 0.3} entry flips (b) to a rollup.
    ts3, cs3 = await _stores(trees=[_function_tree(5)], chunks=[_chunk(f"{_MOD}.f")])
    out3 = await _step(ts3, cs3, min_coverage_by_kind={"function": 0.3}).run(
        _state([_chunk(f"{_MOD}.f#ex0", 0.9), _chunk(f"{_MOD}.f#ex1", 0.8)])
    )
    assert _qnames(out3) == [f"{_MOD}.f"]

    # (d) replace-wholesale: under {"function": 0.3}, "module" is absent so
    # the 0.5 fallback governs the root — 5/10 = 0.5 >= 0.5 rolls up (a
    # per-key merge with the default module: 0.6 would block it).
    ts4, cs4 = await _stores(trees=[_module_tree(10)], chunks=[_chunk(_MOD)])
    out4 = await _step(ts4, cs4, min_coverage_by_kind={"function": 0.3}).run(
        _state([_chunk(f"{_MOD}.f{i}", 0.5) for i in range(5)])
    )
    assert _qnames(out4) == [_MOD]


@pytest.mark.asyncio
async def test_ac40_markdown_heading_and_whole_doc_thresholds() -> None:
    _DOC = "pkg.guide.md"

    def _md_tree(n_examples: int, *, root_text: str = "") -> DocumentNode:
        examples = tuple(
            _node(f"{_DOC}#h1-ex{i}", NodeKind.CODE_EXAMPLE) for i in range(n_examples)
        )
        heading = _node(f"{_DOC}#h1", NodeKind.MARKDOWN_HEADING, children=examples)
        return _node(_DOC, NodeKind.MODULE, text=root_text, children=(heading,))

    def _md_chunk(qname: str, relevance: float | None = None) -> Chunk:
        return _chunk(qname, relevance, module=_DOC)

    # (a) heading rollup: 2/4 = 0.5 >= markdown_heading 0.5 (equality pin).
    ts, cs = await _stores(trees=[_md_tree(4)], chunks=[_md_chunk(f"{_DOC}#h1")])
    out = await _step(ts, cs).run(
        _state([_md_chunk(f"{_DOC}#h1-ex0", 0.9), _md_chunk(f"{_DOC}#h1-ex1", 0.8)])
    )
    assert _qnames(out) == [f"{_DOC}#h1"]

    # (b) 2/5 = 0.4 < 0.5 -> no rollup.
    ts2, cs2 = await _stores(trees=[_md_tree(5)], chunks=[_md_chunk(f"{_DOC}#h1")])
    state = _state([_md_chunk(f"{_DOC}#h1-ex0", 0.9), _md_chunk(f"{_DOC}#h1-ex1", 0.8)])
    assert await _step(ts2, cs2).run(state) is state

    # (c) whole-doc rollup gated by the MODULE entry: preamble-bearing root
    # with 5 headings, 3 co-retrieved -> 3/5 = 0.6 >= module 0.6 (equality
    # pin on the module entry); 2/5 = 0.4 -> no rollup.
    headings = tuple(
        _node(f"{_DOC}#h{i}", NodeKind.MARKDOWN_HEADING) for i in range(5)
    )
    root = _node(_DOC, NodeKind.MODULE, text="preamble prose", children=headings)
    ts3, cs3 = await _stores(trees=[root], chunks=[_md_chunk(_DOC)])
    out3 = await _step(ts3, cs3).run(
        _state([_md_chunk(f"{_DOC}#h{i}", 0.5) for i in (0, 1, 2)])
    )
    assert _qnames(out3) == [_DOC]
    ts4, cs4 = await _stores(trees=[root], chunks=[_md_chunk(_DOC)])
    state4 = _state([_md_chunk(f"{_DOC}#h0", 0.5), _md_chunk(f"{_DOC}#h1", 0.5)])
    assert await _step(ts4, cs4).run(state4) is state4
```

- [ ] **Step 2: Run and fix divergences**

Run: `pytest tests/retrieval/steps/test_parent_rollup.py -q`
Expected: ALL PASS (fix implementation if not; never weaken tests).

- [ ] **Step 3: Commit**

```bash
git add tests/retrieval/steps/test_parent_rollup.py python/pydocs_mcp/retrieval/steps/parent_rollup.py
git commit -m "test(retrieval): parent_rollup per-kind threshold resolution pins"
```

---

### Task 7: Replacement semantics (AC14, AC16, AC25–AC28, AC35)

**Files:**
- Test: `tests/retrieval/steps/test_parent_rollup.py`
- Possibly fix: `python/pydocs_mcp/retrieval/steps/parent_rollup.py`

- [ ] **Step 1: Write the pin tests**

Append:

```python
# ── AC14, AC16, AC25–AC28, AC35: replacement semantics ───────────────────


@pytest.mark.asyncio
async def test_ac14_parent_already_in_results_is_self_folded_and_reused() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    in_list_parent = _chunk(f"{_MOD}.C", 0.6)
    out = await step.run(
        _state([_chunk(f"{_MOD}.C.m0", 0.9), in_list_parent, _chunk(f"{_MOD}.C.m1", 0.8)])
    )
    assert _qnames(out) == [f"{_MOD}.C"]
    # In-list object reused: relevance folded to max of the whole group.
    assert out.candidates.items[0].relevance == 0.9
    # No DB fetch for the parent (reuse path).
    assert not [c for c in cs.calls if c.method == "list"]


@pytest.mark.asyncio
async def test_ac16_duplicate_sibling_qnames_count_once_and_all_collapse() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state(
        [
            _chunk(f"{_MOD}.C.m0", 0.9),
            _chunk(f"{_MOD}.C.m0", 0.85),  # duplicate qname (AST redefinition)
            _chunk(f"{_MOD}.C.m1", 0.8),
        ]
    )
    out = await step.run(state)
    # m0 counts ONCE for coverage (2 distinct hits of 4 = 0.5 >= 0.3);
    # both m0 bearer indices collapse.
    assert _qnames(out) == [f"{_MOD}.C"]


@pytest.mark.asyncio
async def test_ac25_mixed_none_relevance_folds_max_without_typeerror() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.C.m0", None),
                _chunk(f"{_MOD}.C.m1", 0.4),
                _chunk(f"{_MOD}.C.m2", None),
            ]
        )
    )
    assert out.candidates.items[0].relevance == 0.4


@pytest.mark.asyncio
async def test_ac26_all_none_relevance_rolls_up_with_none() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    out = await _step(ts, cs).run(
        _state([_chunk(f"{_MOD}.C.m0", None), _chunk(f"{_MOD}.C.m1", None)])
    )
    assert _qnames(out) == [f"{_MOD}.C"]
    assert out.candidates.items[0].relevance is None


@pytest.mark.asyncio
async def test_ac27_interleaved_groups_rebuild_by_index() -> None:
    a_methods = tuple(_node(f"{_MOD}.A.m{i}", NodeKind.METHOD) for i in range(2))
    b_methods = tuple(_node(f"{_MOD}.B.m{i}", NodeKind.METHOD) for i in range(2))
    cls_a = _node(f"{_MOD}.A", NodeKind.CLASS, children=a_methods)
    cls_b = _node(f"{_MOD}.B", NodeKind.CLASS, children=b_methods)
    root = _node(_MOD, NodeKind.MODULE, text="", children=(cls_a, cls_b))
    ts, cs = await _stores(trees=[root], chunks=[_chunk(f"{_MOD}.A"), _chunk(f"{_MOD}.B")])
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.A.m0", 0.9),  # group A at {0, 3}
                _chunk(f"{_MOD}.B.m0", 0.8),  # group B at {1, 2}
                _chunk(f"{_MOD}.B.m1", 0.7),
                _chunk(f"{_MOD}.A.m1", 0.6),
            ]
        )
    )
    # Index-by-index rebuild: parentA at index 0, parentB at index 1.
    assert _qnames(out) == [f"{_MOD}.A", f"{_MOD}.B"]


@pytest.mark.asyncio
async def test_ac28_parent_candidacy_never_counts_toward_gates() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C", 0.8)])
    # hits = 1 (< _MIN_SIBLINGS): the parent's own candidacy is not a hit —
    # a parent-inclusive count of 2 would wrongly trigger (2/4 = 0.5 >= 0.3).
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac35_retriever_name_rule_on_both_paths() -> None:
    # Fetch path: the DB row carries retriever_name=None (row_to_chunk
    # never sets it); the emitted parent keeps None.
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    out = await _step(ts, cs).run(
        _state([_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8)])
    )
    assert out.candidates.items[0].retriever_name is None

    # Reuse path: the in-list candidate's retriever_name is kept untouched.
    ts2, cs2 = await _stores(trees=[_class_tree(4)])
    in_list_parent = Chunk(
        text="parent",
        relevance=0.5,
        retriever_name="dense",
        metadata={"package": _PKG, "module": _MOD, "qualified_name": f"{_MOD}.C"},
    )
    out2 = await _step(ts2, cs2).run(
        _state([_chunk(f"{_MOD}.C.m0", 0.9), in_list_parent, _chunk(f"{_MOD}.C.m1", 0.8)])
    )
    assert out2.candidates.items[0].retriever_name == "dense"
```

- [ ] **Step 2: Run and fix divergences**

Run: `pytest tests/retrieval/steps/test_parent_rollup.py -q`
Expected: ALL PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/retrieval/steps/test_parent_rollup.py python/pydocs_mcp/retrieval/steps/parent_rollup.py
git commit -m "test(retrieval): parent_rollup replacement-semantics pins"
```

---

### Task 8: Claims machinery, cascade prevention, dedup (AC17, AC19, AC29–AC33)

**Files:**
- Test: `tests/retrieval/steps/test_parent_rollup.py`
- Possibly fix: `python/pydocs_mcp/retrieval/steps/parent_rollup.py`

- [ ] **Step 1: Write the pin tests**

Append:

```python
# ── AC17, AC19, AC29–AC33: claims, cascade, dedup ────────────────────────


def _nested_tree(n_outer_siblings: int) -> DocumentNode:
    """mod(text='') → outer → {inner, s0..s(n-1)}, inner → {leaf0, leaf1}."""
    leaves = tuple(_node(f"{_MOD}.O.I.leaf{i}", NodeKind.METHOD) for i in range(2))
    inner = _node(f"{_MOD}.O.I", NodeKind.CLASS, children=leaves)
    siblings = tuple(
        _node(f"{_MOD}.O.s{i}", NodeKind.METHOD) for i in range(n_outer_siblings)
    )
    outer = _node(f"{_MOD}.O", NodeKind.CLASS, children=(inner, *siblings))
    return _node(_MOD, NodeKind.MODULE, text="", children=(outer,))


@pytest.mark.asyncio
async def test_ac17_no_cascade_rolled_up_inner_is_not_an_outer_hit() -> None:
    ts, cs = await _stores(
        trees=[_nested_tree(7)], chunks=[_chunk(f"{_MOD}.O.I"), _chunk(f"{_MOD}.O")]
    )
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.O.I.leaf0", 0.9),
                _chunk(f"{_MOD}.O.I.leaf1", 0.8),
                _chunk(f"{_MOD}.O.s0", 0.7),
                _chunk(f"{_MOD}.O.s1", 0.6),
            ]
        )
    )
    # inner triggers (2/2). outer's legal hits are {s0, s1} = 2 of 8
    # emitting children -> 0.25 < 0.3 -> no trigger. Counting the rolled-up
    # inner chunk as a hit (3/8 = 0.375) would wrongly trigger.
    assert _qnames(out) == [f"{_MOD}.O.I", f"{_MOD}.O.s0", f"{_MOD}.O.s1"]


@pytest.mark.asyncio
async def test_ac19_scratch_never_mutated_and_new_state_via_replace() -> None:
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    scratch: dict[str, object] = {"upstream.key": "v"}
    state = RetrieverState(
        query=SearchQuery(terms="q"),
        candidates=ChunkList(
            items=(_chunk(f"{_MOD}.C.m0", 0.9), _chunk(f"{_MOD}.C.m1", 0.8))
        ),
        result=None,
        scratch=scratch,
    )
    out = await step.run(state)
    assert out is not state  # rollup happened -> new state via replace
    assert state.scratch == {"upstream.key": "v"}
    assert out.scratch is state.scratch  # replace() keeps the reference; no writes


@pytest.mark.asyncio
async def test_ac29_atomic_claims_with_candidate_parent_self_fold() -> None:
    ts, cs = await _stores(
        trees=[_nested_tree(2)], chunks=[_chunk(f"{_MOD}.O.I"), _chunk(f"{_MOD}.O")]
    )
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.O.I", 0.9),  # inner itself is a candidate
                _chunk(f"{_MOD}.O.I.leaf0", 0.8),
                _chunk(f"{_MOD}.O.I.leaf1", 0.7),
                _chunk(f"{_MOD}.O.s0", 0.6),
                _chunk(f"{_MOD}.O.s1", 0.5),
            ]
        )
    )
    # inner triggers on its leaves and self-folds its own index; outer's
    # re-check excludes inner (claimed) leaving {s0, s1} = 2/3 >= 0.3 ->
    # outer triggers on its direct children. One chunk per original index.
    assert _qnames(out) == [f"{_MOD}.O.I", f"{_MOD}.O"]
    assert out.candidates.items[0].relevance == 0.9
    assert out.candidates.items[1].relevance == 0.6


@pytest.mark.asyncio
async def test_ac30_abandonment_releases_claim_for_shallower_parent() -> None:
    # inner's chunk row deliberately absent; outer's row present.
    ts, cs = await _stores(trees=[_nested_tree(2)], chunks=[_chunk(f"{_MOD}.O")])
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.O.I.leaf0", 0.9),
                _chunk(f"{_MOD}.O.I.leaf1", 0.8),
                _chunk(f"{_MOD}.O.s0", 0.7),
                _chunk(f"{_MOD}.O.s1", 0.6),
            ]
        )
    )
    # inner triggers first (post-order) but its fetch misses -> abandoned,
    # leaves kept. outer triggers on {s0, s1} (2/3 >= 0.3) and collapses.
    assert _qnames(out) == [f"{_MOD}.O.I.leaf0", f"{_MOD}.O.I.leaf1", f"{_MOD}.O"]
    fetches = [c.payload["filter"]["qualified_name"] for c in cs.calls if c.method == "list"]
    assert f"{_MOD}.O.I" in fetches  # the recorded miss


@pytest.mark.asyncio
async def test_ac31_duplicate_parent_qnames_merge_into_one_emission() -> None:
    # Same class qname defined twice (TYPE_CHECKING redefinition), disjoint
    # children.
    first = _node(
        f"{_MOD}.C",
        NodeKind.CLASS,
        children=(
            _node(f"{_MOD}.C.a0", NodeKind.METHOD),
            _node(f"{_MOD}.C.a1", NodeKind.METHOD),
        ),
    )
    second = DocumentNode(
        node_id=f"{_MOD}.C",
        qualified_name=f"{_MOD}.C",
        title="C",
        kind=NodeKind.CLASS,
        source_path="src.py",
        start_line=10,
        end_line=20,
        text="body2",
        content_hash="hash-C2",
        children=(
            _node(f"{_MOD}.C.b0", NodeKind.METHOD),
            _node(f"{_MOD}.C.b1", NodeKind.METHOD),
        ),
    )
    root = _node(_MOD, NodeKind.MODULE, text="", children=(first, second))
    ts, cs = await _stores(trees=[root], chunks=[_chunk(f"{_MOD}.C")])
    out = await _step(ts, cs).run(
        _state(
            [
                _chunk(f"{_MOD}.C.a0", 0.9),
                _chunk(f"{_MOD}.C.b0", 0.8),
                _chunk(f"{_MOD}.C.a1", 0.7),
                _chunk(f"{_MOD}.C.b1", 0.6),
            ]
        )
    )
    # Both nodes trigger; merged: single emission at the lowest combined
    # index, exactly one fetch for the shared qname.
    assert _qnames(out) == [f"{_MOD}.C"]
    fetches = [c for c in cs.calls if c.method == "list"]
    assert len(fetches) == 1


@pytest.mark.asyncio
async def test_ac32_module_key_drift_is_a_pinned_noop() -> None:
    # Tree persisted under module _MOD; candidates carry a divergent module
    # override -> group (pkg, "pkg.mod.override") load misses -> kept.
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[_chunk(f"{_MOD}.C")])
    step = _step(ts, cs)
    state = _state(
        [
            _chunk(f"{_MOD}.C.m0", 0.9, module="pkg.mod.override"),
            _chunk(f"{_MOD}.C.m1", 0.8, module="pkg.mod.override"),
        ]
    )
    assert await step.run(state) is state


@pytest.mark.asyncio
async def test_ac33_cross_group_dedup_keeps_lowest_index_occurrence() -> None:
    # The same class indexed under two (package, module) groups with an
    # IDENTICAL (qualified_name, content_hash) pair: one group's methods
    # roll up; the other group's identical class chunk survives as a
    # candidate -> the text appears exactly once, at the lowest index.
    dup_parent_candidate = Chunk(
        text=f"text-{_MOD}.C",
        relevance=0.95,
        metadata={"package": _PKG, "module": "pkg.dual", "qualified_name": f"{_MOD}.C"},
    )
    # content_hash auto-computes over package+module+title+text (so the two
    # copies would differ on module alone); force identity by constructing
    # the row with the SAME content_hash — the dedup key is (qname, hash).
    parent_row = Chunk(
        text=f"text-{_MOD}.C",
        metadata={"package": _PKG, "module": _MOD, "qualified_name": f"{_MOD}.C"},
        content_hash=dup_parent_candidate.content_hash,
    )
    ts, cs = await _stores(trees=[_class_tree(4)], chunks=[parent_row])
    out = await _step(ts, cs).run(
        _state(
            [
                dup_parent_candidate,  # index 0 — survives (lowest occurrence)
                _chunk(f"{_MOD}.C.m0", 0.9),
                _chunk(f"{_MOD}.C.m1", 0.8),
            ]
        )
    )
    # The emitted parent (from the rollup at index 1) duplicates the
    # surviving candidate at index 0 -> dropped; text appears once.
    texts = [c.text for c in out.candidates.items]
    assert texts == [f"text-{_MOD}.C"]
    assert out.candidates.items[0].relevance == 0.95
```

- [ ] **Step 2: Run and fix divergences**

Run: `pytest tests/retrieval/steps/test_parent_rollup.py -q`
Expected: ALL PASS. Likely divergence to watch: AC33 requires the dedup key to use `content_hash` equality — if it fails, check `_dedup_against_parents` and the fixture's forced `content_hash` identity.

- [ ] **Step 3: Run the full suite**

Run: `pytest tests/ --ignore=tests/test_parity.py -q`
Expected: ALL PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/retrieval/steps/test_parent_rollup.py python/pydocs_mcp/retrieval/steps/parent_rollup.py
git commit -m "test(retrieval): parent_rollup claims machinery, cascade + dedup pins"
```

---

### Task 9: Blueprint loadability (AC34) + docs

**Files:**
- Test: `tests/retrieval/steps/test_parent_rollup.py`
- Modify: `CLAUDE.md`, `DOCUMENTATION.md`

- [ ] **Step 1: Write the AC34 test**

Append to `tests/retrieval/steps/test_parent_rollup.py`. Deliberate deviation from the spec's "exact §3.8 example": the blueprint here is the pipeline TAIL with **non-default** params — non-defaults are the only values that can detect the loader's silent params-drop (defaults would reappear anyway), and the full preset's `dense_fetcher` needs embedder/vector-store wiring that belongs to the dense steps' own tests (the `test_new_pipeline_presets_load.py` docstring pins that split):

```python
# ── AC34: blueprint loadability ──────────────────────────────────────────

_BLUEPRINT_YAML = """
name: chunk_search_rollup_tail
steps:
  - name: topk
    type: top_k_filter
    params:
      k: 50
  - name: rollup
    type: parent_rollup
    params:
      min_coverage: 0.4
      min_coverage_by_kind:
        class: 0.25
        module: 0.7
        markdown_heading: 0.45
  - name: limit
    type: limit
    params:
      max_results: 8
"""


def test_ac34_blueprint_params_reach_the_built_step() -> None:
    from pydocs_mcp.retrieval.pipeline.code_pipeline import CodeRetrieverPipeline

    pipeline = CodeRetrieverPipeline.from_dict(yaml.safe_load(_BLUEPRINT_YAML), _ctx())
    rollup = next(s for s in pipeline.stages if isinstance(s, ParentRollupStep))
    # Non-default values pin that nested `params:` (including the mapping)
    # reach the built step — the loader drops flat entry-level keys silently.
    assert rollup.min_coverage == 0.4
    assert dict(rollup.min_coverage_by_kind) == {
        "class": 0.25,
        "module": 0.7,
        "markdown_heading": 0.45,
    }
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/retrieval/steps/test_parent_rollup.py -q -k ac34`
Expected: PASS (if the loader wraps steps differently, inspect `pipeline.stages` and fix the test's step lookup — never the loader).

- [ ] **Step 3: Update CLAUDE.md**

In the architecture tree, the `steps/` line currently ends `..., graph_expand, centrality_prior, community_diversity`. Append `, parent_rollup`:

```
│   └── steps/       #   One file per step: chunk_fetcher, bm25_scorer, dense_fetcher, dense_scorer, member_fetcher, top_k_filter, metadata_post_filter, pre_filter, limit, token_budget, route, conditional, parallel, sub_pipeline (YAML decoder shim), rrf_fusion, weighted_score_interpolation, llm_tree_reasoning, late_interaction_scorer, graph_expand, centrality_prior, community_diversity, parent_rollup
```

- [ ] **Step 4: Update DOCUMENTATION.md**

Locate the rerank-steps bullet (grep `community_diversity` — the bullet ending "Add either step to a chunk pipeline YAML.", around line 89-92). Insert a new bullet directly after it:

````markdown
- **Parent rollup** (`parent_rollup` step in a chunk pipeline YAML) — when
  several results are children of one symbol or document section (e.g. three
  methods of the same class), the step replaces them with the parent's own
  indexed chunk at the group's best rank. Collapse requires at least two
  co-retrieved siblings AND a per-kind share of the parent's children, tuned
  via `min_coverage_by_kind` (defaults: `class: 0.3`, `module: 0.6`,
  `markdown_heading: 0.5`; a supplied mapping replaces the defaults wholesale)
  with the `min_coverage` fallback (0.5) for other kinds. Place it after
  `top_k_filter` and before `limit`:

  ```yaml
  - name: rollup
    type: parent_rollup
    params:
      min_coverage: 0.5
      min_coverage_by_kind:
        class: 0.3
        module: 0.6
        markdown_heading: 0.5
  ```
````

- [ ] **Step 5: Run doc conformance + full suite**

Run: `pytest tests/test_doc_conformance.py tests/retrieval/steps/test_parent_rollup.py -q`
Expected: ALL PASS (`parent_rollup` is a registered type name, so the DOCUMENTATION.md YAML snippet validates).

- [ ] **Step 6: Commit**

```bash
git add tests/retrieval/steps/test_parent_rollup.py CLAUDE.md DOCUMENTATION.md
git commit -m "docs: parent_rollup usage + blueprint loadability pin"
```

---

### Task 10: Benchmark A/B configs

**Files:**
- Create: `benchmarks/configs/pipelines/exp_parent_rollup.yaml`
- Create: `benchmarks/configs/pipelines/exp_parent_rollup_baseline.yaml`

- [ ] **Step 1: Create `benchmarks/configs/pipelines/exp_parent_rollup.yaml`**

```yaml
# Dense + graph_expand + centrality_prior + parent_rollup — measures the
# kind-aware sibling→parent rollup against its no-rollup twin
# (exp_parent_rollup_baseline.yaml). Per-kind sweep: copy this file and
# edit ONE mapping entry at a time; a supplied mapping replaces the default
# table wholesale, so restate the untouched kinds in every sweep point.
# Placement is normative: after top_k_filter, before limit.
name: exp_parent_rollup
steps:
  - name: pre_filter
    type: pre_filter
    params:
      schema_name: chunk
      target_field: chunk
  - name: fetch
    type: dense_fetcher
    params: {}
  - name: filter
    type: metadata_post_filter
    params: {}
  - name: graph
    type: graph_expand
    params:
      top_s: 10
      max_depth: 1
      decay: 0.9
  - name: centrality
    type: centrality_prior
    params:
      metric: pagerank
      alpha: 0.5
  - name: topk
    type: top_k_filter
    params: {}
  - name: rollup
    type: parent_rollup
    params:
      min_coverage: 0.5
      min_coverage_by_kind:
        class: 0.3
        module: 0.6
        markdown_heading: 0.5
  - name: limit
    type: limit
    params:
      max_results: 10
```

- [ ] **Step 2: Create `benchmarks/configs/pipelines/exp_parent_rollup_baseline.yaml`**

Byte-identical minus the rollup step:

```yaml
# No-rollup twin of exp_parent_rollup.yaml — identical pipeline minus the
# parent_rollup step, so any metric delta isolates the rollup itself.
name: exp_parent_rollup_baseline
steps:
  - name: pre_filter
    type: pre_filter
    params:
      schema_name: chunk
      target_field: chunk
  - name: fetch
    type: dense_fetcher
    params: {}
  - name: filter
    type: metadata_post_filter
    params: {}
  - name: graph
    type: graph_expand
    params:
      top_s: 10
      max_depth: 1
      decay: 0.9
  - name: centrality
    type: centrality_prior
    params:
      metric: pagerank
      alpha: 0.5
  - name: topk
    type: top_k_filter
    params: {}
  - name: limit
    type: limit
    params:
      max_results: 10
```

- [ ] **Step 3: Run the benchmarks-side local gate**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`
Expected: ALL PASS (the eval-suite discovers configs by glob; new configs must not break its loaders).

- [ ] **Step 4: Commit**

```bash
git add benchmarks/configs/pipelines/exp_parent_rollup.yaml benchmarks/configs/pipelines/exp_parent_rollup_baseline.yaml
git commit -m "bench: parent_rollup A/B pipeline configs (+ no-rollup baseline twin)"
```

---

### Task 11: Full CI gate sweep

No new code — run every gate from `.github/workflows/ci.yml` and fix anything red. Known traps (from project memory): local `complexipy` runs rewrite `complexipy-snapshot.json` in place — restore it from HEAD before staging anything; `pip-audit` requirement-mode SIGABRTs under the sandbox — use the frozen-venv form.

- [ ] **Step 1: Lint + format**

Run: `ruff check python/ tests/ benchmarks/ && ruff format --check python/ tests/ benchmarks/`
Expected: clean. If `ruff format --check` flags files, run `ruff format python/ tests/ benchmarks/` and re-check.

- [ ] **Step 2: Types**

Run: `mypy python/pydocs_mcp`
Expected: clean. Likely to surface: the `f.default_factory` access in `serialization.py` may need `# type: ignore[misc]` (dataclasses typeshed marks it `MISSING`-unioned) — mirror whatever the existing codebase does for similar accesses; and `_validated_coverage_mapping`'s `raw.items()` is safe behind the `isinstance(raw, Mapping)` guard.

- [ ] **Step 3: Complexity + dead code**

Run: `complexipy python/pydocs_mcp --max-complexity-allowed 15 && git checkout -- complexipy-snapshot.json 2>/dev/null; vulture python/pydocs_mcp --min-confidence 80`
Expected: clean. If `run()`/`_apply_group` exceed 15, extract another helper (e.g. move the fetch block into `_fetch_parent_chunk`).

- [ ] **Step 4: Tests + coverage**

Run: `pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90 -q`
Expected: PASS at ≥90%. The step suite covers every branch of `parent_rollup.py`; if a branch is missed, check `_dedup_against_parents`' seen-key path and `_reuse_in_list`'s None return.

- [ ] **Step 5: Lockfile + audit**

Run: `uv lock --check` (no dependency changes in this plan — must pass untouched).
Run: `.venv/bin/pip-audit --strict --local` (frozen-venv form; the requirement-file form SIGABRTs under the sandbox).
Expected: both clean.

- [ ] **Step 6: Benchmarks-side gate (again, final state)**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`
Expected: ALL PASS.

- [ ] **Step 7: Final commit (only if gates forced fixes)**

```bash
git status --short   # verify complexipy-snapshot.json is NOT modified
git add -u && git commit -m "chore: CI gate fixes for parent_rollup"
```

---

## Acceptance-criteria → task map (spec §5)

| ACs | Task |
|---|---|
| AC39 (codec extension) | 1 |
| AC22 (fake extensions) | 2 |
| AC1–AC6, AC21, AC23, AC36, AC37 | 3 |
| AC7–AC9, AC24 | 4 |
| AC11–AC13, AC15, AC18, AC20 | 5 |
| AC10, AC38, AC40 | 6 |
| AC14, AC16, AC25–AC28, AC35 | 7 |
| AC17, AC19, AC29–AC33 | 8 |
| AC34 + docs | 9 |
| Benchmark configs (spec §6) | 10 |
| CI gates (spec §5 tail) | 11 |

Out of scope (per spec): no shipped-preset change, no MCP surface change, no schema migration, no cascade, no `rolled_up_from` provenance metadata (spec Open Question Q1 — deferred by user decision).
