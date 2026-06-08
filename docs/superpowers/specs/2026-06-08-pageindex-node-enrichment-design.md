# PageIndex node enrichment — signature, docstring, decorators

**Date:** 2026-06-08
**Status:** Approved (design forks decided with the requester)
**Branch:** `feat/pageindex-node-enrichment` — targets `main` directly. (Originally
stacked on `fix/tree-context-budget` / PR #85; that word-budget work merged to
`main` as `e5e28c4`, so this branch was rebased onto `main` and the budget
commits dropped.)

## Problem

The `llm_tree_reasoning` step serializes the `__project__` `DocumentNode`
forest into a PageIndex-style JSON tree and asks an LLM to pick the nodes
that answer a query. Each node is rendered by `_pageindex_with_qname`
(`retrieval/steps/llm_tree_reasoning.py`) as:

```json
{"qualified_name": "...", "title": "def foo()", "kind": "...", "summary": "...", "nodes": []}
```

The `title` is uninformative — literally `def foo()` with no arguments
(`ast_python.py` `_function_node`, `title = f"def {name}()"`). The LLM
therefore can't match queries that describe **inputs/outputs**
("takes a string, returns a Time"), **author intent** beyond the 140-char
summary first line, or **role markers** (`@property`, `@app.route`,
`@staticmethod`). Three cheap, high-signal discriminators are missing.

## Goal

Enrich each code node in the PageIndex representation with:

1. **Signature** — params + type hints + return annotation, multi-line-safe.
2. **Docstring excerpt** — author's own words beyond the generated summary,
   selectable depth (first-line + Args/Returns/Raises sections, OR full).
3. **Decorators** — `@property`, `@staticmethod`, `@classmethod`,
   `@app.route`, `@validates`, etc.

Scope is the **PageIndex representation only** (the LLM-visible tree). It
must not change chunk-search formatting output.

## Key grounding facts (verified in code)

- `extra_metadata` **survives** the `document_trees` persistence round-trip
  (`storage/sqlite.py` `_node_to_dict` / `_dict_to_node`, JSON-serialized).
- Function/method nodes already carry `extra_metadata["signature"]` (the
  **first physical** `def` line only — multi-line sigs are truncated) and
  `extra_metadata["docstring"]` (full). Class nodes carry `docstring` +
  `inherits_from`.
- `summary` is **already** the docstring's first line (140-char cap,
  `_shared.py` `_docstring_summary`). A "first-line-only" doc field would
  duplicate it — the doc excerpt only earns its tokens by adding the
  Args/Returns/Raises content (or the full body).
- **Decorators are captured nowhere.** `stmt.decorator_list` exists but is
  never read.
- `extra_metadata["signature"]` and `["docstring"]` flow into
  **chunk-search formatting** (`application/formatting.py`,
  `retrieval/formatters.py`) via `flatten_to_chunks`. Changing those keys
  would churn that surface — so we avoid it.
