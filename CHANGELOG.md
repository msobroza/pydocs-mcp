# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Two themes this release.

Reference graph: it goes multilanguage. Per-language tree-sitter analyzers
capture CALLS / INHERITS / IMPORTS edges (plus import-alias tables) for Rust,
C, JavaScript, TypeScript/TSX, and Java behind the existing `get_references`
surface, attributed to the same top-level symbols the multilanguage chunker
persists. Capability declarations are honest per bundle: `meta.resolution`
reports `syntactic` only for a bundle indexed with the language's grammar
loaded. No new tools, parameters, or envelope fields.

Chat UI: the `harness-ask-your-docs` page gains an activity panel that says
what each turn did — its steps, the files it touched and the model's reasoning
when the endpoint returns it — and now holds one `pydocs-mcp serve` child per
browser session instead of one per tool call. The **Connection** dialog gains
model settings (Thinking, Temperature, Max output tokens, and Top p / Seed
under More), showing only the controls the chosen model and endpoint can
honour and pre-filling the maker's recommended values where a model card
publishes them. Light mode is readable again.

### Added

- Per-language reference analyzers for `.rs`, `.c`/`.h`, `.js`, `.ts`/`.tsx`,
  and `.java` (`extraction/strategies/analyzers/`), joinable by construction
  with the persisted document trees.
- Java end-to-end: extension ceiling, structural chunker spec
  (classes/interfaces/enums/records), grammar wheel, analyzer.
- A loadable-grammar fingerprint salt in the package-level content hash:
  deployments indexed while grammars were unavailable re-extract automatically
  once grammars appear (no file touch needed).
- **A chunk-tree salt in the package-level content hash, so a chunker change
  reaches an existing index on its own.** The hash folded paths and mtimes, the
  exclusion fingerprint, the grammar fingerprint and the pipeline identity —
  nothing about what the chunkers emit. So changing a chunker left every cached
  package untouched, and the only cures were touching the files or
  `pydocs-mcp index . --force`; both fixes below shipped with exactly that
  instruction. The salt has two halves: a hand-bumped `CHUNK_TREE_RULE_VERSION`
  for chunker rules that live in code, and a digest of the tree-sitter query
  table (query text, grammar module, accessor and item-kind map per extension)
  so a query edit invalidates on its own and cannot be forgotten. The digest
  reads the rendered queries, not the code that renders them, so refactoring the
  renderer costs nobody a re-extraction. Bumping either half re-extracts every
  package once and re-embeds only the chunks whose text actually moves.
- `harness-ask-your-docs`: an activity panel above every answer. One line says what the
  turn did ("Done in 6.4 s · 4 steps · 3 files · reasoning shown", or "Answered without
  searching"); one click lists the steps in plain words (each tool call led by its own
  Material icon, with its outcome
  and up to three file chips, the model's reasoning when the endpoint returns it, notes
  such as truncated results or a stale index); the sidebar's **Show technical details**
  toggle adds each call's arguments (as proposed and as sent after the scope pin), `meta`
  and a result preview. Once every tool call has returned, a "Writing the answer…" line
  sits below the panel until the answer appears (answers are not streamed yet). A separate
  sidebar caption says whether this model's reasoning is visible, learned from its answers
  and updated as each one lands (no extra calls; the model listing's advertised parameters
  are not consulted yet). Tuned under `ask_your_docs.ui.activity` /
  `ask_your_docs.ui.reasoning`; `activity.enabled: false` restores the plain spinner and
  `reasoning.capture: false` builds the stock chat model, which reads no reasoning at all.
  Everything shown is redacted (the bearer and the configured key variable), error text is
  shown as plain text, and the one `turn_activity` log record per turn holds counts only.
- `ask()` gains keyword-only `on_event` / `live` for the page's panel; with no
  `on_event` (every eval and CLI caller) the agent runs exactly as before.
- `harness-ask-your-docs`: model settings in the **Connection** dialog. Below the model
  picker sit a **Thinking** switch (`Auto · Off · Low · Medium · High`, or `Auto · Off ·
  On` for a model that only turns thinking on and off), **Temperature** and **Max output
  tokens**, with **Top p** and **Seed** under **More**; a blank field means the model's own
  default and is not sent. Only the controls the chosen model and endpoint can honour are
  shown — read from OpenRouter's listing, a LiteLLM gateway's `/model_group/info` and a
  small model-family table — and a saved value for a hidden one is not sent either. What
  was sent is visible in the **Test connection** result line (`test passed: OK · sent
  reasoning_effort=low, temperature=0.2, max_completion_tokens=4096`) and in one
  `chat_params_effective` log line that names the settings sent and dropped, never their
  values. A 400 naming a setting the request carried hides that control for the session,
  says so in the chat and is never retried on its own; **Restore hidden settings** brings
  it back, and a reply that ran out of tokens while thinking says which knob to move.
  Configured under `ask_your_docs.llm.provider` (`auto` reads the endpoint URL) and
  `ask_your_docs.llm.params` (`thinking`, `temperature`, `max_tokens`, `top_p`, `seed`;
  every other key is refused by name), with the dialog's set replacing the YAML one for the
  session. **Behind a LiteLLM proxy, `drop_params` can still remove any of these before the
  upstream call, invisibly to any client — verifying the proxy is the operator's job**; the
  first detection of such an endpoint writes a `litellm_detected` log line saying so.
- `harness-ask-your-docs`: a model whose maker publishes recommended sampling values opens
  the **Connection** dialog with them already in the fields. A Qwen3.8 model starts on the
  card's thinking-mode pair (`Temperature 1.0`, `Top p 0.95`) and swaps to the instruct pair
  (`0.7` / `0.80`) when **Thinking** is turned off. They are pre-filled, never a silent
  default: the dialog shows them, **Test connection** sends exactly them, and they take
  effect on **Apply**. A value from YAML or typed by hand wins and survives the switch; a
  number the endpoint already reports as its own default stays a placeholder instead of
  being sent twice, and **Use YAML settings** ends the recommendation for the session. The
  recommendation never reaches an eval arm: an arm that configures nothing still sends
  nothing, and its fingerprint is unchanged. Qwen3.8 also gains its own family row, so its
  real effort vocabulary is offered (`Low` is reachable; the `High` it has no effort for is
  not) — the one arm this changes is a Qwen3.8 arm pinning `params.thinking: high`, which
  now fails before the run starts instead of asking for an effort the model has no name for.
- `harness-ask-your-docs`: the sidebar's reasoning caption reads
  `Reasoning: off (your setting)` as soon as the request carries Thinking off, instead of
  waiting two answers to conclude the model shares nothing.
- `harness-ask-your-docs`: an eval arm's model settings come only from the arm itself —
  settings reaching the binding from a config file or the environment are refused by key
  name — and an arm asking for something the model cannot honour fails before the run
  spends anything. Each rollout records the provider profile, the exact settings sent and
  the mapping version beside its trajectory.

### Changed

- **One-time full re-embed + re-extract on the first index after upgrading.**
  The extension-scope fold re-embeds when the effective extension scope
  changes (it does under the stock scope configs; an overlay that already pins
  both scopes' `include_extensions` only re-extracts), and the grammar salt, the
  new chunk-tree salt and the new pipeline-identity salt
  (`pipeline:<ingestion_pipeline_hash>|tier:<embed tier>`) are folded into every
  package hash, so the project AND every dependency package re-extract once. Expected duration scales with corpus size
  like a `--force` reindex.
- The `Embedding model changed; re-embedding N package(s)` sweep is gone. It
  compared the embedder identity stamped on each package against
  `embedding.model_name`, two independently-derived strings that legitimately
  differ (a side-loaded model directory; the late-interaction preset), and it
  had never fired anyway because the stamp never landed. A changed embedder,
  pipeline YAML, extension scope or embed tier now invalidates the package
  cache through the identity salt instead.
- Project-scope discovery now indexes code files (`.js .ts .tsx .c .h .rs
  .java`) by default; dependency scope keeps the text/config default. Narrow
  `discovery.project.include_extensions` in YAML to opt out (allowlist
  semantics unchanged).
- The file watcher (`serve --watch` / `watch`) now follows the project
  discovery scope by default: `serve.watch.extensions` defaults to `null`,
  meaning every extension in `extraction.discovery.project.include_extensions`,
  so edits to indexed config and code files (`.toml`, `.rs`, …) reindex too.
  An explicit `serve.watch.extensions` list still overrides it. The watcher
  also skips the directories in discovery's fixed exclusion floor (build
  output such as `target/`, `dist/` and `build/`, tool caches, vendored trees),
  so a compiler or bundler writing its output no longer triggers a reindex;
  your `serve.watch.ignore_globs` still apply on top. **Upgrade note:** an
  overlay that restates the old `extensions: [".py", ".md", ".ipynb"]` list
  (earlier DOCUMENTATION.md samples did) counts as an explicit override and
  keeps watching only those three types; remove `serve.watch.extensions`
  from it (or set it to `null`) to follow the project scope.
- The `get_references` tool description now states that edges are syntactic
  — matched by name and import alias, not scope-resolved — and that
  `meta.resolution` reports the level per target. Description text only; no
  parameter or envelope change.
- `tree-sitter` and the five official MIT grammar wheels are required runtime
  dependencies (about 6–10 MB). Wheel-less installs still index code as
  searchable text and honestly report reference resolution as unavailable.
- `docs/tool-contracts.md` records the change (ADR 0022; amendments
  owner-ratified 2026-09-10): §2.2 says when `meta.resolution` is `unavailable`,
  §4.1 adds `.java` to the extension ceiling and states the per-scope
  defaults, §5.1 adds the two-state capability rows for the tree-sitter
  languages and states each flag's value set (`outline` / `definitions`:
  `available | unavailable`; `references`: `semantic | syntactic |
  unavailable`), and §3.5 names the tree-sitter analyzers as a
  `get_references` backend.
- `harness-ask-your-docs`: the chat model's bearer is now renewed on **403 and 407** as
  well as 401 (`ask_your_docs.llm.renew_on_status` default `[401, 403, 407]`; the accepted
  set is unchanged). Internal gateways routinely answer 403 for an expired token, so a
  token service now works behind one without configuration. If your endpoint means 403 as
  "this key may not use this model", set `renew_on_status: [401]` — otherwise each such
  failure costs one wasted renew and retry before it surfaces.

- `harness-ask-your-docs`: a question that fails after it was sent now stays in the chat
  with its steps and the redacted error ("Your question was not answered"), and a turn
  you stop stays as "Stopped by you", instead of vanishing on the next rerun. Refusals
  before any call still say "(not sent)".

- `harness-ask-your-docs`: each browser session now holds ONE `pydocs-mcp serve` child for
  all of its questions, instead of one child per tool call (a single question used to start
  up to 12). The child starts with the first question and is closed when the tab
  disconnects, the endpoint or model changes, or Streamlit's caches are cleared.
- `harness-ask-your-docs`: the chat agent is no longer shared across browser sessions.
- The `[harness-ask-your-docs]` extra now requires `streamlit>=1.59` (session-scoped
  resource caches with a release hook); the lockfile already resolved 1.59.1.
- The `[harness-ask-your-docs]` extra now requires `langchain-openai>=0.2.14,<2`. The
  floor is the release that types `reasoning_effort`, the field the **Thinking** switch
  sends; the cap is there because the chat model keeps the reasoning text an
  OpenAI-compatible endpoint already returns (OpenRouter `reasoning`, vLLM / DeepSeek
  `reasoning_content`) through two private `ChatOpenAI` hooks, and a contract test fails
  if a release renames them. The lockfile already resolved 1.1.9.

### Deprecated

- `[multilang]` is now an empty no-op alias — remove it from install scripts
  at leisure.

### Fixed

- **Exported JavaScript/TypeScript declarations get their own symbols.**
  `export class B {}`, `export function f() {}`, `export const x = …`,
  `export interface I {}`, `export type T = …`, `export enum E {}` and
  `export default class D {}` — the dominant shape in ES modules — produced
  no symbol node, because the chunker's queries only matched declarations
  sitting directly under the file root. They now produce the same
  `function` / `class` nodes as their unexported twins (the chunk keeps the
  `export` keyword and any decorator written above it), so `get_symbol` finds
  them and the CALLS / INHERITS
  edges inside them attach to the symbol instead of the file's module node.
  Export lists (`export { x }`) and anonymous `export default` expressions
  are not declarations and still get no symbol. This changes the chunk trees
  of every `.js` / `.ts` / `.tsx` file with exported declarations, so those
  chunks re-embed — covered by this release's one-time re-extract on the first
  index after upgrading, and, for anyone who indexed with an earlier build of
  this release, by the new chunk-tree salt: the query change moves the salt, so
  the affected packages re-extract on their own with no file touch and no
  `--force`.
