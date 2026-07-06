# Task-Shaped Surface, Part B (Slice 2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the slice-2a surface's rendering: centrality-ranked skeleton mode for `get_context` (spec §D6, with proportional multi-target budgets) and the real structural `get_overview` card (spec §D17 blocks 1, 3–7: stats, centrality-ranked module map, entry points, communities, dependency profile, doc coverage).

**Architecture:** Two independent tracks on `feature/task-shaped-surface`. Track A (§D6): `format_context` gains a skeleton mode that ranks full-body fidelity by `ContextNode.pagerank` (in-degree fallback, hop tie-break) under a `skeleton_body_ratio` budget share; `ToolRouter.get_context` moves from per-target `_lookup_body` calls to a two-phase gather-then-render so each card's budget share is proportional to its closure size. Track B (§D17): three bounded SQL aggregates on the reference/node-score stores + `[project.scripts]` parsing feed a new `OverviewService` (uow_factory pattern) whose `OverviewCard` value object renders through one new formatting function. Deliberate, documented reduction vs the spec: §D6's "import preamble" is unavailable at `ContextNode` granularity (nodes are symbols, not files) — skeleton cards render signature lines + central bodies only; the preamble joins when document trees carry it (noted in Out-of-scope).

**Tech Stack:** Python 3.11, sqlite3 aggregates, pydantic v2, pytest.

**Conventions:** identical to plan 2a (venv interpreter, ruff check+format before each commit, complexipy ≤ 15 for new functions then `git checkout complexipy-snapshot.json`, plain commits, no trailers).

**Shared code facts** (verified on 296fbed): `ContextNode(qualified_name, hop, pagerank, in_degree, source_text)` and `ReferenceService.context(package, qname, *, max_depth, limit) -> tuple[ContextNode, ...]` (reference_service.py:24-42, 168-211). `format_context(nodes, *, target, token_budget)` (formatting.py:372-414) renders hop-graded tiers via `_render_context_node` (356-369) and `_take_within_budget(..., inclusive_gate=True, on_elide=...)`. `LookupService` fields incl. `context_max_depth`/`context_token_budget` (lookup_service.py:201-229); its `show=="context"` branch resolves the node then calls `ref_svc.context` + `format_context` (310-329). `ToolRouter.get_context` currently loops `_lookup_body(LookupInput(show="context"))` per target (application/tool_router.py). `NodeScore(package, qualified_name, in_degree, pagerank, community)`; `SqliteNodeScoreRepository.scores_for(qnames)` (node_score_repository.py:49-67). `node_references` columns: from_package, from_node_id, to_name, to_node_id, kind (db.py:66-73); `ReferenceKind` includes CALLS/IMPORTS/INHERITS/MENTIONS. `DocumentTreeStore.load_all_modules(package) -> dict[str, DocumentNode]`. `PackageStore.list(filter, limit)`. `deps.py` parses pyproject deps but NOT `[project.scripts]` (grep-verified). `PROJECT_PACKAGE_NAME = "__project__"`. Fakes live in `tests/_fakes.py` (`make_fake_uow_factory`). Config: `reference_graph.context.{max_depth,token_budget}` (models.py `ContextConfig`:183-197). `overview`/`skeleton` config keys do not exist yet. Pointer grammar (2a): actions `lookup`, `lookup-show`, `search`, `overview`.

---

### Task 1: `[project.scripts]` parsing in `deps.py`

**Files:**
- Modify: `python/pydocs_mcp/deps.py`
- Test: `tests/test_deps_project_scripts.py` (create)

- [ ] **Step 1: Failing tests**

```python
"""[project.scripts] parsing for overview entry points (spec §D17 block 4)."""

from pydocs_mcp.deps import parse_project_scripts


def test_parses_scripts_table(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n\n'
        '[project.scripts]\ndemo-cli = "demo.__main__:main"\nother = "demo.app:run"\n'
    )
    assert parse_project_scripts(str(tmp_path / "pyproject.toml")) == {
        "demo-cli": "demo.__main__:main",
        "other": "demo.app:run",
    }


def test_missing_file_or_table_returns_empty(tmp_path) -> None:
    assert parse_project_scripts(str(tmp_path / "absent.toml")) == {}
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    assert parse_project_scripts(str(tmp_path / "pyproject.toml")) == {}


def test_malformed_toml_returns_empty(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("not [ valid")
    assert parse_project_scripts(str(tmp_path / "pyproject.toml")) == {}
```

