# Multilanguage indexing — in-repo seams & blast radius (T1/T2/T3)

Evidence record. Worktree `.claude/worktrees/multilang-indexing` @ `aaed02e`
(= main; verified `git log --oneline -1` → `aaed02e refactor: remove the eval
package's 0.2.x deprecation shims (#211)`, branch `claude/multilanguage-indexing`).
All line refs are from files at this HEAD.

Headline: **the multilanguage seam is already architecturally frozen by ADR 0004.**
Two extension-keyed registries (`chunker_registry`, `analyzer_registry`) plus a
per-language capability matrix (`LanguageCapabilities`) exist specifically so a new
language lands as additive registration, not a code fork. T1/T2/T3 fit these seams.
The descriptions.md Phase-4 hash domain is provably disjoint from anything T1-T3
touch. The one real escalation flag is a "narrow-only" sentence in the FROZEN
`docs/tool-contracts.md` (§4.1, line 352) that T1 contradicts (doc-update, not a
tool-surface break).

---

## 1. The chunker registry + Chunker Protocol contract

**Registry.** `chunker_registry: dict[str, type[Chunker]]` lives in
`python/pydocs_mcp/extraction/serialization.py:36`. A plain dict keyed by
lowercased extension *with leading dot* (`".py"`, `".md"`, `".ipynb"`), value is
the class (not instance). Populated by the `_register_chunker(ext)` decorator
(`serialization.py:47-69`) — duplicate registration raises `ValueError` at import
(line 64-65). Deliberately NOT a `ComponentRegistry` (no YAML dict to decode;
docstring lines 10-14).

**Where `.py`/`.md`/`.ipynb` register today.** Three concrete chunkers, one file
each, each decorated at module scope; side-effect import in
`extraction/strategies/chunkers/__init__.py:25-34`:
- `ast_python.AstPythonChunker` (`.py`)
- `heading_markdown.HeadingMarkdownChunker` (`.md`)
- `notebook.NotebookChunker` (`.ipynb`)

Registration fires because `ChunkingStage` does `import
pydocs_mcp.extraction.strategies.chunkers` for its side effect
(`extraction/pipeline/stages/chunking.py:17`).

**Dispatch.** `ChunkingStage._chunk_one`
(`stages/chunking.py:47-69`): `ext = Path(path).suffix.lower()` →
`chunker_registry.get(ext)`; **unknown extension → `return None`, skipped silently
(policy, not error)** (line 57-58). Per-file exceptions are caught and logged
(line 67-69) — one broken file never aborts the run. `cls.from_config(cfg)` builds
the instance per file (line 59).

