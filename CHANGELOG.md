# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.6.0] — Unreleased

Headline: the MCP surface grows from six to **nine task-shaped tools** — three
filesystem tools (`grep`, `glob`, `read_file`) join the six indexed tools — and
the whole surface is **frozen by contract**: `docs/tool-contracts.md` is the
normative inventory (rationale in `docs/adr/0001`–`0004`). No renames, no
removals — existing six-tool clients keep working unmodified.

### Security

- `mcp` dependency floor raised `>=1.0` → `>=1.28.1` (lock updated 1.27.1 →
  1.28.1) — resolves CVE-2026-52869, CVE-2026-52870, and CVE-2026-59950
  reported against mcp 1.27.1.

### Added

- **The external CLI harness ships in the product wheel** — a second in-tree
  harness, and the first *composed* one. `pydocs_mcp/harness/builtin/external/`
  owns a run's corpus, trace, guidance policy and trajectory, and delegates only
  "what is the command line" and "what does the transcript say" to a CLI coding
  agent ENGINE under `pydocs_mcp/harness/platform/engines/` (a CLI agent is an
  engine, not a harness: several engines run under one harness, sharing its
  guidance sections,
  while the engine name is recorded separately). It satisfies the same harness
  run contract as the in-process agent — one sample in, one trajectory out, with
  both observation points joined — and needs **no optional extra**: the engine is
  driven with stdlib `subprocess`, so a plain `pip install pydocs-mcp` can run
  it. Adding another CLI agent is one adapter subclass plus one registry line,
  checked by a shared adapter conformance battery. The shared guidance
  partition/fold moved to `harness/platform/guidance_fold.py`, parameterized on
  the harness name, and the three trace-correlation environment variables now have
  exactly one spelling (`observability/trace_env.py`) shared by both harnesses.
- **A second CLI engine: `opencode`** — the external harness can now drive the
  `opencode` CLI by naming `engine: opencode` in an arm's settings; nothing else
  changes, and `claude_code` stays the default. Adding it cost exactly what the
  design promised: one adapter class, one registry line, and its own tests.
  Because that CLI has no system-prompt flag, its guidance channel is a
  documented prompt prefix rather than a flag — the first time two engines under
  one harness deliver the same guidance by different mechanisms, which the
  delivery map records automatically. Two further differences are documented
  rather than hidden: the trajectory id names the session's *title* (the CLI's
  session flag only resumes existing sessions), and the event stream carries no
  "stopped at the turn cap" marker — so on this engine a run truncated at the cap
  is scored as a finished one, stated plainly in the adapter rather than guessed
  at. Spend and cache-token accounting are reported normally. The run is pinned
  against its environment: the agent that carries the step cap is named on the
  command line, the repository under test cannot contribute config, plugins or
  system-prompt text to a measured run, and the prompt is passed so that the CLI
  reassembles it byte-for-byte. Every spawned CLI now runs with standard input
  closed, so an agent that reads it can neither hang a rollout nor silently
  append to the prompt. Engine
  MCP-config rendering moved onto the engine port in the same change — the two
  CLIs read different config schemas, and rendering the wrong one would start a
  server that silently loses its trace correlation; the existing engine's config
  bytes are unchanged.
- **External-harness guidance delivery in the eval suite** (`pydocs-mcp-eval`;
  no product change) — the headless-CLI track can now actually receive a
  candidate's sectioned guidance. Its `BACKBONE`, `TASK_HEAD: <task>` and
  `HARNESS_TASK_HEAD: external.<task>` sections fold — in that order, single
  newline, byte-identical to the in-process harness's fold — onto
  `claude --append-system-prompt`, leaving the shared task scaffold untouched
  so the only difference between the two measured arms stays the tool surface.
  Another harness's sections are recognized and dropped; an unrecognized one —
  or a task-scoped one handed in with no task named — raises rather than being
  silently discarded. Which task's sections fold is a new
  `AgentTrackConfig.task_name`. Runs that attach no candidate guidance build
  byte-identical argv to before. The delivery map, the task name and the
  channel a pass delivered on are all arm state, so the external default arm
  hash moves (`f5b2649c…` → `0576f4de…`); no recorded campaign or committed
  ledger is affected. This entry describes the **standalone paired-efficiency
  CLI**, which stays library-free by contract and keeps its own copy of the
  command builder and transcript reader; the **optimization** path for the same
  arms now runs through the product harness above, and an executed parity check
  keeps the two spellings identical.
