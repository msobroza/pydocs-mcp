# Phase 2 evidence — structured result identifiers per tool (D2 schema atoms / D3 attribution tiers)

**Researcher scope:** per-tool `items[]` field sets, surfaced-vs-inspected classification,
line fidelity, hit counts/truncation, and real sample envelopes with payload sizes.

**Repo state:** worktree `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/phase-2-instrumentation-spec-498def`,
branch `claude/phase-2-instrumentation` = origin/main `cebf08c` (verified via the session's git status snapshot;
contract file is Frozen 0.6.0, `docs/tool-contracts.md:3`).

**Method:** every claim below carries a `file:line` cite from THIS worktree, or comes from a
command actually run this session (samples in §6 were produced by driving `ToolRouter`
directly through `server.build_routers` against a fresh `--skip-deps` index of this repo).

---

## 1. The shared envelope (all nine tools)

Wire shape (contract `docs/tool-contracts.md:66-100`):

```json
{"text": "...", "items": [...], "meta": {"tool", "project", "indexed_git_head",
 "live_git_head", "index_stale", "truncated"}}
```

- Emission choke point: `ResponseEnvelope.wrap` → `_assemble_meta`
  (`python/pydocs_mcp/application/envelope.py:82-103`, `113-134`). `meta.truncated` ORs the
  truncation-ledger state with any body-level `truncated` the producer put in `extras`
  (`envelope.py:121-133`).
- Per-tool pydantic envelope models validate `structuredContent` before it leaves the server
  and are advertised as each tool's `outputSchema`
  (`python/pydocs_mcp/application/tool_response.py:215-225` `ENVELOPE_MODELS`;
  `python/pydocs_mcp/server.py:630-640` `_to_call_tool_result`; `server.py:685` return-annotation stamping).
- Meta extensions:
  - `get_references` adds `meta.resolution: "syntactic"` (contract `tool-contracts.md:102-114`;
    code `python/pydocs_mcp/application/tool_router.py:163-168`, model
    `tool_response.py:51-54`).
  - `search_codebase` / `get_why` / `grep` add `meta.suggestion: str | null` (contract
    `tool-contracts.md:116-136`; model `tool_response.py:57-67` — the field is DECLARED because
    pydantic `extra="ignore"` would silently drop an undeclared extras key). Fixed rule texts:
    `python/pydocs_mcp/application/suggestions.py:25-31`
    (`GREP_ZERO_HIT_SUGGESTION`, `GREP_TRUNCATED_SUGGESTION`, `SEARCH_ZERO_HIT_SUGGESTION`);
    each firing also emits a structured log line `{"event": "suggestion_fired", "tool", "rule"}`
    (`suggestions.py:34-36`) — explicitly built as "the Phase 2 attribution input" (`suggestions.py:35`).
- Text form: freshness header `[index: <7-char sha> · Nd old · N packages]` + optional stale
  warning (`envelope.py:35-46`), body, truncation footer
  `[truncated: N section(s) — recovery pointers inline]` + one line per ledger entry
  (`envelope.py:49-71`).
- A bare-string body means "no structured rows" — `items` degrades to `()`
  (`envelope.py:106-110` `_coerce_body`).

---

## 2. Per-tool items[] field sets — contract vs emission code

### 2.1 `get_overview`

- **Contract** (`tool-contracts.md:176-178`): `kind: str`, `id: str`, `qualified_name: str`,
  `path: str | null` ("module rows carry `path` where resolvable; `null` otherwise").
- **Model:** `OverviewItem` (`tool_response.py:70-77`).
- **Emitter:** `_overview_items` (`python/pydocs_mcp/application/tool_router.py:273-287`) — one
  row per module-map entry of the §D17 card; `id` = persisted tree `node_id` falling back to
  the qualified name; `path` = `entry.source_path or None`. Source fields come from
  `ModuleEntry` (`python/pydocs_mcp/application/overview_service.py:41-52`; `kind` default
  `"module"`, `node_id`/`source_path` populated from the ranked tree node at
  `overview_service.py:248-249`).
