# Cross-repo reference linking for multi-repo workspaces

| Field    | Value                                                    |
|----------|----------------------------------------------------------|
| Version  | 0.1 (draft)                                              |
| Status   | Proposed                                                 |
| Date     | 2026-07-11                                               |
| Audience | Implementers + reviewers                                 |
| Component| `storage/`, `application/`, `multirepo.py`, `__main__.py`, `server.py`, YAML config |

## 1. Context & problem statement

When several repos are indexed one at a time and served together as a
workspace (`pydocs-mcp serve --workspace ~/bundles` or `--db a.db --db b.db`),
the reference graph never links them — even when repo A literally
`import repoB.mod` in its source. The user-visible symptom: `get_references`
on a symbol in repo B never shows its repo-A callers; `direction="impact"`
stops dead at the repo boundary; repo-A callees into repo B render as
`⚠ … (unresolved)`.

This is structural, not a bug in any one function. The full root-cause chain,
with evidence:

1. **Capture emits every reference unresolved.** `capture_imports` /
   `capture_calls` / `capture_inherits` construct
   `NodeReference(to_node_id=None, to_name=<absolute dotted name>)` —
   `extraction/strategies/references.py:128-136` (calls), `:156-184`
   (imports + alias table), `:203-211` (inherits). Resolution is a later,
   separate pass (`extraction/pipeline/stages/reference_capture.py:4-13`).

2. **The resolver's qname universe is bundle-local.**
   `IndexingService._resolve_references` builds its universe from
   `uow.packages.list(limit=10_000)` + `uow.trees.load_all_in_package(pkg.name)`
   — i.e. only packages persisted in *this* `.db` (project source plus its
   **installed** dependencies), optionally plus bundled stdlib qnames
   (`application/indexing_service.py:487-499`). A sibling repo indexed into
   its own bundle is never a package row here, so its qnames are absent.

3. **No resolver rule can reach another repo.** Rule B (exact universe match,
   `extraction/strategies/reference_resolver.py:140-141`) fails because the
   sibling's qname is not in the universe; Rules C/D
   (`reference_resolver.py:163-184`) are explicitly scoped to
   `q == ref.from_package or q.startswith(ref.from_package + ".")` — suffix
   matching *within the source package only*. Result: `to_node_id` stays
   `None` (Rule E, `reference_resolver.py:160-161`).

4. **The target "dies but is not lost."** The unresolved row is persisted with
   `NULL to_node_id` but its full dotted `to_name` survives on disk
   (`storage/sqlite/reference_store.py:43-52` UPSERT; queryable via
   `find_by_name`, `:84-103`). `storage/node_reference.py:3-6` documents this
   as deliberate: "stdlib refs, external packages not yet indexed, aliased
   re-exports" stay queryable by name. This persistence is the foothold the
   whole design below stands on.

5. **Uninstalled deps contribute nothing at all.** A dependency that is
   declared but not installed yields `dist is None` →
   `DependencyFileDiscoverer.discover` returns `([], Path())`
   (`extraction/strategies/discovery/dependency.py:33-35`;
   `extraction/strategies/_dep_helpers.py:64-78`). So "repos whose modules
   are not all installed"
   (the user's exact scenario) put zero qnames into any bundle's universe.

6. **The read side is blind to unresolved edges everywhere except callees
   and inherits.** `find_callers` matches `WHERE to_node_id = ?`
   (`storage/sqlite/reference_store.py:62-65`); `find_transitive_callers` /
   `find_transitive_callees` exclude NULL targets structurally
   (`:126-141`, `:164-179`); `find_governing` / `governed_by` require resolved
   targets (`:301-343`). Only `direction="callees"` (from-keyed,
   `find_callees`, `:69-82`) and `direction="inherits"` (name-keyed — the
   `_REF_GETTERS` dispatch routes it through
   `find_by_name(target, kind=INHERITS)`,
   `application/lookup_service.py:191-194`) surface cross-repo *intent*,
   rendered as
   `⚠ from → to_name (unresolved — to_name didn't match any indexed qname)`
   (`application/formatting.py:434-437`) — and even then only when the query
   runs against the bundle that OWNS the referencing row (the source repo),
   never from the target repo's side.

7. **Multi-repo serving performs no federation.** Each loaded bundle gets its
   own `ReferenceService` bound to exactly one `.db` via
   `build_sqlite_uow_factory(loaded.db_path)`
   (`storage/factories.py:145-148`). `MultiProjectLookup` routes
   `get_references` to exactly **one** project (`project=` selector, else
   single service, else recency-ordered first-that-resolves —
   `application/multi_project_search.py:234-250`, `:272-296`). There is no
   cross-bundle union for references and no `ATTACH` anywhere in
   `python/pydocs_mcp` (grep = 0 hits). A search union *does* already exist —
   but only for chunks/members (`_union_docs` / `_union_api`,
   `multi_project_search.py:195-218`), never for references.

8. **The existing late-arrival fixup is bundle-local.**
   `_reresolve_cross_package` → `uow.references.resolve_unresolved(new_qnames)`
   flips `to_node_id = to_name` for NULL rows **in the same `.db`**
   (`application/indexing_service.py:509-538`;
   `storage/sqlite/reference_store.py:209-231`). Reindexing repo B never
   repairs repo A's unresolved refs. Note this primitive is Rule-B-only
   ("this only implements Rule B (exact qname match)") — exactly the semantics
   a cross-bundle link pass needs.

9. **Nothing cross-repo exists yet.** `grep cross_repo python/` = 0 hits;
   `ReferenceGraphConfig` has exactly
   `capture/output/resolver/node_scores/similar_edges/impact/context`
   sub-models (`retrieval/config/models.py:209-227`); the CLI verb inventory
   is `{serve, index, watch, search, overview, symbol, context, refs, why,
   lookup}` (`__main__.py:2`) — no `link` verb.

10. **Constraint any design must respect: workspace bundles are read-only.**
    "A workspace/explicit-db load is READ-ONLY — the real source may be
    absent, so reindex/watch is disabled" (`multirepo.py:9-12`); `_cmd_serve`
    skips indexing and watch entirely for multi loads (`__main__.py:872-881`).
    Read-only is *policy*, not connection mode — but
    `retrieval/pipeline/connection.py:86-100` explicitly degrades the WAL
    pragma because bundles legitimately sit on **read-only filesystems** (CI
    images baking a pre-built index, shared `~/.pydocs-mcp` with restrictive
    permissions). Writing cross-edges into sibling bundles can therefore fail
    at the FS level, not just the policy level.

One favorable fact makes cross-bundle matching feasible **verbatim**:
project-source qnames use the real import name, not `__project__` —
`_module_from_path` anchors qnames at the discovered Python package root "so
the resulting qname matches `import pkg.mod`"
(`extraction/strategies/chunkers/ast_python.py:513-535`). So repo A's
unresolved `to_name = "repoB.mod.fn"` can exact-match repo B's persisted
`document_trees.qualified_name` rows with no rewriting.

## 2. Goals / Non-goals

### Goals

- G1 — When two or more bundles are served together and cross-repo linking is
  enabled, `get_references(direction="callers")` on a repo-B symbol includes
  its repo-A callers, clearly attributed to their owning project.
- G2 — `direction="impact"` crosses repo boundaries: the reverse BFS continues
  into sibling projects through cross-repo edges, still bounded by
  `reference_graph.impact.max_depth`.
- G3 — `direction="callees"` upgrades previously-unresolved rows to resolved,
  project-qualified targets when the target exists in a sibling bundle.
- G4 — The feature works when repo B's modules are **not installed** in repo
  A's environment (the user's scenario) — linking operates on persisted
  bundles, not on `importlib.metadata`.