- **Code chunks after a form feed or a lone carriage return are sliced on the
  right lines.** The tree-sitter chunker built its line list with
  `str.splitlines()`, which also breaks on `\r` alone, `\x0b`, `\x0c`,
  `\x1c`–`\x1e`, `\x85`, `U+2028` and `U+2029`, while tree-sitter's rows count
  `\n` only. After any of those characters the list ran one element ahead of
  the rows: every later symbol's chunk text started a line early and lost its
  own last line, and the character itself came back out as a newline. Lines
  now follow tree-sitter's rows, and the character stays part of its line. No
  chunk text changes for a file with only LF or CRLF line endings — the new
  splitter is proven identical to `splitlines()` on every such file in this
  repository and against node hashes recorded before the change — so no
  re-embedding is triggered by this fix. A file that does contain such a
  character keeps its drifted chunks until it is re-extracted, which the new
  chunk-tree salt now triggers on its own — the fix is a chunker rule that lives
  in code rather than in the query table, so it rides on
  `CHUNK_TREE_RULE_VERSION` rather than on the query digest. The inline decision-marker miner
  (`# DECISION:` comments) now counts chunk rows the same way, so a marker
  after such a character gets the right `file:line` locator. Reference-graph
  edges were never affected: attribution uses tree-sitter rows on both sides.
- **`get_references`: `meta.resolution` describes the index, not the serving
  process.** A bundle built while a tree-sitter grammar could not load, served
  later by a process that can, reported `syntactic` for that language over a
  graph that was never captured. Every index pass now stamps the grammars the
  bundle can vouch for (`index_metadata.loadable_grammars`; schema v17,
  additive — no re-extraction, no re-embed), and `get_references` reads the
  stamp of the bundle that answered, as it is on disk at request time — so a
  re-index by a separate `index` or `watch` process is reflected without a
  restart, and under multi-repo the value describes the bundle the answer
  came from. A complete pass stamps every grammar that loaded. A pass that
  leaves rows it did not re-check — a skipped scope that already holds rows
  (`--skip-deps` / `--skip-project`, which `serve --watch` inherits), or a
  dependency whose re-extraction failed — never widens the stamp, since those
  rows may predate the grammar; the index log names the grammars withheld
  and why. A skipped scope that holds no rows leaves nothing unchecked, so a
  `serve --skip-deps --watch` deployment picks a grammar install up on its
  next pass, and `index --force` always stamps in full. A bundle indexed with
  the grammar reports `syntactic` from any process; one indexed without it —
  or built before this release and not yet re-indexed — reports `unavailable`
  for `.rs .c .h .js .ts .tsx .java` targets until re-indexed. `.py` and
  `.md` are unaffected. Schema v16 → v17 is additive and in place; downgrading
  afterwards is not: 0.6.1 does not recognize v17, so it rebuilds a local
  cache from scratch on open and refuses a v17 read-only bundle.
- **JavaScript/TypeScript: a re-export no longer claims a local binding.**
  `export { X } from './a'` forwards `X` without introducing it into the
  exporting module's scope, and `export * as ns from './a'` binds nothing
  either — but both recorded an import alias. The reference resolver rewrites
  every later target's leading segment through that table, so a same-named
  local was attributed to the re-exported module; and because the table is
  last-write-wins, a re-export appearing after a real `import` of the same name
  overwrote that import's binding and turned a correct edge into a wrong one.
  Re-exports now contribute their IMPORTS row and nothing else. TypeScript
  recorded these aliases in 0.6.x; re-index to clear them.
- **JavaScript/TypeScript: only a binding clause can bind.** Alias parsing read
  the whole import statement, so an import-attribute clause
  (`import './m' with { raw }`) bound `raw`, and a specifier containing braces
  or a `* as` sequence (`import './a{Foo}.js'`) bound what looked like a clause
  inside the filename. Clauses are now read only from the part of the statement
  that precedes the module specifier, which is where ECMAScript puts them.
- **JavaScript: a `require` specifier ending in a quote is read literally.**
  The module string was stripped of every leading and trailing quote rather
  than one delimiter per side, so `require("./a'")` emitted a row to `a` — a
  module the file never names. Read as `a'` it is not an identifier chain and
  produces no row. Vanishingly rare, but a wrong edge.
- **TypeScript: a string inside an export clause could fabricate an import.**
  `export { totals as "sum from 'legacy'" } from './stats'` emitted an IMPORTS
  row to `legacy` — a module the file never names — and dropped the real
  `stats` row entirely. ES2022 allows an arbitrary string as an export alias,
  and the analyzer searched the statement's TEXT for the leftmost `from '…'`,
  so the clause's own string won. JavaScript and TypeScript now read the module
  off the statement's `source:` node, which the grammar has already resolved.
  Re-indexing an affected project replaces the bad rows.
- **Reference graph: a formatter's line break no longer changes the graph.**
  rustfmt and prettier wrap long call chains at the dot, and a target carrying
  internal whitespace was dropped, so `items.iter().map(f).collect()` produced a
  CALLS row and its wrapped twin produced none. Layout next to a `.` / `::`
  separator is healed, in Rust, JavaScript, TypeScript/TSX and Java, for CALLS
  and INHERITS alike. Every edge this adds is identical to the one the same code
  on one line already emitted.
- **Rust: turbofish calls are captured.** `f::<T>()` and `x.collect::<Vec<_>>()`
  matched no CALLS pattern at all. A turbofish whose type arguments sit inside
  the path (`Vec::<u8>::new()`) is still dropped.
- **Rust: `pub(crate)` / `pub(super)` / `pub(self)` / `pub(in …)` `use`
  declarations produce rows.** Only a bare `pub` was stripped, so every
  parenthesised visibility form yielded neither an alias nor an IMPORTS row.
- **JavaScript: side-effect imports and `export … from` re-exports are
  captured.** `import './x'` carries no `from` keyword and was invisible to the
  text search; `export … from` was never queried in `.js`, though `.ts` queried
  it. Minified forms (`export{X}from'./a'`) work too, since the module is read
  from the grammar rather than matched with a whitespace-bearing pattern.

  Scoped npm sources (`@scope/pkg`) still emit no IMPORTS row, now by explicit
  decision: the only mapping that would pass validation, `scope.pkg`, cannot be
  told apart from a local `scope/pkg` module or from a bundler root alias
  (`@app/`, `@src/`). See ADR 0022's v1 capture limits.
- **`--watch`: a `pyproject.toml` or `requirements*.txt` under an excluded
  directory no longer triggers a reindex.** Manifests are exempt from the
  watched `extensions` so that adding a package always reindexes, and that
  exemption skipped the directory checks as well — leaving only
  `ignore_globs`, whose shipped defaults cover `.venv/`, `node_modules/` and
  `.git/` but not `build/`, `dist/`, `.tox/`, `htmlcov/`, `target/`,
  `extern/`, `third_party/` or a virtualenv named anything else. A manifest
  there kept firing cached reindex cycles that could not change the index,
  because dependency discovery is handed the same exclusions and never reads
  it. Manifests now skip the extension allowlist only; the discovery floor and
  your `exclude_dirs` apply to them as they do to source files. A project
  whose own root lives under such a name still reindexes on its own manifest —
  every check is root-relative.
- **`--watch`: an `exclude_dirs` entry directly under the project root is now
  honored.** With `exclude_dirs = ["gen"]`, an edit to `<root>/gen/x.rs` fired a
  reindex while `<root>/src/gen/x.rs` was correctly filtered: user exclusions were
  translated into `fnmatch` globs, and `fnmatch` has no globstar, so the derived
  `<root>/**/gen/**` could not match at the first level below the root. The watcher
  now applies the user's entries with the same predicate the discovery walk uses,
  root-relative — superseding the derived-glob mechanism entirely. Directory names
  holding a glob metacharacter (`gen[1]`) are matched literally instead of as a
  character class, and anchored entries (`docs/generated`) keep matching that
  subtree only. `serve.watch.ignore_globs` is unchanged — those stay
  operator-authored `fnmatch` patterns over the absolute path.
- **`--watch` on macOS: a symlinked project root no longer disables the
  watcher's directory filtering.** macOS resolves the watched path before
  reporting events, so an unresolved symlink as the root made every
  root-relative check fall through and let build output and excluded
  directories fire reindexes. The watcher resolves its root at construction;
  the `serve --watch` and `watch` commands already passed a resolved path, so
  their behavior is unchanged.
- `harness-ask-your-docs`: Light mode is readable again. The launcher pinned Streamlit's
  own theme to dark and the sidebar's **Light mode** toggle only swapped a partial CSS
  overlay, so chat text (about 1.1:1), inline code, code-block highlighting and sidebar
  dropdowns and radios kept dark colours. The launcher now registers both palettes as
  Streamlit themes, and you switch with Streamlit's menu (**⋮** → **System** / **Light** /
  **Dark**); the in-app toggle is gone. Every text colour in both palettes clears 4.5:1
  (the light accent darkens slightly to `#096B5A`).
- `harness-ask-your-docs` turns Streamlit's file watcher off by default. With the
  `[sentence-transformers]` extra installed it printed about 1,400 benign traceback lines
  per rerun; pass `-- --server.fileWatcherType auto` to turn it back on.
- `harness-ask-your-docs`: every docs-server request (the handshake and each tool call) is
  now bounded by a 300 s timeout, so a hung child can no longer hang a question.
- `harness-ask-your-docs`: a docs server that crashed or exited is restarted once, on the
  next question, with a visible notice above the answer.
- `pydocs-mcp index --gpu` (and `serve --gpu`) against a config with
  `embedding.backend: openvino` is now refused at config load with the same error a YAML
  `device: cuda` line raises. `--gpu` applied the device through an unvalidated model
  copy, so an OpenVINO serve config indexed fine under `--gpu` and re-embedded the whole
  corpus under the OpenVINO backend identity (`backend` folds into the chunk-cache identity).
- **Symbol hits in `src/`- and `python/`-layout projects reported names such as
  `src.mypkg.core.Thing` that `get_symbol` could not resolve, and had no file or
  line span.** Project member ids now come from the same package-root rule that
  chunk ids, document-tree ids and reference-graph node ids already use, so the
  name `search_codebase` publishes (`qualified_name`, the `[[next:lookup:…]]`
  pointer, the truncation-recovery pointer) is the name `get_symbol` resolves,
  and member hits carry their file path and line span. Dependency member ids are
  byte-identical to before.
- **Upgrading an existing index:** the next `index`, `serve` or `watch` pass over
  the project source (anything but `--skip-project`) re-reads the project once.
  On its own, this fix re-embeds nothing, re-indexes no dependency, and calls no
  LLM unless `decision_capture.llm_structuring` is on (files reached through a
  symlink may be re-embedded once); the one-time full re-embed + re-extract
  listed under *Changed* still applies to that same first pass. `index
  --skip-project` never runs a project pass, so ids stay stale until a pass
  without the flag.
- **Bundles served with `serve --workspace` / `serve --db` never index**, so they
  keep the old names until their project is re-indexed.
- **Benchmark-cache users** can drop cached entries carrying the old names with
  `pydocs-eval-bench-cache evict`.
- **The index format is unchanged** (no `SCHEMA_VERSION` bump), so older and newer
  installs can share an index. Alternating between them re-reads the project on
  each switch; neither wipes it.
- **Known consequence:** package rooting can map two files to one member module id
  (`examples/a/app/main.py` and `examples/b/app/main.py` both become `app.main`).
  Chunks and document trees already collide the same way; members now match them
  rather than holding unique ids nothing can resolve, and a colliding member hit's
  span comes from whichever file's tree was stored last.
- The `[late-interaction]` extra loads on macOS 14 again: it now caps `numkong<7.5`.
  numkong >= 7.5 ships macOS-arm64 wheels built against the macOS 26 SDK that import a
  libSystem symbol (`___sme_memset`) only macOS 15+ exports, so `import numkong` died at
  dlopen and usearch — fast-plaid's index — then failed on `_nk_capabilities`. The two
  late-interaction integration tests also skip, with a reason, when the native wheels
  cannot load instead of erroring at collection.
- **`get_references` on a module target failed instead of answering.** Every
  module-only target was routed to the module outline, which then failed
  `ReferencesEnvelope` validation on MCP (`get_references failed: 27 validation
  errors …`) while the CLI printed page-index JSON and exited 0. A module target
  now answers its import graph: `callers` returns the modules importing it or
  its members, `callees` its own imports, `impact` the transitive callers of it
  and its members with its own internals excluded, and `governed_by` the
  decisions recorded against it. `inherits` on a module raises a clear
  `InvalidArgumentError` naming the target and its kind. Member fan-out is
  bounded by the new `reference_graph.impact.max_module_seeds` YAML key
  (default 32); hitting the cap records a truncation entry rather than silently
  searching less. `get_symbol(target=<module>, depth="tree")` is byte-identical.