- **Trajectory-grounded scoring in the eval suite** (`pydocs-mcp-eval`; no
  product change) — the rubric's deterministic layer becomes *scored*. A
  rubric section may now spell a `checks:` block (weighted 0-1 measures with
  `required` / `fail` policy) beside its boolean `gates:`. With no `checks:`
  the layer is exactly the gate pass fraction it always was, so no existing
  objective's verdicts move; with `checks:` the gates fall back to pure
  screens (still required, still sparing the judge) and the weighted measures
  own the layer's whole mass. New check kind **`gold_location_evidenced`**
  measures what a run's *retrieval* named rather than what its answer *said* —
  the fraction of the gold file set named in a server-recorded tool call's
  arguments or returned among its distilled result identifiers, read off the
  trace. Named, not read: a recorded result identifier does not imply the model
  saw the item, and broad enumerations (a repo-wide `glob`, a package overview)
  are excluded so listing everything buys no evidence. The three
  shipped `search_skill` rubric sections now apportion their deterministic
  layer `{gold_recall 0.75, gold_location_evidenced 0.25}`, both as pure
  measures that can never gate. Rubric objective hashes move accordingly; no
  campaigns were recorded against the previous ones.
- **The harness run contract** (`pydocs_mcp.harness.platform.contract`) — the
  port every agent harness implements: `HarnessRunner` (one sample +
  guidance sections in, one `Trajectory` out), with tool calls derived from
  the server-side trace (`observed_by: server|client` provenance) and typed
  failure semantics (`UndeliverableGuidanceError`, `TurnBudgetExceededError`).
  Companions: a product-side trace reader
  (`pydocs_mcp.observability.trace_reader`), the ask-your-docs harness
  binding (`pydocs_mcp.harness.builtin.ask_your_docs.binding` — factory
  `make_harness_runner`, declared guidance delivery map), the public
  `parse_skill_artifact` entrypoint on the skill-artifact loader, and four
  harness-private `build_agent` keywords (`tool_names`, `skill_override`,
  `task_name`, `scope_pin`) whose defaults are byte-identical to the
  previous build.
- **The packaged search-guidance skill artifact** — one delimited document
  (`pydocs_mcp.harness.assets.skills`) in three tiers, every section
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
  `pydocs_mcp.harness.platform.skill_artifact` (strict parse against the
  enumerated section set, per-section token caps); the shipped seed is
  hand-written, and an explicitly named override that is missing or invalid
  is a hard error, never a silent fallback.
- **Three filesystem tools: `grep`, `glob`, `read_file`** — exact-string /
  regex search (Python `re` flavor; `content` / `files_with_matches` / `count`
  output modes; the flag parameters are the literal names `-i`, `-n`, `-A`,
  `-B`, `-C` on the MCP wire), file-name matching (`**` recursion, results
  ordered by modification time, newest first), and line-numbered file reads
  (`cat -n` style, so line references round-trip with `grep` output). All
  three operate on the **indexer's discovery scope** — the same excluded-dirs
  floor, extension allowlist, and size cap the semantic index sees, not
  `.gitignore` — and every response is freshness-stamped against the index
  snapshot. Additive: MCP clients discover the tools at connect time. Each is
  mirrored by an identically-named CLI subcommand; output caps are YAML-wired
  under `files.*`.
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
  hard startup error — never a silent fallback. Every run logs the fingerprint
  of the surface it serves (`descriptions artifact <hash12> source=…`), and
  CLI `--help` renders the same bundle the MCP server serves. Default behavior
  is byte-identical to the previous hardcoded text; authoring guide in
  `docs/description-authoring.md` (rationale: `docs/adr/0005`–`0006`).
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
  tokens (`serve.session_start_context.budget_tokens`, card trimmed before
  inventory, truncation always noted). When enabled, the ask-your-docs agent
  injects it into its prompt; the new `pydocs-mcp session-start-context`
  subcommand prints the pack for external harnesses regardless of the flag
  (rationale: `docs/adr/0008`).