- G5 — Zero MCP surface change. The six task-shaped tools and their pinned
  signatures are untouched; the feature is enabled/tuned exclusively via YAML
  (`reference_graph.cross_repo.*`), per CLAUDE.md §"MCP API surface vs YAML
  configuration".
- G6 — Zero bundle schema change. Bundles stay at `SCHEMA_VERSION = 14`
  (`db.py:18`); no `FutureSchemaError` events for older readers
  (`multirepo.py:31-76`), no rebuild triggers, bundles remain portable
  read-only artifacts.
- G7 — Deterministic staleness handling: reindexing one bundle invalidates and
  refreshes only the cross-edges that touch it.
- G8 — Graceful degradation on read-only filesystems: linking still works
  (in-memory), it just isn't persisted.

### Non-goals

- N1 — No workspace-wide PageRank / centrality. `node_scores` stays per-bundle
  (`application/indexing_service.py:587-599`; PK `(package, qualified_name)`,
  `db.py:99-106`). Cross-repo impact ranking uses hop distance as the primary
  key (see §3.7), so per-bundle scores remain valid *within-hop* tiebreakers.
  Workspace-level scores are a possible follow-up (Open question Q3).
- N2 — No cross-repo `SIMILAR` or `GOVERNS` edges in v1. Default linked kinds
  are `calls`/`imports`/`inherits` (`MENTIONS` opt-in via YAML).
- N3 — No alias-table persistence (bundle schema v15). Rule-A alias rewrites
  happen on a copy inside `_resolve_one`; the *original* `to_name` is what
  `save_many` persists for unresolved rows. v1 links on Rule-B exact matching
  of the persisted `to_name` and accepts the fidelity gap (Open question Q1).
- N4 — No resolution into repos that are *not* in the workspace. If a target
  lives in neither the local bundle nor any sibling bundle, it stays
  unresolved, exactly as today.
- N5 — No watch-triggered relinking. Watch is disabled in workspace mode by
  design (`__main__.py:872-881`); staleness is handled at serve startup and
  via the explicit `link` verb (§3.8).
- N6 — No writes into project bundles, ever (see rejected Alternative A′).
- N7 — No change to single-project serving. With one loaded bundle the feature
  is inert regardless of config.

## 3. Detailed design

### 3.0 Shape of the solution (one paragraph)

A **workspace-level link pass** resolves each bundle's persisted unresolved
references (Rule-B exact match on `to_name`) against the qname universes of
its *sibling* bundles, and stores the resulting cross-edges in a small
**overlay sidecar database** that lives next to the bundles — never inside
them. At serve time, a **`CrossLinkStore`** (Protocol; Null Object when
disabled) is unioned into the reference read path: callers/impact/callees
consult the overlay in addition to the bundle-local `node_references`. The
link pass runs automatically at serve startup when stamps say the overlay is
stale (`link_on_serve: true`), and can be pre-baked with a new operator CLI
verb `pydocs-mcp link`. When the overlay location is unwritable (EROFS), the
same pass runs in memory and feeds an `InMemoryCrossLinkStore` — identical
read semantics, no persistence.

### 3.1 The overlay sidecar

**Placement & naming.** `discover_workspace` globs `*.db` non-recursively
(`multirepo.py:145-156`), so the overlay MUST NOT match `*.db` or it would be
mis-loaded as a project bundle. The overlay is therefore named:

```
{workspace}/pydocs-links.sqlite3          # primary location
~/.pydocs-mcp/links/{md5(workspace_resolved)[:10]}.sqlite3   # fallback when
                                          # the workspace dir is unwritable
```

Lookup order at load time: workspace-local first, then the home fallback. The
`--db a.db --db b.db` mode (no workspace directory) uses the home fallback
keyed by the sorted tuple of resolved bundle paths. Defense in depth:
`discover_workspace` additionally gains an explicit exclusion for any file
named `pydocs-links.*`, so a future extension change can never regress into
bundle mis-loading.

**Schema** (independent of bundle `SCHEMA_VERSION`; versioned via its own
`PRAGMA user_version`, `_LINKS_SCHEMA_VERSION = 1` in the new module):

```sql
CREATE TABLE cross_references (
    from_project TEXT NOT NULL,   -- owning project of the source node
    from_package TEXT NOT NULL,   -- as persisted in the source bundle
    from_node_id TEXT NOT NULL,
    to_project   TEXT NOT NULL,   -- owning project of the resolved target
    to_node_id   TEXT NOT NULL,   -- resolved qname in the target bundle
    to_name      TEXT NOT NULL,   -- original unresolved name (audit/debug)
    kind         TEXT NOT NULL,   -- ReferenceKind value
    PRIMARY KEY (from_project, from_node_id, to_project, to_node_id, kind)
);
CREATE INDEX ix_xrefs_to   ON cross_references(to_project, to_node_id);
CREATE INDEX ix_xrefs_from ON cross_references(from_project, from_node_id);

CREATE TABLE linked_bundles (
    bundle_stem  TEXT PRIMARY KEY,  -- {name}_{slug} filename stem
    project_name TEXT NOT NULL,
    bundle_path  TEXT NOT NULL,
    indexed_at   REAL NOT NULL,     -- copied from bundle index_metadata
    git_head     TEXT,              -- copied from bundle index_metadata
    linked_at    REAL NOT NULL
);
```

Project identity comes from each bundle's stamped `index_metadata`
(`project_name`, `indexed_at`, `git_head` — `db.py:137-143`,
`storage/index_metadata.py:23-33`, `multirepo.py:113-142`), which is already
the multi-repo selection currency (`select_project`,
`multirepo.py:187-202`).

**A schema mismatch on the overlay is cheap:** unlike bundles (where an
unrecognized `user_version` triggers `_rebuild_from_scratch` dropping every
table and forcing a costly reindex — `db.py:3`), the overlay is a pure
derivative. On version mismatch we drop and relink; nothing of value is lost.