- **`get_symbol(depth="source")` returned only a fragment for classes and
  modules.** A class returned its header chunk (class line plus docstring) and a
  module returned its docstring, both with `truncated=false`, even though the
  reported span covered the whole node. The whole span is now rebuilt from
  indexed node text: each run of covered lines is its own verbatim fence, and
  each run the index does not store is an explicit `[lines a-b not in the
  index]` marker outside the fences, closed by one note naming the gap-line
  count and the file to read. Spans are unchanged, `truncated` still means only
  "cut by a limit", and nothing is read from disk. Functions, methods, markdown
  headings, text sections and notebook cells are byte-identical to before.
- **`grep(glob="*.py")` matched only root-level files.** grep's glob was
  root-anchored POSIX glob, so the pattern both the tool description and the
  contract use as their example returned "No matches." on any project with
  subdirectories. grep's glob now follows `rg --glob` anchoring: a pattern
  without `/` matches file names at any depth, one with `/` matches the
  root-relative path, a leading `/` (or `./`) anchors at the root, and a
  trailing `/` matches everything under that directory. The `glob` tool's own
  pattern semantics are unchanged.
- **`get_overview` merged bullets onto one line.** Suppressing a follow-up
  pointer removed the line break it sat in front of, so with unresolvable
  targets — or with `output.next_pointers.enabled=false` — whole blocks ran
  together and the blank line before each heading disappeared. Pointer elision
  is now line-aware: an inline token is removed but its line break is kept, and
  a token on its own line still takes the whole line. A module entry with no
  docstring no longer renders a dangling em dash.
- **`get_overview` emitted pointers that could not be followed.** The module map
  pointed at `get_context`, which rejects module targets; dependency pointers
  were emitted for packages that are not indexed; and script pointers named the
  script rather than its callable. The module map now points at
  `get_symbol(depth="tree")`, a dependency pointer appears only for an indexed
  package, and a script points at its dotted callable only when that callable is
  a real node in the index — otherwise no pointer is emitted at all.
- **The `inherits` error text leaked CLI vocabulary into MCP.** It quoted the
  internal `show='inherits' … CLASS nodes` wording on both surfaces; it now
  names the direction, the target and the target's kind, and lists the
  directions that do accept it.
- **`lookup --help` advertised `__project__.<module>.<symbol>`**, a form that
  never resolves. Project code is addressed by its bare dotted name, and the
  help text now says so.
- **Tool descriptions.** The `get_context` example named a module target, which
  that tool rejects; it now names a class. `grep` documents its `rg --glob`
  anchoring, and `get_references` documents what a module target answers.
  Because these edits change the descriptions artifact, any local seed-anchored
  or campaign lockfile built from the previous descriptions hash is stale.

## [0.6.1] — 2026-09-10

**Eval suite.** The eval suite's `pydocs-mcp` floor raise to 0.6.0
(`[retrieval]` and `[all]`) ships with `pydocs-mcp-eval` 0.2.0 and is recorded
in [`benchmarks/CHANGELOG.md`](benchmarks/CHANGELOG.md).

### Fixed

- **The chunk-level embed skip never fired, so every index pass re-embedded every
  eligible chunk of every package — cache hits included.** The skip-set loader
  keyed its query on `state.package`, which the shipped ingestion presets only
  fill in `package_build`, their last stage. It now keys on the package name
  known from discovery. An unchanged second pass makes zero embedder calls.
- **`packages.embedding_model` was NULL in every database ever produced.** The
  embed stages wrote it to `state.package` (still `None` at that point) and
  `package_build` then built a fresh row without it. The identity now travels the
  pipeline state and lands on the package. multirepo's serve-time embedder guard
  can finally read it back for bundles with no `index_metadata` row.
- **Any pipeline change re-embedded the whole corpus and discarded the result, on
  every pass, forever.** An ingestion-YAML edit, an extension-scope change, an
  embedder swap, or `--full-dep` / `dependency_policy` moved every chunk hash but
  not the package hash, so the pass re-embedded everything and then reported a
  cache hit without persisting. The package hash now folds the same pipeline
  identity and embed tier the chunk hashes fold, so such a change re-indexes once
  and settles. `--full-dep` had been a silent no-op on an incremental index.
- **A duplicated section could be persisted without a vector.** The embed skip
  tested hash membership while the chunk diff is a multiset (#69), so a second
  copy of an already-indexed chunk skipped the embedder and was then inserted
  as a new, vectorless row. The skip is now a per-hash budget of persisted copies.
- **The late-interaction ingestion preset now honours `embedding.dependency_policy`
  and `--full-dep`.** `EmbedChunksMultiVectorStage` was cloned before the embed
  policy existed and never applied it, so under `ingestion_late_interaction.yaml`
  every dependency chunk received a ColBERT multi-vector — `dependency_policy:
  none` included. It now uses the same per-package tier as the dense stage
  (`doc_pages` by default). Existing late-interaction indexes keep the
  multi-vectors already written for now-ineligible chunks until `index --force`;
  the tier was already part of their chunk hashes, so nothing is re-embedded or
  dropped by the upgrade re-extract.
- Side-loading a local model directory (`embedding.model_name: ~/models/x`) no
  longer rewrites the embedder's reported `model_name` to the expanded path; the
  loader gets the expanded path, the identity stays as configured. Applies to the
  `sentence_transformers` and `pylate` providers.
- **`harness-ask-your-docs`: every question failed with `McpError: Connection closed`
  when the index uses an API-key embedder.** The UI started its `pydocs-mcp serve`
  child with only the MCP SDK's default variables (`HOME LOGNAME PATH SHELL TERM USER`
  on POSIX), so with `embedding.provider: openai` the child could not find its key
  (`OPENAI_API_KEY`, or the variable named by `embedding.api_key_env`) and exited
  before the handshake. The child now inherits the environment of the shell you
  launched from, so it behaves like `pydocs-mcp serve` run in that shell. That
  includes `TMPDIR` (the default FastEmbed model cache on macOS), `PYDOCS_CONFIG_PATH`
  and other `PYDOCS_*` overlays, `PYDOCS_CACHE_DIR`, and proxy and CA-bundle variables.
  Three kinds of variable are withheld: `PYDOCS_TRACE*` in any spelling (trace
  identity is set per run by the evaluation harness only), exported shell functions,
  and any value containing `${…}` (the MCP adapter would rewrite it, or log it in full
  if unresolved). A names-only JSON warning lists what was withheld.
- **`harness-ask-your-docs --base-url` / `--model` no longer write `OPENAI_BASE_URL` /
  `LLM_MODEL`.** They reach the app under private `HARNESS_ASK_YOUR_DOCS_*` names and
  take the CLI tier of the documented precedence (YAML < `OPENAI_BASE_URL` /
  `LLM_MODEL` < `--base-url` / `--model` < Connection dialog), so the chat endpoint
  and model you get are unchanged. Otherwise the now-inherited environment would have
  pointed an `embedding.provider: openai` embedder with `embedding.base_url: null` at
  the chat endpoint, sending it the embedding key. If you export `OPENAI_BASE_URL` for
  embeddings, it still works, but prefer setting `embedding.base_url` in the YAML.
  An `OPENAI_BASE_URL` exported for the chat model reaches the server too, so give
  the chat endpoint with `--base-url` or `ask_your_docs.llm.base_url` instead.
- **Evaluation binding (`harness.ask_your_docs.binding`):** the serve child now also
  receives the embedder key, `TMPDIR`, proxies and CA bundles, but its configuration
  tier stays sealed as before. Inherited `PYDOCS_*` variables (except
  `PYDOCS_CACHE_DIR`) and `OPENAI_BASE_URL` / `LLM_MODEL` are withheld, so a shell
  export cannot change what an arm measures; set endpoints such as
  `embedding.base_url` in the arm's YAML. A names-only warning lists any withheld
  variable once per process.
- `scripts/validate_traced_run.py` imports `trace_subprocess_env` from
  `pydocs_mcp.observability.trace_env` again (it referenced a removed private name).
- **Package build no longer warns on missing metadata** — optional package
  metadata (such as `Home-page`) is read with `.get()` instead of indexing, so
  indexing a dependency without that field no longer emits the
  implicit-`None` `DeprecationWarning` from `importlib.metadata`.
- **`docs/tool-contracts.md` §4.1 lists the full excluded-directory floor** —
  26 names, adding `.yarn`, `bower_components`, `extern`, `third_party` and
  `crosscommitvuln`, which 0.6.0's code already excluded (owner-ratified
  amendment, 2026-09-10). A conformance test now fails whenever the floor in
  code and the contract diverge.

## [0.6.0] — 2026-09-10

Headline: the MCP surface grows from six to **nine task-shaped tools** — three
filesystem tools (`grep`, `glob`, `read_file`) join the six indexed tools — and
the whole surface is **frozen by contract**: `docs/tool-contracts.md` is the
normative inventory (rationale in `docs/adr/0001`–`0004`). No renames, no
removals — existing six-tool clients that read the text content block keep
working unmodified; clients that parsed `structuredContent.result` must
switch to `structuredContent.text` (see Changed).

**Upgrade notes (from 0.5.1).** The first 0.6.0 open migrates each index in
place, schema v14 → v16 (additive: chunk source spans in v15, the branch
tables in v16); nothing is dropped. Downgrading afterwards is not in place:
0.5.1 does not recognize v16, so it drops the tables and re-indexes from
scratch. The upgrade also re-embeds, because the extension scope is now part
of the chunk-cache identity. The first index pass re-extracts and re-embeds
the whole project: the migration clears the project's package hash so the
branch tables get filled (git checkout or not), and its discovered file set
grows under the widened default scope anyway. It also re-extracts most
dependencies: any whose installed file list includes a newly default
text/config file, which the common `*.dist-info/entry_points.txt`,
`top_level.txt` and `LICENSE.txt` make most of them. Their doc pages are
re-embedded under the default `embedding.dependency_policy: doc_pages`.
Dependencies whose discovered files are unchanged keep their cache hit and
vectors until their files next change. `pydocs-mcp index . --force` rebuilds
everything in one pass; either way, budget embedding time (or API spend with
the `openai` provider). Also check:

- Late-interaction deployments (the shipped `ingestion_late_interaction.yaml`
  preset) gain decision mining and dependency doc pages only in packages that
  re-extract; run `pydocs-mcp index . --force` once to populate them
  everywhere.
- If your environment already exports `PYDOCS_CACHE_DIR` (0.5.1 accepted and
  ignored it), 0.6.0 reads and writes bundles there instead of
  `~/.pydocs-mcp` and indexes from scratch: move the existing files or unset
  the variable.
- If your config already sets `serve.watch.enabled: true` (or
  `PYDOCS_SERVE__WATCH__ENABLED=true`), which 0.5.1 accepted and ignored,
  plain `pydocs-mcp serve` now starts the file watcher; set it to `false` to
  keep the 0.5.1 behavior.
- A config with any `pipelines:` key other than `chunk` or `member` (commonly
  a dead `pipelines.ingestion` list, which 0.5.1 ignored) now fails at load;
  select an ingestion pipeline with `extraction.ingestion.pipeline_path`
  instead.
- Multi-repo workspaces: cross-repo linking is on by default, so the first
  0.6.0 `serve` of two or more bundles runs a full link pass at startup and
  writes `pydocs-links.sqlite3` into the workspace directory. With `--db`
  bundles or an unwritable workspace it writes
  `<cache root>/links/<digest>.sqlite3` instead, and if nothing is writable
  it keeps the links in memory. `get_references` then includes sibling-repo
  rows. Set `reference_graph.cross_repo.enabled: false` to opt out; the
  workspace `get_overview` card then shows `cross-repo links: disabled`.
- Install scripts using `[ask-your-docs]` or the `ask-your-docs` command must
  switch to the `harness-` names, and clients that parsed
  `structuredContent.result` must read `structuredContent.text` (see
  Changed).
- Ask-your-docs users on the default model: `gpt-4o-mini`, still the default
  without an `ask_your_docs.llm` block, is now detected as vision-capable.
  The default `architecture: auto` therefore builds the `inline` agent, which
  adds an image-analysis section to the system prompt and an extra
  `reinspect_images` tool even when no image is attached. Pinning
  `architecture: text_react` does not remove the tool. For a text-only
  agent, set `ask_your_docs.multimodal.detection.override: false`; image
  attachments are then refused. Environments that install
  `[harness-ask-your-docs]` must allow `streamlit>=1.43`.