- **No line spans** in overview rows (path-level fidelity only).
- Multi-repo empty-selector workspace card returns a bare string body → `items == ()`
  (`tool_router.py:247-254` + `envelope.py:106-110`).

### 2.2 `search_codebase`

- **Contract** (`tool-contracts.md:198-200`): `kind` (`chunk|member|decision`), `id: str`,
  `qualified_name: str`, `package: str`, `path: str | null`, `start_line: int | null`,
  `end_line: int | null`, `score: float`.
- **Model:** `SearchItem` (`tool_response.py:79-89`).
- **Emitters** (`python/pydocs_mcp/application/multi_project_search.py`):
  - chunk rows `_chunk_item` (:211-225): `id` = SQLite chunk id (stringified), span keys read
    from chunk metadata `ChunkFilterField.START_LINE/END_LINE` ("schema-v15 span keys"),
    `path` from `ChunkFilterField.SOURCE_PATH`, `score` = `chunk.relevance`.
  - member rows `_member_item` (:228-243): span resolved BEST-EFFORT from the owning
    project's document tree node (`_member_search_items` :246-261, `_resolve_member_node`
    :264-292); any miss (no navigator / no tree / no node) degrades that row to null
    path/span — documented degrade, not an error.
  - decision rows `_decision_item`
    (`python/pydocs_mcp/application/decision_service.py:357-373`): `path`/`start_line`/`end_line`
    are **null BY CONTRACT** ("decision locators live in `get_why`", §3.2 vs §3.6);
    `qualified_name` = `decision_key(title)`, `package` = `__project__`.
  - Which rows: `_ranked_chunks`/`_ranked_members` (:182-208) read
    `SearchResponse.candidates` (the ranked rows the token-budget formatter collapsed),
    falling back to `result`; composite formatter output is excluded
    (`ChunkOrigin.COMPOSITE_OUTPUT` filter :195). So items[] can list MORE rows than the
    token-budgeted text body renders (items capped at `limit`, text capped by the 2000-token
    composite budget — `multi_project_search.py:61-63`).
- Zero-hit path: body in `EMPTY_SEARCH_MESSAGES` → overview pointer appended +
  `meta.suggestion` (`tool_router.py:119-125`).

### 2.3 `get_symbol`

- **Contract** (`tool-contracts.md:217-218`): `node_id: str`, `kind: str`,
  `qualified_name: str`, `path: str | null`, `start_line: int | null`, `end_line: int | null`.
- **Model:** `SymbolItem` (`tool_response.py:92-101`).
- **Emitters:**
  - `depth="summary"|"tree"` → `_outline_items` / `_outline_item`
    (`python/pydocs_mcp/application/lookup_service.py:241-258`): one row per rendered outline
    node, pre-order walk mirroring the PageIndex JSON `nodes` order (:226-238). Items use
    CONTRACT names (`path`/`start_line`/`end_line`) while the text body's PageIndex JSON uses
    `source_path`/`start_index`/`end_index`
    (`python/pydocs_mcp/extraction/model/document_node.py:78-95`) — two vocabularies for the
    same span, by design (`lookup_service.py:242-244`).
  - `depth="source"` → exactly ONE row via `symbol_source._span_item`
    (`python/pydocs_mcp/application/symbol_source.py:26-47`, emitted at :125): span from chunk
    metadata (v15), `kind` recovered from the document tree so it is depth-invariant
    (:61-83); legacy pre-v15 rows degrade path/span to null (:36-37).

### 2.4 `get_context`

- **Contract** (`tool-contracts.md:230-231`): `qualified_name`, `kind`, `path | null`,
  `start_line | null`, `end_line | null`.
- **Model:** `ContextItem` (`tool_response.py:103-110`).
- **Emitter:** ONE row per resolved target — the FOCUS node only, not the closure —
  `_context_item` (`lookup_service.py:261-269`) returned as `focus_row` from
  `context_nodes` (:577-600), collected in client targets order by
  `ToolRouter.get_context` (`tool_router.py:174-195`, items at :192).
  The rendered closure nodes (the card content) are NOT itemized.

