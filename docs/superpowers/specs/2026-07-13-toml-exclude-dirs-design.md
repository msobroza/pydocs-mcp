# Per-project directory exclusion from indexation (`[tool.pydocs-mcp] exclude_dirs`)

| Field    | Value                                            |
|----------|--------------------------------------------------|
| Version  | 0.1 (draft)                                      |
| Status   | Approved (design), pending implementation plan   |
| Date     | 2026-07-13                                       |
| Audience | Implementers + reviewers                         |

**One-line summary:** let users exclude *additional* directories from
indexation via two additive surfaces — the indexed project's own
`pyproject.toml` (`[tool.pydocs-mcp] exclude_dirs`) and server YAML
(`extraction.discovery.{project,dependency}.exclude_dirs`) — layered on top of
the hardcoded, non-removable `_EXCLUDED_DIRS` floor; this amends spec decision
#6b from "the directory blocklist is not user-configurable" to "the FLOOR is
not user-configurable; user exclusions are additive-only".

---

## 1. Context & motivation

Today the directory blocklist used by file discovery is fully hardcoded:
`_EXCLUDED_DIRS` in `python/pydocs_mcp/extraction/config.py:34` (`.git`,
`.venv`, `__pycache__`, `node_modules`, `site-packages`, …). Spec decision
#6b made that deliberate — an un-excluded `.git` / `.venv` / `site-packages`
would leak secrets, balloon the FTS index, and break inspect-mode imports —
and `DiscoveryScopeConfig`'s `extra="forbid"` (`extraction/config.py:135`)
rejects any attempt to set `exclude_dirs` in YAML at load time (pinned by
`tests/extraction/test_config.py::test_discovery_scope_config_forbids_exclude_dirs`).

What #6b never provided is the opposite direction: a project that wants to
index *less* than the default. Real projects carry directories that are pure
retrieval noise or actively harmful to index:

- **Evaluation datasets — the answer-key leak.** Repos that carry eval-task
  definitions in-tree (the motivating integration: coding-agent-playbook's
  repo-local `playbook/eval_tasks/` overlay — expected answers and grading
  rubrics as Markdown, reference solutions as Python) are un-evaluable over a
  default index: the agent under evaluation can `search_codebase` its way to
  the rubric, so retrieval-quality scores measure answer-key lookup, not
  retrieval. Only `.py`/`.md`/`.ipynb` files are ingestible, so pure
  `.json`/`.yaml` datasets never leaked — the exclude list closes the gap for
  exactly the risky formats. The pyproject surface lets the repo pin this
  ("this directory is never indexed, for everyone"); the YAML surface lets an
  eval harness enforce it across every corpus repo in a sweep without editing
  those repos, recorded in the run config like any other experiment knob.
- **Generated documentation** (`docs/generated/`, Sphinx `_build/` output
  checked into the tree) — thousands of near-duplicate markdown sections that
  drown the hybrid ranker and inflate the `.tq` sidecar.
- **Test fixtures** (`fixtures/`, `testdata/`) — synthetic `.py` / `.md`
  files that pollute `search_codebase` results with symbols nobody will ever
  call.
- **Vendored or example trees** the user wants out of `get_overview` /
  `get_symbol` results without renaming directories to hit the hardcoded
  floor.

The only workaround today is `max_file_size_bytes` (blunt, wrong axis) or
narrowing `include_extensions` (whole-corpus, not per-directory). Both punish
the rest of the tree to silence one directory.

This design adds per-project directory exclusion while preserving the entire
#6b rationale: the hardcoded floor stays non-removable, and every user surface
is **additive-only** — users can exclude *more*, never less. The MCP surface
is untouched (six task-shaped tools, zero new params): exclusion is index-time
deployment configuration, exactly the category CLAUDE.md §"MCP API surface vs
YAML configuration" routes to YAML/config, never to tool parameters.

## 2. Goals / Non-goals

### Goals

1. A project can declare its own exclusions in its `pyproject.toml`, next to
   its code, versioned with the repo — no server-side YAML edit required.
2. A deployment can declare exclusions in server YAML for both discovery
   scopes (project walk and dependency walks), consistent with every other
   extraction tunable.
3. Exclusions compose additively over the hardcoded `_EXCLUDED_DIRS` floor;
   the floor can never be shrunk from any surface.
4. Exclusions apply uniformly to **every** write-side file reader, not just
   the two walks: chunk discovery (`ProjectFileDiscoverer` /
   `DependencyFileDiscoverer`), project member extraction
   (`AstMemberExtractor._parse_dir`'s post-filter), the file-reading
   decision-mining sources (`extraction/decisions/sources/` — §7.8), and
   dependency-manifest discovery (`deps.list_dependency_manifest_files` —
   §7.9). An excluded directory contributes no chunks, no symbols, no
   decision records, and no dependency manifests.
5. `--watch` mode picks up `pyproject.toml` exclude edits without a server
   restart (the watcher already treats `pyproject.toml` as a reindex
   trigger).
6. Widening the exclude list and reindexing removes previously-indexed
   chunks/symbols of the newly-excluded directories from SQLite *and* the
   `.tq` sidecar atomically — no orphaned rows or vectors.

### Non-goals

- **No glob patterns.** Entries are exact directory names or anchored
  directory paths (§4). Globs are a complexity/perf trap (`fnmatch` per path
  component per file) and nothing in the motivating cases needs them.
- **No file-level exclusion.** The unit is a directory subtree, matching the
  existing `_EXCLUDED_DIRS` granularity.
- **No per-dependency pyproject reads.** A dependency's own
  `pyproject.toml` is NEVER consulted (§6, decision D4). Only the server
  operator (via YAML `dependency.exclude_dirs`) shapes dependency walks.
- **No MCP surface change.** No new tool, no new tool parameter. Exclusion is
  not a corpus-scope filter in the `package=`/`scope=`/`project=` sense — it
  is index-time configuration and stays out of the request path entirely.
- **No Rust change.** `src/lib.rs`'s `SKIP_DIRS` and `walk_py_files` are
  untouched (§10); the fallback contract (`_fallback.py`) is untouched.
- **No change to the member path for dependencies.**
  `AstMemberExtractor._dep_sync` keeps its current behavior (it lists
  `dist.files` directly and has never applied the directory blocklist);
  dependency *chunk* discovery honors the new YAML excludes because it flows
  through `DependencyFileDiscoverer`.

## 3. Configuration surfaces

Two user surfaces, one hardcoded floor. Both surfaces are lists of directory
entries with identical syntax and identical matching semantics (§4).

### 3.1 Surface 1 — the indexed project's `pyproject.toml`

```toml
# pyproject.toml at the root of the project being indexed
[tool.pydocs-mcp]
exclude_dirs = ["docs/generated", "fixtures"]
```

- Lives in the *indexed project*, not the server deployment. Travels with the
  repo; every developer indexing the project gets the same exclusions.
- Read by a new loader, `load_project_excludes(project_root)` in a new module
  `python/pydocs_mcp/project_toml.py` (§7.1) — a peer of `deps.py` /
  `multirepo.py` and the single owner of the `[tool.pydocs-mcp]` schema.
- Applies to the **project walk only** (`__project__` package). Never applied
  to dependency walks.

### 3.2 Surface 2 — server YAML

```yaml
# user overlay loaded via AppConfig.load(...), or the shipped
# python/pydocs_mcp/defaults/default_config.yaml
extraction:
  discovery:
    project:
      exclude_dirs: ["fixtures"]          # additive over the floor + TOML
    dependency:
      exclude_dirs: ["tests", "examples"] # additive over the floor
```

- A new `exclude_dirs: list[str] = []` field on `DiscoveryScopeConfig`
  (`python/pydocs_mcp/extraction/config.py:126`), so both scopes get it for
  free through the existing `DiscoveryConfig.project` /
  `DiscoveryConfig.dependency` split.
- Resolves at startup like every other tunable, through the existing
  `AppConfig` layering (defaults → shipped YAML → explicit overlay → env
  vars; `python/pydocs_mcp/retrieval/config/app_config.py:78`).

### 3.3 The merge rule

For a given walk, the effective exclusion set is a pure union — order
irrelevant, duplicates harmless, nothing subtracts:

| Walk / reader | Effective exclusion set |
|---|---|
| **Project** (`ProjectFileDiscoverer`, `AstMemberExtractor._parse_dir`) | `_EXCLUDED_DIRS` floor ∪ YAML `extraction.discovery.project.exclude_dirs` ∪ pyproject `[tool.pydocs-mcp].exclude_dirs` |
| **Dependency** (`DependencyFileDiscoverer`) | `_EXCLUDED_DIRS` floor ∪ YAML `extraction.discovery.dependency.exclude_dirs` |
| **Decision mining** (file-reading sources, §7.8) | Same as the project row — the set is threaded from the same run's project walk (§9.1), never re-derived |
| **Dependency-manifest discovery** (`list_dependency_manifest_files`, §7.9) | Same as the project row — it walks the project tree |

Consequences of union-only semantics:

- Listing a floor entry (e.g. `".git"`) in either surface is a harmless
  no-op — allowed, not an error (§8).
- There is no syntax, on any surface, that removes a floor entry. The
  secret-leak / index-bloat / inspect-import rationale of #6b is preserved
  verbatim (§6, decision D1).
- The two user surfaces never conflict: both can only add.

## 4. Matching semantics

Hybrid **"paths + bare names"**, decided during brainstorming. Each entry is
classified by whether it contains a `/`:

1. **Bare name** (no `/`) — matches that name as **any path component at any
   depth**, identical to today's `_EXCLUDED_DIRS` semantics
   (`path_under_excluded`, `extraction/config.py:65`). `"fixtures"` excludes
   `fixtures/`, `src/myproj/fixtures/`, `a/b/c/fixtures/` — each with its
   whole subtree.
2. **Anchored path** (contains `/`) — a POSIX-style relative path anchored at
   the walk root: the **project root** for the project scope; for the
   dependency scope, the **first path component of each `dist.files` relpath**
   is stripped before matching, so one YAML entry applies uniformly across
   dependencies. (For a typical wheel that first component is the top-level
   package directory, but the rule is defined purely on the relpath, not on
   any notion of "the package": a distribution shipping several top-level
   components — extra packages, `*.dist-info/` — has the entry applied under
   **each** of them, and a relpath with fewer than two components — a flat
   top-level module like `six.py` — never matches an anchored entry, since
   stripping leaves nothing to match.) An anchored entry excludes exactly
   that directory and its whole subtree — nothing else, no matter how many
   sibling directories share the leaf name.
3. **No globs.** `*`, `?`, `[` have no special meaning; they are (unwise but
   legal) literal characters in a directory name.

Normalization runs in a **fixed order, entirely before classification**:
first backslashes normalize to `/` (so Windows-authored configs behave
identically, mirroring the separator normalization already done by
`path_under_excluded`), then a trailing `/` is stripped, and only **then** is
the entry classified by whether a `/` remains. Consequence: `"fixtures/"`
(and Windows-authored `"fixtures\"`) is a **bare name** — the trailing
separator never makes an entry anchored. The order is load-bearing: the
opposite order would silently anchor `"fixtures/"` to the walk root, and the
two readings are indistinguishable on multi-segment entries like
`"docs/generated/"` — so AC-1 pins the single-segment case explicitly.

Two further rules, both pinned by ACs:

- **Directories only.** Entries name directories, never files (matching the
  "no file-level exclusion" non-goal, §2). Matching is applied to *directory*
  paths on both walks: chunk discovery prunes `os.walk` `dirnames`, and the
  member post-filter applies the effective set to each candidate file's
  **parent directory** relpath, not the full file relpath (§7.5). An entry
  that happens to collide with a file's name (bare `"conf.py"`, anchored
  `"docs/conf.py"`) is therefore a uniform no-op on **both** walks — the
  file's chunks and symbols are both kept, never one without the other.