- Eval suite users (`pydocs-mcp-eval` 0.2.0) need pydocs-mcp 0.6.0: its
  `[retrieval]`, `[ask]` and `[all]` extras require it. Upgrade both
  together, e.g. `pip install -U pydocs-mcp "pydocs-mcp-eval[retrieval]"`.
  The eval suite's own upgrade notes are in `benchmarks/CHANGELOG.md`.

**Eval suite.** Changes to the separately published eval suite
(`pydocs-mcp-eval` 0.2.0, under `benchmarks/`) — its new datasets, optimizers,
scoring, console commands, module moves and upgrade notes — are recorded in
[`benchmarks/CHANGELOG.md`](benchmarks/CHANGELOG.md), which also rebuilds its
0.1.0 and 0.1.1 releases. The entries below mention the eval suite only where
the product itself changed.

### Security

- `mcp` dependency floor raised `>=1.0` → `>=1.28.1` (lock updated 1.27.1 →
  1.28.1) — resolves CVE-2026-52869, CVE-2026-52870, and CVE-2026-59950
  reported against mcp 1.27.1.
- `cryptography` (pulled in through `mcp` → `pyjwt[crypto]`) constrained
  `>=48.0.1` → `>=50.0.0` in `[tool.uv] constraint-dependencies` (lock
  49.0.0 → 50.0.1), which resolves PYSEC-2026-3552 in the locked and audited
  environment. The constraint is not part of the published wheel metadata,
  so `pip install pydocs-mcp` does not enforce it: upgrade `cryptography` to
  50.0.0 or later in your own environment.

### Added

- **Branch dimension, foundation (schema v16)** — every project index now stamps the
  checked-out branch (`branches`), its file manifest with git blob ids (`branch_files`),
  chunk membership with per-branch spans (`branch_chunks`), and a blob-keyed extraction
  cache (`file_extractions`); project chunks with no branch references are
  garbage-collected with their vectors. Every tool response carries an additive
  `meta.branch` field (`null` for non-git projects and the other cases enumerated in
  `docs/tool-contracts.md` §2.4). New verb: `pydocs-mcp branches` lists the indexed
  branches. Git is optional: without a `git` binary or repository, behavior is unchanged
  except for one `git_unavailable` log. The v16 migration is additive and in place, and
  the first index pass after upgrading re-extracts the project package once to populate
  the new tables. Branch stamping by itself changes no chunk content hash, but an upgrade
  from 0.5.1 re-embeds the project anyway, because the extension scope is now part of the
  chunk-cache identity (see Changed and Upgrade notes). Text output of every tool is
  byte-identical. Design:
  `docs/superpowers/specs/2026-09-03-multi-branch-indexing-design.md` (P0).
- **Multilanguage indexing: wider extension scope (ADR 0021)** — the
  extension ceiling (`ALLOWED_EXTENSIONS`) grows from `.py .md .ipynb` to
  also admit the text/config set `.toml .yaml .yml .cfg .ini .rst .txt .json`
  and the code set `.js .ts .tsx .c .h .rs`. The default
  `extraction.discovery.{project,dependency}.include_extensions` now add the
  text/config set, so a project's `pyproject.toml`, YAML configs and
  `.rst` / `.txt` docs index out of the box, as do dependencies' installed
  text/config files (including `*.dist-info` files such as
  `entry_points.txt`). Those dependency sections are BM25-only under the
  default `embedding.dependency_policy: doc_pages`; they get no vectors. Code
  extensions stay opt-in: name them in YAML `include_extensions`. Extensions
  outside the ceiling are still rejected at config load. `grep` and `glob`
  walk the same widened scope. The widening re-embeds on upgrade (see the
  extension-scope entry under Changed). Rationale:
  `docs/adr/0021-multilanguage-indexing.md`; the discovery-scope section of
  `docs/tool-contracts.md` is amended to match.
- **`TextSectionChunker` for text/config files** — one language-agnostic
  chunker for `.rst .txt .toml .yaml .yml .cfg .ini .json`: `.rst` / `.txt`
  split on reStructuredText section titles (fixed-line windows when a file
  has none), `.toml` / `.cfg` / `.ini` on `[table]` / `[[array]]` header
  lines, `.yaml` / `.yml` on column-0 top-level keys, and `.json` on
  top-level keys. Each section becomes a searchable chunk with 1-indexed line
  spans and the new `text_section` node kind and chunk origin. A `.json` file
  with more than `extraction.chunking.text_section.json_max_chunks` (default
  50) top-level keys, or an unkeyed/minified blob over 2,000 characters,
  collapses to one module node holding a truncated 2,000-character preview.
  `extraction.chunking.text_section.window_lines` (default 80) sizes the
  fallback windows, the `[multilang]` chunker's included. Empty or malformed
  files degrade to a single module node instead of failing the build.
- **`[multilang]` extra: structural chunking for JS/TS/C/Rust** —
  `pip install 'pydocs-mcp[multilang]'` adds `tree-sitter>=0.25,<0.26`
  (capped because 0.26.0 has a use-after-free in `QueryCursor.matches()`)
  plus the individually MIT-licensed grammar wheels
  `tree-sitter-javascript`, `-typescript`, `-c` and `-rust`. The new
  `MultilangChunker`, registered for `.js .ts .tsx .c .h .rs`, extracts
  top-level symbols (functions, classes, structs, enums, traits, impls,
  interfaces and similar) with 1-indexed spans; it runs only on code
  extensions you opt into via `include_extensions`. Without the extra those
  files still index as fixed-line text windows, and the build logs one
  structured `multilang_fallback` JSON warning per extension carrying the
  install hint; a file that fails to parse or has no top-level symbols gets
  the same windows. `tree_sitter` is imported lazily, so
  `import pydocs_mcp` never loads it.
- **Per-project directory exclusions** — YAML
  `extraction.discovery.project.exclude_dirs` and
  `extraction.discovery.dependency.exclude_dirs` (both default `[]`), plus
  an `exclude_dirs` list in the `[tool.pydocs-mcp]` table of the indexed
  project's own `pyproject.toml` (project scope only; a dependency's
  `pyproject.toml` is never read). Bare names match a directory at any
  depth; entries containing `/` anchor at the project root, or for
  dependencies below the first path component of each installed file (so
  `docs/examples` excludes `<pkg>/docs/examples/` in every dependency). No
  globs (`*`, `?`, `[` are literal); matching is case-sensitive and names
  directories only. Entries only add to the built-in floor, never remove
  from it. A non-string, absolute or empty entry, or one with an empty, `.`
  or `..` segment, is an error: in YAML it fails config load; in
  `pyproject.toml` it (like a non-list value) raises
  `ProjectExcludeConfigError` at index time, and under `--watch` that error
  is logged and the reindex cycle skipped. A `pyproject.toml` that cannot
  be read or parsed only logs a warning and applies no project entries. In
  the project walk every indexing reader honors them (chunks, symbols,
  mined decisions for `get_why`, dependency manifests), as do `grep` /
  `glob` and the file watcher's ignore globs; under `--watch`,
  `pyproject.toml` edits apply on the next reindex without a restart (YAML
  changes need one). `dependency.exclude_dirs` prunes dependency files
  (their chunks and trees, and dependency-scope `grep`) but not dependency
  symbols, since dependency member extraction (live import, or an AST pass
  over every shipped `.py` file with `--no-inspect`) ignores exclusions.
  With no user entries every package content hash is unchanged; adding or
  changing entries re-extracts the affected packages once, which for
  `dependency.exclude_dirs` means every dependency.
- **Cross-repo reference linking in multi-repo workspaces** — when
  `serve --workspace <dir>` or repeated `--db` loads two or more bundles, a
  link pass resolves each bundle's still-unresolved references against its
  siblings' symbols, so `get_references` crosses repository boundaries:
  `callers` / `inherits` / `governed_by` add rows from sibling repos (marked
  `(project: <name>)` and counted as `, N cross-repo` in the summary line),
  `callees` replaces an unresolved callee with its resolved sibling target,
  and `impact` walks into sibling repos. References the local index already
  resolved always win; cross rows only add what one bundle could not see.
  Re-exports resolve through the sibling's import graph when exactly one
  candidate matches. Cross rows in `items[]` carry `null` `path` /
  `start_line` / `end_line`. Links live in a disposable sidecar:
  `pydocs-links.sqlite3` in the workspace directory; with `--db` bundles, or
  when the workspace directory is not writable,
  `<cache root>/links/<digest>.sqlite3`; if neither is writable, in memory,
  recomputed at every serve. Bundles are never modified and their schema is
  unchanged. On by default and tuned under `reference_graph.cross_repo`:
  `enabled: true` (inert with one bundle); `link_on_serve: true` (refresh
  stale links at serve startup; `false` serves detection-only, dropping
  edges that touch a stale or removed bundle and warning to run
  `pydocs-mcp link`); `match_scope: project_only` (`all_packages` also
  matches dependency symbols); `kinds: [calls, imports, inherits, governs]`
  (`mentions` / `similar` opt in); `max_projects_per_walk: 8` (sibling repos
  one `impact` walk may enter, 1-32); `workspace_scores: true` (workspace
  ranking for `impact`: in-degree always, PageRank with the `[graph]`
  extra); `alias_resolution: imports_graph` (`off` disables re-export
  resolution); `similar.{top_k: 5, min_score: 0.6}`; and `overlay_dir`
  (puts the sidecar there instead of any default location). Opt-in
  `similar` edges re-embed the source repo's chunks with the serving
  embedder and search the sibling's `.tq`; a pair is skipped unless both
  bundles' stamped embedder identity matches the serving embedder. New
  operator verb `pydocs-mcp link --workspace DIR` (or `--db A --db B`) runs
  a full pass and prints per-project counts (exit 2 if no overlay location
  is writable; exit 0 with fewer than two bundles); `--check` writes
  nothing and exits 1 when links are stale or missing, or a linked bundle
  has left the workspace. The no-selector multi-repo `get_overview` card
  gains a `cross-repo links:` line (`fresh`, `stale(<projects>)`,
  `stale(unlinked)` or `disabled`). One-shot CLI queries read links
  persisted by an earlier `serve` or `link` but never run a pass. A
  workspace `.db` whose name starts with `pydocs-links.` is never loaded as
  a bundle. No new MCP tool or parameter; see Upgrade notes. Design:
  `docs/superpowers/specs/2026-07-11-multirepo-cross-linking-spec.md`.
- **Opt-in server-side tool-call tracing** — a new `trace:` YAML block
  (`trace.enabled`, default `false`; `trace.dir`) makes `pydocs-mcp serve`
  record every MCP tool call, successful or raising, to
  `<trace.dir>/<trajectory_id>/server_events.jsonl`: a header line
  (trajectory id, trace `schema_version` 1, the served description
  artifact's hash, installed `pydocs-mcp` and `mcp` versions, timestamp),
  then one sorted-key JSON line per call with its raw arguments, latency and
  a monotonic per-process `seq`. A successful call adds its per-item
  identifiers, `hit_count`, `meta.truncated`, `meta.suggestion` and a
  2048-byte preview, and stores its full serialized result in a shared
  content-addressed `<trace.dir>/blobs/<sha256>`; a raising call records the
  error's type and message and its direct cause's type. Fired routing
  suggestions get their own lines, keyed to the call's `seq`. The per-run id
  comes from `PYDOCS_TRACE__TRAJECTORY_ID`; it is deliberately not
  documented as a YAML key, since a fixed id would make every run collide.
  `PYDOCS_TRACE__ENABLED` / `PYDOCS_TRACE__DIR` override the YAML keys.
  Tracing enabled without an id or a directory fails startup with
  `TraceStartupError`, and an id whose trace file already has content fails
  with `TrajectoryIdReuseError` (both exported from
  `pydocs_mcp.observability`). Unknown `trace:` keys are rejected. No tool
  schema changes; with tracing off the server is a plain `FastMCP` as
  before. Traces hold raw arguments and full results in plain files, so keep
  `trace.dir` private. No new dependency. Rationale: `docs/adr/0009`–`0010`.
- **The external CLI harness ships in the product wheel** — a second in-tree
  harness, and the first *composed* one. `pydocs_mcp/harness/external/` owns a
  run's corpus, trace, guidance policy and trajectory, and delegates only "what
  is the command line" and "what does the transcript say" to a CLI coding agent
  ENGINE under `pydocs_mcp/harness/cli_agents/` (a CLI agent is an engine, not a
  harness: several engines run under one harness, sharing its guidance sections,
  while the engine name is recorded separately). It satisfies the same harness
  run contract as the in-process agent — one sample in, one trajectory out, with
  both observation points joined — and needs **no optional extra**: the engine is
  driven with stdlib `subprocess`, so a plain `pip install pydocs-mcp` can run
  it. Adding another CLI agent is one adapter subclass plus one registry line,
  checked by a shared adapter conformance battery. Its guidance partition/fold
  lives in the new `harness/core/guidance_fold.py`, parameterized on the harness
  name (the in-process ask-your-docs harness keeps its own), and the three
  trace-correlation environment variables have exactly one spelling
  (`observability/trace_env.py`), shared by both harnesses.
- **The harness run contract** (`pydocs_mcp.harness.core.run_contract`) — the
  port every agent harness implements: `HarnessRunner` (one sample +
  guidance sections in, one `Trajectory` out), with tool calls derived from
  the server-side trace (`observed_by: server|client` provenance) and typed
  failure semantics (`UndeliverableGuidanceError`, `TurnBudgetExceededError`).
  Companions: a product-side trace reader
  (`pydocs_mcp.observability.trace_reader`), the ask-your-docs harness
  binding (`pydocs_mcp.harness.ask_your_docs.binding` — factory
  `make_harness_runner`, declared guidance delivery map, one serve session
  held open for a whole traced run), the public `parse_skill_artifact`
  entrypoint on the skill-artifact loader, and six harness-private
  `build_agent` keywords (`tool_names`, `skill_override`, `task_name`,
  `scope_pin`, `subprocess_env`, `mcp_tools`) whose defaults together
  reproduce the previous build byte-for-byte. `tool_names` can only narrow
  the tools the server advertises: an unknown name or an empty tuple raises
  `ToolBindingError` instead of binding a smaller surface. `mcp_tools` hands
  over tools already bound to a caller-owned session, so no serve subprocess
  is spawned. The serve subprocess's stdio connection (argv plus
  `subprocess_env`) is built by one helper, `serve_connection()`, which
  `build_agent` and the binding both use.