### 2.5 `get_references`

- **Contract** (`tool-contracts.md:249-254`): `from_qualified_name`, `to_qualified_name`,
  `kind`, `direction`, `path | null`, `start_line | null`, `end_line | null` — "path/span are
  those of the resolvable endpoint's *defining* node (per-call-site line numbers are not
  stored in the graph)".
- **Model:** `ReferenceItem` (`tool_response.py:113-122`).
- **Emitter:** `_reference_item` (`lookup_service.py:280-297`) via `_reference_items`
  (:518-535): rows mirror the (already limit-sliced) rendered edges 1:1; span rule — callees
  attribute the to-node, every other direction the from-node (:523-529, `_row_span` :537-554,
  `_defining_span` :556-575). Degrades to `_NULL_SPAN` (:277) for unresolved endpoints,
  synthetic `decision:<key>` nodes, and cross-repo rows (:544-547).
  `to_qualified_name` degrades to the captured `to_name` for unresolved edges (:285-291).
- **`direction="impact"` and the lookup `context` show emit EMPTY items[]** — they render
  ranked NODES, not edges, so the §3.5 edge rows don't apply
  (`lookup_service.py:352-356` docstring; impact branch :449-457 returns `()`; context branch
  :465-468 returns `()`). NOTE: `impact` is a legal `get_references.direction` on the frozen
  surface (contract `tool-contracts.md:240`), so Phase 2 must expect
  `get_references(direction="impact")` responses with zero structured rows.
- `meta.resolution = "syntactic"` from `PYTHON_CAPABILITIES["references"]`
  (`tool_router.py:163-168`).

### 2.6 `get_why`

- **Contract** (`tool-contracts.md:266-267`): `decision_id: int`, `title: str`, `status: str`,
  `locators: list[str]` (each `path:start-end` or a commit sha), `affected_files: list[str]`.
- **Model:** `WhyItem` (`tool_response.py:125-132`).
- **Emitter:** `_why_item` / `_why_items` (`decision_service.py:376-406`): one row per
  rendered record, first-seen-deduped on `decision_id`; unpersisted records (`id is None`)
  are skipped. `locators` = the record's verbatim evidence locator strings. All four modes
  emit rows: `why_search` (:207-213), `why_targets` (:263-291), `why_dashboard` (:321-340),
  and `search_codebase(kind="decision")` shares the same retrieval run but emits §3.2 rows
  instead (`search_with_items` :190-205).
- **No structured path/span fields** — file evidence is encoded inside the `locators`
  strings (`path:start-end` form) and `affected_files` (paths only, no lines).

### 2.7 `grep`

- **Contract** (`tool-contracts.md:292-294`): `path: str`, `start_line: int`, `end_line: int`
  (`start_line == end_line` unless multiline), `text: str`. All fields REQUIRED (filesystem
  tools always know their span — `tool_response.py:12-17`).
- **Model:** `GrepItem` (`tool_response.py:135-141`).
- **Emitters** (`python/pydocs_mcp/application/file_tools.py`):
  - `_span_item` (:216-222) — path uses `cand.display` (project-root-relative POSIX for
    project files, ABSOLUTE for dependency files; :12-14 module docstring, `_CandidateFile`
    :86-91, `_dependency_candidates` :451-458).
  - `output_mode="content"` → `_render_grep_content` (:271-289): one item per match span
    (capped at head_limit), `text` = the matched line (or the full multiline match).
  - `output_mode="files_with_matches"` and `"count"` → `_render_grep_per_file` (:292-301):
    one item per FILE, and the item **keeps the first-match span + matched-line text** "so
    clients can jump straight in" (:298-299) — i.e. even the path-list modes leak one line of
    file content into items[].
  - Multiline spans: `_multiline_spans` / `_multiline_end_line` (:162-180).

### 2.8 `glob`

- **Contract** (`tool-contracts.md:310`): `path: str`, `mtime: float`; mtime-descending
  ordering is contractual (:306-308).