- **Case-sensitive.** All matching is byte-wise case-sensitive against path
  names exactly as reported by the walk, regardless of the filesystem's own
  case-folding (macOS/Windows default volumes are case-insensitive; the
  matcher is not). An on-disk `Docs/Generated/` does NOT match an entry
  `"docs/generated"`. This keeps bare-name matching bit-identical to today's
  `path_under_excluded` comparison and gives anchored matching — which has no
  precedent to inherit — one pinned answer on every platform.

### Worked example

Project tree, with `pyproject.toml` declaring
`exclude_dirs = ["docs/generated", "fixtures"]` and no YAML excludes:

```
myproj/                          (project root)
├── pyproject.toml               # [tool.pydocs-mcp] exclude_dirs = ["docs/generated", "fixtures"]
├── docs/
│   ├── generated/               ✗ excluded — anchored "docs/generated"
│   │   └── api.md               ✗   (whole subtree gone)
│   └── guide.md                 ✓ indexed
├── src/
│   └── myproj/
│       ├── core.py              ✓ indexed
│       └── fixtures/            ✗ excluded — bare name matches at any depth
│           └── sample.py        ✗
├── fixtures/                    ✗ excluded — bare name matches here too
│   └── data.md                  ✗
├── tools/
│   └── generated/               ✓ indexed — anchored "docs/generated" does
│       └── gen.py               ✓   NOT match "tools/generated" (leaf-name
│                                    collision is not a match)
└── .venv/                       ✗ excluded — floor (_EXCLUDED_DIRS), as today
```

The discriminating rows are `tools/generated/` (anchored entries prune only
their own path — a sibling with the same leaf name survives) and the two
`fixtures/` directories (bare names prune every occurrence, at any depth).

## 5. Read timing — why pyproject excludes load per index run

The two surfaces intentionally resolve at different times:

- **YAML** (`exclude_dirs` on both scopes) resolves **once at startup** via
  `AppConfig.load(...)`, like every other extraction tunable. Changing server
  YAML has always required a restart; no exception here.
- **pyproject excludes** are read **per index run**, through an injected
  loader strategy on `ProjectFileDiscoverer` / `AstMemberExtractor` (§7.3,
  §7.5) — NOT captured once at composition time.

The per-run read exists for `--watch` freshness. The watcher already treats
`pyproject.toml` as a dependency manifest whose edits always trigger a
reindex regardless of the watched-extensions filter
(`_is_dependency_manifest`, `python/pydocs_mcp/serve/watcher.py:43`, applied
in `FileWatcher._matches` at `serve/watcher.py:119`). With a per-run loader,
the causal chain closes with zero new plumbing:

1. User edits `[tool.pydocs-mcp] exclude_dirs` in the project's
   `pyproject.toml`.
2. The watcher fires (manifest match) → debounced `_on_change` → the same
   `_run_indexing` path used at startup
   (`python/pydocs_mcp/__main__.py:556`).
3. The reindex run calls the loader fresh, computes the new effective set,
   and discovery output changes.
4. The package `content_hash` changes — the discovered path set changed
   and/or the exclusion-set fingerprint folded into the hash input changed
   (§9) — so the cache-skip check in
   `application/project_indexer.py:86` does NOT short-circuit, and
   `IndexingService.reindex_package` replaces the package.

Had the excludes been captured once at startup, step 3 would silently apply
the *old* list until restart — a stale-config class of bug the watcher was
specifically built to avoid for dependency edits. The loader is a
`Callable[[Path], ProjectExcludes]` field so tests inject fakes and never
touch the filesystem (CLAUDE.md: inject dependencies over hidden I/O).

Cost note: the read is a handful of small-TOML-file reads per project index
run — one each for chunk discovery (§7.3), member extraction (§7.5), and
dependency-manifest resolution (§7.9); not per file, not per directory —
noise next to the walk itself. Decision mining does NOT add a read: it
receives the set the discovery walk already computed, via the state bundle
(§7.8, §9.1).

## 6. Decision records

### D1 — Amendment to spec decision #6b (blocklist configurability)