- **The packaged search-guidance skill artifact** — one delimited document
  (`pydocs_mcp.harness.core.skills`) in three tiers, every section
  required: the shared `BACKBONE` search policy, one harness-invariant
  `TASK_HEAD: <task_name>` section per task name (every harness running a task
  reads and updates the same one), and one
  `HARNESS_TASK_HEAD: <harness>.<task_name>` section per harness/task pair for
  per-harness convention. The v1 task names are `repo_qa`
  (repository-comprehension QA), `vuln` (security needle-search) and `bug_loc`
  (file-level bug localization: name the file(s) a described bug requires
  changing); the section count is derived from that enumeration times the two
  harness names — ten today — so widening it is a single, reviewed edit. A task name names a FRAMING, not
  a corpus — several corpora share one task head, which is the tier's whole
  point, and evaluation dataset names and task-id prefixes are a separate
  vocabulary this one never touches.
  Loaded and firewalled by
  `pydocs_mcp.harness.core.skill_artifact_loader` (strict parse against the
  enumerated section set, per-section token caps); the shipped seed is
  hand-written, and an explicitly named override that is missing or invalid
  is a hard error, never a silent fallback.
- **Three filesystem tools: `grep`, `glob`, `read_file`** — exact-string /
  regex search (Python `re` flavor; `content` / `files_with_matches` / `count`
  output modes; the flag parameters are the literal names `-i`, `-n`, `-A`,
  `-B`, `-C` on the MCP wire), file-name matching (`**` recursion, results
  ordered by modification time, newest first), and line-numbered file reads
  (`cat -n` style, so line references round-trip with `grep` output). `grep`
  and `glob` walk the **indexer's discovery scope** — the same excluded-dirs
  floor, extension allowlist, and size cap the semantic index sees, not
  `.gitignore`; `read_file` reads any file inside the project root or an
  indexed dependency's root, so any path another tool returns is readable.
  Every response is freshness-stamped against the index snapshot. Additive:
  MCP clients discover the tools at connect time. Each is mirrored by an
  identically-named CLI subcommand. Output caps are YAML-wired under
  `files.*`: `grep_head_limit` and `glob_head_limit` (default `100`) and
  `read_limit` (default `2000` lines) apply when the client omits
  `head_limit` / `limit`, and `max_head_limit` (default `10000`) is the
  ceiling. A larger `grep` / `glob` `head_limit` fails input validation,
  while a larger `read_file` `limit` is silently clamped to the ceiling. An
  unknown `files.*` key, or a default above `max_head_limit`, fails at
  config load. The ask-your-docs agent's system prompt now describes the
  three tools: `grep` for literal identifiers, error strings and config
  keys, `search_codebase` for ranked or conceptual questions.
- **Frozen tool contract** — `docs/tool-contracts.md` pins the nine tool
  names, every parameter schema, the response envelope (structured `items[]`
  field sets + `meta` fields), and the frozen vocabularies; changing any of it
  is a design-doc-level versioning event. Tool *descriptions* stay deliberately
  mutable (they are the substrate the description optimizer rewrites).
- **Externalized description source** — every LLM-visible description string
  (nine tool descriptions, server instructions, session-start preamble) now lives in
  one packaged delimited document, `defaults/descriptions.md`, validated at
  load (closed section set, required markers, token budgets) and swappable per
  deployment: `pydocs-mcp serve . --descriptions PATH` >
  `PYDOCS_SERVE__DESCRIPTIONS_PATH` env var > YAML `serve.descriptions_path` >
  packaged default. An explicitly named source that is missing or invalid is a
  hard startup error — never a silent fallback — and so is a set-but-empty
  `PYDOCS_SERVE__DESCRIPTIONS_PATH` (`EmptyDescriptionsEnvError`), which would
  otherwise mask the YAML key. Every run logs the fingerprint of the surface it
  serves (`descriptions artifact <hash12> source=…`). CLI `--help` renders the
  bundle named by `PYDOCS_SERVE__DESCRIPTIONS_PATH`; a YAML
  `serve.descriptions_path` override or the `serve`-only `--descriptions` flag
  changes what the server serves but not `--help`, which is built before
  either is read. Default behavior is byte-identical to the previous hardcoded
  text; authoring guide in `docs/description-authoring.md` (rationale:
  `docs/adr/0005`–`0006`).
- **Deterministic routing suggestions** — dead-end responses now carry a fixed
  `[suggestion: …]` line with the escape hatch: zero-hit `grep` redirects
  conceptual queries to `search_codebase`, truncated `grep` shows how to
  narrow (`path=` / `glob=` / `head_limit=`), and the existing zero-hit
  `search_codebase` / `get_why` overview pointer gains its own switch. One
  YAML flag per rule (`output.suggestions.{grep_zero_hit,grep_truncated,
  search_zero_hit}`, all default on); the fired suggestion also travels as the
  additive envelope field `meta.suggestion` on those three tools
  (`docs/tool-contracts.md` §2.3; rationale: `docs/adr/0007`). With a flag
  off, that rule's output is byte-identical to before.
- **Session-start context pack** — an opt-in, deterministic context block for
  agent-session start: a fixed harness-injected marker line, the
  session-start preamble from the description source, the same overview card
  `get_overview` serves, and an installed-package version inventory. Off by
  default (`serve.session_start_context.enabled`); budget-capped in real
  tokens (`serve.session_start_context.budget_tokens`, default `2000`; card
  trimmed before inventory, truncation always noted). When enabled, the
  ask-your-docs agent injects it into its prompt; the new
  `pydocs-mcp session-start-context` subcommand prints the pack for external
  harnesses regardless of the flag (rationale: `docs/adr/0008`).
- **Chunk source spans persisted (schema v15)** — chunks now carry
  `source_path` / `start_line` / `end_line` through SQLite, so structured
  items cite exact file spans. Additive in-place migration; rows indexed
  before v15 carry empty spans until the next reindex re-extracts their
  package. Within the 0.6.x line that reindex backfills spans onto unchanged
  (hash-matched) rows without re-embedding them; an index upgraded from
  0.5.1 instead gets fresh rows, with spans, for every re-extracted package,
  because the chunk-cache identity moved (see Upgrade notes).
- **OpenAI-compatible embedding endpoints from YAML** — three new
  `embedding.*` keys, read only by the `openai` provider. `base_url` points
  the client at any OpenAI-shaped `/v1/embeddings` service such as
  OpenRouter (`null` keeps the client default: `OPENAI_BASE_URL` if set,
  else api.openai.com). `api_key_env` names the environment variable that
  holds the key (`null` = `OPENAI_API_KEY`), so a third-party key never
  lives in YAML; the missing-key error names that variable.
  `send_dimensions` (default `true`) keeps sending OpenAI's Matryoshka
  `dimensions` parameter; `false` omits it, for endpoints that reject it and
  return the model's native size. Three remote models join the
  known-dimension table, so a config naming one must set `embedding.dim` to
  its native size or fail at load: `mistralai/codestral-embed-2505` (1536),
  `qwen/qwen3-embedding-4b` (2560), `qwen/qwen3-embedding-8b` (4096). Only
  `send_dimensions: false` changes the embedder hash; `base_url` /
  `api_key_env` are never hashed, so moving a model id to another endpoint
  keeps its vectors. Flipping `send_dimensions` does not invalidate the
  package-level cache, so only re-extracted packages re-embed; run
  `pydocs-mcp index . --force` to re-embed everything.
- **Query-embedding cache** — `serve` and the CLI query commands now wrap
  query-time embedding in an in-process LRU cache. A repeated query skips
  the model, and concurrent identical queries (a multi-repo fan-out, or a
  hybrid pipeline's fetcher and scorer embedding the same text) share one
  computation. It is on by default and tuned under `embedding.query_cache`
  (`enabled: true`, `max_entries: 512`, `ttl_seconds: 0`, meaning no
  age-based expiry; env `PYDOCS_EMBEDDING__QUERY_CACHE__*`). The
  `[late-interaction]` query encoder has its own
  `late_interaction.query_cache` (default `max_entries: 128`). Cache keys are
  the embedder identity, `embedding.query_prompt_name` included, plus the
  whitespace-stripped query text. A failed embedding is never cached. Cache
  settings are not part of any pipeline hash, so changing them never forces
  a reindex, and stored document vectors are untouched. A multi-repo server
  (`--db` bundles or workspace mode) now builds the embedding model,
  multi-vector encoder and LLM client once for all bundles instead of once
  per bundle.
- **`parent_rollup` retrieval step (opt-in)** — a rerank-only step for
  chunk pipeline YAML that collapses co-retrieved siblings into their
  parent. When at least two results are children of the same symbol or
  document section (say, three methods of one class) and they cover enough
  of that parent's chunk-bearing children, they are replaced by the
  parent's own indexed chunk at the group's best rank, keeping the group's
  best relevance. The coverage threshold is per kind: `min_coverage_by_kind`
  defaults to `class: 0.3`, `module: 0.6`, `markdown_heading: 0.5`. A
  supplied mapping replaces those defaults wholesale, and its keys must be
  node-kind values with thresholds in [0.0, 1.0]. `min_coverage` (default
  `0.5`, must be in (0.0, 1.0]) covers every other kind. The two-sibling
  floor is fixed. The result list never grows. A group whose tree or parent
  chunk is missing, whose gates are unmet, or whose metadata is malformed is
  passed through unchanged. Place it after `top_k_filter` and before
  `limit`. No shipped pipeline enables it, so default retrieval is
  unchanged; see `DOCUMENTATION.md` for a YAML example.
- **`PYDOCS_CACHE_DIR` relocates the bundle root** — when set, per-project
  `.db` / `.tq` (and `.plaid`) bundles live under it instead of
  `~/.pydocs-mcp` (a leading `~` is expanded; an empty value is ignored).
  Precedence: `--cache-dir` > `PYDOCS_CACHE_DIR` > `~/.pydocs-mcp`. Child
  processes inherit it, so one export relocates a whole shell session. The
  multi-repo cross-link overlay's home-cache file
  (`<root>/links/<digest>.sqlite3`, used with explicit `--db` bundles and,
  in workspace mode, when the workspace directory is unwritable) follows
  `PYDOCS_CACHE_DIR` but not `--cache-dir`;
  `reference_graph.cross_repo.overlay_dir` still overrides both. The YAML
  `cache_dir` key is still not read. 0.5.1 accepted this variable but
  ignored it; see Upgrade notes.
- **Multimodal ask-your-docs agent** — a new top-level `ask_your_docs:`
  config block (validated at load, unknown keys rejected; env overrides as
  `PYDOCS_ASK_YOUR_DOCS__<KEY>__<SUBKEY>`) and image attachments in the chat
  UI: PNG / JPEG / WebP / GIF, up to `images.max_per_turn` per question
  (default 3; extra files are dropped with a warning), each at most
  `images.max_bytes` (default 5,000,000; a larger file is dropped with an
  error). `ask_your_docs.architecture` (default `auto`) picks the agent from a
  registry: `text_react` (the image-free ReAct agent), `inline` (one prompt
  answers and sees; image tokens ride on every ReAct step), `vision_subagent`
  (a separate describe hop; image tokens are paid once per turn) and `auto`.
  `auto` builds `vision_subagent` when `ask_your_docs.llm.vision` names a
  second model (see the next bullet), `text_react` when the model is
  text-only, and otherwise `multimodal.preferred_architecture` (default
  `inline`). Selecting `inline` or `vision_subagent` for a model that cannot
  see fails at build, with the fix in the message. Unless
  `ask_your_docs.llm.vision` settles it, vision capability comes from the
  ladder under `multimodal.detection`: `override` (`true` / `false` /
  `null`), then a model-name prefix table (`static_table`, on), then two
  opt-in probes, both off: `endpoint_probe` (`/models` metadata; needs a base
  URL) and `image_probe` (one tiny-image call). A model no rung recognizes
  counts as text-only. Images sent when no configured model can see them are
  refused with the fix spelled out, or, under
  `multimodal.text_only_fallback: describe`, answered from text with an
  explicit cannot-see note. When the model receiving images can see, the
  agent also gets a local `reinspect_images` tool (not an MCP tool) that
  re-reads images from earlier turns against a new question. The session
  keeps the last `images.session_retention` images (12; 0 disables), and each
  turn allows at most `images.max_reinspect_per_turn` vision calls (2;
  repeated calls are free). The `[harness-ask-your-docs]` extra now needs
  `streamlit>=1.43` (0.5.1's `[ask-your-docs]` needed `>=1.36`). MCP surface
  unchanged.
- **Ask-your-docs LLM connection.** One `ask_your_docs.llm` YAML block
  configures the chat model's OpenAI-format endpoint, its bearer (an internal
  token service renewed on `401` with the request retried once, or a named
  environment variable), and vision (`true` / `false` / detect / a second model
  on the same endpoint). The sidebar's four connection inputs become one status
  line — host, model, bearer, vision verdict — plus a **Connection** dialog that
  lists the endpoint's models, renews the token and tests the connection, all
  scoped to the session. Secrets stay out of YAML, argv and the UI: the dialog
  has no key field, no launch flag carries one, only a token's last four
  characters are ever shown, and every failure the page renders — a rejected
  bearer included — is redacted. A bearer that cannot be fetched, or a model
  listing that fails, degrades to a caption rather than breaking the page, and a
  question that cannot be answered is echoed back instead of lost. Both
  capability probes now use the agent's credential (without a block, the
  endpoint probe therefore carries `OPENAI_API_KEY` when that variable is set). The bearer follows the
  effective endpoint; an override on another origin, or a plain-http
  non-loopback endpoint, is flagged on the status line and in one log line. No
  block ⇒ otherwise unchanged behavior. Design:
  `docs/superpowers/specs/2026-09-05-ask-your-docs-llm-connection-design.md`.