### 3.2 Data model & Protocol

New value object, mirroring `storage/node_reference.py`:

```python
# python/pydocs_mcp/storage/cross_link_edge.py
from dataclasses import dataclass
from pydocs_mcp.extraction.reference_kind import ReferenceKind

@dataclass(frozen=True, slots=True)
class CrossLinkEdge:
    """A resolved reference whose source and target live in DIFFERENT bundles.

    Unlike NodeReference, to_node_id is never None — an unresolved candidate
    simply isn't materialized as a cross-link. to_name keeps the original
    dotted name from the source bundle for auditability.
    """
    from_project: str
    from_package: str
    from_node_id: str
    to_project: str
    to_node_id: str
    to_name: str
    kind: ReferenceKind
```

New Protocol in `storage/protocols.py` (hexagonal seam — application code
depends on this, never on the concrete store, per CLAUDE.md §"SOLID
Principles" / §"Dependency Inversion"):

```python
@runtime_checkable
class CrossLinkStore(Protocol):
    """Read/write port for workspace-level cross-repo reference edges."""

    async def edges_into(
        self, to_project: str, to_node_id: str,
        *, kinds: tuple[ReferenceKind, ...] | None = None, limit: int = 200,
    ) -> tuple[CrossLinkEdge, ...]: ...

    async def edges_from(
        self, from_project: str, from_node_id: str,
        *, kinds: tuple[ReferenceKind, ...] | None = None, limit: int = 200,
    ) -> tuple[CrossLinkEdge, ...]: ...

    async def replace_edges_touching(
        self, project: str, edges: tuple[CrossLinkEdge, ...],
    ) -> None:
        """Atomically delete every edge where from_project=project OR
        to_project=project, then insert `edges`. The staleness unit (§3.8)."""

    async def bundle_stamps(self) -> tuple[LinkedBundleStamp, ...]: ...
    async def stamp_bundle(self, stamp: LinkedBundleStamp) -> None: ...
```

Three implementations, one file each (files stay small per house style):

- `storage/sqlite/cross_link_store.py` — `SqliteCrossLinkStore`, bound to the
  overlay path via its own `build_cross_link_store(path)` factory in
  `storage/factories.py`. It is a *single-file* store like the FTS read path,
  so it takes a connection provider, not a UoW — the overlay is outside the
  bundle `CompositeUnitOfWork` world by design (there is no multi-bundle UoW,
  `storage/factories.py:88-108`; the overlay's `replace_edges_touching` is
  its own transaction boundary).
- `storage/in_memory_cross_link_store.py` — `InMemoryCrossLinkStore`, dict
  over `(to_project, to_node_id)` / `(from_project, from_node_id)`. Used for
  EROFS degradation (§3.8) and as the test fake.
- `storage/null_cross_link_store.py` — `NullCrossLinkStore`: every read
  returns `()`, writes are silent no-ops. Wired whenever
  `reference_graph.cross_repo.enabled` is false or only one bundle is loaded.
  Per CLAUDE.md §"Null Object pattern": the field is typed `CrossLinkStore`,
  never `CrossLinkStore | None` — no `if x is not None:` guards downstream.
  Silent-empty (not raising) is the correct asymmetry here: like
  `NullVectorStore`, cross-links are *advisory enrichment* of an answer that
  is already valid bundle-locally; absence must not break `get_references`.

### 3.3 The link pass — `WorkspaceLinker`

New application service, `application/workspace_linker.py`:

```python
@dataclass(frozen=True, slots=True)
class WorkspaceLinker:
    """Resolves persisted unresolved references across sibling bundles.

    Not a UoW-factory service: it spans MULTIPLE bundles (one read-only
    uow_factory per bundle) plus the overlay store — there is no multi-bundle
    UnitOfWork and the overlay is its own transactional world (§3.1).
    """
    bundle_factories: Mapping[str, Callable[[], UnitOfWork]]  # project -> uow
    cross_links: CrossLinkStore
    config: CrossRepoConfig

    async def link(self, stale_projects: frozenset[str] | None = None) -> LinkReport: ...
```

(The CLAUDE.md §"Creating new application services" single-`uow_factory` rule
governs services over *one* bundle's persisted entities; `WorkspaceLinker` is
the documented exception-by-necessity, exactly like the retrieval pipelines'
`ConnectionProvider` carve-out — it must be called out in the service's
docstring and in the PR description.)

**Algorithm:**

1. **Export universes.** For each bundle, build
   `{qualified_name -> (project, package, is_project_source)}` from
   `document_trees` rows. With the default
   `match_scope: project_only`, only qnames whose package is `__project__`
   (`models.py:35`) are exported — first-party code. With
   `match_scope: all_packages`, dependency-package qnames are exported too
   (needed when repo A references repo B's *vendored* dep). Universe reads go
   through each bundle's read-only `uow_factory` (reads need no commit; the
   `__aexit__` rollback is a no-op — CLAUDE.md atomicity model).
2. **Collect unresolved refs.** Per bundle:
   `SELECT ... FROM node_references WHERE to_node_id IS NULL AND kind IN (?)`
   — a new read-only `ReferenceStore.list_unresolved(kinds, limit)` method
   (additive Protocol member + SQL over the existing `ix_refs_to_name`-adjacent
   columns; **no bundle schema change**, it reads the v14 table as-is). Kinds
   come from `cross_repo.kinds`. Relative-import refs need one caveat
   (capture persists no `level` — `references.py:156-184`): `from . import x`
   persists a bare `x`, which can never exact-match a sibling's dotted qname
   — harmless. But `from .sub import x` persists `sub.x`, which COULD
   falsely exact-match a sibling whose real top-level package happens to be
   named `sub`. v1 accepts this as a precision risk in the same class as Q1
   — it requires repo A's internal subpackage name to collide with a
   sibling's real top-level package name (rare), and Q1's measurement pass
   on real repo pairs should quantify it alongside alias fidelity. AC9 pins
   both shapes.
3. **Rule-B match against SIBLING universes only** (a bundle never links to
   itself — its own resolver already had that chance, and self-matches would
   duplicate bundle-local rows). Semantics deliberately mirror
   `resolve_unresolved` (`reference_store.py:209-231`): exact string equality
   of the persisted `to_name` against exported qnames.
4. **Collision precedence.** Two bundles can export the same qname (both
   index `requests`, or two repos ship a same-named top-level package). Order:
   (i) project-source (`is_project_source=True`) beats dependency copies —
   mirroring `_merge_ranked`'s root-beats-dependency rule
   (`multi_project_search.py:79-118`); (ii) ties break by bundle
   `indexed_at` recency (mirroring `select_project`,
   `multirepo.py:187-202`); (iii) exactly ONE edge is emitted per
   `(from, to_name, kind)` — never one edge per matching bundle.
5. **Write.** `replace_edges_touching(project, edges)` per source project,
   then `stamp_bundle(...)` with the bundle's current `indexed_at`/`git_head`.
6. **Report.** `LinkReport` (frozen dataclass): per-bundle counts of
   unresolved scanned / edges created / collisions resolved — printed by the
   CLI verb and logged (JSON fields) on serve.

Cost model: `O(sum(unresolved) + sum(universe))` with a single dict lookup
per unresolved row — no pairwise bundle × bundle scan. Universes for
`project_only` scope are the size of each repo's own tree (thousands, not
hundreds of thousands).