- **Model:** `GlobItem` (`tool_response.py:144-148`).
- **Emitter:** `_mtime_entries` (`file_tools.py:308-317`) — `{"path", "mtime"}` pairs sorted
  `(-mtime, path)`; sliced to the head limit at :423. NO line spans, NO content — the only
  pure hit-list tool.

### 2.9 `read_file`

- **Contract** (`tool-contracts.md:328`): `path: str`, `start_line: int`, `end_line: int`;
  text output is `cat -n` style (:323-325).
- **Model:** `ReadFileItem` (`tool_response.py:151-157`).
- **Emitter:** `_read_window` (`file_tools.py:334-353`): exactly ONE item — the returned
  window `{"path", "start_line": offset, "end_line": end}`; empty file →
  `{"start_line": 0, "end_line": 0}` (:337). Body lines rendered `f"{ln:>6}\t{line}"`
  (:344-346).

### 2.10 Regression pins (tests already freeze these shapes)

`tests/test_structured_envelope.py` pins nearly every claim above, e.g.:
`test_structured_meta_contract_fields` (:191), `test_search_items_chunk_row_carries_source_span`
(:436), `test_search_items_member_row_without_tree_has_null_span` (:460),
`test_search_items_decision_row_keeps_locators_in_get_why` (:467),
`test_symbol_items_mirror_rendered_outline` (:505), `test_symbol_package_target_emits_no_items`
(:536 — a PACKAGE-level `get_symbol` target renders the package doc with ZERO items, matching
`lookup_service.py:396-411`), `test_symbol_source_depth_emits_one_span_item` (:542),
`test_context_items_one_row_per_resolved_target` (:560),
`test_reference_items_callers_span_from_from_node` (:608),
`test_reference_items_impact_direction_stays_empty` (:664),
`test_why_items_query_mode` (:673), `test_envelope_model_registry_covers_all_nine_tools` (:278).
Phase 2 can treat items[] shapes as regression-guarded, not merely documented.

---

## 3. D3 classification — "surfaced" (hit list) vs "inspected" (content returned)

Precise per tool/mode. "CONTENT" = verbatim (or near-verbatim) file bytes reach the model.