- **Prompt overrides for the ask agent** — `build_agent` takes an optional
  `prompts=` override (`AskPrompts`, an alias of
  `pydocs_mcp.harness.core.prompt_override.PromptOverrides`) whose
  `system_prompt` replaces the shipped system prompt, and `reformulate` takes
  an optional `rewrite_template=` (a `str.format` template with `{history}` /
  `{question}`) for the follow-up rewrite. The app and CLI pass neither, so
  default prompts are unchanged. The eval suite's ask-agent optimization
  drives these seams (see `benchmarks/CHANGELOG.md`).

### Changed

- **BREAKING: the ask-your-docs harness moved under the `harness/` namespace
  with harness-scoped install names.** The console script `ask-your-docs` is
  now **`harness-ask-your-docs`**, the extra `[ask-your-docs]` is now
  **`[harness-ask-your-docs]`** (`pip install
  'pydocs-mcp[harness-ask-your-docs]'`), and the module path
  `pydocs_mcp.ask_your_docs` is now `pydocs_mcp.harness.ask_your_docs`.
  There is **no compatibility shim**: the old script name vanishes from PATH,
  and pip treats an unknown extra as a warning, so an old install command
  silently yields an agent-less install — update install scripts and MCP/CLI
  wrappers together. The move does not rename the new `ask_your_docs:` YAML
  block (see Added).
- **Extension scope is now part of the chunk-cache identity; upgrading
  re-embeds.** `ingestion_pipeline_hash` now always folds in the sorted union
  of `extraction.discovery.project.include_extensions` and
  `extraction.discovery.dependency.include_extensions`, so it changes for
  every deployment on upgrade, the default config included, and again
  whenever either list is widened or narrowed. The hash feeds every chunk's
  `content_hash`, so each chunk of a re-extracted package gets a new hash and
  is re-inserted, and re-embedded wherever the embed policy gives it a
  vector. Packages whose discovered files are unchanged keep their
  package-level cache hit and existing vectors. See Upgrade notes for the
  first 0.6.0 index pass.
- **Five more built-in directory exclusions: `extern`, `third_party`,
  `bower_components`, `.yarn`, `crosscommitvuln`** join the non-removable
  floor (`node_modules` was already there). The first four are vendored
  second-language trees (ADR 0021); `crosscommitvuln` is a leak guard that
  keeps the eval suite's vendored CrossCommitVuln-Bench gold answers, which
  `pydocs-mcp-eval` ships as package data, out of every index. Like every
  floor entry, each matches a whole directory name at any depth (a file named
  `crosscommitvuln_fixtures.jsonl` still indexes) in project and dependency
  file discovery and in the project member walk, and therefore in `grep` /
  `glob`. Neither YAML nor `[tool.pydocs-mcp] exclude_dirs` can re-include
  them, since user exclusions only add to the floor; rename a real source
  directory with one of these names (a vendored `extern/` package, say) to
  keep it indexed. `read_file` still opens such files, since it checks only
  the project and dependency roots. A package containing such a directory
  re-extracts once on the next index pass and drops those files' chunks and
  trees (and, in the project, their symbols); dependency symbols are
  unaffected, since dependency member extraction has never applied the
  floor. Indexes without such a directory are unaffected.
- **Unknown `pipelines:` handler keys fail at config load** — only
  `pipelines.chunk` and `pipelines.member` are read. Any other key, most
  commonly a `pipelines.ingestion` route list, used to load and be silently
  ignored, which left the default single-vector ingestion in place. It is
  now a config validation error that names the offending keys; for
  `ingestion` the message points at `extraction.ingestion.pipeline_path`,
  the key that actually selects the ingestion pipeline. A config that loaded
  under 0.5.1 with a stray handler key must be fixed before upgrading.
- **`structuredContent` is now the typed envelope `{text, items, meta}`**,
  with a matching `outputSchema` advertised per tool at registration.
  Previously the SDK auto-wrapped the markdown string as
  `{"result": "<markdown>"}`. The **text content block is byte-identical** for
  the six pre-existing tools, so text-reading clients see no difference;
  clients that parsed `structuredContent.result` must read
  `structuredContent.text` instead. `meta` carries `tool`, `project`,
  `indexed_git_head`, `live_git_head`, `index_stale`, `truncated` and (see
  Added) `branch` on every tool.
- **`inputSchema` advertises enum values** — handler parameters are typed as
  `Literal`s, so the advertised JSON schema carries the same enums the CLI
  always did. Values unchanged; no call-shape change for existing clients.
- **CLI canonical subcommands named exactly like the tools** —
  `pydocs-mcp get_overview`, `pydocs-mcp search_codebase`, … The short verbs
  (`overview`, `search`, `symbol`, `context`, `refs`, `why`) remain as
  aliases, and `lookup` stays a deprecated alias. All nine subcommands source
  their help text from `TOOL_DOCS` (single source with the MCP descriptions),
  and the CLI-local `--limit` default literal is removed in favor of the
  YAML-wired default. Existing invocations keep working; scripts may migrate
  to canonical names at leisure.
- **Tool-description token budget raised for nine tools** —
  `TOTAL_TOKEN_BUDGET` (public in `pydocs_mcp.application.tool_docs` since
  0.5.1) rises from `2400` to `3600` estimated tokens (characters ÷
  `CHARS_PER_TOKEN`), summed over the nine tool descriptions only; the server
  instructions and the session-start preamble do not count.
  `PER_TOOL_TOKEN_BUDGET` (`500`), `CHARS_PER_TOKEN` (`4`) and
  `REQUIRED_MARKERS` are unchanged. The constants are now defined in
  `pydocs_mcp.application.description_source` and remain importable from
  `tool_docs`.
- **`get_references` declares its resolution level** — the tool description
  is re-hedged (edges are name/alias-matched with import awareness, not
  scope-resolved) and responses carry one additive meta field,
  `meta.resolution: "syntactic" | "semantic" | "unavailable"`, the capability
  level for the target's language. `.py` and `.md` targets report
  `"syntactic"`; every other target — `.ipynb`, the new text/config and code
  files, a bare dependency-package name, or a target with no resolvable
  source file — reports `"unavailable"` rather than overstating the
  Python-only reference graph. A future semantic backend flips only this
  value; the tool contract is invariant under the swap
  (`docs/tool-contracts.md` §2.2, ADR 0021).
- **`watchdog` is a required dependency** — `serve --watch` and `watch` work
  on a plain `pip install pydocs-mcp`: `watchdog>=4.0,<6.0` moved from the
  `[watch]` extra into the runtime deps (~684 KB installed, no transitive
  dependencies). `[watch]` is now an empty alias (see Deprecated).
- **`[sentence-transformers]` and `[openvino]` cap `transformers<6.0`**
  (previously `>=4.48` with no upper bound; `[late-interaction]` keeps its
  uncapped `transformers>=4.57.3`). When the `sentence_transformers`
  provider fails to build its model, on any `embedding.backend`, with an
  error that mentions torchvision, the error is now re-raised as an
  `ImportError` listing the remedies. transformers 5.0–5.9 require
  torchvision for image-processing classes that some model repos
  reference. The remedies: upgrade to `transformers>=5.10,<6`, install a
  `torchvision` build matching the installed torch, or point
  `embedding.model_name` at a text-only model. torchvision stays
  deliberately out of both extras because it exact-pins its torch version.

### Deprecated

- **The `[watch]` extra** — now an empty alias, since `watchdog` is a
  required dependency (see Changed). Existing
  `pip install 'pydocs-mcp[watch]'` commands keep working; the alias goes
  away in the next major version.

### Fixed

- **`mcp` capped below 2.0** — the requirement is now `mcp>=1.28.1,<2`. mcp
  2.x (2.0.0 onward) removed `mcp.server.fastmcp`, so an uncapped fresh
  install resolved mcp 2.2.0 and `pydocs-mcp serve` failed at startup with
  `ModuleNotFoundError`; the `[harness-ask-your-docs]` agent also failed to
  import (langchain-mcp-adapters under mcp 2.x). 0.5.1 (`mcp>=1.0`) is
  affected the same way on fresh installs; pin `mcp<2` when installing it.
- **Project-code addressing** — dotted targets now resolve bare
  project-qualified names for project source (stored under the reserved
  `__project__` package) in `get_symbol` / `get_context` / `get_references`.
  Previously project-source symbols were unreachable through target strings;
  now previously-erroring targets resolve and no working call changes
  behavior. Companion reference-graph fixes: relative imports honor
  `ast.ImportFrom.level`, and suffix matching is scoped to project qualified
  names so dependency symbols can't shadow project code.
- **`get_symbol(depth="source")` source header** — the `# Source — <target> ·
  <path>` header renders a real file path again: source paths now round-trip
  through the chunk store (schema v15) instead of being dropped on persist.
- **`get_references(direction="inherits")` answers both senses** — the tool
  now returns the target's base classes (from-side edges, kept even when the
  base name is unresolved) AND its subclasses (edges into the target), each
  under its own labelled section; previously dotted targets returned "No
  bases found" and any rows that did match were subclasses mislabeled as
  bases.
- **Search pointers are always callable** — a markdown heading hit
  (qualified name `pkg.FILE.md#section-slug`) used to advertise
  `get_symbol(target="pkg.FILE.md#section-slug")`, which the tool's own
  input validator rejects. The `#fragment` is now stripped, so the pointer
  targets the parent document node (`pkg.FILE.md`), and so does the
  recovery pointer shown when the token budget elides results. A pointer
  whose target is still not a dotted identifier, such as a markdown or
  decision document path like `docs.adr.0001-greeting-format.md`, is
  suppressed at render time rather than promising a call that always fails
  validation.