### 3.4 Read-path federation — `ReferenceService` + a workspace coordinator

Two layers change, matching the existing layering:

**(a) `application/reference_service.py`** gains one field:

```python
@dataclass(frozen=True, slots=True)
class ReferenceService:
    uow_factory: Callable[[], UnitOfWork]
    project_name: str = ""                      # "" in single-project mode
    cross_links: CrossLinkStore = field(default_factory=NullCrossLinkStore)
```

- `callers(package, qname)` (the existing 2-arg shape — `package` is
  informational, `reference_service.py:81-99`) → bundle-local `find_callers`
  ∪ `cross_links.edges_into(self.project_name, qname)`. Cross rows come back
  as `NodeReference`-shaped results tagged with their owning project (see
  rendering, §3.6).
- `callees(package, qname)` → unchanged local query; then for each
  *unresolved* local row, probe
  `cross_links.edges_from(self.project_name, from_node_id)` and substitute
  the resolved, project-qualified target where a cross-edge matches
  `(to_name, kind)`.
- `inherits` → the local read stays name-keyed (it routes through
  `find_by_name(target, kind=INHERITS)` via the `_REF_GETTERS` dispatch,
  `lookup_service.py:191-194`); the cross-repo union adds
  `cross_links.edges_into(self.project_name, target)` filtered to INHERITS —
  the same union shape as callers (INHERITS edges land in the overlay like
  any other kind).
- `find_by_name` semantics are untouched — its whole point is exposing
  unresolved rows (`reference_service.py:116-127`).

With `NullCrossLinkStore` every union degenerates to today's behavior — the
single-project path pays one no-op call, no branching.

**(b) A workspace coordinator for impact,**
`application/cross_repo_navigator.py`:

```python
@dataclass(frozen=True, slots=True)
class CrossRepoNavigator:
    """Hop-wise federation of impact BFS across bundles.

    Owns the map {project_name -> ReferenceService} plus the shared
    CrossLinkStore; single-bundle traversal stays inside each
    ReferenceService (recursive CTE, reference_store.py:105-145).
    """
    services: Mapping[str, ReferenceService]
    cross_links: CrossLinkStore
    max_projects_per_walk: int
```

Impact algorithm (bounded, deterministic):

1. Run the target project's local impact walk as today
   (`reference_service.py:148-185`), collecting `(qname, hop)` frontier sets
   per depth.
2. After each hop `h < max_depth`, batch-query
   `cross_links.edges_into(project, qname)` for the hop's frontier. Each hit
   seeds `(to_project=edge.from_project, qname=edge.from_node_id)` at hop
   `h+1` in the *source* project, whose own `ReferenceService` then continues
   the local CTE walk with the remaining depth budget.
3. Global visited-set keyed `(project, qname)` guarantees termination across
   cyclic cross-repo graphs; `max_projects_per_walk` caps fan-out.
4. Ranking: hop ascending first (unchanged — hop already dominates), then
   within a hop the owning bundle's `node_scores` (pagerank desc, in_degree
   desc), then `(project, qname)` ascending for cross-bundle determinism.
   Per-bundle scores are **not comparable across bundles**; because they are
   only ever compared within a hop *after* the project key, the ordering
   stays total and stable. This is the accepted N1 trade-off.

`MultiProjectLookup` (`multi_project_search.py:234-250`) keeps routing the
*target* exactly as today — `project=` selector, else single-service, else
recency-ordered resolution; `LookupService._symbol_lookup` still requires the
target's tree node in the routed bundle (`lookup_service.py:305-310`). The
only change: the routed `LookupService`'s `ref_svc` is now
cross-link-capable, and its impact getter delegates to the
`CrossRepoNavigator` when one is wired (Null Object:
`NullCrossRepoNavigator` returns the local walk unchanged).

### 3.5 Composition roots & config plumbing

Only the sanctioned composition roots change (`server.py`, `__main__.py`,
`storage/factories.py` — CLAUDE.md §"Creating new application services"):

- `storage/factories.py`: NEW `build_cross_link_store(overlay_path)` +
  `build_null_cross_link_store()`. There is no standalone
  reference-service factory today — `build_sqlite_lookup_service`
  (`storage/factories.py:126-165`) constructs the per-bundle
  `ReferenceService` inline; it grows optional `cross_links=` /
  `project_name=` kwargs and threads them into that constructor.
- `server.py::_resolve_projects` (`server.py:91-115`): after loading bundles,
  when `config.reference_graph.cross_repo.enabled` and `len(loaded) >= 2`:
  1. resolve overlay path (§3.1) → `SqliteCrossLinkStore`, or
     `InMemoryCrossLinkStore` if neither location is writable;
  2. staleness check + `WorkspaceLinker.link(...)` if `link_on_serve` (§3.8);
  3. wire `cross_links` + `project_name` into every per-bundle
     `ReferenceService`, build the `CrossRepoNavigator`, hand it to
     `MultiProjectLookup`.
  Otherwise: `NullCrossLinkStore` + `NullCrossRepoNavigator` everywhere.
- `__main__.py`: the same wiring for CLI `refs`/`why` queries in
  `--workspace`/`--db` mode, plus the new `link` verb (§3.9).

Config is installed via the existing `configure_from_app_config` startup hook
precedent (`server.py:192-194`,
`extraction/pipeline/stages/reference_capture.py:48-64`) where process-global
install is needed; the linker/navigator take the typed sub-model directly.

### 3.6 Rendering — project-qualified rows

`application/formatting.py::format_references` (`:418-453`) grows one
concept: an optional owning-project qualifier per row.

- Cross-repo rows render as
  `from_node_id (project: repoA) → to_node_id [kind]`; the summary line
  becomes `N references found (R resolved, U unresolved, X cross-repo)`.
- `from_package='__project__'` in cross rows is normalized to the owning
  project's real name before rendering — the same normalization
  `_dedup_identity` already performs for search results
  (`multi_project_search.py:79-118`). This resolves the ambiguity that
  `__project__` appears as `from_package` in *every* bundle.
- Formerly-unresolved callees that a cross-edge resolves drop the `⚠` marker
  and render as resolved with the `(project: …)` qualifier.