| Tool / mode | items[] carry | text body carries | D3 class |
|---|---|---|---|
| `get_overview` | ids + paths (no lines) | structural card: ranked module qnames + ONE first-doc-line each (`ModuleEntry.first_doc_line`, `overview_service.py:41-47`) | **hit list** (identifier-level; one-line doc extracts only) |
| `search_codebase` kind=docs (chunk rows) | pointer rows w/ path+span | `## {title}\n{chunk.text}` — the FULL chunk text, i.e. real file content sections (`formatting.py:283-293` `_chunk_piece`; budget-joined at :312-360) | **CONTENT surfaced** (chunk-sized excerpts) |
| `search_codebase` kind=api (member rows) | pointer rows w/ best-effort span | `**[pkg] mod.name(sig)** (kind)\n{docstring}` (`formatting.py:296-309` `_member_piece`) | **CONTENT-extract** (signature + docstring, not body source) |
| `search_codebase` kind=decision | pointer rows, null span | rendered decision records (mined rationale text) | **derived content** (mined, not verbatim file bytes) |
| `get_symbol` depth=summary/tree | outline rows w/ spans | PageIndex JSON: title/kind/source_path/start_index/end_index/summary per node — **NO `text` field** (`document_node.py:88-95`), so no verbatim source | **hit list / outline** (structure + generated summaries) |
| `get_symbol` depth=source | 1 span row | verbatim source in a ```python fence, ≤ `symbol_source.max_lines`=400 (`symbol_source.py:107-114`; YAML `default_config.yaml:118-119`) | **CONTENT (verbatim)** |
| `get_context` | 1 focus row per target (span) | context card: skeleton render = signatures + FULL BODIES for the most-central closure nodes under the token budget (`default_config.yaml:101-104` render: skeleton, skeleton_body_ratio 0.35) | **CONTENT surfaced** (graded-fidelity code) |
| `get_references` (callers/callees/inherits/governed_by) | edge rows w/ defining-node span | qname edge lists + counts lead; NO file content | **hit list** (identifier-level) |
| `get_references` direction=impact | **EMPTY items** | ranked qname list | **hit list**, structured-blind (text-only) |
| `get_why` | decision rows + locator strings | rendered decision records | **derived content** |
| `grep` output_mode=content | one row PER MATCH: path+span+matched line | `file:line:content` lines + `-` context lines (`file_tools.py:249-268`) | **CONTENT (matched lines + context)** |
| `grep` files_with_matches | one row PER FILE incl. first-match line text | paths only | **hit list in text, but items leak 1 content line/file** (`file_tools.py:298-299`) |
| `grep` count | one row per file incl. first-match line text | `path: N` count lines | same as above |
| `glob` | path+mtime only | paths only | **pure hit list** |
| `read_file` | 1 window row | `cat -n` numbered file content | **CONTENT (verbatim window)** |

Key D3 caveat: for grep's two per-file modes the TEXT (what the model reads by default)
is a pure hit list, while `items[]` embed one matched line per file — an instrumentation
harness deciding "was content shown?" from items alone will over-count content exposure;
deciding from text alone under-counts what a structured-content-reading client saw.

---

## 4. Line fidelity per tool (does the span map to real file lines?)

- **Chunk rows (search_codebase kind=docs):** YES for v15+ rows. `chunks` metadata persists
  the 1-indexed line span OF THE ORIGINATING DocumentNode — `ChunkFilterField.START_LINE/END_LINE`,
  comment: "Schema v15: 1-indexed line span of the originating DocumentNode — persisted so
  the tool-contracts items[] fields (path/start_line/end_line) can hydrate from store-loaded
  chunks" (`python/pydocs_mcp/models.py:152-157`). Chunkers stamp `DocumentNode.start_line/end_line`
  from AST/heading positions and `source_path` root-relative
  (`extraction/strategies/chunkers/_shared.py:115-130` `_relpath`;
  `document_node.py:62-68`). So the Phase 0 D3 chunking decision preserved line fidelity:
  chunk = tree-node projection, span = node span (hunk-level, not sub-chunk). Pre-v15 rows
  and prose chunks without spans degrade to null (`_chunk_item` guards
  `isinstance(start, int)`, `multi_project_search.py:222-223`).
  Verified live in §6: chunk rows carry real spans (e.g. `start_line: 74, end_line: 103` for
  `ResponseEnvelope` — matches the class location in `envelope.py`).
- **Member rows (kind=api):** best-effort tree-node span; null on any miss
  (`multi_project_search.py:246-292`). File-level fidelity NOT even guaranteed (path may be
  null too).
- **Decision rows (kind=decision):** NO span ever (contract rule). Hunk-level metrics must
  route through `get_why.locators` (`path:start-end` strings) instead — but note locators are
  STRINGS needing parsing, and may be commit shas rather than file spans
  (`tool-contracts.md:266-267`; `decision_service.py:381-384`).
- **get_symbol / get_context:** real DocumentNode spans (same provenance as above).
- **get_references:** spans are the DEFINING NODE of the attributed endpoint — NOT call
  sites. "per-call-site line numbers are not stored in the graph"
  (`tool-contracts.md:252-254`; `lookup_service.py:523-529`). Phase 2 must NOT mark
  get_references evidence as hunk-accurate-at-use-site; it is hunk-accurate-at-definition.
- **grep / read_file:** exact live-disk line numbers (computed by enumerate/splitlines,
  `file_tools.py:154-159`, `334-346`). NOTE freshness: these are LIVE lines; the indexed
  tools' spans are AS-OF-LAST-INDEX lines — after edits without reindex the two disagree, and
  the only signal is commit-granularity `meta.index_stale` (`tool-contracts.md:372-394`).
- **glob / get_overview:** file-level (glob) / path-level (overview) fidelity only, no lines.

---

## 5. Hit counts + truncation reporting, per tool

Structured side: **the ONLY structured signals are `meta.truncated: bool` (all nine) and the
row count `len(items)`. No tool reports a numeric total-hits in meta.**

- Envelope: `meta.truncated` = ledger non-empty OR producer `extras["truncated"]`
  (`envelope.py:100`, `:121-133`); text footer lists each elision with a recovery pointer
  (`envelope.py:49-71`).
- `search_codebase`: token-budget elisions record `"N result(s) elided by the token budget"`
  ledger entries (`formatting.py:330-352`, `:433-444`); count of surviving items is capped by
  `limit` (YAML `search.output.default_limit=10`, `max_limit=1000`,
  `default_config.yaml:109-110`; `SearchInput.limit` `mcp_inputs.py:249-259`).
- `get_references`: text lead carries counts — `"N references found (R resolved, U
  unresolved[, C cross-repo])."` (`formatting.py:549-561`); a FULL page (`row_count == limit`)
  records "exactly {limit} rows returned — possibly more exist; raise
  reference_graph.output.default_limit" (`formatting.py:564-584`) → sets `meta.truncated`.
  Defaults `reference_graph.output.default_limit=50`, `max_limit=1000`
  (`default_config.yaml:77-78`).