- **The `[late-interaction]` ingestion preset no longer drops decision
  mining and dependency doc pages** — the shipped
  `ingestion_late_interaction.yaml` (selected via
  `extraction.ingestion.pipeline_path`) had fallen behind `ingestion.yaml`
  and lacked the `capture_decisions` and `dependency_doc_pages` stages. A
  late-interaction deployment therefore mined no decisions, leaving `get_why`
  and `search_codebase(kind="decision")` nothing to return, and embedded no
  dependency doc pages. Both stages are back. The preset now differs from
  the default in exactly two ways: `embed_chunks_multi_vector` replaces
  `embed_chunks`, and `synthesize_similar_edges` is left out (it reads only
  single-vector embeddings). Only packages that re-extract pick up the two
  stages; a package whose files are unchanged keeps its cache hit. Run
  `pydocs-mcp index . --force` once after upgrading to cover every package.
- **`serve.watch.enabled: true` now turns on the file watcher** — 0.5.1
  accepted the key (its docs said the `--watch` flag "overrides" it) but never
  read it, so `pydocs-mcp serve` watched only with `--watch`. Now either
  switch enables watching: the YAML key (or `PYDOCS_SERVE__WATCH__ENABLED=true`)
  or the flag. The flag cannot turn watching off while the key is `true`. The
  default stays `false`, and multi-repo serves (`--workspace` / `--db`) still
  never watch. A config that already sets the key to `true` starts reindexing
  on edits after upgrading.
- **`get_why` targets accept file paths** — each `targets` item (and each
  `why --target` value on the CLI) may be a repo-relative path such as
  `python/pydocs_mcp/db.py` as well as a dotted name; the grammar is
  `^[A-Za-z0-9_.\-/]+$` (`docs/tool-contracts.md` §3.6). 0.5.1 validated
  targets as dotted identifiers and rejected any `/`, even though its own
  `get_why` tool description used a path in its example. Characters outside
  that set are still rejected, including `:` and `]`, which would corrupt
  response pointer tokens. `get_symbol`, `get_context` and `get_references`
  still take dotted names only.
- **Ask-your-docs: a collapsed sidebar can be reopened** — the chat UI's
  theme hid Streamlit's whole toolbar (`stToolbar`), and that container also
  holds the expand-sidebar chevron. Once the sidebar was collapsed (and
  Streamlit remembers that across reloads), 0.5.1 offered no on-page way to
  get back the workspace / model settings or the project / package / code
  pickers. The theme now hides only the toolbar's actions, deploy button and
  status widget, so the chevron stays visible. UI only; no config change.

## v0.5.2

### Fixed

- **`mcp` capped below 2.0** — the requirement is now `mcp>=1.28.1,<2` (floor:
  see Security). mcp 2.x
  (2.0.0 onward) removed `mcp.server.fastmcp`, so a fresh
  `pip install pydocs-mcp==0.5.1` resolved mcp 2.2.0 and `pydocs-mcp serve`
  failed at startup with `ModuleNotFoundError`; the `[ask-your-docs]` agent
  also failed to import (langchain-mcp-adapters under mcp 2.x). 0.6.0
  carries the same cap.

### Security

- `mcp` floor raised `>=1.0` → `>=1.28.1` (lock 1.27.1 → 1.30.0) — resolves
  PYSEC-2026-3481, PYSEC-2026-3482 and PYSEC-2026-3483 reported against mcp
  1.27.1, matching 0.6.0.
- `cryptography` (transitive, via `mcp` → `pyjwt[crypto]`) constrained
  `>=48.0.1` → `>=50.0.0` in `[tool.uv] constraint-dependencies` (lock
  49.0.0 → 50.0.1) — resolves PYSEC-2026-3552 in the locked and audited
  environment. The constraint is not part of the published wheel metadata;
  upgrade `cryptography` in your own environment.
- `pillow` (transitive) lock 12.2.0 → 12.3.0 — resolves the PYSEC-2026-2253…2257
  and PYSEC-2026-3451…3454 / 3493…3496 advisories in the locked environment.

## v0.5.1

### Changed

- The tool-docs contract constants — `REQUIRED_MARKERS`, `CHARS_PER_TOKEN`,
  `PER_TOOL_TOKEN_BUDGET`, and `TOTAL_TOKEN_BUDGET` — are now part of the public
  importable surface of `pydocs_mcp.application.tool_docs`. The `pydocs-mcp-eval`
  optimizer artifacts import them to share the §D13 tool-docs validation logic,
  so they need a published release that exposes them.

## v0.5.0

Headline: the MCP surface becomes **six task-shaped tools**, every response now
travels in a **freshness / next-step / truncation envelope**, and the index
grows an **architectural-decision layer** (mine decisions at index time, ask
`get_why` at query time).

### Added