- Single-project mode and disabled mode render byte-identically to today
  (regression-tested, AC17).

### 3.7 MCP surface — nothing moves

Restating the contract because it is the constitution's hardest rule
(CLAUDE.md §"MCP API surface vs YAML configuration"; `server.py:1-9`):

- The six tools and their pinned signatures are untouched.
  `get_references(target, direction, project, limit)` keeps its exact
  `ReferencesInput` shape (`application/mcp_inputs.py:354-384`);
  `ToolRouter.get_references` keeps its thin remap
  (`tool_router.py:108-115`).
- `project=` remains a corpus-scope selector for the **target** (the second
  sanctioned param category). It does *not* scope the answer: callers from
  other projects are part of the truthful answer about that target. This
  mirrors how `search_codebase` without `project=` already unions bundles.
- Whether boundaries are crossed at all is a deployment property —
  `reference_graph.cross_repo.enabled` in YAML — precisely because it is
  A/B-testable against a benchmark (link precision/recall, impact usefulness)
  and therefore belongs in YAML by the litmus test.
- `limit` continues to bound the final merged list against
  `reference_graph.output.max_limit` (read at call time, as today).

### 3.8 Staleness handling

The staleness unit is **one bundle**; the invariant is: *an overlay edge may
only be trusted if both its endpoint bundles' stamps match the overlay's
recorded stamps.*

- **Detection.** At serve startup (and at the top of the `link` verb), for
  each loaded bundle compare `LoadedProject.indexed_at` (+ `git_head` when
  present) against the overlay's `linked_bundles` row. Missing row, mismatch,
  or a bundle present in the overlay but absent from the workspace ⇒ that
  project is *stale*.
- **Repair.** `WorkspaceLinker.link(stale_projects=...)` performs an
  incremental relink: for each stale project P,
  `replace_edges_touching(P, ...)` drops every edge where P is source OR
  target, then recomputes (i) P's unresolved refs against all sibling
  universes and (ii) every sibling's unresolved refs against P's fresh
  universe (siblings' unresolved lists are re-read; their universes are
  already loaded for (i)). Departed bundles get their edges dropped and their
  stamp deleted. A full relink (`stale_projects=None`) is the same code path
  with every project marked stale.
- **`link_on_serve: true` (default).** Startup does detection + repair before
  the server accepts requests. A no-change startup costs one stamp
  comparison per bundle (a few reads) — consistent with the package-level
  `<100ms` no-change reindex ethos.
- **`link_on_serve: false`.** Startup does detection only; stale projects'
  edges are **excluded from reads** (the store filters edges touching
  stale-stamped projects) and a warning names the fix
  (`pydocs-mcp link <workspace>`). Stale edges are never silently served —
  a dangling `to_node_id` into a reindexed bundle could point at a node that
  no longer exists.
- **EROFS degradation.** If the overlay cannot be opened read-write in either
  location, the linker runs against an `InMemoryCrossLinkStore` at startup
  (always fresh by construction, nothing persisted, one log line). CLI
  one-shot query commands in workspace mode skip in-memory linking (cost
  would be paid per invocation) and read whatever persisted overlay exists,
  applying the stale-exclusion rule — pre-baking via `link` is the supported
  path for read-only deployments.