- `get_symbol(depth="source")`: cap `symbol_source.max_lines=400`; over-cap adds body line
  `"[… N more lines — read {path} directly]"` + ledger entry (`symbol_source.py:114-124`).
- `grep`: `truncated` extras key when spans/files exceed the effective head limit
  (`file_tools.py:276`, `:293`, `_truncation_meta` :304-305); default
  `files.grep_head_limit=100`, ceiling `files.max_head_limit=10000`
  (`default_config.yaml:113-116`). `count` mode's per-file counts are TEXT-ONLY (`path: N`,
  `file_tools.py:297`). Zero-hit body `"No matches."` + suggestion.
- `glob`: `truncated` extras when matches exceed the limit (`file_tools.py:421-423`); default
  `files.glob_head_limit=100`. Zero-hit body `"No files matched."`.
- `read_file`: continuation marker `"... (file continues: N more lines; re-read with
  offset={end+1})"` + `truncated` extras (`file_tools.py:348-353`); default
  `files.read_limit=2000` lines.
- `get_overview` / `get_context` / `get_why`: no numeric totals; ledger-driven
  `meta.truncated` only (e.g. package-doc cap `formatting.py:407-419`).

---

## 6. Real sample envelopes (measured this session)

### 6.0 How the samples were produced (and one honest caveat)

Four attempts to index THIS worktree project-only
(`.venv/bin/python -m pydocs_mcp index . --skip-deps`, inspect and `--no-inspect` variants)
accumulated 15–77 CPU-minutes each without reaching the embed/commit phase (db stayed at the
136 KB schema-only state, no `.tq` sidecar; the last log line was always
`SearchBackend=SqliteCompositeBackend: lexical✓ dense✓ …`). Three of the four were my own
overlapping launches and were killed to stop the contention; the fourth (static mode,
`--no-inspect`) also disappeared without writing its exit marker. Root cause not diagnosed
this session — the worktree corpus is simply large (extraction alone runs many minutes) and
4-way CPU contention made every run look hung. **The envelope SHAPES are
corpus-independent**, so live capture was completed against a purpose-built 7-file fixture
project which indexes in 7s:

```
cd <scratchpad>/miniproj && .venv/bin/python -m pydocs_mcp index . --skip-deps --no-inspect
# → "Project: 16 chunks, 3 symbols, 5 trees" / "Done: ... (db: 156 KB)"
```

Fixture: `miniproj/` (pyproject + `miniproj/core.py` UoW class + `miniproj/render.py` caller +
README + `docs/adr/0001-unit-of-work.md`). Driver: scratchpad `sample_envelopes_mini.py` —
builds the REAL router stack via `pydocs_mcp.server.build_routers(config,
db_path=cache_path_for_project(miniproj))` and measures `len(json.dumps(resp.structured()))`.
Full transcript: scratchpad `mini_samples.out` (258 lines). All 14 calls returned EXIT=0.