- **Chunk source spans persisted (schema v15)** — chunks now carry
  `source_path` / `start_line` / `end_line` through SQLite, so structured
  items cite exact file spans. Additive in-place migration; rows indexed
  before v15 carry empty spans until the next reindex re-extracts their
  package, which backfills spans even onto unchanged (hash-matched) rows
  without re-embedding them.

### Changed

- **The `harness/` tree is now split by what a diff to a folder MEANS**, so the
  optimizable surface is a folder rather than a convention. `harness/platform/`
  holds machinery only (the run contract — renamed
  `harness.core.run_contract` → `harness.platform.contract` — the skill-artifact
  loader, renamed `skill_artifact_loader` → `skill_artifact`, the guidance fold,
  the prompt namespace/pool/freeze/override/surfaces modules, the serve-config
  renderer, the reusable `CliAgentHarness` promoted out of the external harness
  and typed against its own `ComposedHarnessSettings` Protocol so `platform`
  names no concrete harness at all,
  and the CLI-agent adapters, ex `harness/cli_agents/`, now
  `harness/platform/engines/`). `harness/assets/` holds the optimizable
  "weights" — the cross-harness prompt pool and the packaged search-guidance
  seed — with zero code beside them. `harness/builtin/` holds the shipped
  concrete harnesses (`ask_your_docs/`, `external/`). Behavior is unchanged: the
  packaged seed bytes, the section vocabulary (`HARNESS_NAMES`, `TASK_NAMES`),
  both harnesses' delivery-map digests and every prompt-freeze manifest are
  byte-identical across the move. There is **no compatibility shim** — 0.6.0 is
  unpublished, so the old module paths never shipped. One recorded consequence:
  the shipped optimize configs' `runner:` dotted paths change, so any arm run
  under them fingerprints differently than a pre-relayout one would have; no
  campaign has been recorded against those configs, so nothing measured moves.
- **BREAKING: the ask-your-docs harness moved under the `harness/` namespace
  with harness-scoped install names.** The console script `ask-your-docs` is
  now **`harness-ask-your-docs`**, the extra `[ask-your-docs]` is now
  **`[harness-ask-your-docs]`** (`pip install
  'pydocs-mcp[harness-ask-your-docs]'`), and the module path
  `pydocs_mcp.ask_your_docs` is now `pydocs_mcp.harness.builtin.ask_your_docs`.
  There is **no compatibility shim**: the old script name vanishes from PATH,
  and pip treats an unknown extra as a warning, so an old install command
  silently yields an agent-less install — update install scripts and MCP/CLI
  wrappers together. The `ask_your_docs:` YAML config block and its env-var
  prefix are **unchanged**. Wheels now really ship the agent's prompt
  templates: the packaging include was a flat glob that matched none of the
  nested `prompts/**/*.j2` files; it is now recursive.
- **`structuredContent` is now the typed envelope `{text, items, meta}`**,
  with a matching `outputSchema` advertised per tool at registration.
  Previously the SDK auto-wrapped the markdown string as
  `{"result": "<markdown>"}`. The **text content block is byte-identical** for
  the six pre-existing tools, so text-reading clients see no difference;
  clients that parsed `structuredContent.result` must read
  `structuredContent.text` instead. `meta` carries `tool`, `project`,
  `indexed_git_head`, `live_git_head`, `index_stale`, and `truncated` on every
  tool.
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
- **`get_references` declares syntactic resolution** — the tool description
  is re-hedged (edges are name/alias-matched with import awareness, not
  scope-resolved) and responses carry one additive meta field,
  `meta.resolution: "syntactic" | "semantic"`, the declared capability level
  of the reference graph that produced the answer. A future semantic backend
  flips only this declared value; the tool contract is invariant under the
  swap.

### Fixed

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
- Search responses no longer advertise follow-up `get_symbol` calls whose
  target the tool's own validator rejects (markdown/decision document paths
  like `docs.adr.0001-greeting-format.md`) — such pointers are suppressed at
  render time instead of promising a call that always fails input validation.

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

[0.5.1]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.5.1
[0.5.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.5.0
[0.4.1]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.4.1
[0.4.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.4.0
[0.3.1]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.3.1
[0.3.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.1.0