**Prior decision (#6b, as recorded in `extraction/config.py:10-18` and
enforced since):** the extension allowlist is narrowable via YAML; the
directory blocklist is HARDCODED in `_EXCLUDED_DIRS` and not
user-configurable in any direction. `DiscoveryScopeConfig` carries
`extra="forbid"` precisely so that a stray `exclude_dirs:` YAML key fails
loud at load time (spec AC #6b), and
`tests/extraction/test_config.py::test_discovery_scope_config_forbids_exclude_dirs`
pins that rejection.

**Amended decision:** the **floor** is hardcoded and non-removable;
user-supplied exclusions are **additive-only**. `exclude_dirs` becomes a
declared, validated field on `DiscoveryScopeConfig` (so `extra="forbid"` no
longer rejects it — it now rejects only genuinely unknown keys), and the
project's own `pyproject.toml` gains the equivalent additive list.

**Why the amendment is safe — the original rationale is preserved, not
weakened:** #6b existed to prevent users from *un-excluding* `.git` /
`.venv` / `site-packages` (secret leaks, FTS bloat, broken inspect-mode
imports). Every surface introduced here is union-only over the floor (§3.3):
there is no syntax to shrink `_EXCLUDED_DIRS` from YAML, from TOML, or from
anywhere else. Users can only exclude MORE. The threat model #6b defended
against remains impossible by construction.

**Follow-through obligations (implementation must land these in the same
change):**

- Amend the #6b policy docstrings that state "not user-configurable" /
  "users cannot widen or shrink the directory blocklist":
  - `python/pydocs_mcp/extraction/config.py` — module docstring (lines
    10-18), the `_EXCLUDED_DIRS` attribute docstring (lines 59-62), and the
    `DiscoveryScopeConfig` class docstring (lines 129-133).
  - `python/pydocs_mcp/extraction/strategies/discovery/project.py` — module
    docstring (lines 9-15).
  - `python/pydocs_mcp/extraction/strategies/discovery/__init__.py` —
    package docstring (lines 13-14).
  - `python/pydocs_mcp/extraction/strategies/discovery/_shared.py` — module
    docstring (lines 3-7) references "the hardcoded blocklist"; reword to
    "the effective exclusion set (hardcoded floor + configured additions)".
- Invert the `extra="forbid"` rejection test for this key: the assertion
  that `exclude_dirs` is not a declared field flips to asserting it IS one,
  with additive semantics tests replacing the rejection test (§11, AC-14).

### D2 — Hybrid matching: bare names + anchored paths, no globs

Bare names keep parity with `_EXCLUDED_DIRS` semantics users already
implicitly rely on; anchored paths cover the "exclude *this* `docs/generated`
but not that other `generated`" case that bare names cannot express. Globs
were rejected: they invite per-component `fnmatch` costs inside `os.walk`
pruning, and none of the motivating cases need them. Classification is
syntactic (`"/" in entry`) — no mode flags, no second config key.

### D3 — pyproject excludes read per index run via an injected loader

See §5. Startup-time capture would go stale under `--watch`; per-run read
composes with the watcher's existing manifest trigger for free. The loader is
an injected strategy (`excludes_loader` field) so the discoverer and member
extractor stay testable without filesystem fixtures.

### D4 — A dependency's own `pyproject.toml` is never consulted

Dependency walks honor exactly floor ∪ YAML `dependency.exclude_dirs`.
Reading each installed distribution's own TOML would (a) let third-party
packages shape what the *user's* server indexes — an untrusted-input channel
pointed at index composition, (b) add a per-dependency file read to every
index run, and (c) create unfixable-by-the-user exclusions (you can't edit a
wheel). The server operator owns dependency-scope policy, full stop.

### D5 — Shared entry validation between TOML and YAML surfaces

Both surfaces funnel through one normalizer/validator,
`split_exclude_entries` in `project_toml.py` (§7.1): the TOML loader calls it
directly; the `DiscoveryScopeConfig.exclude_dirs` field validator delegates
to it. One function, one set of rejection rules, zero drift between surfaces
— the same single-source-of-truth discipline CLAUDE.md mandates for
defaults.

### D6 — Watcher ignore-globs are derived best-effort; discovery owns correctness

At `serve --watch` / `watch` startup, exclusion entries are translated into
watchdog ignore globs and appended to `serve.watch.ignore_globs` (§7.6) so
edits inside excluded directories don't churn reindex cycles. This is an
optimization only: if the glob derivation misses, the reindex the watcher
triggers still applies the correct effective set at discovery time.
Correctness lives in exactly one place — discovery — never in the watcher.

One refinement is required for the "a reindex fires at all" precondition to
hold in both directions: the *derived* globs (and only those — configured
YAML globs stay static) are **re-derived after every manifest-triggered
reindex** (§7.6). Startup-only derivation fails the shrink direction:
removing an exclude entry mid-session re-includes the directory on the
manifest-triggered reindex, but the stale startup glob would then swallow
every subsequent event inside it — no event, no reindex, a silently stale
subtree until restart, violating Goal 5 (AC-25).

### D7 — No Rust change

`walk_py_files`'s hardcoded `SKIP_DIRS` (both `src/lib.rs:88` and the parity
copy in `python/pydocs_mcp/_fallback.py:21`) stays as-is. See §10.

### D8 — Decision mining honors the effective project set, threaded from the same run's walk

Three decision-mining sources read project files directly from
`ctx.project_root`, bypassing `ProjectFileDiscoverer` entirely (§7.8).
Without coverage, excluding `docs` removes the directory's chunks and
symbols while `get_why` / `search --kind decision` / GOVERNS nodes keep
resurfacing decision records mined from the excluded tree on every reindex —
never converging, and directly intersecting motivating case #1 (§1). So the
effective project set applies to the file-reading sources' candidate paths.
The set arrives on `CaptureContext` from the state bundle filled by the same
run's discovery walk (§9.1) — one derivation per run, no second TOML read,
no intra-run drift.

### D9 — Dependency-manifest discovery honors the effective project set

`list_dependency_manifest_files` walks the project tree with only the
hardcoded `_SKIP_DIRS` pruning (§7.9). A manifest inside a user-excluded
directory (fixture projects routinely carry fixture `pyproject.toml`s) would
otherwise still contribute packages to the dependency index — and would be
internally inconsistent under `--watch`, where §7.6's derived globs suppress
its events (the manifest rule is subordinate to `ignore_globs`,
`serve/watcher.py:40-42`): read at startup, deaf to edits. Applying the
effective project set to the manifest walk closes both, and makes startup
and watch-session runs agree about which manifests matter.

### D10 — The content-hash fingerprint fold is state-carried, per-scope, and conditional

Three rules pin the §9 fold (details and rationale in §9.1–§9.2): (1) the
effective set is derived once per run by the discovery walk and carried on
the state bundle — the hash stage folds what the walk *actually used*, never
a re-derived set; (2) each scope folds its own set — project runs fold
floor ∪ YAML-project ∪ TOML, dependency runs fold floor ∪ YAML-dependency
only, so project exclude edits can never invalidate dependency caches; (3)
the fold is conditional — nothing is folded when the effective set equals
the bare floor, so every pre-upgrade stored hash (all of which were written
without the fold) stays valid and upgrading costs existing deployments
nothing.

## 7. Detailed design — component by component

### 7.1 NEW `python/pydocs_mcp/project_toml.py` — owner of the `[tool.pydocs-mcp]` schema

A peer of `deps.py` / `multirepo.py`. Single owner of the project-side TOML
schema so future `[tool.pydocs-mcp]` keys have an obvious home.

```python
@dataclass(frozen=True, slots=True)
class ProjectExcludes:
    """User-declared exclusion entries, pre-classified (§4)."""

    names: frozenset[str]        # bare names — any-component matching
    anchored: frozenset[str]     # normalized POSIX relpaths — subtree-anchored

    def matches(self, relpath: str) -> bool:
        """True iff relpath (walk-root-relative, POSIX separators; a
        DIRECTORY path per the directories-only rule of §4 — callers pass a
        file's parent directory, never the file path) falls under any entry:
        anchored entries match the directory itself and anything beneath it;
        names match any path component. Byte-wise case-sensitive (§4)."""
```

- `load_project_excludes(project_root: Path) -> ProjectExcludes` — reads
  `<project_root>/pyproject.toml` with stdlib `tomllib` (same engine as
  `deps.py:108`), extracts `[tool.pydocs-mcp].exclude_dirs`, and delegates to
  `split_exclude_entries`. Error posture per §8.