- The budget pruner (`_fit_trees_to_budget`, merged to `main` via PR #85)
  measures `json.dumps(...).split()` **words**, so any enrichment is
  automatically counted and self-throttles. No new overflow risk; but the
  pruner's `_prune_to_node_budget` rebuilds node dicts and must learn to
  preserve the new `doc` key.

## Design

### Write path (minimal, additive) — `extraction/strategies/chunkers/ast_python.py`

Add **one additive key** to function/method and class nodes:

```python
extra_metadata={..., "decorators": _decorator_labels(stmt.decorator_list)}
```

`_decorator_labels` maps each decorator AST node to `@<dotted-name>`,
reusing `canonical_dotted` (already used for class bases). A call decorator
`@app.route('/login')` becomes `@app.route` (callable name only — bounded,
high-signal; args are dropped to keep the tree tight). Non-dotted exotic
decorators fall back to `@` + bounded `ast.unparse`. Empty list → `()`.

This is purely additive: no existing key changes, no consumer reads
`decorators`, no formatter impact. The render path tolerates the missing key
on older caches (`extra_metadata.get("decorators", ())`). Existing caches are
auto-refreshed by the **schema v8 bump** below (no `--force` needed).

### Read path (PageIndex-only) — `retrieval/steps/llm_tree_reasoning.py`

New pure helpers:

- `_header_from_text(text)` — assemble the full def/class header from
  `node.text` by scanning to the first paren-depth-0 `:` (annotation colons
  live inside `()`/`[]`, so they're skipped). Multi-line-safe; whitespace
  collapsed; bounded. Render-time, so no extraction change and no formatter
  impact. `node.text` starts at the `def`/`class` line (Python 3.11
  `lineno` points at `def`, not decorators), so decorators are added
  separately from `extra_metadata`.
- `_enriched_title(node)` — `" ".join(decorators) + " " + header` for
  function/method/class kinds; falls back to `node.title` for other kinds
  and when text/header is empty. Bounded by `_TITLE_MAX_CHARS`.
- `_doc_excerpt(docstring, mode, max_chars)` — `"sections"` (first line +
  Args/Arguments/Parameters/Returns/Yields/Raises blocks, Google + NumPy +
  Sphinx field lists), `"full"` (whole docstring, whitespace-collapsed),
  `"off"` (empty). Always bounded to `max_chars`.

`_pageindex_with_qname(node, *, doc_mode, doc_max_chars)` now emits:

```json
{"qualified_name": "...", "title": "@app.route async def login(req: Request) -> Response",
 "kind": "function", "summary": "...", "doc": "Args: ... Returns: ... Raises: ...", "nodes": []}
```

`doc` is **omitted** when empty. `_prune_to_node_budget`'s `rebuild`
preserves `doc` when present.

### New step parameters (YAML-tunable, not MCP) — `LlmTreeReasoningStep`

| param | default (single-source constant) | values |
|-------|----------------------------------|--------|
| `doc_excerpt` | `_DEFAULT_DOC_EXCERPT = "sections"` | `"sections"` \| `"full"` \| `"off"` |
| `doc_excerpt_max_chars` | `_DEFAULT_DOC_EXCERPT_MAX_CHARS = 240` | `int >= 1` |

Wired through `to_dict` (omit-when-default) and `from_dict` (YAML fallback +
validation of the `doc_excerpt` enum). Documented in the three tree
pipeline YAMLs. The signature/decorator/title caps are internal constants
(`_TITLE_MAX_CHARS`), not exposed — only the docstring depth was requested
as a knob.

## Testing (TDD)

1. `_decorator_labels`: `@property`, `@staticmethod`, `@app.route('/x')` →
   `@app.route`, stacked decorators, non-dotted fallback, empty → `()`.
2. Decorator capture in `_function_node` / `_class_node`; round-trip
   survival through `document_trees` JSON serialization.
3. `_header_from_text`: one-liner, multi-line def, return annotation with
   brackets (`-> Dict[str, int]`), one-line body (`def f(): return 1`),
   class with/without bases, default values with `:` inside parens.
4. `_enriched_title`: decorators + header; non-code kind fallback; bound.
5. `_doc_excerpt`: empty, first-line-only docstring, Google Args/Returns/
   Raises, NumPy headers, Sphinx `:param:`/`:returns:`, `full` mode, `off`
   mode, char cap, whitespace collapse.
6. `_pageindex_with_qname`: enriched title, `doc` present/omitted, recursion
   over children, params threaded.
7. `_prune_to_node_budget` preserves `doc`.
8. `to_dict` / `from_dict` for the two new params incl. enum validation.

## Post-review refinements

An adversarial multi-dimension review (correctness / conventions /
integration / tests) returned **0 must-fix**; the following optional
hardening landed anyway:

- **`doc_excerpt_max_chars` validated `>= 1`** in `from_dict` (mirrors the
  `doc_excerpt` enum gate) **and** `_doc_excerpt` clamps `max(0, cap)` — a
  non-positive cap can no longer become a tail-dropping negative slice.
- **`_doc_sections` no longer leaks unrecognized NumPy sections.** A bare
  `---` underline (Notes / Examples / See Also / a horizontal rule) used to
  turn capture on; it now only continues a *recognized* header
  (Args/Parameters/Returns/Yields/Raises), symmetric with the Google-header
  path.
- **`doc` omitted when it's only a longer cut of the first line.** Previously
  a single-line docstring of 141–240 chars produced a `doc` that differed
  from the 140-char `summary` yet added no structured content. The omit
  check now also drops `doc` when it's a prefix-equal of the docstring's
  first line; a genuinely richer excerpt (first line + sections) is longer
  and survives.
- **Decorator-label bound made symmetric** — the dotted branch is now sliced
  to `_DECORATOR_LABEL_MAX_CHARS` like the `ast.unparse` fallback.
- **Over-cap truncation is no longer silent.** When emitted docstring
  excerpts hit the `doc_excerpt_max_chars` cap, `run()` logs ONE aggregated
  warning per query (count + cap + remediation), mirroring the
  `max_tree_words` over-budget warning — never per-node, and only for
  excerpts actually emitted (not ones the dedup omits anyway).
- **Characterization tests added** locking: `decorators` does not persist
  past `_chunk_to_row` (SQLite boundary), arg-dropping end-to-end through
  `build_tree`, the decorator-label bound, `_header_from_text`'s best-effort
  behavior on a `)`-in-string-default, and blank-line section termination.

## Cache refresh (schema v8)

Because neither the chunk `content_hash`
(`sha256(package\0module\0title\0text\0pipeline_hash)`) nor the node
`content_hash` (`md5(kind:title:text)`) covers `extra_metadata`, adding
`decorators` invalidates no hash — so an unchanged-files reindex would skip
the package and never refresh its trees. Excluding metadata from the hash is
*correct* (decorators aren't embedded; hashing them would force spurious
re-embeds), so the refresh is handled at the schema layer instead.

`SCHEMA_VERSION` is bumped **7 → 8** (`db.py`). v8 carries **no structural
change**; the migration is deliberately **non-destructive**: for a
v2/v3/v4/v6/v7 cache it runs the additive structure sweeps, then
`UPDATE packages SET content_hash = NULL` (mirroring
`check_integrity_and_repair`). On the next index every package is treated as
stale and re-extracted, which:

- rewrites `document_trees` **with** decorators (`reindex_package` always
  `delete_for_package` + `save_many`), and
- diffs chunks by `content_hash` — unchanged text means unchanged hash, so
  chunks **and their `.tq` / multi-vector vectors stay in place (no
  re-embed)**.

Every row survives the migration; the stale (decorator-less) trees keep
serving BM25/dense/tree queries until re-extraction replaces them, so there is
no empty-cache window. (v5 / unknown stamps still fall through to the full
drop-and-recreate path — v5 was a deliberate fast-plaid wipe.)

## Out of scope (noted for follow-up)

- Improving the persisted `extra_metadata["signature"]` to be multi-line
  (would improve chunk-search formatting too, but widens blast radius).
- Including decorator **arguments** (`@app.route('/login')` path) in the
  tree.
- Decorator capture in any non-AST chunker (markdown/notebook have no code
  decorators).