- **Freshness surfacing.** The response-envelope freshness probe stays
  first-project-only ("Multi-repo per-project staleness is get_overview
  territory", `server.py:143-152`); `get_overview` in workspace mode appends
  a one-line cross-link status (`cross-repo links: fresh | stale(repoA) |
  disabled`). No envelope change.

### 3.9 CLI: the `link` verb

```
pydocs-mcp link --workspace ~/bundles        # full link pass + report
pydocs-mcp link --db a.db --db b.db          # explicit-bundle form
pydocs-mcp link --workspace ~/bundles --check  # detection only, exit 1 if stale
```

Rationale for a new verb (the CLI inventory is currently fixed at ten,
`__main__.py:2`, but unlike the MCP surface it is not constitutionally
frozen): `link` is an **operator action** — materialize/refresh a derived
artifact — not a tuning knob, so it does not violate the "A/B-testable ⇒
YAML" rule. It exists for exactly two operational needs: pre-baking overlays
into CI images / read-only deployments (§3.8), and CI freshness gating
(`--check`). All *behavioral* tuning (`kinds`, `match_scope`, …) still comes
from YAML via `AppConfig.load(...)`; the verb takes no tuning flags.

### 3.10 YAML config surface

New typed sub-model in `retrieval/config/models.py`, slotted into
`ReferenceGraphConfig` — whose docstring already anticipates this ("future
reference-graph tunables … get an obvious home",
`retrieval/config/models.py:209-227`). Single-source-of-truth constants per
CLAUDE.md §"Default values":

```python
# Single source of truth for cross-repo linking defaults (spec 2026-07-11).
_DEFAULT_CROSS_REPO_ENABLED = False
_DEFAULT_CROSS_REPO_LINK_ON_SERVE = True
_DEFAULT_CROSS_REPO_MATCH_SCOPE: Literal["project_only", "all_packages"] = "project_only"
_DEFAULT_CROSS_REPO_KINDS = ("calls", "imports", "inherits")
_DEFAULT_CROSS_REPO_MAX_PROJECTS_PER_WALK = 8

class CrossRepoConfig(BaseModel):
    """Workspace-level cross-repo reference linking (spec 2026-07-11).

    Server-side deployment tunables, NOT MCP parameters — get_references
    keeps its pinned six-tool-surface signature; enabling/tuning linking is
    a YAML-only concern (CLAUDE.md §"MCP API surface vs YAML configuration").
    """
    model_config = ConfigDict(extra="forbid")

    enabled: bool = _DEFAULT_CROSS_REPO_ENABLED
    link_on_serve: bool = _DEFAULT_CROSS_REPO_LINK_ON_SERVE
    match_scope: Literal["project_only", "all_packages"] = _DEFAULT_CROSS_REPO_MATCH_SCOPE
    kinds: tuple[str, ...] = _DEFAULT_CROSS_REPO_KINDS      # validated against ReferenceKind
    max_projects_per_walk: int = Field(
        _DEFAULT_CROSS_REPO_MAX_PROJECTS_PER_WALK, ge=1, le=32
    )
    overlay_dir: Path | None = None   # explicit overlay placement override


class ReferenceGraphConfig(BaseModel):
    ...
    cross_repo: CrossRepoConfig = Field(default_factory=CrossRepoConfig)
```

`defaults/default_config.yaml` (values restated for user-facing clarity —
the sanctioned YAML exemption):

```yaml
reference_graph:
  # ... existing keys unchanged ...
  cross_repo:
    enabled: false            # opt-in: link references across workspace bundles
    link_on_serve: true       # auto-refresh stale links at serve startup
    match_scope: project_only # project_only | all_packages
    kinds: [calls, imports, inherits]
    max_projects_per_walk: 8  # impact BFS cross-project fan-out cap
    # overlay_dir: /path      # optional: overrides sidecar placement (§3.1)
```

`extra="forbid"` on the sub-model (house convention) makes typo'd keys fail
fast at `AppConfig.load(...)`.

### 3.11 Module layout (new / changed files)

```
NEW  python/pydocs_mcp/storage/cross_link_edge.py          # CrossLinkEdge + LinkedBundleStamp value objects
NEW  python/pydocs_mcp/storage/sqlite/cross_link_store.py  # SqliteCrossLinkStore + overlay DDL + _LINKS_SCHEMA_VERSION
NEW  python/pydocs_mcp/storage/in_memory_cross_link_store.py
NEW  python/pydocs_mcp/storage/null_cross_link_store.py    # sibling of null_vector_store.py
NEW  python/pydocs_mcp/application/workspace_linker.py     # WorkspaceLinker + LinkReport
NEW  python/pydocs_mcp/application/cross_repo_navigator.py # CrossRepoNavigator + NullCrossRepoNavigator
CHG  python/pydocs_mcp/storage/protocols.py                # +CrossLinkStore Protocol; +ReferenceStore.list_unresolved
CHG  python/pydocs_mcp/storage/sqlite/reference_store.py   # +list_unresolved (read-only SQL, no schema change)
CHG  python/pydocs_mcp/storage/factories.py                # +build_cross_link_store; cross_links/project_name kwargs on build_sqlite_lookup_service
CHG  python/pydocs_mcp/application/reference_service.py    # +cross_links field, union reads
CHG  python/pydocs_mcp/application/lookup_service.py       # impact getter delegates to navigator when wired
CHG  python/pydocs_mcp/application/multi_project_search.py # carries navigator; no routing change
CHG  python/pydocs_mcp/application/formatting.py           # project-qualified rows + summary counts
CHG  python/pydocs_mcp/multirepo.py                        # overlay path resolution + pydocs-links.* exclusion
CHG  python/pydocs_mcp/retrieval/config/models.py          # CrossRepoConfig + ReferenceGraphConfig.cross_repo
CHG  python/pydocs_mcp/defaults/default_config.yaml        # cross_repo block
CHG  python/pydocs_mcp/server.py                           # composition-root wiring (§3.5)
CHG  python/pydocs_mcp/__main__.py                         # `link` verb + workspace-mode query wiring
CHG  python/pydocs_mcp/application/tool_docs.py            # get_references / get_overview doc text mentions cross-repo behavior when enabled
```

No changes to: `db.py` (bundle schema stays v14), `extraction/*` (capture and
the bundle-local resolver are untouched), `retrieval/steps/*`, the Rust
crate.

## 4. Alternatives considered

### Alternative A — index-time/workspace link pass persisted to an overlay sidecar (CHOSEN, with B as degradation)

Persist unresolved refs per bundle (already the case — nothing to add on the
write side), then a workspace-level link step resolves them across sibling
bundles into a separate overlay database; reads union bundle + overlay.

**Pros**

- Respects the read-only bundle policy (`multirepo.py:9-12`) and EROFS
  reality (`connection.py:86-100`) — bundles are never written.
- Zero bundle schema change: no `SCHEMA_VERSION` bump, no `FutureSchemaError`
  for older readers (`multirepo.py:31-76`), no rebuild trigger, bundles stay
  portable/distributable artifacts.
- Pay-once cost: link at startup / pre-bake, then reads are two indexed
  lookups (`ix_xrefs_to` / `ix_xrefs_from`). CLI one-shot queries get
  cross-links for free from the persisted overlay.
- Clean incremental staleness unit (`replace_edges_touching`), directly
  derived from the per-bundle `indexed_at`/`git_head` stamps that already
  exist (`index_metadata`, `db.py:137-143`).
- Builds on the proven Rule-B primitive (`resolve_unresolved`,
  `reference_store.py:209-231`) — same matching semantics, different scope.
- The overlay is disposable — worst case (corruption, version bump) is a
  relink, never a reindex.

**Cons**

- A new sidecar artifact with a naming/discovery convention to get right
  (the `*.db` glob trap, §3.1).
- Staleness protocol to implement and test (though small: stamp compare +
  targeted delete/reinsert).
- One new Protocol + three impls; a workspace-scope service that doesn't fit
  the single-`uow_factory` mold and needs an explicit carve-out.
- Cross-edges are invisible to per-bundle `node_scores` (accepted, N1).

### Alternative A′ — link pass that writes `to_node_id` back into source bundles (REJECTED sub-variant)

Flip `to_node_id` in place inside each bundle, as `resolve_unresolved` does
bundle-locally.

**Pros**

- No new store or read-path change — existing `find_callers` / CTEs would
  "just work"… in the wrong bundle (see first con).

**Cons (each individually disqualifying)**

- **Semantically wrong, not just risky:** callers of a repo-B symbol live in
  repo A's `node_references`; repo B's `find_callers` queries repo B's table
  and would *still* miss them. And a repo-A row whose `to_node_id` names a
  node absent from repo A's `document_trees` breaks the forward-hop
  assumption ("a forward hop needs a resolved target" *in this bundle*,
  `reference_store.py:126-141`) and 404s on follow-up lookups
  (`lookup_service.py:305-310`).
- Violates the declared read-only workspace policy (`multirepo.py:9-12`) and
  fails outright on read-only filesystems (`connection.py:86-100`).
- Destroys bundle portability: a bundle's content would depend on which
  workspace it last sat in; copying it elsewhere carries dangling foreign
  qnames.
- Staleness becomes untrackable: nothing distinguishes a cross-repo
  `to_node_id` from a local one, so undoing links after a sibling reindex
  means re-running the full bundle resolver.

### Alternative B — serve-time federated resolution in memory (no persistence)

`MultiProjectLookup` / `ReferenceService` compute cross-links lazily at serve
time: build sibling universes in memory, match unresolved rows on the fly.

**Pros**

- No sidecar, no staleness protocol — always exactly as fresh as the loaded
  bundles; nothing to invalidate.
- Least new surface: no overlay schema, no `link` verb.
- Read-only-filesystem-proof by construction.

**Cons**

- Startup pays the full `O(sum(unresolved) + sum(universe))` scan on *every*
  serve — and worse, CLI one-shot query commands (`pydocs-mcp refs …
  --workspace`) would pay it **per invocation**, which is exactly the
  workload the persisted-bundle architecture exists to avoid.
- Holds every sibling universe (or a full edge map) resident in memory for
  the server's lifetime, or re-derives per query.
- Nothing to pre-bake: a CI image or shared read-only deployment cannot ship
  warm links.
- No audit artifact: link decisions (collision precedence) are invisible,
  which hurts benchmark evaluation of link quality.

**Disposition:** not chosen as the primary design, but its machinery is the
EROFS degradation mode (§3.8) — `WorkspaceLinker` + `InMemoryCrossLinkStore`
*is* Alternative B, scoped to the case where persistence is impossible. The
two alternatives share all code except the store impl.

### Alternative C — one shared workspace `.db` instead of per-repo bundles

Index every repo into a single database; the existing bundle-local
cross-package fixup (`_reresolve_cross_package`) then resolves everything
with zero new code.

**Pros**

- Resolution is free: one qname universe, existing Rule B + late-arrival
  fixup already handle cross-"repo" (now just cross-package) edges.
- One `node_scores` computation covers the whole workspace (fixes N1).
- No federation layer, no overlay, no staleness protocol beyond what
  reindexing already does.

**Cons (structural, not incremental)**

- **Hard collision:** `packages` rows are keyed by name; two repos indexing
  the same third-party dependency at *different versions* cannot coexist —
  last writer silently wins, corrupting the loser's docs/graph. Likewise
  `__project__` (`models.py:35`) collides across repos, so the special
  project-package convention — and everything keyed on it (search dedup,
  overview, decision mining) — needs a schema redesign.
- Breaks the entire multi-repo contract: `{name}_{md5[:10]}.db` per-project
  caching (`db.py:163-172`), `discover_workspace`/`select_project`
  (`multirepo.py:145-202`), read-only distribution of independently-built
  bundles, and per-repo `content_hash` skip economics.
- Reindexing any one repo requires write access to the shared db —
  serializing all repos' index jobs on one file and killing the "index each
  repo where it lives, copy the bundle" workflow that read-only workspaces
  are built on.
- Migration burden for every existing deployment; a schema-version event of
  the largest possible kind.
- Doesn't even solve the user's core scenario better: uninstalled sibling
  deps still contribute no qnames unless each repo is indexed *into* the
  shared db — i.e. it mandates co-located sources, which workspaces
  explicitly do not assume (`multirepo.py:9-12`).

### Recommendation

**Alternative A** (overlay link pass) with **B as its EROFS degradation
mode**. A is the only option that simultaneously (i) fixes the user's
uninstalled-sibling scenario, (ii) honors the read-only bundle policy and
filesystem reality, (iii) leaves the bundle schema and MCP surface untouched,
and (iv) keeps one-shot CLI queries fast. C is architecturally simpler on
paper but breaks the multi-repo storage contract at its keys (`packages`
PK, `__project__`); A′ is semantically incorrect. The A+B pairing costs
almost nothing extra because both run the same `WorkspaceLinker` against
different `CrossLinkStore` impls.

## 5. Testing & acceptance criteria

Test fixtures: a `make_two_bundle_workspace()` helper in `tests/_fakes.py`
builds two real tmp-dir SQLite bundles (schema v14, minimal
`document_trees` + `node_references` + `index_metadata` rows) plus a tmp
workspace dir — real SQLite because the feature under test *is* the SQL.
Application-layer tests use `InMemoryCrossLinkStore` as the fake and the
existing `make_fake_uow_factory` per bundle. All tests headless via
`pytest -q`; coverage gate ≥90% applies (CI set in CLAUDE.md).

Numbered acceptance criteria — each independently checkable:

- **AC1** (`tests/test_cross_link_store.py`) — `SqliteCrossLinkStore` creates
  the overlay with `user_version = _LINKS_SCHEMA_VERSION`; `edges_into` /
  `edges_from` round-trip `CrossLinkEdge` tuples; `kinds` and `limit` filters
  apply.
- **AC2** (same file) — `replace_edges_touching("A", edges)` deletes every
  prior row where `from_project='A'` OR `to_project='A'` and inserts the new
  set atomically (a mid-write exception leaves the old rows intact).
- **AC3** (same file) — opening an overlay with a different `user_version`
  drops and recreates it (relink-not-migrate policy), leaving bundle files
  untouched.
- **AC4** (`tests/test_workspace_linker.py`) — given bundle A with an
  unresolved `to_name="repob.mod.fn"` (calls) and bundle B exporting
  `repob.mod.fn` under `__project__`, `link()` produces exactly one
  `CrossLinkEdge(from_project="repoA", …, to_project="repoB",
  to_node_id="repob.mod.fn", kind=calls)` — **with repo B's package not
  installed in the test environment** (G4).
- **AC5** — a bundle never links to itself: an unresolved name that matches
  only the *same* bundle's universe produces no edge.
- **AC6** — collision precedence: when both a sibling's `__project__` qname
  and another sibling's dependency-copy qname match, the project-source wins;
  two project-source matches tie-break by `indexed_at` recency; exactly one
  edge is emitted.
- **AC7** — `match_scope: project_only` (default) ignores sibling
  dependency-package qnames; `all_packages` links them.
- **AC8** — `kinds` filtering: with the default, `mentions` rows are never
  scanned or linked; adding `mentions` to YAML links them.
- **AC9** — relative-import refs, both shapes (§3.3 caveat): a bare
  `to_name` (`"x"` from `from . import x`) produces no cross-edges and no
  errors; a dotted relative `to_name` (`"sub.x"` from `from .sub import x`)
  links if and only if a sibling genuinely exports a top-level `sub`
  package — the documented precision trade-off, asserted in both directions.
- **AC10** (`tests/test_reference_store_unresolved.py`) —
  `ReferenceStore.list_unresolved(kinds, limit)` returns exactly the
  `to_node_id IS NULL` rows of the requested kinds from a v14 bundle; the
  bundle's `PRAGMA user_version` is unchanged after the call (read-only
  proof, G6).