- **Six task-shaped MCP tools + full CLI parity** — the surface is now
  `get_overview`, `search_codebase`, `get_symbol`, `get_context`,
  `get_references`, and `get_why`, each mirrored by a CLI subcommand
  (`overview` / `search` / `symbol` / `context` / `refs` / `why`). `get_context`
  packs one or more targets under a shared token budget; `get_overview` returns a
  structural orientation card for a package or the whole workspace. The old
  `search` / `lookup` pair is retired (`lookup` stays as a deprecated CLI alias).
  (#141)
- **Response conventions — one envelope around every answer** — each response
  (MCP or CLI) carries a freshness header (`[index: <sha> · <N>d old · <M>
  packages]`, plus a stale warning when the working tree has moved past the
  indexed commit), inline next-step pointers resolved to the calling surface,
  and a recoverable truncation footer (`[truncated: …]`) that names every clipped
  section and the pointer to fetch it in full. On by default; tunable under
  `output.envelope`. (#139)
- **Architectural-decision layer** — decisions are mined from your project at
  index time (ADR files, inline markers, commit messages, changelog, docs prose,
  deduplicated; optional LLM structuring). The read side exposes them via the new
  `get_why` tool (free-text or by-target), `search_codebase(kind="decision")`,
  `get_references(direction="governed_by")`, and dedicated overview blocks; each
  decision is a graph node with `GOVERNS` edges to the symbols it affects.
  Configured under `decision_capture:` (write) and `decisions.output` (read);
  schema v14. (#145, #146)
- **Multi-repo workspace orientation card** — an empty `get_overview` against a
  multi-repo server now returns one line per loaded repo with its package count,
  so a freshly connected agent can orient before narrowing to a `project`. (#153)
- **`graph_expand` per-edge-kind trust (`kind_weights`) + `MENTIONS` traversal** —
  graph expansion can now traverse weaker edge kinds at a discounted weight (the
  weight compounds along each path), with sweep configs for tuning. (#166)
- **`ask-your-docs` as a first-class install extra** — the LangGraph ReAct agent
  + Streamlit chat UI now ship inside the package (`pydocs_mcp/ask_your_docs/`)
  behind `pip install 'pydocs-mcp[ask-your-docs]'` and the `ask-your-docs`
  console command, with sidebar project/package/scope pickers enforced on every
  tool call and a read-only interactive graph-explorer page. (#157)
- **Benchmark harness expansion** — the harness is now a first-class programmatic
  surface with a paired agent-efficiency track (indexed vs bare, blind judge,
  spend guardrails), a SWE-QA / SWE-QA-Pro retrieval track, a `small_dev` split,
  and a `comparing-retrieval-methods` guide. Developer tooling under
  `benchmarks/`. (#132, #133, #144, #171)

### Changed

- **`dense_scorer` is now a post-fusion re-ranker** — instead of a standalone
  dense retriever, it re-scores the fused candidate subset against the TurboQuant
  vectors via an allowlist search (no fresh ANN scan) and sorts the vector-scored
  hits to the top; candidates without a dense vector keep their fused order and
  trail behind, so recall is preserved. Mirrors the late-interaction scorer on
  the single-vector side. (#154)
- **Docs modernized to the six task-shaped surface** — README, DOCUMENTATION,
  SPEC, IDEAS, EXTENSIONS, and the benchmarks README no longer describe the
  retired two-tool `search` / `lookup` surface; the root README is now
  vendor-neutral (no named third-party comparisons). (#141)
- **Storage / retrieval internals refactored** for maintainability — `sqlite.py`
  split into a per-repository package with shared CRUD helpers, the CLI
  write-side composition root extracted into `storage/factories`, the retrieval
  config split into a package, and several hexagonal-seam leaks closed
  (`FilterAdapter` wiring, FTS builder dedup, `db.py` layering). No user-facing
  behavior change. (#128, #130, #135, #136, #137)
- **Benchmark suite repackaged for PyPI as `pydocs-mcp-eval`** — the benchmark
  distribution is renamed from `pyctx7-benchmarks` to **`pydocs-mcp-eval`**, and
  its import package is hoisted from `benchmarks.eval.*` / `benchmarks.optimize`
  to **`pydocs_eval.*`** (`pydocs_eval.datasets`, `pydocs_eval.systems`,
  `pydocs_eval.optimize`, …); the `benchmarks/` directory name is unchanged. The
  optional-dependency extras are now split **by coupling, not by feature**: the
  base install serves the black-box agent-efficiency track (needs only the
  `pydocs-mcp` CLI on `PATH`), and a new **`[retrieval]`** extra declares
  `pydocs-mcp>=0.5` for the library-coupled parts (in-process retrieval systems,
  the optimize overlay server, the `tool_docs` / `usage_skill` artifacts). Those
  boundaries now carry import guards that raise an actionable
  `pip install "pydocs-mcp-eval[retrieval]"` hint instead of a bare
  `ModuleNotFoundError` when the extra is absent.

### Removed

- **`SqliteVectorStore` deprecated alias** — the class was renamed
  `SqliteLexicalStore` (it is the FTS5/BM25 lexical store, not a vector
  store); the back-compat alias is gone from `pydocs_mcp.storage` and
  `pydocs_mcp.storage.sqlite`. Import `SqliteLexicalStore` instead.

### Fixed

- **Two audit-hardening waves — ~65 bug fixes with regression tests** — a
  high-risk wave (18 fixes + 24 new regression-test files) followed by a
  medium/low wave across storage, db, server, retrieval, extraction, the
  envelope, the CLI, the watcher, and the Rust core. Includes three reproduced
  crash bugs (FTS5 operator queries, composite-UoW enter-leak, migration
  crash-loop), dead watch mode with the real watchdog, `--force` inherited on
  every save, and `chunks_fts` desync on package deletes.
  (#148, #150, #152, #158, #159, #160, #161, #162, #163, #164)
- **`get_context` budget accounting** — `_split_budget` now honors one shared
  budget so the summed output never exceeds the requested total. (#161)
- **`examples/ask_your_docs_agent` crashed on startup** — the agent fetched
  the removed `lookup` tool (`StopIteration` on connect); it now targets the
  six task-shaped tools and reads the indexed-projects listing via
  `get_overview`.

### CI

- **`uv lock --check` drift gate** added; the `[graph]` extra now pulls `scipy`
  (PageRank stopped crashing) and is exercised in CI; the heavy `ask-your-docs`
  extra is kept off the core test matrix. (#168, #169, #170, #173, #174)

## [0.4.1] — 2026-07-03

### Added

- **Air-gapped / offline model loading** — point `embedding.model_name` at a
  local directory of side-loaded weights and nothing is ever downloaded, for
  every provider. fastembed states the model recipe in YAML (new `pooling`
  knob + `normalize` / `model_file_name`) and loads via a pinned local path;
  sentence-transformers and PyLate take the directory natively (the right
  choice for last-token models like Qwen3-Embedding) with HF offline mode
  forced so a missing file fails locally; `openai` rejects a local path with
  an actionable error. Existing configs keep their exact pipeline hashes —
  nothing re-embeds. (#121)
- **Ask-your-docs Streamlit webapp** — `examples/ask_your_docs_agent` now
  ships a themed chat UI (`streamlit run streamlit_app.py`) over the same
  LangGraph agent: sidebar config, conversation memory with follow-up
  reformulation, code snippets rendered in fenced blocks. The example is now
  Streamlit + notebook only (the terminal REPL is gone). (#122)

### Fixed

- **Full-suite test failures in the fast-plaid storage tests** — the
  default-install no-torch test evicted torch from `sys.modules` without
  restoring it, so any later torch import in the same run crashed
  (`function '_has_torch_function' already has a docstring`). The evicted
  modules are now restored, and the suite is fully green. (#123)

### Changed

- The ask-your-docs example defines its dependencies in a single
  `requirements.txt` (the short-lived `requirements.py` variant is gone).

## [0.4.0] — 2026-07-03

### Added

- **Multi-repo search** — one MCP server (or CLI query) over several already-
  indexed repos: `serve --workspace <dir>` / `--db <file>` load pre-built
  `{name}_{hash}.db` bundles read-only; a new `project` filter on `search` /
  `lookup` scopes one repo, omitted it unions across all with dedup (a repo's
  own code beats the same symbol seen as a dependency; most-recently-indexed
  wins among duplicates). A per-database identity stamp (`index_metadata`)
  rejects bundles built with a mismatching embedder up front.
- **Reference-graph readers on `lookup`** — `show="impact"` (everything that
  transitively calls a symbol, ranked — "what breaks if I change X?") and
  `show="context"` (the symbol's dependency closure packed under a token
  budget at graded fidelity — "everything to understand X").
- **Graph-boosted retrieval** — `graph_expand` step (dense-seeded 1-hop
  reference-graph expansion), index-time `node_scores` (PageRank / community,
  optional `[graph]` extra) with centrality / diversity rerankers, synthetic
  embedding-kNN `similar` edges, graph pipeline presets, and a
  structural-recall benchmark split.
- **Selective dependency embedding** — everything stays BM25/FTS-indexed, but
  dense vectors are written per package tier: the project embeds fully;
  dependencies embed one docstring **page per module** (module + public
  signatures + docstrings) plus markdown/READMEs by default. Promote chosen
  dependencies to full embedding with `--full-dep NAME` /
  `embedding.full_index_dependencies` (globs supported);
  `embedding.dependency_policy: full | doc_pages | none`. Indexing
  torch-sized dependencies drops from ~an hour to seconds on CPU.
- **ONNX / OpenVINO backends for `sentence_transformers`** —
  `embedding.backend: torch | onnx | openvino` + `embedding.model_file_name`
  (e.g. a qint8-quantized export) for ~2–4× faster CPU inference; new
  `[openvino]` extra. Index on GPU, serve on CPU with the same model.
- **New embedders** — `gte-modernbert-base` and the code-specialized
  `F2LLM-v2` family via the `sentence_transformers` provider (RepoQA
  leaderboard + figures in the benchmark docs).
- **Dependency manifests** — `[project.optional-dependencies]` and PEP 735
  `[dependency-groups]` (what `uv add --group` writes) are now parsed; the
  `--watch` watcher re-indexes when `pyproject.toml` / `requirements*.txt`
  change, so adding a package updates the index automatically.
- **Example agent** — `examples/ask_your_docs_agent/`: a minimal LangGraph
  ReAct chat agent (terminal or notebook) answering questions about your
  indexed repos through the MCP tools, with conversation memory, follow-up
  reformulation, and project inference.
- **Documentation site** — Sphinx + Furo under `documentation/`.

### Changed

- **Default chunk search is now dense + graph expansion**
  (`chunk_search_graph.yaml`), replacing BM25-only — RepoQA recall@10 0.40 →
  0.77 on standard queries and 0.30 → 1.00 on structurally-reachable answers,
  at no extra indexing cost. BM25 and hybrid remain as presets.
- Dependencies embed documentation pages only by default (see Added);
  `scope="deps"` searches route to a BM25 ∥ dense fusion preset so dependency
  code stays reachable by keyword.
- `graph_expand` decay default raised to 0.9.
- Schema v10 → v12 (`node_scores`, `index_metadata`, `chunks.embedded`) —
  additive, migrated automatically on open. Note: the ingestion pipeline
  identity changed, so the first re-index after upgrading re-extracts and
  re-embeds packages; serving existing indexes keeps working without it.

### Fixed

- Reference resolver no longer rescans the whole symbol universe per
  reference (O(N²) → bucketed) — indexing large dependencies such as numpy /
  torch previously appeared to hang.
- The startup SQLite ↔ vector-store integrity check compares intended
  embeddings instead of raw chunk counts, ending the repeated
  re-extract-everything loop for deployments that don't embed every chunk.
- Dense search over a partially-embedded corpus no longer raises when the
  candidate set contains vectorless chunks.
- BM25 candidates carry `qualified_name`, unblocking LLM tree reranking.
- GPU benchmark runs no longer silently fall back to CPU
  (onnxruntime CUDA library path).

## [0.3.1] — 2026-06-10

### Added

- **`--skip-deps` CLI flag** on `serve` / `index` / `watch` — index only the
  project source, skipping dependency resolution + indexing entirely. The CLI
  counterpart of `ProjectIndexer.index_project(include_dependencies=False)`
  and the inverse of `--skip-project`.

## [0.3.0] — 2026-06-10

### Added (LLM tree-reasoning — enrichment, token budget, two-stage rerank)

- **PageIndex node enrichment** — each LLM-visible tree node now carries its real
  signature (params + type hints + return annotation), its decorators, and a
  docstring excerpt, beyond the generated summary. Tunable via `doc_excerpt`
  (`sections` | `full` | `off`) and `doc_excerpt_max_chars`. A non-destructive
  schema auto-refresh (v9) re-extracts the metadata on next index without
  re-embedding unchanged chunks.
- **Token-counted tree budget** — the serialized tree handed to the LLM is bounded
  in real `tiktoken` tokens (previously whitespace words, which under-counted code
  ~3× and could overflow the model's context window with a 400
  `context_length_exceeded`). `max_tree_words` → **`max_tree_tokens`**
  (`int | None`; `None` auto-derives from the configured model's context window).
  Over-budget pruning is content-first — drop per-node doc excerpts before whole
  nodes. Adds `tiktoken` as a runtime dependency.
- **BM25 → tree two-stage rerank** — opt-in `rerank_candidates` mode on the
  `llm_tree_reasoning` step scopes the LLM-visible tree to a prior BM25/dense
  candidate set and writes its ranked picks back as the pipeline's final ranking
  (with a `repoqa_bm25_tree_rerank` benchmark config).
- Persist `chunks.qualified_name` (schema v7) so tree-reasoning picks resolve to
  the correct chunks.

### Added (on-device dense embeddings)

- **`sentence_transformers` embedding provider** (`provider: sentence_transformers`)
  serving `Qwen/Qwen3-Embedding-0.6B` and other SentenceTransformer models via
  torch — a GPU-reliable on-device dense embedder (torch frees CUDA memory
  between sequential index-builds). Opt-in via the `[sentence-transformers]`
  extra. New `EmbeddingConfig` knobs `max_seq_length` / `normalize` /
  `query_prompt_name` (the first two fold into the pipeline hash; the
  query-only prompt does not).

### Removed

- **The `onnx` embedding provider** (`OnnxEmbedder` and the `onnx_file` /
  `query_instruction` config fields). The torch-backed `sentence_transformers`
  provider replaces it for on-device Qwen3-Embedding — onnxruntime leaked the
  CUDA arena across the benchmark's sequential index-builds.

### Added (GPU inference)

- **`--gpu` flag** on `serve`, `index`, and `watch` (and the benchmark runner)
  to run all embedder inference — FastEmbed, the `sentence_transformers`
  provider, and PyLate late-interaction — on CUDA. No YAML change; covers both
  index-time and query-time embedding. The execution device is excluded from the
  pipeline / index-cache hash, so toggling `--gpu` shares the same `.tq` /
  fast-plaid index and never forces a re-index (it is a latency knob, not a
  quality change).
- **`EmbeddingConfig.device`** (`cpu` / `cuda`) wiring through `build_embedder`
  into the FastEmbed and sentence_transformers embedders;
  `AppConfig.with_device(gpu=...)` stamps the device after config load. GPU
  runtimes (`onnxruntime-gpu`, `fastembed-gpu`, CUDA torch) are documented in
  `INSTALL.md`, not auto-installed.

## [0.2.0] — 2026-05-28

### Added (late-interaction retrieval — ColBERT / PyLate via fast-plaid)

- **Late-interaction (multi-vector / MaxSim) retrieval backend**, opt-in via
  `pip install 'pydocs-mcp[late-interaction]'` + `late_interaction.enabled: true`
  in YAML. Ships `lightonai/LateOn-Code` as the default model via PyLate
  ([arXiv:2508.03555](https://arxiv.org/abs/2508.03555)) and scores MaxSim
  through [fast-plaid](https://github.com/lightonai/fast-plaid) (PLAID —
  [arXiv:2205.09707](https://arxiv.org/abs/2205.09707)).
- **`chunk_multi_vector_ids` SQLite mapping table** (schema v6) bridges
  `chunk_id` ↔ fast-plaid's auto-assigned `plaid_doc_id`. The existing
  `FilterAdapter` Protocol scopes MaxSim to the SQLite-filtered candidate set
  via fast-plaid's `subset=` parameter.
- **Three new YAML presets** (`ingestion_late_interaction.yaml`,
  `chunk_search_late_interaction.yaml`,
  `chunk_search_late_interaction_ranked.yaml`) plus benchmark sweep configs
  (`repoqa_hybrid_li_rrf.yaml`, `ds1000_hybrid_li_rrf.yaml`).
- **`LateInteractionScorerStep` retrieval step** + `EmbedChunksMultiVectorStage`
  ingestion stage + `FastPlaidUnitOfWork` storage adapter + `NullMultiVectorStore`
  for the disabled deployment path.

### Added (PyPI packaging polish)

- `[project] authors`, `keywords`, `classifiers`, `[project.urls]` for PyPI
  rendering. `Cargo.toml` version synced to `0.2.0`.

### Fixed

- `build_retrieval_context` now wires `BuildContext.embedder =
  build_embedder(config.embedding)`. Any pipeline that referenced
  `DenseFetcherStep` / `DenseScorerStep` previously crashed at decode time
  with the actionable `ValueError`. FastEmbed's ONNX model stays lazy so the
  BM25-only deployment still pays nothing at startup.

### Added (final P2 follow-ups — closes #14 audit findings)

- `SECURITY.md` at repo root — GitHub-rendered private vulnerability reporting flow with 72h ack / 7d confirm / 30d fix SLAs.
- `CONTRIBUTING.md` at repo root — external-contributor entry point referencing the `make install` / `make test` workflow.
- `pip-audit` security job in `.github/workflows/ci.yml` — scans the locked dep tree for CVEs in strict mode.
- `release.yml` syncs version from the pushed git tag — `sed -i` updates both `Cargo.toml` and `pyproject.toml` in every build job, gated on `refs/tags/v`.

### Changed

- `python/pydocs_mcp/__init__.py` attaches a `logging.NullHandler` at the package logger (PEP 282 library convention). Users who configure logging via `logging.basicConfig()` see no behaviour change.


### Added
- MIT `LICENSE` file at the repository root.
- PEP 561 `py.typed` marker so downstream type-checkers honor the package's type hints.
- `PydocsMCPError` root exception so embedders can catch any pydocs-mcp failure with one `except`.
- `__all__` declaration in `pydocs_mcp/__init__.py` with the public exception hierarchy re-exported.
- `[dependency-groups]` (PEP 735) for dev / test / lint deps.
- mypy configuration + CI typecheck step.
- Multi-OS CI matrix (macOS + Windows in addition to Linux).
- `.pre-commit-config.yaml`, `Makefile`, `.editorconfig` for contributor ergonomics.
- This `CHANGELOG.md`.

### Changed
- `pyproject.toml` license declaration migrated to PEP 639 SPDX form (`license = "MIT"` + `license-files`).
- `pydocs_mcp.__version__` sourced from installed metadata via `importlib.metadata` (was hard-coded; drifted from `pyproject.toml`).
- Ruff `target-version` bumped to `py311` (matches `requires-python`).
- Ruff `select` expanded with `B`, `UP`, `S`, `SIM`, `RUF`, `C901`, `PT`, `PTH`.

### Fixed
- Cross-platform path-separator / encoding / newline handling in six
  extraction / IO tests so they execute on Windows as well as POSIX hosts.
  Assertions now compare via `Path.as_posix()` (test fixtures previously
  embedded forward slashes in `str(Path)` comparisons), test fixtures
  pin UTF-8 explicitly when writing test files with non-ASCII content
  (`write_text(..., encoding="utf-8")`) and use `write_bytes` to avoid
  Windows CRLF translation on the file-read round-trip.

## [0.2.0]

### Added
- `pydocs-mcp serve --watch` flag — live re-indexing via the new `FileWatcher` module.
- `pydocs-mcp watch` standalone subcommand — watcher only, no MCP server.
- Rich `description=` + `epilog=` on the `search` / `lookup` CLI subparsers.
- Server-level `FastMCP(instructions=...)` block with workflow framing for AI clients.

### Changed
- Tool annotations on MCP `search` + `lookup` (`readOnlyHint`, `idempotentHint`, `openWorldHint`).

## [0.1.0]

### Added
- Initial public release.
- Local MCP server indexing Python project + dependency docs/code into a hybrid (BM25 + dense embeddings) index.
- 2 MCP tools: `search` (BM25 + dense, RRF-fused) and `lookup` (with reference-graph traversal).
- Rust acceleration via maturin (PyO3) with a pure-Python fallback.

[Unreleased]: https://github.com/msobroza/pydocs-mcp/compare/v0.6.1...HEAD
[0.6.1]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.6.1
[0.6.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.6.0
[0.5.1]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.5.1
[0.5.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.5.0
[0.4.1]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.4.1
[0.4.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.4.0
[0.3.1]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.3.1
[0.3.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.1.0