### 6.1 Measured payload sizes (fixture corpus — lower bound on real sizes)

| call | structured bytes | text bytes | items |
|---|---|---|---|
| search_codebase kind=any limit=5 | 3030 | 1751 | 5 |
| search_codebase kind=api limit=3 | 583 | 212 | 1 |
| get_overview(package=__project__) | 1827 | 989 | 5 |
| get_symbol depth=summary | 2206 | 1134 | 4 |
| get_symbol depth=source | 644 | 270 | 1 |
| get_context (1 target) | 755 | 423 | 1 |
| get_references callers | 948 | 302 | 2 |
| get_why (query mode) | 921 | 542 | 1 |
| grep files_with_matches | 330 | 61 | 1 |
| grep content | 354 | 85 | 1 |
| grep count | 509 | 86 | 2 |
| grep zero-hit | 444 | 148 | 0 |
| glob head_limit=5 | 383 | 84 | 2 |
| read_file limit=15 (truncated) | 866 | 577 | 1 |

D2 sizing observations: (a) `text` dominates and `items` add roughly 40–120% on top of text
for list-shaped tools (each SearchItem row ≈ 200 bytes serialized, GrepItem ≈ 100–130 bytes,
SymbolItem ≈ 180 bytes); (b) at production defaults the text body is budget-capped
(search composite budget 2000 tokens ≈ 8 KB chars, `multi_project_search.py:61-63`;
read_file 2000 lines; grep 100 entries), so a worst-case default-config envelope is
tens-of-KB, not MB — ESTIMATE, not measured on a full corpus; (c) the envelope duplicates
the full text inside `structuredContent.text` on top of the MCP text content block, so the
wire carries every body twice (contract §2 dual-form, `tool-contracts.md:53-64`).

### 6.2 Sample envelopes (trimmed; full versions in scratchpad `mini_samples.out`)

**search_codebase — chunk row with real span:**

```json
meta: {"tool": "search_codebase", "project": "miniproj", "indexed_git_head": null,
       "live_git_head": null, "index_stale": false, "truncated": false}
item[0]: {"kind": "chunk", "id": "12", "qualified_name": "miniproj.core.run_atomic",
          "package": "__project__", "path": "miniproj/core.py",
          "start_line": 22, "end_line": 25, "score": 0.8553546071052551}
item[1]: {"kind": "chunk", "id": "1", "qualified_name": "README.md#miniproj",
          "package": "__project__", "path": "README.md",
          "start_line": 1, "end_line": 4, "score": 0.8161823153495789}
```

Text body renders the FULL chunk source (`## def run_atomic()` + the 4-line function body +
`→ get_symbol(...)` pointer). Spans verified against the fixture file: `run_atomic` really
occupies lines 22–25 of `miniproj/core.py`. Markdown-heading chunks carry a `#fragment`
qname (`README.md#miniproj`).

**get_references — meta.resolution + defining-node spans:**

```json
meta: {..., "truncated": false, "resolution": "syntactic"}
item[0]: {"from_qualified_name": "miniproj.render", "to_qualified_name":
          "miniproj.core.MiniUnitOfWork", "kind": "imports", "direction": "callers",
          "path": "miniproj/render.py", "start_line": 1, "end_line": 9}
item[1]: {"from_qualified_name": "miniproj.render.render_commit_report", ...,
          "kind": "calls", ..., "start_line": 6, "end_line": 9}
```

Spans are the CALLER's defining node (whole module 1–9; whole function 6–9) — not the call
site — confirming the §4 defining-node rule live. Text lead: `2 references found (2
resolved, 0 unresolved).`

**grep files_with_matches — the content leak into items:**

```json
text body: "miniproj/core.py"          # path only
item[0]: {"path": "miniproj/core.py", "start_line": 4, "end_line": 4,
          "text": "class MiniUnitOfWork:"}   # first-match LINE CONTENT in items
```

**grep zero-hit — meta.suggestion:**

```json
meta: {..., "suggestion": "[suggestion: no exact matches — for conceptual queries, try search_codebase(query=\"...\")]"}
text: "No matches.\n[suggestion: no exact matches — ...]"
```