- **AC11** (`tests/test_reference_service_cross.py`) — `callers(qname)` on the
  target project returns local callers ∪ overlay `edges_into` rows;
  with `NullCrossLinkStore` the result is byte-identical to today's.
- **AC12** — `callees` substitution: a local unresolved row with a matching
  overlay `edges_from` entry is returned resolved and project-qualified; a
  non-matching unresolved row still comes back unresolved.
- **AC13** (`tests/test_cross_repo_navigator.py`) — impact BFS crosses the
  boundary: target in B at hop 0, B-local caller at hop 1, A-side caller (via
  cross-edge) at hop 2; total depth respects
  `reference_graph.impact.max_depth`; a cross-repo cycle (A→B→A) terminates
  via the `(project, qname)` visited set; fan-out stops at
  `max_projects_per_walk`.
- **AC14** — impact ranking is deterministic: hop asc, then per-bundle
  (pagerank desc, in_degree desc), then `(project, qname)` asc; two runs
  produce identical order.
- **AC15** (`tests/test_multi_project_references.py`) — end-to-end through
  `MultiProjectLookup`: `get_references(target=<B symbol>,
  direction="callers")` with two loaded bundles and `enabled: true` includes
  the repo-A caller with a `(project: repoA)` qualifier; `project=` target
  routing (`_select_service` / recency fallback) is unchanged.