**Chunker Protocol** (`extraction/protocols.py:42-64`, `@runtime_checkable`):
```python
def build_tree(self, path: str, content: str, package: str, root: Path) -> DocumentNode: ...
```
Plus a CONVENTIONAL (not structurally enforced — PEP 544 can't check classmethods)
`from_config(cfg: ChunkingConfig) -> Self` classmethod, tested in
`tests/extraction/test_protocols.py` (docstring lines 46-52). **A new-language
chunker needs exactly these two methods** — no storage, no imports of the target
language.

**DocumentNode & NodeKind vocabulary** (`extraction/model/document_node.py`):
- `DocumentNode` is `@dataclass(frozen=True, slots=True)` (line 53). Carries
  `node_id, qualified_name, title, kind, source_path, start_line, end_line, text,
  content_hash, summary, extra_metadata, parent_id, children`. **Line spans are
  first-class** (`start_line`/`end_line`, 1-indexed).
- `NodeKind(StrEnum)` (line 29-40): `PACKAGE, SUBPACKAGE, MODULE, IMPORT_BLOCK,
  CLASS, FUNCTION, METHOD, MARKDOWN_HEADING, NOTEBOOK_MARKDOWN_CELL,
  NOTEBOOK_CODE_CELL, CODE_EXAMPLE`.
- **Is the kind vocabulary extensible?** It is a `StrEnum` — extensible only by
  editing the enum (a code change, not YAML/data). It is NOT frozen against
  additions, but every consumer that switches on kind must be updated. Two coupled
  tables must gain entries per new kind:
  - `STRUCTURAL_ONLY_KINDS` (document_node.py:45) — kinds that never flatten to chunks.
  - `_KIND_TO_ORIGIN: dict[NodeKind, ChunkOrigin]` in
    `extraction/model/tree_flatten.py:30-39`. **A kind missing from this map gets
    `origin=None`** (`_origin_for_node`, tree_flatten.py:79-85) → the chunk is still
    emitted (via `_should_emit`: not structural + non-empty text, line 75-76) but
    with no `origin` metadata key. So a new-language node could reuse an existing
    kind, OR add a new `NodeKind` + `ChunkOrigin` + `_KIND_TO_ORIGIN` entry.
- `ChunkOrigin(StrEnum)` in `models.py:91` (values: `python_def`,
  `markdown_section`, `notebook_markdown_cell`, `notebook_code_cell`).

**What NodeKind would non-Python nodes use?** Cheapest v1: reuse `MODULE` for the
file root and `FUNCTION`/`CLASS` for defs (they map to `python_def` origin — a
mild lie in the origin label but harmless to retrieval). Honest v1: add a
language-neutral kind or per-language kinds, plus matching `ChunkOrigin` +
`_KIND_TO_ORIGIN` entries. Either is a small, localized code change; neither is
YAML.

---

## 2. include_extensions plumbing

**NOT in `retrieval/config/models.py`** — it lives in
`python/pydocs_mcp/extraction/config.py`.

- `ALLOWED_EXTENSIONS: frozenset = {".py", ".md", ".ipynb"}` (config.py:29). This
  is the **ceiling**.
- `DiscoveryScopeConfig.include_extensions: list[str] = [".py", ".md", ".ipynb"]`
  (config.py:141), with `model_config = ConfigDict(extra="forbid")`.
- **`_enforce_allowlist` field-validator (config.py:148-158) REJECTS any extension
  not in `ALLOWED_EXTENSIONS`** ("must be subset of ..."). So YAML `include_extensions`
  is **narrow-only** — it can subset the ceiling, never exceed it.

> **T1 finding:** widening the *effective* set is a TWO-part code change, impossible
> via YAML alone: (a) add extensions to the `ALLOWED_EXTENSIONS` frozenset ceiling
> (config.py:29); and optionally (b) widen the `include_extensions` default list
> (config.py:141) if you want them on by default. Docstrings at config.py:30-33 and
> ALLOWED_EXTENSIONS itself state this explicitly ("adding a new extension requires
> registering a matching Chunker AND amending this allowlist — can't be done via
> YAML alone").

**Who consumes `include_extensions`:**
- `ProjectFileDiscoverer.discover` (`strategies/discovery/project.py:85-87`):
  `if ext not in self.scope.include_extensions: continue`.
- `DependencyFileDiscoverer` (`strategies/discovery/dependency.py:122`): same guard.

**One scope or two? — COUPLED.** File tools and indexing both read
`config.extraction.discovery.{project,dependency}`:
- **Indexing side:** `FileDiscoveryStage.from_dict` builds
  `ProjectFileDiscoverer(scope=disc.project)` /
  `DependencyFileDiscoverer(scope=disc.dependency)`
  (`extraction/pipeline/stages/file_discovery.py:62-63`), where `disc =
  config.extraction.discovery`.
- **File-tools side:** `FileToolsService` is wired with
  `project_scope=config.extraction.discovery.project`,
  `dependency_scope=config.extraction.discovery.dependency`
  (`storage/factories.py:254-255`), and reconstructs the SAME discoverers
  (`application/file_tools.py:444, 452`).

> **Coupling finding:** there is exactly ONE `include_extensions` knob per
> project/dependency scope, shared by indexing and by grep/glob/read_file. **You
> CANNOT widen file-tool visibility without also widening what gets indexed** (and
> vice-versa). This is by design — `file_tools.py:5-9` and tool-contracts.md §4.1
> ("walk exactly the file set the indexer sees", ADR 0003). If a v1 wants
> "browse .rs files but don't embed them", that is a NEW seam, not an existing knob.

---

## 3. pipeline_hash / cache invalidation — precise trigger conditions

**`AppConfig.ingestion_pipeline_hash`** (cached_property) /
`compute_ingestion_pipeline_hash()` shim — `retrieval/config/app_config.py:375-430`.
Payload fed to `hashlib.sha256` (lines 404-419):
1. `embedding.compute_pipeline_hash()` — embedder identity (line 405).
2. `search_backend.compute_identity()` — storage-backend identity (line 410).
3. Conditionally `late_interaction.compute_pipeline_hash()` **only if the bytes
   `b"embed_chunks_multi_vector"` appear in the ingestion YAML** (lines 417-418).
4. The **ingestion pipeline YAML file bytes** (`yaml_bytes`, read at line 404;
   default `pipelines/ingestion.yaml`).

**What it does NOT include:** `include_extensions`, `ALLOWED_EXTENSIONS`,
`chunker_registry` contents, `analyzer_registry` contents, `ChunkingConfig`
(markdown heading bounds / notebook `include_outputs`). Verified by reading the full
payload assembly (app_config.py:404-419) — none of those are referenced.

**Chunk cache key.** `compute_chunk_content_hash(package, module, title, text,
pipeline_hash)` = `SHA-256(package \0 module \0 title \0 text \0 pipeline_hash)`
(`models.py:191-245`). Set by `AssignChunkContentHashStage` in
`pipelines/ingestion.yaml:17`.

**Package-level cache key.** `ContentHashStage`
(`extraction/pipeline/stages/content_hash.py`) hashes `hash_files(state.files.paths)`
(line 51) — a function of the **discovered path set** (plus an exclude-fingerprint
fold, lines 38-42, 65-69). More discovered paths ⇒ different package hash.

> **T1/T2/T3 invalidation, precise:**
> - **Adding a T2/T3 chunker or analyzer (decorator registration) alone:
>   pipeline_hash UNCHANGED** (registries aren't in the payload) **and package hash
>   UNCHANGED** if `include_extensions` isn't widened (no new files discovered). A
>   deployment that ships the code but doesn't enable it sees **byte-identical
>   hashes → zero reindex.** ✅ (This directly satisfies the "does NOT invalidate
>   when the feature is unused" requirement.)
> - **Widening `include_extensions` (T1):** pipeline_hash UNCHANGED, but the project
>   discovers MORE files ⇒ `hash_files(paths)` changes ⇒ **package-level hash changes
>   ⇒ that package re-extracts.** New files produce new chunks with new `text` ⇒ new
>   `content_hash` ⇒ they get embedded. **Existing Python/md/ipynb chunks keep their
>   hashes (pipeline_hash unchanged) ⇒ NOT re-embedded.** Correct incremental
>   behavior. ✅
> - **Editing `pipelines/ingestion.yaml`** (e.g. to add a new stage for T3): changes
>   `yaml_bytes` ⇒ pipeline_hash changes ⇒ **every chunk hash invalidates ⇒ full
>   re-embed of the whole index.** Avoid touching the shipped ingestion YAML for
>   T2/T3 unless a re-embed is intended.
>
> **Latent gap (pre-existing, worth noting for T2):** `ChunkingConfig` tunables are
> NOT in pipeline_hash. If T2 adds a `plaintext:`/config sub-model under
> `ChunkingConfig`, changing those tunables will NOT invalidate chunk hashes — a
> re-tune won't re-chunk unless the file bytes change. Same latent staleness already
> exists for `markdown.min_heading_level` etc. Not introduced by us, but T2 config
> design should be aware.

---

## 4. BLAST RADIUS census

### (a) descriptions.md Phase-4 seed hash — UNAFFECTED (disjoint hash domain) ✅

Packaged seed: `python/pydocs_mcp/defaults/descriptions.md`.
- Product hash `_artifact_hash` (`application/description_source.py:450-490`):
  hashes `f"renderer:v{RENDERER_VERSION}\n{normalize(render_sections(instructions,
  tool_view, preamble))}"` — i.e. `SERVER_INSTRUCTIONS` + the nine tool descriptions
  + session-start preamble. `RENDERER_VERSION = 1` (description_source.py:46).
- Eval-side parity: `Candidate.candidate_hash`
  (`benchmarks/.../optimize/candidates/candidate.py:104-116`) re-implements the
  SAME payload (`renderer:v{RENDERER_VERSION}\n{normalized}`), pinned byte-for-byte
  against the product `apply_source` return by a parity test.
- `ToolDocsArtifact.fingerprint`
  (`benchmarks/.../optimize/artifacts/tool_docs.py:87-90`): `sha256(render())` over
  the live `TOOL_DOCS` + `SERVER_INSTRUCTIONS` surface.

> **All three hash ONLY the tool-description text surface** (TOOL_DOCS /
> SERVER_INSTRUCTIONS / preamble). They do NOT read `include_extensions`, the
> chunker/analyzer registries, `pipeline_hash`, or any index bytes. **T1/T2/T3 leave
> these hashes byte-identical as long as `defaults/descriptions.md` and
> `application/tool_docs.py` are not edited.** Expected-finding CONFIRMED. The only
> way T1-T3 could disturb them is a well-intentioned edit to a tool description
> (e.g. adding "supports Rust" to `get_references`) — which would be a deliberate
> descriptions edit and must be treated as a Phase-4 seed change (forbidden by the
> constraint). Do not touch those two files.

### (b) pipeline_hash / index bytes pins — conditionally affected

`pipeline_hash` string appears across ~30 test files (grep list captured). None pin
the *value* of the hash to a literal in a way T1/T2 changes — they assert
round-trips, backend-identity folds, and stability. Since T1 (widen extensions) and
T2/T3 (register chunker/analyzer) DON'T change `pipeline_hash` (§3), these are
UNAFFECTED. Only editing `pipelines/ingestion.yaml` would move the value — avoid.
No golden `.db` byte-fixture was found; the closest is `test_parity_golden.py`
(retrieval parity, not index bytes).

### (c) extension / config-shape / tool-count pins — DIRECTLY affected by T1

These pin the current extension set and WILL break if T1 widens the DEFAULT
`include_extensions` (they do NOT break if T1 only lifts the `ALLOWED_EXTENSIONS`
ceiling while leaving the default narrow):
- `tests/retrieval/test_config.py:281-284` and `:312-315` — assert
  `include_extensions == [".py", ".md", ".ipynb"]`.
- `tests/test_config_serve_watch.py:14` — `cfg.extensions == (".py", ".md", ".ipynb")`.
- `tests/test_default_config_serve_watch.py:38` — `".ipynb" in watch["extensions"]`
  (survives widening; only asserts membership).
- `tests/test_watcher.py:126` — watcher `extensions=(".py", ".md", ".ipynb")`.
- `python/pydocs_mcp/defaults/default_config.yaml:48,55` — literal
  `include_extensions: [".py", ".md", ".ipynb"]` (would be the canonical edit point
  if widening the default).

**Tool-count / doc-conformance pins — UNAFFECTED by T1/T2/T3:**
- `tests/test_mcp_registration_snapshot.py` pins the "nine frozen tools" and asserts
  each `entry["description"] == TOOL_DOCS[name]` (lines 3, 71-75). T1-T3 add no
  tools/params and don't touch TOOL_DOCS ⇒ green.
- `ExtractionConfig` sub-models use `extra="forbid"`. **Adding a NEW config field
  (e.g. a T2 `plaintext:` sub-model) is safe** — `forbid` rejects unknown keys, it
  does not reject the model gaining a known field. No test enumerates the config
  schema shape in a way a new optional field would trip (config round-trip tests use
  default construction).

**NodeKind/ChunkOrigin enumeration pins:** many test files reference specific
`NodeKind.X` members, but grep found none doing `len(NodeKind)` / `set(NodeKind)`
exhaustiveness. Adding a new enum member is therefore additive-safe for tests
(individual-member assertions keep passing). New members still require
`_KIND_TO_ORIGIN` + `STRUCTURAL_ONLY_KINDS` review (§1).

---

## 5. Member/symbol seam — honest v1 capability matrix

**`get_symbol` / `get_context` resolve off DOCUMENT TREES, not module_members.**
`LookupService` calls `tree.find_node_by_qualified_name(target)`
(`application/lookup_service.py:437, 572, 645`) — a BFS over the persisted
`DocumentNode` tree (`document_node.py:98-116`). **Any chunker's DocumentNode tree
suffices for `get_symbol`/`get_context` outline + span navigation**, because those
tools read the tree a chunker already produces. This is why `PYTHON_CAPABILITIES`
and `MARKDOWN_CAPABILITIES` both declare `outline: "available"`
(`analyzers.py:73-83`).

**What stays Python-only:**
- **`AstMemberExtractor` / `InspectMemberExtractor`**
  (`extraction/strategies/members/`) — both parse Python (`ast` / live `inspect`).
  These feed `module_members` (signatures, type hints). A non-Python language gets
  NO `module_members` rows unless a language-specific member extractor is added.
- **Reference graph** — `ReferenceCaptureStage._capture_all`
  (`stages/reference_capture.py:94-130`) dispatches via
  `analyzer_registry.get(Path(path).suffix.lower())` (line 108); **unknown extension
  → skipped silently** (line 109-110). The only registered analyzers are
  `PythonAstAnalyzer` (`.py`, CPython `ast`) and the markdown MENTIONS analyzer. A
  new language with no registered `LanguageAnalyzer` gets an EMPTY reference graph.

**THE MULTILANGUAGE SEAM IS ALREADY FROZEN — ADR 0004** ("Code-structure
abstraction: LanguageAnalyzer seam + capability flags", Accepted 2026-07-17,
`docs/adr/0004-code-structure-abstraction.md`). It ships:
- `LanguageAnalyzer` Protocol + `analyzer_registry` +
  `register_analyzer(ext)` decorator (`extraction/strategies/analyzers.py:48-116`).
- `LanguageCapabilities` TypedDict = `{outline, definitions} ∈
  {available|unavailable}` × `references ∈ {semantic|syntactic|unavailable}`
  (analyzers.py:40-45) — the honest per-language matrix.
- `language_capabilities(ext)` lookup helper (analyzers.py:119-122).

**Capability matrix, per tool per language (v1 honest expectation):**

| Tool | `.py` today | `.md` today | New lang, chunker only (T2) | New lang, chunker+analyzer (T3) |
|------|-------------|-------------|-----------------------------|---------------------------------|
| `search_codebase` (BM25/dense over chunks) | ✅ | ✅ | ✅ (chunks flatten from tree) | ✅ |
| `get_overview` | ✅ | ✅ | ✅ | ✅ |
| `get_symbol` (outline+spans, off trees) | ✅ | ✅ (headings) | ✅ **outline available** | ✅ |
| `get_context` (off trees) | ✅ | ✅ | ✅ | ✅ |
| `parent_rollup` (retrieval step, sibling→parent over tree) | ✅ | ✅ | ✅ (kind-aware weights fall back to .5) | ✅ |
| `get_references` | syntactic | syntactic (MENTIONS, opt-in) | **empty / unavailable** | syntactic (if analyzer added) |
| `module_members` signatures (feeds get_symbol depth) | ✅ (ast/inspect) | n/a | **Python-only — none** | Python-only unless member extractor added |
| grep/glob/read_file | ✅ | ✅ | ✅ (once include_extensions widened) | ✅ |

> **v1 honest story:** register a chunker (T2/T3) and you get search + outline +
> context + rollup + filesystem tools for the new language immediately. References
> require a registered `LanguageAnalyzer`; without one they are honestly empty. Rich
> signatures (`module_members`) stay Python-only until a per-language member
> extractor exists. ADR 0004's whole point is that this degrade is DECLARED, not
> silent — see §6 caveat on where the declaration is (not) surfaced.

---

## 6. Contract audit — Python-only claims that multilanguage would falsify

**`docs/tool-contracts.md` (FROZEN):** grepped for Python-only language. Findings:
- **ESCALATION FLAG — §4.1 line 352:** *"An extension allowlist, default
  `['.py', '.md', '.ipynb']` (narrow-only) ..."*. T1 contradicts the word
  **"narrow-only"** — lifting `ALLOWED_EXTENSIONS` makes the allowlist *widenable*.
  This is in the frozen contract document. **It is NOT a tool-name/param/envelope
  change** (the frozen surface per ADRs 0002-0004 is names+params+response shape),
  so it does not break the nine-tool freeze — but the sentence is a documented
  behavioral invariant that a T1 landing must UPDATE, and the update should be
  ratified by the owner since the doc is declared frozen. **Flag, do not
  self-edit.**
- Other `.py`/"python" hits in the contract are (i) implementation file paths
  (`mcp_inputs.py`, `envelope.py`, etc. — stable, not behavioral claims), (ii)
  glob EXAMPLES (`*.py`, `**/*_test.py` — illustrative, not exclusive), and (iii)
  the `.gitignore`-divergence note (lines 358-365). None assert "Python files only".

**`python/pydocs_mcp/defaults/descriptions.md` (Phase-4 seed, must not change):**
only Python mention is line 69 — grep is *"Python `re` flavor"* describing the grep
tool's REGEX ENGINE, which is accurate regardless of target-file language. **No
binding Python-only claim.** ✅

**`server.py` docstrings / TOOL_DOCS:** `get_references` description promises
call-graph semantics; ADR 0004 already flags it as the surface whose honesty is
handled by the `meta.resolution` capability flag rather than by hedging the text.
No new falsification introduced by T2/T3 as long as references degrade to
`unavailable` for unanalyzed languages.

> **Caveat / v1 honesty gap (code, not contract):** `meta.resolution` on
> `get_references` is **hardcoded** to `PYTHON_CAPABILITIES["references"]`
> (`application/tool_router.py:52, 168`) — it does NOT dispatch through
> `language_capabilities(ext)` per target. So a Rust/JS target today would still
> report Python's `"syntactic"`, overstating capability. Routing line 168 through
> `language_capabilities(...)` for the target's extension is the small honest fix a
> multilanguage v1 should include. (One-line-ish; ADR 0004 anticipates it — "a
> future semantic backend flips only the declared value".)

**Net:** no binding Python-only claim blocks multilanguage. One frozen-doc sentence
("narrow-only") needs an owner-ratified update; one code line needs to become
per-language for honest `meta.resolution`.

---

## 7. Gate exposure (mypy / coverage / complexipy)

Gates (`.github/workflows/ci.yml`): `--cov-fail-under=90` (lines 178, 183),
`complexipy python/pydocs_mcp --max-complexity-allowed 15` (line 136), plus
`ruff format --check`, `mypy python/pydocs_mcp`, `vulture --min-confidence 80`.
(Coverage 96.7% "baseline" is the current headroom above the 90 floor — ~6.7pts of
slack, so new uncovered branches must stay well-tested but the floor is not tight.)

**Product files each tier touches:**
- **T1:** `extraction/config.py` (widen `ALLOWED_EXTENSIONS` frozenset line 29; maybe
  default list line 141) + `defaults/default_config.yaml:48,55`. Trivial complexity;
  needs a test flipping the previously-rejected extension to accepted (mirrors the
  existing `_enforce_allowlist` rejection test). Update the extension-pinning tests
  in §4(c).
- **T2:** new file under `extraction/strategies/chunkers/` (+ `__init__.py` re-export
  + side-effect registration); optionally a `LanguageAnalyzer` under
  `strategies/analyzers.py` (or new file) for references; optionally new `NodeKind` +
  `ChunkOrigin` + `_KIND_TO_ORIGIN`/`STRUCTURAL_ONLY_KINDS` entries; optionally a
  `ChunkingConfig` sub-model. Each new public function needs a test (coverage floor).
  Keep `build_tree` ≤ complexity 15 (the existing chunkers are the size template —
  files ~200-300 lines).
- **T3:** tree-sitter behind an optional extra (mirrors `[late-interaction]`
  pattern). Heavy import must be function-local so `import pydocs_mcp` stays lean
  (precedent: analyzer `capture` uses deferred imports, analyzers.py:147-150; embedder
  providers keep concrete imports function-local per CLAUDE.md). The subpackage may
  need mypy-exclusion if tree-sitter grammars are untyped (precedent: ask_your_docs
  is mypy-excluded). New optional dep must not enter the default install (extras bar
  in CLAUDE.md).

**Purity constraint (ADR 0014) survives T3.** ADR 0014
(`docs/adr/0014-rollout-execution-remote-host-index-cache.md:52, 179`): *"The
PROJECT index is a pure function of the repo at base commit"*, pure over
`{repo files at base_commit, embedder + ingestion config}`. **Tree-sitter parses
file BYTES only — no imports, no env, no network** — so T3 PRESERVES this purity
(unlike inspect-mode, which imports modules). This is a strong architectural reason
to prefer tree-sitter (static parse) over any import/execution-based multilanguage
approach: it keeps the index a deterministic function of repo files, exactly as
ADR 0014 requires.

---

## Summary of load-bearing findings

1. **Seam already exists (ADR 0004):** `chunker_registry` (tree) +
   `analyzer_registry` (references) + `LanguageCapabilities` matrix. Adding a
   language = additive decorator registration on one or both.
2. **T1 needs a code change, not just YAML:** lift `ALLOWED_EXTENSIONS`
   (extraction/config.py:29). The `_enforce_allowlist` validator makes YAML
   narrow-only by contract (config.py:148-158).
3. **Indexing and file-tools share ONE `include_extensions` scope** — cannot widen
   file-tool visibility without widening indexing (factories.py:254-255 vs
   file_discovery.py:62-63).
4. **pipeline_hash excludes extensions + registries + ChunkingConfig** — registering
   chunkers/analyzers or widening extensions does NOT full-invalidate the chunk
   cache; unused features cost zero reindex; only editing `pipelines/ingestion.yaml`
   forces a full re-embed.
5. **descriptions.md Phase-4 hash is a disjoint domain** (tool-text surface only) —
   provably unaffected by T1-T3 unless `descriptions.md`/`tool_docs.py` is edited.
   Don't edit them.
6. **Two escalation flags (no self-edits):** (a) frozen tool-contracts.md line 352
   says "narrow-only" — T1 contradicts it, owner must ratify the doc update; (b)
   `tool_router.py:168` hardcodes `PYTHON_CAPABILITIES["references"]` for
   `meta.resolution` — should become per-language for honest multilanguage output.
7. **Test blast radius of T1 (if default widens):** test_config.py:281/312,
   test_config_serve_watch.py:14, test_watcher.py:126, default_config.yaml:48/55.
   Tool-count snapshot + config-shape + enum tests are unaffected.
8. **T3 preserves ADR 0014 index purity** — tree-sitter is a static byte parse, no
   imports/env.