**read_file — truncation:**

```json
meta: {..., "truncated": true}
item[0]: {"path": "miniproj/core.py", "start_line": 1, "end_line": 15}
text tail: "... (file continues: 10 more lines; re-read with offset=16)"
```

**get_why — locators observed:**

```json
item[0]: {"decision_id": 1, "title": "ADR 0001: Route all writes through a unit of work",
          "status": "active", "locators": ["docs/adr/0001-unit-of-work.md"],
          "affected_files": []}
```

### 6.3 Nuances discovered ONLY by running (not visible in contract or code reading)

1. **`get_why.locators` can be a BARE path** — the contract says "each `path:start-end` or a
   commit sha" (`tool-contracts.md:267`) but the observed ADR-file locator has NO `:start-end`
   suffix. Phase 2 locator parsing must accept `path`, `path:start-end`, AND sha forms.
2. **`get_symbol(depth="source")` on a CLASS returns only the class-header chunk text**
   (class def + docstring; method bodies live in child chunks) while the item span covers the
   whole class range (4–19). Rendered-text coverage ≠ span coverage; a harness diffing
   "what source did the model see" against the span will over-credit.
3. **Member-row `score` can be `0.0`** for a rendered hit (`relevance=None` → `float(m.relevance
   or 0.0)`, `multi_project_search.py:243`) — score 0.0 must not be read as "no relevance
   signal available" vs "genuinely scored 0"; the two are indistinguishable.
4. **`indexed_git_head` is `null` and the header reads `[index: unstamped · …]` for a
   non-git-repo project** even after a successful index pass — `index_stale` stays `false`
   (both-heads rule). Attribution code must not treat `null` head as "never indexed".
5. The `-v index` runs' own startup log line `SearchBackend=...: lexical✓ dense✓ multi✗ …`
   (`server.py:582-585` equivalent in CLI path) is a useful per-run capability stamp for
   trace headers.

---

## 7. Summary table — identifier atoms available per tool (for the D2 schema)

| tool | stable row id | path | span | content in items | content in text | count signals |
|---|---|---|---|---|---|---|
| get_overview | node_id (fallback qname) | rel path or null | — | no | 1-line doc extracts | text census line only |
| search_codebase | chunk/member DB id (str), decision key | rel path or null | chunk: v15 real; member: best-effort; decision: never | no (pointer rows) | YES (chunk text) | len(items); budget elisions → truncated |
| get_symbol | node_id (= qname for code) | rel path or null | real node span | no | summary/tree: outline+summaries; source: verbatim ≤400 lines | truncated on line-cap |
| get_context | qname (focus only) | rel path or null | real focus span | no | YES (skeleton code) | closure size in text |
| get_references | from/to qnames | defining-node path or null | defining node, NOT call site | no | qname lists | "N references found (R resolved, U unresolved)" text; full-page → truncated |
| get_why | decision_id (int) | in locator strings | in locator strings (when present) | mined rationale | mined rationale | none |
| grep | (path,span) | rel (proj) / abs (deps) | exact live lines | YES — matched line text in ALL modes | content mode only | count mode text `path: N`; truncated flag |
| glob | path | rel path | — | no | no | truncated flag |
| read_file | path+window | rel (proj) / abs (deps) | exact live window | n/a (text IS content) | YES verbatim | continuation line + truncated |

**Cross-cutting Phase 2 facts:** no numeric total-hits anywhere in meta (boolean `truncated`
+ `len(items)` only); all index-backed paths are index-root-relative (`_relpath`,
`chunkers/_shared.py:115-130`) and live-tool project paths are project-root-relative POSIX
(dependency paths absolute, `file_tools.py:12-14`); the model-visible text and the
machine-readable items are produced by ONE pipeline run (no re-retrieval skew —
`SearchResponse.candidates`, `models.py:421-436`), but items may enumerate MORE rows than the
token-budgeted text rendered (§2.2), so "surfaced to the model" must be judged from `text`,
not from items presence.