- **AC16** (`tests/test_formatting_cross_repo.py`) — rendering: cross rows
  carry the project qualifier, `__project__` is normalized to the owning
  project name, the summary reads `… (R resolved, U unresolved, X
  cross-repo)`, and formerly-unresolved-now-linked callees lose the `⚠`.
- **AC17** — regression: with `enabled: false` (default) OR a single loaded
  bundle, every `get_references` output is byte-identical to pre-feature
  behavior (golden comparison against current fixtures).
- **AC18** (`tests/test_cross_repo_staleness.py`) — after reindexing bundle A
  (bump its `index_metadata.indexed_at`), startup detection marks A stale;
  with `link_on_serve: true` relink refreshes exactly the A-touching edges
  (B↔C edges' rowids unchanged); with `link_on_serve: false` A-touching edges
  are excluded from reads and a warning is logged.
- **AC19** — a bundle removed from the workspace has its edges and stamp
  purged on the next link pass.
- **AC20** (`tests/test_cross_repo_erofs.py`) — with both overlay locations
  unwritable (chmod-simulated), serve startup falls back to
  `InMemoryCrossLinkStore`, serves correct cross-links, and writes nothing;
  CLI one-shot query commands skip in-memory linking and apply the
  stale-exclusion rule to any persisted overlay.
- **AC21** (`tests/test_cli_link.py`) — `pydocs-mcp link --workspace …`
  creates/refreshes the overlay and prints the `LinkReport`;
  `--check` exits 1 on staleness without writing; the overlay file never
  matches the `*.db` glob and `discover_workspace` never loads it as a
  project (including a planted `pydocs-links.db` decoy).
- **AC22** (`tests/test_config_cross_repo.py`) — `CrossRepoConfig` defaults
  match the `_DEFAULT_*` constants; unknown keys under `cross_repo:` raise at
  `AppConfig.load` (`extra="forbid"`); `kinds` rejects values outside
  `ReferenceKind`; YAML overlay → env-var layering works as for existing
  sub-models.
- **AC23** — MCP surface freeze: an assertion-style test that
  `ReferencesInput` field names and the registered tool list are unchanged
  (guards G5 mechanically).

Lint/type gates: all new files pass the full CI set (`ruff format --check`,
`ruff check`, `mypy`, `complexipy ≤15`, `vulture`, coverage ≥90%,
`uv lock --check` untouched — no new runtime deps).

## 6. Rollout / migration / back-compat

- **Default off.** `enabled: false` ships in `default_config.yaml`; zero
  behavior change for every existing deployment, single- or multi-repo.
- **No bundle migration.** Bundles stay at `SCHEMA_VERSION = 14`; bundles
  produced before this feature link exactly as well as new ones (the feature
  reads only long-standing v14 tables). Older pydocs-mcp versions reading a
  workspace that *has* an overlay simply ignore the unknown
  `pydocs-links.sqlite3` file (it doesn't match their `*.db` glob either).
- **Overlay lifecycle.** Created on first enabled serve/`link`; safe to
  delete at any time (next startup relinks); versioned independently via
  `PRAGMA user_version` with drop-and-relink on mismatch.
- **Dependencies.** None added — stdlib `sqlite3` + existing machinery. The
  default install stays at its current footprint; no new extra is needed
  (this is core-graph functionality, not an optional heavy stack).
- **Docs.** README multi-repo section gains a "cross-repo references"
  subsection (capability-and-file references only — no PR/task jargon, per
  CLAUDE.md §"README files"); `TOOL_DOCS` for `get_references`/`get_overview`
  mention project-qualified rows so MCP clients see accurate tool
  descriptions; `default_config.yaml` comments document each key.
- **Benchmark hook.** Because the toggle is YAML, the eval harness can A/B
  `cross_repo.enabled` / `match_scope` across `configs/*.yaml` with no client
  changes — the exact property the YAML rule exists to protect.
- **Suggested landing order** (each PR independently green):
  1. `CrossLinkEdge` + Protocol + three stores + overlay DDL (AC1-3);
  2. `ReferenceStore.list_unresolved` + `WorkspaceLinker` (AC4-10);
  3. read-path federation: `ReferenceService` union + `CrossRepoNavigator` +
     formatting (AC11-17, AC23);
  4. staleness + composition-root wiring + `link` verb + config + docs
     (AC18-22).

## 7. Open questions

- **Q1 — Alias fidelity (Rule A across bundles).** Rule-A alias rewrites
  happen on a copy inside `_resolve_one`; `save_many` persists the *original*
  `to_name` for unresolved rows, so a re-exported alias
  (`from repob.api import fn` where `fn` really lives at
  `repob.core.fn`) may exact-match nothing in the sibling universe. Options:
  accept the gap (v1 stance, N3); persist alias tables (bundle schema v15 —
  a `FutureSchemaError` event to schedule deliberately); or add a bounded
  dotted-suffix fallback rule to the linker (recall up, precision risk).
  Needs a measurement pass on real repo pairs before choosing.
- **Q2 — `MENTIONS` in the default kinds.** Doc-prose mentions crossing repos
  could be high-value for `get_why`/docs navigation but are noisier than AST
  edges. Default stays off; revisit with benchmark data.
- **Q3 — Workspace-level node scores.** Should a link pass optionally compute
  workspace PageRank over `bundle graphs ∪ overlay` and store it in the
  overlay (e.g. a `workspace_node_scores` table) for cross-repo impact
  ranking? Deferred until AC14's hop-first ordering proves insufficient in
  practice.
- **Q4 — Same-qname symbols in multiple repos.** If two repos both define
  `pkg.mod.fn` as project source, `get_references` target routing already
  disambiguates via `project=` / recency; the *linker* disambiguates via
  AC6 precedence — but should collisions be surfaced in the `LinkReport`
  loudly (they usually indicate a vendored fork)? Leaning yes, as a warning
  count.
- **Q5 — Per-request staleness checks.** v1 checks stamps at startup only; a
  long-running server whose bundles are replaced underneath it serves stale
  links until restart (matching existing behavior — bundles themselves go
  stale identically today, and watch is disabled in workspace mode). Is a
  cheap per-N-requests stamp probe worth it, or is this `get_overview`
  territory only?
- **Q6 — Local multi-serve (non-workspace) linking.** Should `pydocs-mcp
  serve .` with sibling projects indexed in `~/.pydocs-mcp` ever auto-link?
  v1 scopes linking strictly to explicit workspace/`--db` serving; expanding
  to the shared cache dir raises consent and blast-radius questions
  (which bundles are "one workspace"?).