- `split_exclude_entries(entries: Sequence[str]) -> tuple[frozenset[str], frozenset[str]]`
  — the shared normalizer/validator (decision D5). Rejects absolute paths,
  `..` segments, `.`, and empty entries; then normalizes in the §4 order —
  `\` → `/` first, trailing `/` stripped second, classification by remaining
  `/` presence **last** (so `"fixtures/"` and `"fixtures\"` both classify as
  the bare name `"fixtures"`). Used by BOTH `load_project_excludes` and the
  YAML field validator.
- `ProjectExcludeConfigError(PydocsMCPError, ValueError)` — the fail-loud
  exception for malformed entries (§8). Defined here, next to the code that
  raises it, per the `exceptions.py` module rule ("concrete exception classes
  live in their respective modules"; `python/pydocs_mcp/exceptions.py:7-9`).
- The module imports nothing from `extraction/` (only stdlib +
  `pydocs_mcp.exceptions`), so `extraction/config.py` can import it without
  a cycle.

### 7.2 `python/pydocs_mcp/extraction/config.py` — the YAML field

- `DiscoveryScopeConfig` gains
  `exclude_dirs: list[str] = Field(default_factory=list)` plus a
  `@field_validator("exclude_dirs")` that calls
  `project_toml.split_exclude_entries` and re-raises validation failures as a
  `ValueError` (pydantic wraps it into the usual startup
  `ValidationError`, matching the `include_extensions` allowlist validator
  precedent at `extraction/config.py:143-153`).
- `_EXCLUDED_DIRS` and `path_under_excluded` are **unchanged** — they remain
  the floor and the canonical bare-name matcher. `path_under_excluded`
  already takes an `excluded: frozenset[str]` parameter, so callers pass the
  union set without any signature change.
- Docstring amendments per decision D1.

### 7.3 `ProjectFileDiscoverer` (`extraction/strategies/discovery/project.py`)

Current shape: frozen slotted dataclass with a single `scope:
DiscoveryScopeConfig` field; `discover()` prunes `os.walk` in place against
`_EXCLUDED_DIRS` (`project.py:36`).

Changes:

- New field `excludes_loader: Callable[[Path], ProjectExcludes]` with default
  `= load_project_excludes` (the real loader); tests inject fakes.
- `discover(target)` computes the effective set **once per run**:
  `names = _EXCLUDED_DIRS | frozenset(scope.exclude_dirs bare names) |
  loader(target).names`, plus the anchored set from both surfaces. Pruning
  stays in-place inside the `os.walk` loop:
  - bare names: the existing O(1) frozenset membership test per `dirnames`
    entry, now against the union set;
  - anchored: an O(1)-per-directory check of the walk-root-relative POSIX
    path of each candidate `dirnames` entry against the anchored set
    (`ProjectExcludes.matches` on the directory relpath — pruning at the
    directory level means the subtree is never descended, so per-file checks
    never happen).
- With empty user excludes on both surfaces, output is byte-identical to
  today (regression AC-6).
- `discover()` returns the effective set it pruned against alongside the
  walk output — `(paths, root, effective_excludes)`, updating the discoverer
  Protocols at `extraction/protocols.py:61-71` (both discoverers,
  symmetric). This is the single derivation point per run: downstream
  consumers that need the set (`ContentHashStage` §9.1, `MineDecisionsStage`
  §7.8) read it off the state bundle, never re-invoke the loader.

### 7.4 `DependencyFileDiscoverer` (`extraction/strategies/discovery/dependency.py`)

- Honors YAML `dependency.exclude_dirs` only (floor ∪ YAML; decision D4 — it
  never calls the pyproject loader and gains no loader field).
- Bare names: extend the existing `_in_excluded_dir(rel_str)` check
  (`dependency.py:39`) to test against the union set (floor ∪ YAML bare
  names) by passing the merged frozenset to `path_under_excluded`.
- Anchored entries: matched against each `dist.files` relpath with its
  **first path component stripped** (§4 — a relpath rule, not a
  package-directory rule), so one entry like `"docs/examples"` excludes
  `<first-component>/docs/examples/**` uniformly for every dependency. The
  two edge rules of §4 apply verbatim: single-component relpaths (flat
  top-level modules like `six.py`) never match an anchored entry, and a
  distribution with several top-level components has the entry applied under
  each of them.
- Like the project discoverer (§7.3), `discover()` returns the effective set
  it applied — floor ∪ YAML `dependency.exclude_dirs` — as the third return
  element, which is what makes the per-scope hash fold of §9.1 fall out with
  no extra rule.

### 7.5 `AstMemberExtractor` (`extraction/strategies/members/ast_extractor.py`)

The members side already post-filters `walk_py_files` output against the
canonical Python-side policy (`_parse_dir`, `ast_extractor.py:96-97`) —
precisely because the Rust walker's `SKIP_DIRS` doesn't track
`_EXCLUDED_DIRS`. That post-filter is the extension point:

- New fields: `excludes_loader: Callable[[Path], ProjectExcludes]` (default =
  real loader) and `scope_exclude_dirs: tuple[str, ...] = ()` (the YAML
  **project**-scope entries, wired at composition roots — §7.7).
- `_parse_dir(root, package)` computes the same effective project set as
  §7.3 (floor ∪ YAML project entries ∪ `excludes_loader(root)`) and filters
  candidates against it instead of filtering against the floor alone. The
  match target is each candidate file's **parent directory** relpath — bare
  names via `path_under_excluded` with the union set, anchored via
  `ProjectExcludes.matches` — NOT the full file relpath. This is the
  directories-only rule of §4: matching the full file relpath would let an
  entry that collides with a file name (bare `"conf.py"`, anchored
  `"docs/conf.py"`) drop that file's symbols here while chunk discovery —
  which prunes only `dirnames` — kept its chunks, a silent chunk/member
  divergence. Matching the parent directory makes such entries a no-op on
  both walks identically (AC-19).
- This is the ONLY member path needing change:
  `InspectMemberExtractor.extract_from_project` ALWAYS delegates to the
  composed `AstMemberExtractor`
  (`extraction/strategies/members/inspect_extractor.py:42-47`), so
  inspect-mode project indexing inherits the behavior. The dependency member
  path (`_dep_sync`, `ast_extractor.py:71`) is unchanged (non-goal §2).

### 7.6 Watcher wiring (`python/pydocs_mcp/__main__.py`)

In `_build_watcher_and_callback` (`__main__.py:525`), where the
`FileWatcher` is constructed with `ignore_globs=tuple(watch_cfg.ignore_globs)`
(`__main__.py:543`):

- At startup, load the project excludes (best-effort, same error posture as
  §8) plus the YAML project-scope entries, derive ignore globs, and append
  them to the configured `serve.watch.ignore_globs`
  (`WatchConfig.ignore_globs`, `python/pydocs_mcp/retrieval/config/models.py:579`):
  - bare `"fixtures"` → `"<project_root>/**/fixtures/**"` — anchored beneath
    the project root, NOT the bare `"**/fixtures/**"` house style of the
    shipped defaults;
  - anchored `"docs/generated"` → `"<project_root>/docs/generated/**"`
    (absolute-prefixed, since `FileWatcher._matches` fnmatches the full path
    string — `serve/watcher.py:121`).

  The root-anchoring of *derived* bare-name globs is load-bearing, not style:
  `FileWatcher._matches` fnmatches the full **absolute** path, and the
  manifest rule is subordinate to `ignore_globs` (a vendored
  `pyproject.toml` under an ignored `.venv` never fires —
  `serve/watcher.py:40-42`). An unanchored `"**/<name>/**"` therefore matches
  ancestor components of the project root's own path: a project at
  `/home/user/docs/myproj` with `exclude_dirs = ["docs"]` would derive
  `"**/docs/**"`, which matches EVERY event under the root — including the
  root `pyproject.toml` — permanently silencing the watcher. That is an
  over-match, which D6's "discovery owns correctness" escape hatch does NOT
  cover: correctness at discovery time only helps if a reindex fires at all.
  Prefixing `<project_root>/` puts the wildcard segment strictly below the
  root, so ancestor-path components can never collide, and the root
  `pyproject.toml` (directly at the root, with no intervening `<name>`
  component) can never match a derived glob. The shipped *configured*
  defaults (`"**/__pycache__/**"` etc.) keep their unanchored form — they
  are operator-owned YAML, not derived from user excludes.
- Best-effort churn suppression only (decision D6): correctness is owned by
  discovery. In particular, `pyproject.toml` itself always triggers as a
  manifest, so exclude-list edits still reindex (§5) even though files
  *inside* excluded dirs stop waking the debouncer.
- **Derived globs re-derive after every manifest-triggered reindex** (D6,
  shrink direction). Startup-only derivation has a hole: with `"fixtures"`
  excluded at startup, REMOVING the entry mid-session correctly re-includes
  `fixtures/` on the manifest-triggered reindex — but every subsequent edit
  *inside* `fixtures/` would still be swallowed by the stale startup-derived
  glob: no event, no reindex, a silently stale subtree until restart, in
  violation of Goal 5. So the derived suffix is kept separate from the
  configured globs: `FileWatcher` keeps its static `ignore_globs` tuple
  (operator YAML, never refreshed) and gains a
  `derived_globs_provider: Callable[[], tuple[str, ...]]` seam (default:
  `lambda: ()` — same injected-callable pattern as its existing
  `observer_factory` field) that `_matches` consults in addition to the
  static tuple. The watcher callback built in `_build_watcher_and_callback`
  recomputes the derived suffix from the fresh effective set (same
  loader + error posture as startup) at the end of every manifest-triggered
  reindex and swaps the provider's backing value. The grow direction was
  already safe (a glob for a new entry cannot over-suppress before the
  reindex that introduces it — the reindex is what derives it); the shrink
  direction is now safe too (AC-25). Still best-effort per D6 — discovery
  owns correctness — but the "a reindex fires at all" precondition D6 rests
  on now holds in both directions.

### 7.7 Composition roots and pipeline decode

- `python/pydocs_mcp/storage/factories.py:592` — `AstMemberExtractor()`
  becomes `AstMemberExtractor(scope_exclude_dirs=tuple(config.extraction.discovery.project.exclude_dirs))`
  (loader default stands; `config` is already in scope at that call site).
- `python/pydocs_mcp/extraction/pipeline/stages/file_discovery.py:56-57` —
  `FileDiscoveryStage.from_dict` already constructs
  `ProjectFileDiscoverer(scope=disc.project)` /
  `DependencyFileDiscoverer(scope=disc.dependency)` from
  `context.app_config.extraction.discovery`; the new YAML field rides in on
  `scope` with no stage change. The loader field takes its default.
- The composition roots that construct `StaticDependencyResolver`
  (`extraction/strategies/dependencies.py`) pass the YAML project-scope
  entries the same way (`scope_exclude_dirs=tuple(config.extraction.discovery.project.exclude_dirs)`;
  loader default stands) — §7.9.
- One Protocol change, nothing else: the discoverer Protocols at
  `extraction/protocols.py:61-71` grow the third return element
  (`effective_excludes`, §7.3) so `FileDiscoveryStage.run` can persist it as
  `state.files.effective_excludes` on the `FileBundle`
  (`extraction/pipeline/ingestion.py:52`) alongside `paths`/`root`.
  Downstream stages (`ContentHashStage` §9.1, `MineDecisionsStage` §7.8)
  read the field; no service or stage re-derives the set.
- Wiring coverage is pinned end-to-end: AC-26 loads YAML
  `extraction.discovery.project.exclude_dirs` through `AppConfig.load` and
  the REAL composition root and asserts both chunks and members vanish — so
  forgetting the `factories.py:592` edit cannot pass the suite (AC-12 alone
  injects the field in-test and would not catch it).

### 7.8 Decision mining (`extraction/decisions/`) — the third write-side file reader

Chunk discovery and member extraction are not the only write-side readers of
project files. Three decision-mining sources read directly from
`ctx.project_root`, bypassing `ProjectFileDiscoverer` entirely:

- `adr_files.py` globs the conventional ADR directories (`docs/adr`,
  `doc/adr`, `docs/decisions`, `adr`) for `*.md` (`_adr_paths`,
  `extraction/decisions/sources/adr_files.py:61-68`);
- `docs_prose.py` reads the root prose files plus `docs/*.md`
  (`_candidate_files`, `docs_prose.py:67-73`);
- `changelog.py` reads the conventional changelog paths
  (`_changelog_paths`, `changelog.py:62-67`).

`inline_markers` mines the already-filtered trees and `commit_messages`
reads git history — neither touches undiscovered files, neither changes.

Without coverage here, a user who excludes `docs` (bare or anchored) would
see the directory's chunks and symbols vanish while `get_why`,
`search --kind decision`, and GOVERNS graph nodes kept surfacing
`decision_records` whose verbatim evidence text comes from the excluded
tree — refreshed on every reindex, never converging, and directly
intersecting motivating case #1 of §1 (generated/vendored docs trees). So
the effective **project** set applies to the file-reading sources (decision
D8):

- `CaptureContext` (`extraction/decisions/_types.py:57`) gains
  `excluded: ProjectExcludes` (default: empty `ProjectExcludes` — a
  directly-constructed context behaves exactly as today), carrying the
  pre-classified effective project set with the floor folded into `names`.
- `MineDecisionsStage._build_context`
  (`extraction/pipeline/stages/decisions/mine_decisions.py:60`) fills it
  from `state.files.effective_excludes` (§9.1) — the very set the same
  run's discovery walk pruned against. Decision mining performs **no** TOML
  read of its own: one derivation per run, no intra-run drift (the stage
  runs downstream of `FileDiscoveryStage` in the same ingestion pipeline).
- Each file-reading source filters its candidate list before reading: a
  candidate whose **parent-directory** relpath (the directories-only rule,
  §4) satisfies `ctx.excluded.matches(...)` is dropped. Bare and anchored
  semantics apply verbatim — bare `"docs"` silences `docs/adr`,
  `docs/decisions`, the `docs/*.md` prose glob, and `docs/CHANGELOG.md`;
  anchored `"docs/generated"` leaves all of them alone.
- With empty user excludes the sources' output is byte-identical to today:
  the conventional candidate paths never live under a floor directory, so
  folding the floor into the filter is inert.

Pinned by AC-21: an excluded ADR/prose/changelog directory yields no
`decision_records` (and hence nothing via `get_why` /
`search --kind decision` / `get_references(direction="governed_by")`).

### 7.9 Dependency-manifest discovery (`deps.py`) — the fourth write-side walk

`list_dependency_manifest_files` (`deps.py:49-65`) recursively walks the
whole project tree pruning only the hardcoded `_SKIP_DIRS`, and feeds
`discover_declared_dependencies` (`deps.py:173`). Left alone, a
`pyproject.toml` or `requirements*.txt` inside a user-excluded directory —
the spec's own `fixtures/` motivating case; fixture projects routinely carry
fixture manifests — would still contribute packages to the dependency index.
And the design would be internally inconsistent under `--watch`: §7.6's
derived ignore globs suppress ALL events under excluded dirs, and the
manifest rule is explicitly subordinate to `ignore_globs`
(`serve/watcher.py:40-42`), so such a manifest would be read at startup
indexing yet could never retrigger a reindex when edited — startup and
watch-session runs disagreeing about which manifests matter.

Decision D9: the effective **project** set applies to the manifest walk.

- `list_dependency_manifest_files(root, excludes=...)` gains an optional
  pre-classified `ProjectExcludes` parameter (default: empty — other
  callers keep today's behavior). The walk prunes `dirnames` in place
  exactly as §7.3: bare names via the union frozenset, anchored entries via
  `matches` on the walk-root-relative directory path.
  `discover_declared_dependencies` grows the same pass-through parameter.
- `StaticDependencyResolver` (`extraction/strategies/dependencies.py`)
  gains the same two fields as the project discoverer —
  `excludes_loader: Callable[[Path], ProjectExcludes]` (default = real
  loader) and `scope_exclude_dirs: tuple[str, ...]` (YAML project-scope
  entries, wired at composition roots, §7.7) — computes
  floor ∪ YAML-project ∪ TOML per `resolve` call, and passes it down. A
  fresh per-call TOML read is correct here (the per-run posture of D3) and
  safe: unlike the content hash (§9.1), nothing fingerprints this set, so
  there is no intra-run coupling to preserve. Loader error posture is §8's,
  unchanged.

This resolves the watch/startup asymmetry for free: a manifest inside an
excluded directory neither contributes dependencies at startup nor wakes the
watcher — the two runs agree. Pinned by AC-22.

## 8. Error handling

| Condition | Behavior | Rationale |
|---|---|---|
| No `pyproject.toml` at project root, or file exists but has no `[tool.pydocs-mcp]` table (or no `exclude_dirs` key) | Empty `ProjectExcludes`, **silent** | The normal case for virtually every project; not an event worth logging. |
| `pyproject.toml` unparseable (`tomllib.TOMLDecodeError`) | **Loud `log.warning`** naming the file and stating "project excludes NOT applied"; proceed with floor ∪ YAML | Matches `deps.py`'s best-effort posture for the same file (`parse_pyproject_dependencies` degrades rather than aborting, `deps.py:95-116`). The index run must not die on a half-saved TOML mid-`--watch`; the floor still protects the dangerous directories. |
| Table present but `exclude_dirs` is the wrong type (not a list of strings) | Raise `ProjectExcludeConfigError` (fail loud, aborts the index run) | A declared-but-malformed config is user intent gone wrong — same posture as bad YAML at `AppConfig.load` startup. Silently ignoring a typo'd list would index everything the user asked to exclude. |
| An entry is absolute (`/tmp/x`), contains `..`, equals `.`, or is empty | Raise `ProjectExcludeConfigError` (TOML surface) / `ValidationError` at `AppConfig.load` (YAML surface) — both via `split_exclude_entries` | Escapes the walk root or is meaningless; fail-loud with the offending value and expected shape in the message (CLAUDE.md error convention). |
| An entry names a floor dir (e.g. `".git"`) | Harmless no-op, **allowed** | Union semantics make it inert; rejecting it would punish users for being explicit. |
| `ProjectExcludeConfigError` escapes a **watch-triggered** reindex (`--watch` / `serve --watch`) | Caught at the `_run_indexing` boundary in the watcher callback: **log the error, skip that reindex cycle, keep the server and watcher alive** — the index stays at its last good state and the next `pyproject.toml` edit retries | The half-saved-TOML rationale of row 2 applies with equal force here: a mid-edit save can parse successfully with a wrong-typed value (e.g. `exclude_dirs = "docs"` before the brackets are typed). Killing the serve process on a keystroke race would be worse than the misconfiguration; the fail-loud signal is preserved in the log, and the retry path is the very next save. |
| `ProjectExcludeConfigError` on a **non-watch** run (`pydocs-mcp index`, or the startup index of `serve`) | Propagates — **aborts the run** with the fail-loud message | Rows 3-4's posture: a declared-but-malformed config at a deliberate invocation is user intent gone wrong; the user is present to fix it. "Aborts the index run" in rows 3-4 means exactly this row's behavior — the watch row above is the only softening, and it softens delivery, not detection. |
| Watcher glob derivation encounters the unparseable-TOML case | Same warning path as row 2; watcher starts with YAML-derived + configured globs only | Decision D6 — the watcher is churn suppression, never correctness. |

## 9. Cache correctness — one fingerprint fold, existing machinery for the rest

The existing two-level cache does almost all the work; the design adds
exactly one thing to it — the effective exclusion set is folded into the
package content-hash input:

1. **Package level.** `packages.content_hash` is computed by the ingestion
   pipeline's content-hash stage
   (`extraction/pipeline/stages/content_hash.py:30-32`) via `hash_files`
   over the **discovered** path list — `(path, mtime_ns)` pairs (xxh3 in the
   Rust impl, `src/lib.rs:198`; an md5 fingerprint with identical framing in
   `_fallback.py:58`). The stage additionally folds a **normalized
   fingerprint of the effective exclusion set** (sorted bare names + sorted
   anchored entries, kind-tagged so the same string in the two sets cannot
   collide; floor included) into the hash input — analogous to how
   `pipeline_hash` invalidates chunk hashes on embedder/config change. The
   fold's plumbing, per-scope semantics, and upgrade conditionality are
   pinned in §9.1–§9.2 (decision D10). So any edit that changes the run's
   effective set forces a hash miss (a floor-duplicate entry, which changes
   nothing, correctly does not — §9.2): the skip check in
   `ProjectIndexer._index_project_source`
   (`application/project_indexer.py:86`) compares against the stored hash,
   misses, and proceeds to a full re-extract.

   The fold is necessary, not belt-and-braces: hashing the discovered path
   set alone has a hole. The hash covers only **chunk**-discovered paths
   (`ProjectFileDiscoverer` output), while the member walk
   (`AstMemberExtractor._parse_dir` via `walk_py_files`) applies no
   `max_file_size_bytes` budget and ignores `include_extensions`. A
   directory contributing members but zero chunk paths — e.g. only `.py`
   files above the size budget, or only `.py` files outside a narrowed
   `include_extensions` — leaves the path set (hence a path-only hash)
   unchanged when newly excluded; the run would skip as "no changes
   (cached)" and the excluded directory's symbols would survive, violating
   Goal 6. Fingerprinting the exclusion set itself makes Goal 6
   unconditional regardless of what kind of content a directory holds.
2. **Atomic replacement.** `IndexingService.reindex_package`
   (`application/indexing_service.py:106`) replaces the package's chunks,
   members, trees, and references under the `CompositeUnitOfWork`
   (SQLite + `.tq` coherent), exactly as it does for any content change. So
   after widening the excludes and reindexing, the chunks, symbols, and
   dense vectors of newly-excluded directories are **gone** — not orphaned in
   FTS, not stranded in the `.tq` sidecar. `remove_package`
   (`indexing_service.py:540`) and `clear_all` (`indexing_service.py:574`)
   keep their existing roles for the `--force` and removal paths.
3. **Chunk level.** The chunk-level `content_hash` diff-merge inside
   `reindex_package` sees the surviving chunks as unchanged (their
   `package+module+title+text+pipeline_hash` digests are untouched), so
   narrowing the corpus does NOT re-embed what remains — only deletions
   happen. Excluding a directory is cheap.

Edge worth stating: *shrinking* the exclude list (re-including a directory)
also changes the exclusion fingerprint (and usually the discovered set) →
hash miss → reindex, and the re-included chunks appear as adds and get
embedded. Symmetric, no special case.

### 9.1 Fingerprint plumbing & per-scope semantics

How the hash stage obtains the set is pinned, because two correctness holes
live in the obvious alternatives (decision D10):

- **One derivation per run, carried on the state — never re-derived.**
  `FileBundle` (`extraction/pipeline/ingestion.py:52`) gains
  `effective_excludes: ProjectExcludes` (default: empty), filled by
  `FileDiscoveryStage.run` from the discoverer's third return element
  (§7.3, §7.7) — the exact set the walk pruned against.
  `ContentHashStage` (`extraction/pipeline/stages/content_hash.py:30-32`)
  folds `state.files.effective_excludes`'s normalized fingerprint and never
  invokes the TOML loader itself. Rationale: the discovery stage and the
  hash stage are separate pipeline stages; if the hash stage re-invoked the
  loader, a mid-`--watch` `pyproject.toml` save landing between the two
  stages would fold a fingerprint for a set the walk did not actually use —
  exactly the stale-config class §5 argues against. (`MineDecisionsStage`
  reads the same field for the same reason, §7.8.)
- **Per-scope folds — project edits cannot invalidate dependency caches.**
  Dependency packages run the same ingestion pipeline and the same
  cache-skip check (`project_indexer._index_one_dependency`,
  `application/project_indexer.py:114/124`). Each run folds the set its own
  walk used — which the state-carried value yields with no extra rule:
  project runs fold floor ∪ YAML `project.exclude_dirs` ∪ TOML; dependency
  runs fold floor ∪ YAML `dependency.exclude_dirs` **only** (that is what
  `DependencyFileDiscoverer.discover` returns). Folding the project set (or
  a union) into dependency hashes would make every project-only exclude
  tweak spuriously re-extract every dependency package. Pinned by AC-23:
  editing project excludes between two runs leaves every dependency on the
  cached-skip path.

### 9.2 Upgrade compatibility — the fold is conditional on user excludes

Folding unconditionally would change the hash-input framing for every
package: every existing `~/.pydocs-mcp/*.db` was written without the fold,
so the first post-upgrade index run would miss every stored hash and
re-extract the project AND all dependency packages in full — the expensive
walk + parse + chunk path the package-level cache exists to avoid (minutes
on large dependency trees; chunk-level hashes survive, so re-embedding is
skipped, but re-extraction alone is the cost) — for every existing
deployment, including ones that never set a single exclude.

So the fold is **conditional** (decision D10): when the run's effective set
equals the bare `_EXCLUDED_DIRS` floor — the overwhelmingly common
no-excludes case — NOTHING is folded, and the hash input is byte-identical
to today's `hash_files(paths)` framing. Consequences, each correct:

- **Upgrade is free.** Existing stored hashes keep matching post-upgrade;
  the first run after upgrading skips unchanged packages exactly as before.
- **First user exclude → miss.** Adding any exclude makes the set differ
  from the floor → fingerprint appears in the input → hash miss → reindex
  (required by Goal 6 regardless of whether the discovered path set moved).
- **Removing the last user exclude → miss.** The set collapses back to the
  floor → the fold drops out → the framing returns to the unfolded form →
  miss → reindex that re-includes the content. Symmetric with §9's shrink
  edge, no special case.
- **A floor-duplicate entry stays inert.** Listing `".git"` alone leaves
  the effective set equal to the floor → no fold, no spurious miss —
  consistent with §3.3's "harmless no-op" rule.

Transitions between two *distinct* non-empty exclude sets always miss too,
since the fingerprint covers the full effective set whenever it is folded.
AC-24 pins the upgrade behavior and both transition directions.

## 10. Why no Rust change

`walk_py_files` (Rust `src/lib.rs:88` `SKIP_DIRS`; Python parity copy
`_fallback.py:21`, pinned by `tests/test_skip_dirs_rust_sync.py`) is used on
exactly one path this design touches: `AstMemberExtractor._parse_dir`. That
call site **already** treats the walker's built-in skip list as a coarse
pre-filter and post-filters the output against the canonical Python-side
policy (`ast_extractor.py:88-97` — added precisely because the two lists
diverge on `.hg` / `.svn` / `target` / `site-packages` / `.coverage` /
`.cache`). The design extends that same post-filter to the effective
exclusion set (§7.5), so:

- No new PyO3 parameter, no signature change, no wheel rebuild.
- The fallback contract (`_fast.py` ↔ `_fallback.py`, same signatures both
  sides) is untouched — `tests/test_parity.py` and
  `test_skip_dirs_rust_sync.py` continue to pass unmodified.
- Passing user config into Rust would also violate the boundary-thinness
  guideline (CLAUDE.md §"Rust Guidelines") for zero gain: the post-filter is
  a frozenset membership test per candidate path, noise next to file I/O and
  AST parsing.

Chunk-side discovery never calls `walk_py_files` at all
(`ProjectFileDiscoverer` does its own `os.walk`; `DependencyFileDiscoverer`
iterates `dist.files`), so it needs nothing from Rust either.

## 11. Testing plan & acceptance criteria

TDD throughout: each AC lands as a failing test first, then the smallest
change to green. The full CI gate set must pass before push (`ruff format
--check`, `ruff check`, `mypy python/pydocs_mcp`, `complexipy
--max-complexity-allowed 15`, `vulture --min-confidence 80`, `pytest tests/
--cov-fail-under=90`, `uv lock --check`, pip-audit).

### `tests/test_project_toml.py` (new)

- **AC-1** `split_exclude_entries` classifies correctly: `"fixtures"` →
  names; `"docs/generated"` → anchored; `"docs/generated/"` (trailing slash)
  → anchored `"docs/generated"`; `"fixtures/"` → **names** `"fixtures"` and
  `"fixtures\\"` → **names** `"fixtures"` (strip-before-classify order, §4 —
  a trailing separator never anchors an entry); `"a\\b"` → anchored `"a/b"`.
- **AC-2** `split_exclude_entries` raises (message carrying the offending
  value) for: absolute path, an entry containing `..`, `"."`, `""`, and a
  non-string element.
- **AC-3** `load_project_excludes` returns empty `ProjectExcludes` silently
  when `pyproject.toml` is missing, when the file has no
  `[tool.pydocs-mcp]` table, and when the table lacks `exclude_dirs`.
- **AC-4** `load_project_excludes` on an unparseable `pyproject.toml` logs a
  warning naming the file and stating excludes are not applied (asserted via
  `caplog`) and returns empty excludes; a wrong-typed `exclude_dirs` (e.g. a
  string, or a list containing an int) raises `ProjectExcludeConfigError`,
  which is a `PydocsMCPError`.
- **AC-5** `ProjectExcludes.matches`: anchored `"docs/generated"` matches
  `"docs/generated"` and `"docs/generated/deep/file.md"` but NOT
  `"tools/generated/x.py"` nor `"docs/generated2/x"`; bare `"fixtures"`
  matches `"fixtures/a.py"` and `"src/pkg/fixtures/a.py"`. Case mismatch is
  a non-match on both entry kinds (§4): anchored `"docs/generated"` does NOT
  match `"Docs/Generated/x.md"`, bare `"fixtures"` does NOT match
  `"src/Fixtures/a.py"` — byte-wise comparison regardless of platform
  case-folding.

### Discoverer tests (extend `tests/extraction/test_discovery.py`)

- **AC-6** Regression: with `exclude_dirs` empty on both surfaces,
  `ProjectFileDiscoverer.discover` output is byte-identical to today's
  (sorted list, same paths, floor-only pruning).
- **AC-7** A bare-name entry prunes matching directories at every depth
  (worked-example tree of §4, both `fixtures/` occurrences gone).
- **AC-8** An anchored entry prunes only its own path: `docs/generated/**`
  gone, sibling `tools/generated/**` present.
- **AC-9** Floor still applies with user excludes set (`.venv/` contents
  never discovered), and a user entry duplicating a floor name is a no-op,
  not an error.
- **AC-10** YAML `project.exclude_dirs` and pyproject entries merge (a tree
  where each surface excludes a different directory → both gone); the
  pyproject side is supplied via an injected fake `excludes_loader`, proving
  the injection seam.
- **AC-11** `DependencyFileDiscoverer` honors YAML
  `dependency.exclude_dirs` (bare and anchored, anchored matched with the
  first path component stripped) and NEVER invokes the pyproject loader (a
  fake loader asserting it is never called). The fixture includes a
  flat top-level module (single-component relpath a la `six.py`) — it
  survives every anchored entry (§4 edge rule) — and a distribution with two
  top-level components, where the anchored entry prunes under both.

### Decision-mining tests (extend `tests/extraction/decisions/`)

- **AC-21** With `"docs"` excluded (fake effective set on
  `CaptureContext.excluded`), the file-reading sources emit nothing from
  under `docs/`: `adr_files` skips `docs/adr/*.md` and `docs/decisions/*.md`
  (a root-level `adr/` fixture still mines), `docs_prose` skips `docs/*.md`
  (root `README.md` still mines), `changelog` skips `docs/CHANGELOG.md`
  (root `CHANGELOG.md` still mines); anchored `"docs/generated"` leaves all
  candidates intact; with the default empty `excluded`, each source's output
  is byte-identical to today. `MineDecisionsStage` fills
  `CaptureContext.excluded` from `state.files.effective_excludes` (unit
  test on `_build_context`). End-to-end: indexing a tmp project whose
  `pyproject.toml` excludes its ADR directory yields zero `decision_records`
  sourced from that directory — `get_why` / `search --kind decision` /
  `get_references(direction="governed_by")` surface nothing from it.

### Dependency-manifest tests (extend `tests/test_deps.py`)

- **AC-22** `list_dependency_manifest_files` with an excludes set prunes
  excluded subtrees: a tmp tree with `fixtures/pyproject.toml` declaring a
  dependency yields that manifest with empty excludes (regression) and omits
  it with `"fixtures"` excluded (bare and anchored variants); via
  `StaticDependencyResolver` with a fake `excludes_loader`, the declared
  dependency does not appear in `resolve()` output — so a manifest inside an
  excluded directory contributes no packages to the dependency index,
  matching the watcher's event suppression for the same path (§7.9).

### Member-extractor tests (extend `tests/extraction/test_members.py`)

- **AC-12** `AstMemberExtractor.extract_from_project` with excludes (fake
  loader + `scope_exclude_dirs`) emits no `ModuleMember` from excluded
  directories; via the `InspectMemberExtractor` delegation, inspect mode
  inherits the same behavior.
- **AC-13** The dependency member path (`extract_from_dependency`) is
  byte-identical to today regardless of configured excludes.

### Config tests (rework in `tests/extraction/test_config.py`)

- **AC-14** Inversion of the old #6b rejection test:
  `test_discovery_scope_config_forbids_exclude_dirs` is replaced —
  `exclude_dirs` IS a declared field on `DiscoveryScopeConfig`, defaulting to
  `[]`, and loading YAML with `extraction.discovery.project.exclude_dirs:
  ["fixtures"]` succeeds through `AppConfig.load`.
- **AC-15** Invalid entries (absolute, `..`, empty) in YAML fail at
  `AppConfig.load` with a `ValidationError` naming the field.

### Watcher tests (extend `tests/test_watcher.py` / `tests/test_main_cli_watch.py`)

- **AC-16** Glob derivation: bare `"fixtures"` yields
  `"<root>/**/fixtures/**"`, anchored `"docs/generated"` yields
  `"<root>/docs/generated/**"`, and both land in the constructed
  `FileWatcher.ignore_globs` alongside the configured defaults; a path
  inside an excluded dir does not match `FileWatcher._matches`, while the
  project's root `pyproject.toml` still does (manifest rule). Collision
  case: with the project root itself placed under a directory named like a
  bare-name exclude (tmp root `<tmp>/docs/myproj`, exclude `"docs"`), the
  root `pyproject.toml` STILL matches — the derived glob's root-anchoring
  (§7.6) keeps ancestor-path components out of reach.
- **AC-20** Watch-mode config-error resilience: a watch-triggered reindex
  whose `excludes_loader` raises `ProjectExcludeConfigError` (fake loader)
  logs the error, does not propagate out of the watcher callback, and leaves
  the watcher able to process the next event — a subsequent manifest edit
  with a valid `exclude_dirs` triggers a reindex that applies it (§8 watch
  row).
- **AC-25** Shrink re-derivation (§7.6): with startup excludes
  `["fixtures"]`, an event for `fixtures/x.py` does not match
  `FileWatcher._matches`; after a manifest-triggered reindex whose fresh
  effective set is empty (fake loader flips), the `derived_globs_provider`
  is swapped and the same event DOES match — edits inside the re-included
  directory fire reindexes again without a restart. The configured YAML
  `ignore_globs` are unchanged throughout (only the derived suffix
  refreshes).

### End-to-end (extend `tests/extraction/test_end_to_end.py` or peer)

- **AC-17** Index a tmp project whose `pyproject.toml` carries
  `[tool.pydocs-mcp] exclude_dirs`; assert no chunks and no symbols from the
  excluded directories exist in the store for `__project__`.
- **AC-18** Widen the excludes, reindex: the package `content_hash` changes,
  the cached-skip path is NOT taken, and previously indexed chunks/symbols
  from the newly-excluded directory are absent afterwards (SQLite rows gone;
  vector count consistent — the §9 argument, observed). A second case pins
  the §9 fingerprint fold: the newly-excluded directory contains ONLY
  member-producing, chunk-invisible content (a `.py` file above
  `max_file_size_bytes`), so the chunk-discovered path set is unchanged —
  the hash must still miss and the directory's symbols must still vanish.
- **AC-19** Directories-only parity (§4): with an exclude entry that
  collides with a file name (anchored `"docs/conf.py"` where
  `docs/conf.py` is a file, and a bare entry equal to a filename), index the
  tree and assert the file's chunks AND its symbols are both present —
  identical no-op on the chunk walk and the member post-filter, never one
  without the other.
- **AC-23** Dependency-cache isolation (§9.1): index a tmp project plus at
  least one dependency; edit ONLY the project excludes (the project's
  `pyproject.toml` `exclude_dirs`, and separately YAML
  `project.exclude_dirs`) and rerun. The project package's `content_hash`
  misses and it re-extracts; every dependency package's `content_hash` is
  unchanged and hits the cached-skip path
  (`project_indexer._index_one_dependency`, `stats.cached` increments).
  Conversely, editing YAML `dependency.exclude_dirs` misses the dependency
  hashes without touching the project's.
- **AC-24** Upgrade compatibility / conditional fold (§9.2): (a) with empty
  user excludes on both surfaces, the package `content_hash` equals the
  value produced by today's framing (pure `hash_files(paths)`), so an index
  written before this change skips as cached on the first post-upgrade run;
  (b) adding the first user exclude → hash miss; (c) removing the last user
  exclude → hash miss, and the hash returns to the unfolded value of (a);
  (d) an exclude list containing only floor duplicates (e.g. `[".git"]`)
  produces the same hash as (a) — no spurious miss.
- **AC-26** Composition-root wiring, YAML surface end-to-end: load a YAML
  overlay with `extraction.discovery.project.exclude_dirs: ["fixtures"]`
  through `AppConfig.load(...)`, build the indexing services through the
  REAL composition root (`storage/factories.py` — no in-test construction
  of `AstMemberExtractor` or the discoverers), index a tmp project, and
  assert BOTH chunks AND `ModuleMember` rows from `fixtures/` are absent.
  This pins the `factories.py:592` wiring of §7.7: an implementation that
  forgets it would pass AC-12 (field injected in-test) and AC-14/15 (config
  loading only) while YAML excludes silently never reached member
  extraction — exactly the chunk/member divergence class §7.5 prevents.

## 12. Documentation plan

- **`DOCUMENTATION.md`** — new user-facing section covering both surfaces
  (TOML + YAML examples as in §3), the two matching rules with the §4 example
  tree, the additive-over-floor rule, the watch-mode freshness behavior, and
  a one-line statement of reach: an excluded directory contributes no chunks,
  no symbols, no decision records (`get_why`), and no dependency manifests
  (Goal 4).
- **`README.md`** — short user-facing subsection (the "exclude directories
  from indexing" recipe) linking into `DOCUMENTATION.md`. Per the README
  policy (CLAUDE.md §"README files: no internal PR / sub-PR / task jargon"),
  no PR numbers, no "#6b", no spec labels — describe behavior and cite file
  paths only; run the README audit grep before merge.
- **`python/pydocs_mcp/defaults/default_config.yaml`** — add commented
  `exclude_dirs: []` under BOTH `extraction.discovery.project` and
  `extraction.discovery.dependency` (currently lines 46-51), with a one-line
  comment stating entries are additive over the built-in floor. (YAML files
  are exempt from the single-source-of-truth literal rule — explicit values
  for user-facing clarity.)
- **Docstring amendments** — the #6b follow-through list in decision D1.

## 13. Conventions compliance (CLAUDE.md)

- `ProjectExcludes` and all touched strategies are
  `@dataclass(frozen=True, slots=True)` value objects; no in-place mutation.
- Strategy injection over hidden I/O: `excludes_loader` is a constructor
  field with the real loader as default; tests inject fakes — no
  monkey-patching of module globals.
- Single source of truth: entry validation lives only in
  `split_exclude_entries` (D5); the floor lives only in `_EXCLUDED_DIRS`;
  the YAML default is pydantic `Field(default_factory=list)`.
- Comments explain WHY (the amendment rationale, the best-effort watcher
  posture, the post-filter-not-Rust choice), and the stale #6b wording dies
  with the code it described (D1).
- MCP surface untouched: six task-shaped tools, zero new tool parameters —
  this is index-time configuration, the category the YAML-vs-MCP rule exists
  for.