- [ ] **Step 2:** Run — FAIL (ImportError).

- [ ] **Step 3: Implement** in `deps.py`, next to `parse_pyproject_dependencies` (line ~61), same tomllib style:

```python
def parse_project_scripts(path: str) -> dict[str, str]:
    """Console entry points from ``[project.scripts]`` — overview entry-point detector.

    Missing file / missing table / malformed TOML all return ``{}``: entry
    points are advisory card content, never a reason to fail an overview.
    """
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    scripts = data.get("project", {}).get("scripts", {})
    return {str(k): str(v) for k, v in scripts.items()} if isinstance(scripts, dict) else {}
```

- [ ] **Step 4:** Run new + existing deps tests (`pytest tests/test_deps_project_scripts.py tests/test_deps.py tests/test_deps_extended.py -q`) — PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/deps.py tests/test_deps_project_scripts.py
git commit -m "feat(deps): parse [project.scripts] for overview entry points"
```

---

### Task 2: Storage aggregates — reference degrees, import profile, community cohesion, per-package scores

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` (`ReferenceStore` + `NodeScoreStore` gain read methods)
- Modify: `python/pydocs_mcp/storage/sqlite/reference_store.py`, `python/pydocs_mcp/storage/sqlite/node_score_repository.py`
- Modify: `tests/_fakes.py` (fake stores implement the new methods)
- Test: `tests/storage/test_overview_aggregates.py` (create)

- [ ] **Step 1: Failing tests** — seed a real SQLite db via the existing storage-test fixtures (mirror `tests/storage/` conventions for opening a db and inserting `node_references` / `node_scores` rows), then:

```python
def test_degree_by_package_counts_in_and_out(seeded_conn) -> None:
    # rows: a->b CALLS, c->b CALLS, b->d CALLS  (all package "__project__")
    degrees = asyncio.run(store.degree_by_package("__project__"))
    assert degrees["b"] == (2, 1)      # (in_degree, out_degree)
    assert degrees["a"] == (0, 1)


def test_imports_grouped_by_target_package(seeded_conn) -> None:
    # IMPORTS edges: x->numpy.array, y->numpy.linalg.solve, z->pydantic.BaseModel
    profile = asyncio.run(store.imports_grouped_by_target("__project__"))
    assert profile == {"numpy": 2, "pydantic": 1}


def test_community_cohesion(seeded_conn) -> None:
    # node_scores: a,b community 1; c community 2. references: a->b (intra), a->c (cross)
    cohesion = asyncio.run(score_store.community_cohesion("__project__"))
    assert cohesion[1].size == 2
    assert cohesion[1].intra_edges == 1 and cohesion[1].cross_edges == 1


def test_scores_for_package_returns_all_rows(seeded_conn) -> None:
    rows = asyncio.run(score_store.for_package("__project__"))
    assert {r.qualified_name for r in rows} == {"a", "b", "c"}
```

- [ ] **Step 2:** Run — FAIL (methods missing).

- [ ] **Step 3: Implement**

(a) Protocol additions (protocols.py, inside `ReferenceStore` after `find_transitive_callees`):

```python
    async def degree_by_package(self, package: str) -> dict[str, tuple[int, int]]:
        """(in_degree, out_degree) per resolved qname — overview ranking fallback
        and entry-point root detection (spec §D17 blocks 3-4)."""
        ...
```

and inside `NodeScoreStore`:

```python
    async def for_package(self, package: str) -> list[NodeScore]:
        """All score rows of one package — overview module map + communities."""
        ...

    async def community_cohesion(self, package: str) -> dict[int, CommunityCohesion]:
        """Per-community size + intra/cross edge counts (one bounded SQL, §D17 block 5)."""
        ...
```

with a `CommunityCohesion` frozen value object `(community: int, size: int, intra_edges: int, cross_edges: int)` in `storage/node_score.py`. `imports_grouped_by_target` goes on `ReferenceStore`:

```python
    async def imports_grouped_by_target(self, package: str) -> dict[str, int]:
        """IMPORTS edge counts grouped by the target's top-level package (§D17 block 6)."""
        ...
```

(b) SQLite implementations — each one bounded SQL via the module's existing `asyncio.to_thread` connection idiom (mirror `find_transitive_callers`'s shape):

```sql
-- degree_by_package (two grouped scans unioned in Python):
SELECT to_node_id AS q, COUNT(*) FROM node_references
  WHERE from_package = ? AND to_node_id IS NOT NULL GROUP BY to_node_id;
SELECT from_node_id AS q, COUNT(*) FROM node_references
  WHERE from_package = ? GROUP BY from_node_id;

-- imports_grouped_by_target (top-level segment split done in Python):
SELECT to_name, COUNT(*) FROM node_references
  WHERE from_package = ? AND kind = 'imports' GROUP BY to_name;

-- community_cohesion (sizes from node_scores; edge partition via one join):
SELECT s1.community,
       SUM(CASE WHEN s2.community = s1.community THEN 1 ELSE 0 END),
       SUM(CASE WHEN s2.community != s1.community THEN 1 ELSE 0 END)
FROM node_references r
JOIN node_scores s1 ON s1.package = r.from_package AND s1.qualified_name = r.from_node_id
JOIN node_scores s2 ON s2.package = r.from_package AND s2.qualified_name = r.to_node_id
WHERE r.from_package = ? AND r.to_node_id IS NOT NULL
GROUP BY s1.community;
```

(for `imports_grouped_by_target`, aggregate `to_name.split(".")[0]` counts in Python after the grouped fetch; exclude targets whose top segment equals the package itself). `for_package`: `SELECT ... FROM node_scores WHERE package = ?` reusing the row mapper at node_score_repository.py:92.

(c) `tests/_fakes.py`: the fake reference/node-score stores implement the same methods over their in-memory rows (pure-Python equivalents, same return shapes).

- [ ] **Step 4:** Run `pytest tests/storage/test_overview_aggregates.py tests/storage/ -q` — PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/ tests/
git commit -m "feat(storage): overview aggregates — degrees, import profile, community cohesion"
```

---

### Task 3: `overview:` config block

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config/models.py`, `python/pydocs_mcp/defaults/default_config.yaml`
- Test: extend `tests/test_config_output_block.py` sibling-style (`tests/test_config_overview_block.py`, create)

- [ ] Steps follow Task 1 of plan 2a exactly (failing test → models → YAML → green → commit). Models:

```python
class OverviewConfig(BaseModel):
    """get_overview card caps (spec §D17) — list caps keep the card inside token budgets."""

    max_modules: int = Field(20, ge=1, le=200)
    max_communities: int = Field(10, ge=1, le=50)
```

`AppConfig` gains `overview: OverviewConfig = OverviewConfig()`; YAML:

```yaml
overview:                     # get_overview card caps (spec §D17)
  max_modules: 20
  max_communities: 10
```

(`git_activity` / `llm_summary` land with slice 3 — do NOT add them here.)

```bash
git add python/pydocs_mcp/retrieval/config/models.py python/pydocs_mcp/defaults/default_config.yaml tests/test_config_overview_block.py
git commit -m "feat(config): overview card caps"
```

---

### Task 4: `OverviewService` + `OverviewCard`

**Files:**
- Create: `python/pydocs_mcp/application/overview_service.py`
- Test: `tests/application/test_overview_service.py` (create)

- [ ] **Step 1: Failing tests** — build fakes via `make_fake_uow_factory` seeded with: 2 packages (`__project__`, `numpy`), 3 project modules in trees, members with/without docstrings, node_scores rows (2 communities), reference rows (CALLS + IMPORTS). Assert on the returned `OverviewCard` value object, not rendered text:

```python
def test_card_stats_and_module_map_ranked_by_pagerank(...) -> None:
    card = asyncio.run(service.build(package="__project__"))
    assert card.package_count == 2
    assert [m.qualified_name for m in card.modules][:2] == ["proj.core", "proj.api"]  # pagerank order
    assert card.doc_coverage == pytest.approx(2 / 3)


def test_module_map_falls_back_to_in_degree_without_scores(...) -> None:
    # node_scores empty → ranking uses degree_by_package in-degree
    ...


def test_entry_points_union_scripts_dunder_main_and_roots(...) -> None:
    card = asyncio.run(service.build(package="__project__"))
    kinds = {(e.name, e.kind) for e in card.entry_points}
    assert ("demo-cli", "script") in kinds
    assert ("proj.__main__", "module") in kinds
    assert ("proj.cli.main", "root") in kinds        # zero in-degree, out-degree above card median
    assert all("test" not in e.name for e in card.entry_points)


def test_communities_labeled_by_shared_prefix_with_cohesion(...) -> None:
    card = asyncio.run(service.build(package="__project__"))
    top = card.communities[0]
    assert top.label == "proj.core" and top.size == 2 and 0.0 <= top.cohesion <= 1.0


def test_dependency_profile_from_imports(...) -> None:
    assert card.dependency_profile[0] == ("numpy", 2)


def test_caps_respected(...) -> None:
    service = OverviewService(uow_factory=..., max_modules=1, max_communities=1, scripts={})
    card = asyncio.run(service.build(package="__project__"))
    assert len(card.modules) == 1 and len(card.communities) == 1
```

- [ ] **Step 2:** Run — FAIL.

- [ ] **Step 3: Implement** `python/pydocs_mcp/application/overview_service.py`:

```python
"""OverviewService — the §D17 structural orientation card (blocks 1, 3-7).

uow_factory service (CLAUDE.md contract). Every block reads data the index
already holds; centrality ranking uses node_scores.pagerank and degrades to
the degree_by_package in-degree proxy — the SAME rule §D6/§D11 use, one
degradation strategy across features. Blocks 2/8/9 (LLM summary, decisions,
git activity) land with the decision layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.storage.protocols import UnitOfWork

_DEFAULT_MAX_MODULES = 20
_DEFAULT_MAX_COMMUNITIES = 10
_TEST_PATH_MARKERS = ("test", "conftest")


@dataclass(frozen=True, slots=True)
class ModuleEntry:
    qualified_name: str
    first_doc_line: str
    rank_score: float


@dataclass(frozen=True, slots=True)
class EntryPoint:
    name: str
    kind: str  # script | module | root


@dataclass(frozen=True, slots=True)
class CommunityEntry:
    label: str
    size: int
    cohesion: float
    top_member: str


@dataclass(frozen=True, slots=True)
class OverviewCard:
    package: str
    package_count: int
    module_count: int
    symbol_count: int
    doc_coverage: float                       # 0..1, members with docstrings
    modules: tuple[ModuleEntry, ...]
    entry_points: tuple[EntryPoint, ...]
    communities: tuple[CommunityEntry, ...]   # empty + hint when node_scores off
    dependency_profile: tuple[tuple[str, int], ...]
    node_scores_available: bool


@dataclass(frozen=True, slots=True)
class OverviewService:
    uow_factory: Callable[[], UnitOfWork]
    scripts: dict[str, str]                   # [project.scripts], parsed at composition
    max_modules: int = _DEFAULT_MAX_MODULES
    max_communities: int = _DEFAULT_MAX_COMMUNITIES

    async def build(self, package: str = "") -> OverviewCard:
        target = package or PROJECT_PACKAGE_NAME
        async with self.uow_factory() as uow:
            packages = await uow.packages.list()
            trees = await uow.trees.load_all_modules(target)
            members = await uow.module_members.list(filter={"package": target})
            scores = await uow.node_scores.for_package(target)
            degrees = await uow.references.degree_by_package(target)
            imports = await uow.references.imports_grouped_by_target(target)
            cohesion = (
                await uow.node_scores.community_cohesion(target) if scores else {}
            )
        return self._assemble(
            target, packages, trees, members, scores, degrees, imports, cohesion
        )
```

`_assemble` (plus small pure helpers, each ≤15 complexity) implements:

- **module map**: candidates = tree module names; `rank_score` = pagerank from `scores` when available else `in_degree` from `degrees` (module-level qnames; missing → 0.0); sort desc, tie-break name; cap `max_modules`; `first_doc_line` = first non-empty line of the module tree node's docstring/text field (adapt to the actual `DocumentNode` field — check `extraction/model`'s DocumentNode: use its docstring-ish attribute; if none exists, fall back to "").
- **doc coverage**: members with a non-empty `docstring` metadata value / max(1, len(members)).
- **entry points**: `scripts` items → `EntryPoint(name, "script")`; tree modules ending `.__main__` → `"module"`; qnames in `degrees` with `in_degree == 0` and `out_degree` strictly above the median out-degree of the card's candidates → `"root"` (cap 5, exclude any qname containing a `_TEST_PATH_MARKERS` segment).
- **communities**: group `scores` by `community` (skip -1); size from group; label = longest shared dotted prefix of member qnames (fall back to top member's module); `top_member` = highest pagerank; cohesion = `intra / max(1, intra + cross)` from the cohesion map; sort by size desc, cap `max_communities`.
- **dependency profile**: `imports` sorted by count desc, top 10.
- `node_scores_available = bool(scores)`; when False, `communities=()`.

- [ ] **Step 4:** Run `pytest tests/application/test_overview_service.py -q` — PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/overview_service.py tests/application/test_overview_service.py
git commit -m "feat(overview): OverviewService — structural orientation card data"
```

---

### Task 5: Render the card + wire `ToolRouter.get_overview`

**Files:**
- Modify: `python/pydocs_mcp/application/formatting.py` (`format_overview_card`)
- Modify: `python/pydocs_mcp/application/tool_router.py` (`get_overview` uses the service)
- Modify: `python/pydocs_mcp/application/multi_project_search.py` (`ProjectServices` gains `overview: OverviewService`)
- Modify: `python/pydocs_mcp/server.py` `_build_project_services` + `storage/factories.py` (construct `OverviewService` with `parse_project_scripts(project_root/"pyproject.toml")` + config caps)
- Test: `tests/application/test_format_overview.py` (create) + update `test_tool_router.py`'s overview expectations

- [ ] **Step 1: Failing golden test** (`format_overview_card`):

```python
def test_golden_card_layout() -> None:
    out = format_overview_card(_card_fixture())
    assert out.startswith("# Overview — __project__\n")
    assert "[2 packages · 3 modules · 12 symbols · 67% documented]" in out
    assert "## Module map" in out and "[[next:lookup-show:proj.core:context]]" in out
    assert "## Entry points" in out and "[[next:lookup:proj.__main__]]" in out
    assert "## Structure communities" in out and "cohesion 0.50" in out
    assert "## Dependency profile" in out and "numpy (2 imports)" in out


def test_communities_hint_when_scores_disabled() -> None:
    out = format_overview_card(_card_fixture(node_scores_available=False))
    assert "enable reference_graph.node_scores" in out
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** `format_overview_card(card: OverviewCard) -> str` in formatting.py — pure rendering: H1, one stats line (`doc_coverage` as integer percent), the four H2 blocks in §D17 order, each entry ending with the appropriate pointer token (`lookup-show:<module>:context` for modules — resolves to `get_context`/`pydocs-mcp context` via the 2a table; `lookup:<name>` for entry points; dependency profile entries get `lookup:<pkg>`); communities block replaced by the enablement hint line when `node_scores_available` is False. `ToolRouter.get_overview` becomes:

```python
    async def get_overview(self, payload: OverviewInput) -> str:
        svc = self._svc(payload.project)
        return await self.envelope.wrap(
            lambda: _render_overview(svc.overview, payload.package)
        )
```

with a module-level `async def _render_overview(service, package): return format_overview_card(await service.build(package))`. Composition roots construct `OverviewService(uow_factory=..., scripts=parse_project_scripts(str(root / "pyproject.toml")), max_modules=cfg.overview.max_modules, max_communities=cfg.overview.max_communities)`.

- [ ] **Step 4:** Run overview + router + CLI overview tests; update 2a's `test_overview_lists_packages` / CLI `test_overview_subcommand` to the new card assertions. PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/ tests/
git commit -m "feat(overview): structural card rendering wired into get_overview"
```

---

### Task 6: Skeleton rendering for context cards (§D6)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config/models.py` (`ContextConfig` gains `render`, `skeleton_body_ratio`) + `defaults/default_config.yaml`
- Modify: `python/pydocs_mcp/application/formatting.py` (`format_context` skeleton mode)
- Modify: `python/pydocs_mcp/application/lookup_service.py` (fields + pass-through)
- Test: `tests/application/test_format_context_skeleton.py` (create)

- [ ] **Step 1: Failing tests**

```python
def _node(qname, hop, pagerank=0.0, in_degree=0, body="def f():\n    return 1\n"):
    return ContextNode(qualified_name=qname, hop=hop, pagerank=pagerank,
                       in_degree=in_degree, source_text=body)


def test_skeleton_gives_full_bodies_to_most_central_only() -> None:
    nodes = (
        _node("seed", 0, pagerank=0.9),
        _node("hot", 1, pagerank=0.8, body="def hot():\n    return 'big'\n" * 3),
        _node("cold", 1, pagerank=0.1, body="def cold():\n    return 'big'\n" * 3),
    )
    out = format_context(nodes, target="seed", token_budget=200,
                         render="skeleton", body_ratio=0.5)
    assert "return 'big'" in out.split("cold")[0]      # hot's body rendered
    assert "def cold():" in out                        # cold: signature line only
    assert out.count("return 'big'") < 6               # cold's body NOT rendered


def test_in_degree_breaks_ties_when_pagerank_absent() -> None:
    nodes = (_node("seed", 0), _node("a", 1, in_degree=9), _node("b", 1, in_degree=1))
    out = format_context(nodes, target="seed", token_budget=200,
                         render="skeleton", body_ratio=0.4)
    assert out.index("a") < out.index("b")


def test_render_full_preserves_hop_graded_bytes() -> None:
    nodes = (_node("seed", 0), _node("x", 1))
    legacy = format_context(nodes, target="seed", token_budget=500)
    explicit = format_context(nodes, target="seed", token_budget=500, render="full")
    assert legacy == explicit
```

- [ ] **Step 2:** FAIL (unexpected kwargs). **Step 3: Implement:**

- `format_context` signature becomes `(nodes, *, target, token_budget, render: str = "full", body_ratio: float = _DEFAULT_SKELETON_BODY_RATIO)` with `_DEFAULT_SKELETON_BODY_RATIO = 0.35`. `render="full"` path is byte-identical to today (the existing `_render_context_node` tiering). Skeleton path: every node renders its signature line (+ first docstring line when the second source line is a docstring); additionally, nodes are ranked by `(pagerank if any node has pagerank else in_degree, -hop)` desc and full bodies are appended for the top-ranked nodes while their cumulative rendered length stays within `body_ratio * token_budget * _CHARS_PER_TOKEN`; the whole card still flows through `_take_within_budget(..., on_elide=...)` with the existing recovery entry, and every body elision keeps a `lookup-show:<qname>:source` recovery pointer line (resolves to `get_symbol(..., depth="source")`).
- `ContextConfig` gains `render: Literal["skeleton", "full"] = "skeleton"` and `skeleton_body_ratio: float = Field(0.35, gt=0.0, le=1.0)`; YAML block `reference_graph.context` gains both keys (skeleton is the new default per §D6). `LookupService` gains `context_render: str` / `context_body_ratio: float` fields wired from config in `storage/factories.py` (same path as `context_token_budget`, Explore-verified factories lines ~98-100) and passes them to `format_context`.
- Keep each new helper ≤ 15 cognitive complexity (rank + budget-select as separate pure functions).

- [ ] **Step 4:** Run `pytest tests/application/test_format_context_skeleton.py tests/application/test_format_context.py tests/application/test_truncation_recording.py -q` — PASS (the legacy-bytes test pins `render="full"` equivalence; existing format_context tests may need `render="full"` pinned where they assert full-source bytes — prefer updating them to pass `render="full"` explicitly over weakening assertions).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/ tests/
git commit -m "feat(context): centrality-ranked skeleton rendering (reference_graph.context.render)"
```

---

### Task 7: Proportional multi-target budgets in `get_context`

**Files:**
- Modify: `python/pydocs_mcp/application/lookup_service.py` (expose `context_card(target, *, token_budget) -> str`)
- Modify: `python/pydocs_mcp/application/tool_router.py` (`get_context` two-phase)
- Test: `tests/application/test_context_budget_split.py` (create)

- [ ] **Step 1: Failing tests**

```python
def test_budget_split_proportional_to_closure_size() -> None:
    # target A resolves to a 9-node closure, target B to a 1-node closure;
    # global budget 1000 tokens → A's card visibly longer than B's, and the
    # combined output stays within ~1000*4 chars + headers.
    out = asyncio.run(router.get_context(ContextInput(targets=["pkg.A", "pkg.B"])))
    card_a, card_b = out.split("# Context for `pkg.B`")
    assert len(card_a) > 3 * len(card_b)


def test_single_target_uses_full_budget() -> None:
    solo = asyncio.run(router.get_context(ContextInput(targets=["pkg.A"])))
    pair = asyncio.run(router.get_context(ContextInput(targets=["pkg.A", "pkg.B"])))
    assert len(solo.split("# Context for")[1]) >= len(pair.split("# Context for")[1])


def test_minimum_share_floor() -> None:
    # every card gets at least 10% of the global budget regardless of size skew
    ...  # assert B's card is non-trivial (contains its focus block)
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:**

- `LookupService` gains two methods extracted from the `show=="context"` branch (310-329): `async def context_nodes(self, target: str) -> tuple[str, tuple[ContextNode, ...]]` (resolve target → `ref_svc.context(...)`, returning the display target + nodes; NotFound propagates unchanged) and `def render_context_card(self, target: str, nodes, *, token_budget: int) -> str` (calls `format_context` with the service's render/ratio fields). The existing `show=="context"` branch now composes these two with `self.context_token_budget` — behavior unchanged (existing context tests must stay green untouched).
- `ToolRouter.get_context` two-phase: phase 1 gathers `(target, nodes)` for every target via the project-routed `LookupService` (`self._svc(payload.project).lookup` is the per-project `LookupService`; for project="" multi-db routing reuse `_select_service`/recency order exactly as `MultiProjectLookup._lookup_body` does — extract that resolution loop into a small shared helper on `MultiProjectLookup` rather than duplicating); phase 2 splits the global `context_token_budget`: `share_i = max(floor, total * size_i / Σsizes)` with `floor = total // (10 * len(targets))`... use `_MIN_SHARE_RATIO = 0.10`: `floor = int(total * _MIN_SHARE_RATIO)`; render each card via `render_context_card(..., token_budget=share_i)` and join with `"\n\n"` inside one `envelope.wrap`.

- [ ] **Step 4:** Run context suites + tool-router suite — PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/ tests/
git commit -m "feat(context): proportional per-card budget split for multi-target get_context"
```

---

### Task 8: Full gates + live smoke

- [ ] **Step 1:** Full suite, benchmarks suite, ruff check + format --check, mypy, coverage ≥ 90, complexipy on changed files (then restore snapshot) — same commands as plan 2a Task 12; pre-existing benchmarks-scripts/fast_plaid noise remains excepted.
- [ ] **Step 2:** Live smoke on the scratch project: `pydocs-mcp overview --project-dir <scratch>` shows the stats line + module map with pointers; `pydocs-mcp context app.greet app --project-dir <scratch>` renders two cards; skeleton default visible on a target with a multi-node closure. Commit fixups as `fix(slice2b): gate fixups`.

---

## Out of scope (explicit)

- §D6 import-preamble line in skeleton cards — `ContextNode` is symbol-granular; the preamble joins when document trees expose per-module import blocks (record as a spec deviation note in the PR body).
- §D17 blocks 2, 8, 9 (LLM architecture summary, decisions summary, git activity) — slice 3.
- `overview.git_activity` / `overview.llm_summary` config keys — slice 3.
- Anything decision-related, SWE-QA (slices 3–5).
