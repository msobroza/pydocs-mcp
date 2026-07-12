# CLI/MCP help + documentation conformance — audit and permanent doc-tests

| | |
|---|---|
| **Version** | 0.1 (draft) |
| **Status** | Proposed |
| **Date** | 2026-07-11 |
| **Audience** | Implementers + reviewers |
| **Component** | `tests/test_doc_conformance.py` (new), root markdown docs, `python/pydocs_mcp/{__main__.py, application/, retrieval/}` (targeted fixes) |

## 1. Context & problem statement

The user report: *"Need to check helpers/documentation of CLIs/MCPs because I
tested some and they didn't work."* A full static cross-check of every
documented command, flag, tool parameter, and YAML key was performed against
the code. The audit surface was:

- **Docs:** `README.md`, `INSTALL.md`, `DOCUMENTATION.md`, `CLAUDE.md`
  (command blocks), `SPEC.md`, `EXTENSIONS.md`,
  `examples/ask_your_docs_agent/README.md`, and the `documentation/` Sphinx
  site (which is almost entirely MyST `{include}` shims over the root docs —
  e.g. `documentation/user-guide/cli.md:3-5` includes `DOCUMENTATION.md`
  between literal heading markers, so auditing the root files covers the site).
- **Code:** the argparse tree in `python/pydocs_mcp/__main__.py`
  (10 subcommands, buildable side-effect-free via `_build_parser()`,
  `__main__.py:50-52`), the six MCP tool registrations in
  `python/pydocs_mcp/server.py:251-336`, `TOOL_DOCS` in
  `python/pydocs_mcp/application/tool_docs.py`, the MCP input models in
  `python/pydocs_mcp/application/mcp_inputs.py`, and `AppConfig` +
  sub-models in `python/pydocs_mcp/retrieval/config/{app_config,models,embedder_models}.py`.

The headline finding is good news with sharp edges: the **vast majority** of
documented invocations are correct. Every `pydocs-mcp` flag mentioned in
README/INSTALL/DOCUMENTATION/CLAUDE exists in the argparse tree
(`__main__.py:59-363`); the six-tool MCP table in `DOCUMENTATION.md:314-321`
matches `server.py:269-336` exactly; the 172-line "Building pipelines in
Python" example (`DOCUMENTATION.md:119-193`) was verified name-by-name and is
import-correct; all 18 shipped pipeline YAMLs listed in docs exist; every YAML
key referenced in docs exists in `AppConfig`; and `SPEC.md:355-392`'s command
block parses in full.

But **eleven concrete mismatches** exist (§1.1), including one *functional*
defect that exactly matches the user's "I tested it and it didn't work"
experience (Defect 1: the documented `why --target <path>` form is rejected by
input validation before it ever reaches the code that supports it), one
documented flag that silently does nothing on the most common invocation
(Defect 2: `search --limit` on single-project searches), and one dead YAML key
(Defect 3: `serve.watch.enabled`).

The deeper problem is structural: nothing prevents this drift from recurring.
The repo already has narrow doc-presence tests
(`tests/test_docs_late_interaction.py`,
`tests/test_docs_updated_for_tree_reasoning.py`,
`tests/application/test_tool_docs_lint.py`,
`tests/application/test_server_surface.py`,
`tests/test_tool_descriptions.py`), but no test parses documented commands
against the real parser, no test cross-checks `TOOL_DOCS` prose against the
pydantic input schemas, and no test validates doc-referenced YAML keys against
`AppConfig`. This spec delivers **(A)** the defect list with a fix plan and
**(B)** a permanent doc-conformance pytest harness so the drift class dies.

### 1.1 The audit — numbered defect list (deliverable A)

Every defect cites file:line on **both** sides (doc claim vs code reality).

---

**DEFECT 1 — `why --target <path>` is documented and half-implemented, but
rejected by input validation.** *(CONFIRMED, functional — the user-visible
breakage.)*

- Doc/help side: `python/pydocs_mcp/__main__.py:299-307` (`why --target`
  help: "A path (a/b.py) or a qualified name (pkg.mod) — a value with / or a
  source-file extension is treated as a path"); `DOCUMENTATION.md:304-305`
  ("--target PATH|QNAME … a file path (a/b.py) or a qualified name").
- Supporting code that *expects* paths:
  `python/pydocs_mcp/application/decision_service.py:74-89`
  (`_classify_target` explicitly branches on `/` paths).
- Rejecting code: `python/pydocs_mcp/application/mcp_inputs.py:36-38`
  (`_TARGET_RE` forbids `/`) and `WhyInput._check_targets` at
  `mcp_inputs.py:396-408` raises `ValueError` for any target containing `/`.
- Empirically verified against the regex: `a/b.py` and
  `src/pydocs_mcp/db.py` → REJECTED; `b.py`, `pkg.mod` → accepted.
- Blast radius: both the CLI (`_run_why`, `__main__.py:718-732`, constructs
  `WhyInput`) and the MCP tool (`server.py:328-334`) route through the same
  validator, so `DecisionService`'s path branch is **unreachable** for any
  slash path from either surface.

**DEFECT 2 — `search --limit` / `search_codebase(limit=)` has no effect on
single-project searches, but `DOCUMENTATION.md` documents it as a general
result cap.** *(CONFIRMED, doc vs behavior.)*

- Doc side: `DOCUMENTATION.md:271-272` ("# Cap client-visible results
  (default 10; top-K is also configurable in YAML)" +
  `pydocs-mcp search "logging" --limit 5`); `DOCUMENTATION.md:317` (tool
  table lists `limit` with no caveat); `DOCUMENTATION.md:957`
  (`search_codebase("batch inference vllm", kind="api", package="vllm",
  limit=20)` client example); `DOCUMENTATION.md:685-687` (overlay example
  implies `search.output.default_limit` changes result count).
- Code side: `python/pydocs_mcp/application/multi_project_search.py:180-186`
  routes single-project / `project=`-scoped queries to
  `render_single_search` (lines 121-145), which **never reads
  `payload.limit`**; `payload.limit` is consumed only at
  `multi_project_search.py:198,200` (`_union_docs` / `_union_api` — the
  ≥2-project union path).
  `python/pydocs_mcp/application/search_query.py:38-52`
  (`build_search_query`) never threads `limit` into `SearchQuery`; the
  single-project result count comes from the pipeline YAML (e.g.
  `python/pydocs_mcp/pipelines/chunk_search_graph.yaml` `limit` step,
  `max_results: 8`).
- The argparse help at `__main__.py:250-257` states the **true** behavior
  ("Result cap for multi-repo union searches … Single-project result count
  is set by the retrieval pipeline YAML, not this flag") — the two doc
  surfaces contradict each other.

**DEFECT 3 — `serve.watch.enabled` is a dead YAML key; docs claim the CLI
"overrides" it.** *(CONFIRMED, dead key + misleading docs.)*

- Doc side: `DOCUMENTATION.md:413-415` ("The CLI --watch flag overrides
  `enabled` at runtime") and `:421` (`enabled: false  # CLI --watch
  overrides this at runtime`);
  `python/pydocs_mcp/defaults/default_config.yaml:145-153` (same comment +
  key).
- Code side: `__main__.py:891` — `if getattr(args, "watch", False):` is the
  **only** watch-mode trigger. The `watch_cfg` reads at
  `__main__.py:517-521`, `:572-577`, `:612-617` consume only
  `debounce_ms` / `extensions` / `ignore_globs`. A grep over
  `python/pydocs_mcp` finds no read of `watch.enabled` besides the model
  field declaration (`retrieval/config/models.py:518`) and comments. Setting
  `serve.watch.enabled: true` in YAML does nothing; "overrides" implies YAML
  alone could enable it — it can't.

**DEFECT 4 — CLAUDE.md's "Full CI gate set" does not match `ci.yml`.**
*(CONFIRMED, doc vs CI.)*

- Doc side: `CLAUDE.md:75` documents `ruff check python/ tests/ benchmarks/`
  (the "# Python lint" command block, directly above the "Full CI gate set
  (.github/workflows/ci.yml)" block — which itself lists only
  `ruff format --check`, not `ruff check`, so CLAUDE.md's sole lint command
  claims `benchmarks/` coverage CI doesn't deliver);
  `CLAUDE.md:72` lists `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`.
- Code side: `.github/workflows/ci.yml:111` runs
  `uv run ruff check python/ tests/` — `benchmarks/` is **not**
  lint-checked in CI (`ruff format --check` does include it, `ci.yml:114`);
  `Makefile:15,34` include `benchmarks/` locally. The benchmarks pytest
  suite is not run by any workflow (`benchmark.yml` only runs
  `pydocs_eval.runner` / `ci_compare`).

**DEFECT 5 — stale internal docstrings still describe the removed 2-tool MCP
surface.** *(CONFIRMED.)*

- `python/pydocs_mcp/retrieval/config/models.py:505-507` — `WatchConfig`
  docstring: "The MCP surface stays at the fixed 2 tools (``search``,
  ``lookup``)". The surface is six task-shaped tools (`server.py:1-9`).
- `python/pydocs_mcp/application/mcp_inputs.py:172` ("Input for the
  ``search`` MCP tool") and `:220` ("Input for the ``lookup`` MCP tool") —
  no MCP tools by those names exist; `SearchInput` backs `search_codebase`,
  `LookupInput` is internal routing for `get_symbol` / `get_references`.

**DEFECT 6 — EXTENSIONS.md contradicts itself on
`WeightedScoreInterpolationStep`.** *(CONFIRMED.)*

- `EXTENSIONS.md:222` calls it "the planned
  `WeightedScoreInterpolationStep`" while `EXTENSIONS.md:175` marks it
  "[SHIPPED]" — and the step is registered as
  `weighted_score_interpolation`
  (`python/pydocs_mcp/retrieval/steps/weighted_score_interpolation.py`,
  confirmed in the step-registry dump).

**DEFECT 7 — `RetrieverPipeline`'s own class docstring example raises
`TypeError` if copy-pasted.** *(CONFIRMED.)*

- `python/pydocs_mcp/retrieval/pipeline/base.py:67-75` constructs
  `ChunkFetcherStep(name="fetch", limit=200)` — missing the required
  `provider` positional and `filter_adapter` kw-only field
  (`retrieval/steps/chunk_fetcher.py:113,117`) — and
  `TokenBudgetStep(name="budget", max_tokens=2000)` — no `max_tokens` field
  exists; its fields are `formatter` (required), `budget` (required), `name`
  (`retrieval/steps/token_budget.py:50-56`). (The `DOCUMENTATION.md:119-193`
  Python example, by contrast, is correct.)

**DEFECT 8 — the docs site overstates the offline guarantee.** *(CONFIRMED.)*

- `documentation/index.md:19-21` claims "The only call that ever leaves your
  machine is the optional LLM reasoning mode" — contradicted by
  `README.md:75-77` (network also with `embedding.provider: openai`), the
  openai embedder provider (`retrieval/config/embedder_models.py:55`), and
  `decision_capture.llm_structuring` (`defaults/default_config.yaml:132-135`).

**DEFECT 9 — `--gpu` docs omit the `watch` subcommand in two places.**
*(MINOR.)*

- `README.md:132-133` and `DOCUMENTATION.md:104-105` say pass `--gpu` to
  `serve` / `index` only; `INSTALL.md:80` (`pydocs-mcp serve|index|watch`)
  and `DOCUMENTATION.md:231-232` correctly include `watch`; argparse adds
  `--gpu` to all three (`__main__.py:143-147` + `:189-194`).

**DEFECT 10 — unquoted extras install breaks under zsh.** *(MINOR.)*

- `DOCUMENTATION.md:381` shows unquoted `pip install pydocs-mcp[watch]`
  (zsh glob expansion fails); `README.md:145` uses the correct quoted form
  `pip install 'pydocs-mcp[watch]'`.

**DEFECT 11 — external MCP-client config snippets are unverifiable from the
repo and likely stale.** *(PLAUSIBLE — needs maintainer verification.)*

- `DOCUMENTATION.md:923-924` documents an agent-CLI MCP config at
  `~/.config/<client>/mcp_servers.json` plus a workspace-level
  `mcp_servers.json` (the doc names the concrete per-client paths; this spec
  refers to them generically per the vendor-neutrality policy); current
  client conventions for AI coding assistants have shifted (e.g.
  project-level `.mcp.json` / a CLI registration command). The other client
  snippets at
  `DOCUMENTATION.md:934-952` are likewise unverifiable from inside this
  repo. A doc-test can only pin the JSON well-formedness of these fenced
  blocks, not external client file paths.

### 1.2 Why a permanent harness (deliverable B)

Defects 5-7 are the same drift class inside code docstrings; 1-4 and 8-10 are
doc-vs-code drift. All of them share one property: **each is statically
detectable** from the repo alone — no server, no index, no network. That is
exactly the profile of a cheap, deterministic pytest suite. Per the repo's TDD
rule (CLAUDE.md §Design Patterns: "failing test first"), every fix in §3.8
lands with the harness test that would have caught it already red.

### 1.3 Concurrent same-day spec — watch promotion (shared-file collision)

A sibling spec of the same date,
`docs/superpowers/specs/2026-07-11-watch-default-install-spec.md` (status
Proposed), promotes `watchdog` from the `[watch]` extra into the required
runtime deps and edits surfaces this spec also touches. The house practice
for shared files — survey concurrent work before fixing (the same rule the
repo applies to concurrent PRs) — applies at spec level too. The concrete
overlaps, resolved per-defect in §3.8 and sequenced in §6:

- **D10's target line is deleted outright by the sibling.** D10 quotes the
  unquoted `pip install pydocs-mcp[watch]` at `DOCUMENTATION.md:381`; the
  watch spec's §3.6 rewrite of the `DOCUMENTATION.md:376-388` "### Install"
  subsection drops that command line and the exit-with-hint paragraph
  (`:386-388`) entirely, and its rewritten
  `tests/test_readme_watch_mention.py` *forbids* any watch extras-install
  instruction in README/DOCUMENTATION. If the watch spec lands first, D10's
  line edit is moot — only the harness quoting lint survives (it
  generalizes; see §3.8-D10).
- **D3 is compatible with the sibling's Non-goal 1, but its doc anchors are
  rewritten by it.** The watch spec's Non-goal 1 / §7.1 deliberately keep
  `serve.watch.enabled: false` and defer any *default flip* to a future
  spec. D3 does not flip the default (AC19 pins `false`); it wires the
  existing key so an overlay opt-in works — which is also the prerequisite
  that makes the sibling's §7.1 question meaningful (flipping a dead key's
  default would change nothing). No conflict in behavior — but the sibling
  rewrites the very text D3 cites: its §3.5 replaces the
  `defaults/default_config.yaml:145-150` comment (which today carries both
  the "CLI flag overrides `enabled`" claim and the now-false "Requires
  `pip install pydocs-mcp[watch]`" sentence), and its §3.6 deletions inside
  `DOCUMENTATION.md:376-388` renumber the ":411-421" YAML-knobs block D3
  cites. D3's "docs then become true as written" claim must be re-anchored
  against whichever comment text and line numbers survive.
- **Doc-pin test interplay.** The sibling inverts
  `tests/test_readme_watch_mention.py` (README/DOCUMENTATION must NOT
  instruct an extras install for the watcher). The harness must not re-pin
  any `pip install …[watch]` string as *required* prose; D10's quoting lint
  is order-safe because it asserts quoting only of lines that exist — zero
  watch-install lines satisfy it vacuously.

Line citations in this spec are as of the audit commit; whichever spec lands
second re-resolves the shared anchors (§6 gives the sequencing rule, §7 Q9
tracks the ordering decision).

## 2. Goals / Non-goals

### Goals

1. **G1 — Ship the audit.** §1.1 is the authoritative, numbered defect list;
   §3.8 assigns each defect an explicit fix (or an owner decision, tracked in
   §7).
2. **G2 — CLI doc-conformance tests.** Every `pydocs-mcp …` (and
   `ask-your-docs …`) invocation inside fenced command blocks of the root
   markdown docs must parse against the *real* argparse tree —
   `--help`-level validation, zero execution.
3. **G3 — TOOL_DOCS ↔ MCP schema snapshot tests.** `TOOL_DOCS` keys, its
   `Examples` call kwargs, and its numeric claims ("up to 20 targets") must
   match the pydantic input models and the registered server handlers.
4. **G4 — doc YAML-key conformance tests.** Every YAML key referenced in the
   docs (fenced ```yaml blocks + backtick-quoted dotted paths in prose) must
   exist in `AppConfig` / its sub-models; pipeline-blueprint blocks are
   validated against the step registry instead.
5. **G5 — docs-site include integrity.** Every MyST `{include}`
   `:start-after:` / `:end-before:` marker string in `documentation/` must
   exist in the included root file (a moved heading currently produces a
   silently empty site page).
6. **G6 — zero CI churn.** The suite lives at `tests/test_doc_conformance.py`
   and is auto-collected by the existing ci.yml pytest invocation
   (`ci.yml:169-181`); it must be Windows-safe pure Python (precedent:
   `tests/test_docs_late_interaction.py:25-30` deliberately re-implemented a
   bash audit in Python "so the test runs on Windows runners too").

### Non-goals

- **No new MCP tools or parameters.** The surface stays fixed at the six
  task-shaped tools (CLAUDE.md §"MCP API surface vs YAML configuration");
  every fix in §3.8 lands behind the existing surface. Defect 1's fix
  changes a *validator*, not a signature.
- **No execution-level doc testing.** The harness never runs commands, never
  builds an index, never opens a server. Runtime behavior parity (e.g.
  "does `--limit 5` actually return 5 results") is the benchmark harness's
  job, not this suite's.
- **No coverage of `benchmarks/README.md`** in the first iteration. It
  documents a separate published package (`pydocs-mcp-eval`) with its own
  parsers and `PYTHONPATH=benchmarks/src` requirement; a sibling test under
  `benchmarks/tests/` is proposed as follow-up (§7 Q4), not blocked on here.
- **No verification of external MCP-client file paths** (Defect 11): the
  harness pins only JSON validity of those fenced blocks; the path claims
  need a maintainer with access to the client docs.
- **No prose-quality linting.** Only machine-checkable claims (commands,
  flags, params, keys, markers, JSON validity) are in scope.

## 3. Detailed design

### 3.1 Module layout

```
tests/
└── test_doc_conformance.py        # NEW — the whole harness, one file (~450 lines)
python/pydocs_mcp/
├── ask_your_docs/cli.py           # CHANGED — extract _build_parser(); defer theme import
├── application/mcp_inputs.py      # CHANGED — Defect 1 (why-target regex) + Defect 5 docstrings
├── application/multi_project_search.py  # unchanged (Defect 2 resolved doc-side; see §3.8)
├── retrieval/config/models.py     # CHANGED — Defect 5 (WatchConfig docstring), Defect 3 direction
├── retrieval/pipeline/base.py     # CHANGED — Defect 7 (docstring example)
└── __main__.py                    # CHANGED — Defect 3 (wire serve.watch.enabled)
README.md, INSTALL.md, DOCUMENTATION.md, CLAUDE.md, EXTENSIONS.md,
documentation/index.md             # CHANGED — doc-side fixes (Defects 2, 4, 8, 9, 10, 11)
.github/workflows/ci.yml           # CHANGED — Defect 4 (add benchmarks/ to ruff check)
```

One file for the harness keeps it grep-able and within the ~500-line file
budget (CLAUDE.md §Clean Code); extraction helpers are module-level functions
in the same file, each 4-20 lines. If the file outgrows the budget in a later
iteration, split into a `tests/doc_conformance/` package then — not now
(YAGNI).

### 3.2 Data models (harness-internal)

Per house convention (`@dataclass(frozen=True, slots=True)` value objects):

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True, slots=True)
class FencedBlock:
    """One fenced code block lifted from a markdown file."""
    file: Path          # repo-relative
    line: int           # 1-based line of the opening fence
    language: str       # "" for untagged fences
    text: str

@dataclass(frozen=True, slots=True)
class DocCommand:
    """One tokenized CLI invocation found inside a fenced block."""
    file: Path
    line: int           # line of the command itself (for assertion messages)
    raw: str            # the original line, for error output
    tokens: tuple[str, ...]   # shlex-split, prompt/continuations stripped

@dataclass(frozen=True, slots=True)
class YamlKeyRef:
    """A dotted config path referenced by the docs."""
    file: Path
    line: int
    dotted_path: str    # e.g. "serve.watch.debounce_ms"
```

Every assertion failure message embeds `file:line` + `raw` so a red test reads
like a defect report, mirroring §1.1's format.

### 3.3 Doc corpus and block extraction

```python
_DOC_FILES: tuple[str, ...] = (
    "README.md",
    "INSTALL.md",
    "DOCUMENTATION.md",
    "CLAUDE.md",
    "SPEC.md",
    "EXTENSIONS.md",
    "examples/ask_your_docs_agent/README.md",
)
```

This set is the root source of every `documentation/` site page (the site is
MyST `{include}` shims, all under `documentation/` — `user-guide/cli.md:3-5`,
`user-guide/configuration.md:3-6`, `getting-started/quickstart.md:3-5`,
`extending.md:1` (a top-level page including `EXTENSIONS.md`),
`benchmarks/index.md:1` — so parsing the roots covers the whole site; G5
separately pins the include markers themselves).

`_iter_fenced_blocks(path) -> Iterator[FencedBlock]` is a ~15-line state
machine over ``` fences (no markdown dependency — pure stdlib, Windows-safe
per G6). It records language tag and opening-fence line number. Nested/quad
fences are out of scope (none exist in the corpus).

### 3.4 Test group 1 — CLI command conformance (G2)

**Command harvesting.** From blocks whose language ∈
`{"bash", "sh", "shell", "console", ""}`:

1. Join backslash line-continuations.
2. Strip a leading `$ ` prompt and full-line `#` comments.
3. Split compound lines on shell operators (`&&`, `;`, `|` with surrounding
   whitespace, `>`); keep only segments whose first token is `pydocs-mcp` or
   `ask-your-docs`. (Env-var prefixes like `PYTHONPATH=… pytest` never start
   with the entry-point name, so they fall out naturally.)
4. Expand *intra-token* alternation — `pydocs-mcp serve|index|watch --gpu`
   (`INSTALL.md:80`) becomes three `DocCommand`s. Alternation applies only to
   a token containing `|` with no surrounding spaces.
5. Tokenize with `shlex.split(..., posix=True)`.

**Parse validation, zero execution.** The argparse tree is buildable without
side effects (`__main__.py:50-52`: `_build_parser` is "kept as a named helper
so tests can build the parser without triggering main's dispatch logic";
`tests/test_main_cli.py:50` already imports it). `parse_args` only builds a
`Namespace` — dispatch lives in `main()` via `_CMD_TABLE`
(`__main__.py:975-986`) — so `--help`-level validation is inherently
non-executing. To turn argparse's `sys.exit` into assertions:

```python
class _DocCommandError(Exception):
    """argparse rejected a documented invocation."""

def _parse_or_raise(tokens: tuple[str, ...]) -> None:
    parser = _build_parser()
    # Performance: patch at class level so subparser instances inherit it.
    with unittest.mock.patch.object(
        argparse.ArgumentParser, "error",
        lambda self, message: (_ for _ in ()).throw(_DocCommandError(message)),
    ):
        parser.parse_args(list(tokens[1:]))   # tokens[0] is the entry point

@pytest.mark.parametrize("cmd", _harvest_commands(), ids=_command_id)
def test_documented_cli_invocation_parses(cmd: DocCommand) -> None:
    try:
        _parse_or_raise(cmd.tokens)
    except _DocCommandError as exc:
        pytest.fail(f"{cmd.file}:{cmd.line}: `{cmd.raw}` rejected: {exc}")
```

Paths in examples (`/path/to/project`, `~/pydocs-index`) are plain strings to
argparse — no filesystem access happens. `choices=` validation (e.g.
`--kind {docs,api,any,decision}`, `--direction {callers,…,governed_by}`,
`--show {default,…,governed_by}`, `symbol --depth {summary,tree,source}` —
`__main__.py:228-363`) fires exactly as it would for a real user, which is
the point.

**Flag-inventory assertion** (complements the per-invocation test): a second
test asserts that every `--flag` token appearing in *prose* backticks across
the corpus (`--gpu --watch --kind --workspace --config --direction --full-dep
--depth --db --cache-dir --force --project --no-inspect --workers --target
--skip-project --skip-deps --scope --limit --show --project-dir --no-rust`,
per the verified inventory) is a registered option string on at least one
subparser. This catches docs inventing a flag outside a fenced block.

**`ask-your-docs` commands.** `ask_your_docs/cli.py` currently builds its
parser inside `main()` *after* `_require_extra()` (`cli.py:30-53`), so in a
core-only env `main()` exits (`SystemExit`) before any parser exists — which
blocks help-level testing without the `[ask-your-docs]` extra. (The
module-level theme import at `cli.py:16` is *not* a blocker and does not
violate the subpackage's lazy-import contract — `ask_your_docs/theme.py` is
pure constants with no third-party imports, so `import
pydocs_mcp.ask_your_docs.cli` already succeeds core-only; CLAUDE.md §Key
Technical Details' "imported lazily, so `import pydocs_mcp` never pulls in
langgraph/streamlit" holds today.) Fix, mirroring `__main__.py`:

```python
# ask_your_docs/cli.py — extracted; importable with core deps only.
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ask-your-docs", ...)
    p.add_argument("--workspace"); p.add_argument("--model")
    p.add_argument("--base-url"); p.add_argument("--config")
    p.add_argument("--port", type=int, default=_DEFAULT_PORT)  # 8501
    # dest stays "streamlit_args" (the current name, cli.py:47-51) so
    # main()'s consumption of the parsed Namespace is unchanged.
    p.add_argument("streamlit_args", nargs=argparse.REMAINDER)
    return p
```

`main()` calls `_require_extra()` first, then `_build_parser()`.
`_DEFAULT_PORT = 8501`
becomes a module constant (single-source-of-truth rule, CLAUDE.md §Default
values). The harness then validates `ask-your-docs …` doc invocations
identically. If the refactor is deferred, the harness must
`pytest.importorskip`-skip these commands — see §7 Q6; the refactor is the
recommendation because CI does not install the extra (`ci.yml` comment) and
a permanently-skipped test is a non-test.

### 3.5 Test group 2 — TOOL_DOCS ↔ MCP schema snapshot (G3)

The six-tool surface is constitutionally fixed
(`get_overview, search_codebase, get_symbol, get_context, get_references,
get_why` — CLAUDE.md §MCP API surface), registered in `server.py:251-267`
with `description=TOOL_DOCS[name]`. Existing tests already pin registration
count (`tests/application/test_server_surface.py`), §D13 doc structure +
token budgets (`tests/application/test_tool_docs_lint.py`, against
`tool_docs.py:14-23` markers/budgets), and the server wiring
(`tests/test_tool_descriptions.py`). This group adds the *schema* dimension:

```python
_TOOL_INPUT_MODELS: dict[str, type[pydantic.BaseModel]] = {
    "get_overview": OverviewInput,      # mcp_inputs.py:283
    "search_codebase": SearchInput,     # mcp_inputs.py:171
    "get_symbol": SymbolInput,          # mcp_inputs.py:304
    "get_context": ContextInput,        # mcp_inputs.py:326
    "get_references": ReferencesInput,  # mcp_inputs.py:354
    "get_why": WhyInput,                # mcp_inputs.py:389
}
```

Assertions, one test each:

1. **Key parity:** `TOOL_DOCS.keys() == _TOOL_INPUT_MODELS.keys() ==` the
   set of tools registered by `server._register_tools` (via
   `inspect.signature` on the registered closures, extending the
   `inspect.getsource` precedent of `test_server_surface.py:18-24`).
2. **Example-call kwargs are real fields:** parse every
   `tool_name(kw=value, …)` call in each TOOL_DOCS `Examples` section with
   `ast.parse` (each example is a valid Python expression — verified
   per-example during the audit) and assert every keyword is in
   `model.model_fields`. This is the test that would turn red if a future
   TOOL_DOCS edit invents `search_codebase(min_score=0.5)` — which the
   constitution forbids as an MCP param anyway (YAML litmus test).
3. **Handler ↔ model parity:** for each registered handler,
   `inspect.signature` parameter names must be a subset of the input model's
   fields (the handlers construct the models — `server.py:269-336`).
4. **Numeric-claim parity, single-sourced:** TOOL_DOCS' "up to 20" claims
   (`tool_docs.py:74`, `:94`) must equal
   `ContextInput.model_fields["targets"].metadata` max_length and
   `WhyInput` ditto (`mcp_inputs.py:329`, `:393`). The test reads the bound
   from the model — never re-hardcodes `20` (single-source-of-truth rule).
   Likewise, `DOCUMENTATION.md`'s "(default 10)" for search limit must equal
   `AppConfig().search.output.default_limit`, and the refs default must
   equal `reference_graph.output.default_limit` — read from a default
   `AppConfig()`, since the module slots in `mcp_inputs.py:56-73` are
   installed from those keys by `configure_from_app_config`
   (`mcp_inputs.py:106-168`).
5. **DOCUMENTATION.md tool table:** parse the table at
   `DOCUMENTATION.md:314-321` (row regex on `| get_x(` cells); every
   parameter named in a row must be a field of that tool's input model, and
   every non-internal model field must appear in the row. Currently green —
   verified exact match during the audit — this pins it.

### 3.6 Test group 3 — YAML keys in docs exist in AppConfig (G4)

`AppConfig` uses `extra="ignore"` at the top level
(`app_config.py:183-187`) while sub-models mostly use `extra="forbid"` (30
occurrences in `models.py`, 2 in `embedder_models.py`) — so **load-time
validation cannot catch a doc-invented top-level key**; the test must
introspect `model_fields` recursively:

```python
def _valid_dotted_paths(model: type[pydantic.BaseModel]) -> set[str]:
    """Flatten model_fields into dotted paths, recursing into sub-models.

    dict[str, X]-typed fields terminate the walk (any subkey is legal);
    Union members are each walked and their paths unioned.
    """
```

**Harvesting**, two channels:

- **Fenced ```yaml blocks** in the corpus: `yaml.safe_load` each; if the
  top-level mapping contains `steps` (a pipeline blueprint, e.g. the
  EXTENSIONS.md examples), validate each step's registered name against
  `retrieval.pipeline` `step_registry` (the 21 registered names, from
  `bm25_scorer` through `weighted_score_interpolation`) and — for ingestion
  blueprints — `stage_registry` (14 names). Otherwise flatten the mapping to
  dotted paths and assert each against `_valid_dotted_paths(AppConfig)`.
  Blocks that fail `yaml.safe_load` fail the test outright (a doc YAML block
  that doesn't parse is itself a defect).
- **Prose dotted paths**: backtick-quoted tokens matching
  `` `[a-z_]+(\.[a-z_]+)+` `` whose **first segment is an AppConfig field
  name** (`cache_dir, log_level, metadata_schemas, pipelines, extraction,
  reference_graph, search, symbol_source, output, overview, decision_capture,
  decisions, serve, embedding, search_backend, llm, late_interaction` —
  `app_config.py:77-167`). The first-segment gate eliminates false positives
  (module paths like `pydocs_mcp.db` never collide with these roots; if one
  ever does, the token is still a legitimate check candidate).

Step-param spot checks that the audit verified stay pinned the same way:
`graph_expand.kind_weights` (`retrieval/steps/graph_expand.py:137`) and
`llm_tree_reasoning.rerank_candidates` (`llm_tree_reasoning.py:116`) are
fields on their step dataclasses; the pipeline-blueprint branch validates
step param names against `dataclasses.fields(step_cls)` when the registry
exposes the class.

**Dead-key direction (the Defect 3 lesson):** existence-in-AppConfig is
necessary but not sufficient — `serve.watch.enabled` *exists* and is dead.
A full "every field is read somewhere" reachability test is out of scope
(static call analysis, high false-positive rate); instead the fix list wires
the one known dead key (§3.8-D3) and §7 Q8 tracks whether a
grep-based read-site audit is worth a follow-up.

### 3.7 Test groups 4 & 5 — include-marker integrity and client-snippet JSON (G5)

**Group 4:** walk `documentation/**/*.md`; for every MyST include directive,
extract the target path and the `:start-after:` / `:end-before:` literal
strings; assert the target file exists and contains each marker string
verbatim. This turns "someone reworded `## CLI reference` in
DOCUMENTATION.md" from a silently-empty published page into a red test.
Extra guard: flag any `:end-before: "---"` marker (the fragile pattern
already present at line 5 of six `documentation/` pages:
`user-guide/configuration.md`, `getting-started/mcp-clients.md`,
`user-guide/live-reindex.md`, `rnd/cache.md`, `rnd/reference-graph.md`,
`architecture/index.md`) with a *warning-level* xfail-style allowlist rather
than a hard failure — migrating those to next-heading markers is a doc
follow-up, not a gate (the `---` trap is a known repo hazard).

**Group 5:** the fenced ```json blocks in the MCP-client integration section
(`DOCUMENTATION.md:919-952`) must `json.loads` cleanly. That is the entire
machine-checkable surface of Defect 11.

### 3.8 The fix list (deliverable A closure)

Each fix names its regression test (TDD rule); harness tests are those of
§3.4-3.7 and would already be red where noted.

- **D1 — admit slash paths in `WhyInput.targets`** *(behavior fix;
  recommended direction, §7 Q1 gates final confirmation).* Add a dedicated
  `_WHY_TARGET_RE` in `mcp_inputs.py` that admits `/` (and keeps forbidding
  `:` and `]]`-hostile characters — the `ContextInput` docstring warns that
  unvalidated `:` / `]]` corrupts the `[[…]]` pointer-token grammar in
  `application/formatting.py`; `/` appears grammar-safe but must be confirmed
  before merge). `_TARGET_RE` stays untouched for the symbol/context tools.
  This changes no MCP signature — the surface stays six tools with the same
  params, per the constitution. Tests: new unit tests in
  `tests/application/test_mcp_inputs.py` (`a/b.py`,
  `src/pydocs_mcp/db.py` accepted; `pkg.mod`, `b.py` still accepted;
  injection strings still rejected) + a `DecisionService` test asserting the
  path branch of `_classify_target` (`decision_service.py:74-89`) is now
  reachable end-to-end from `WhyInput`.
- **D2 — doc fix: align DOCUMENTATION.md with the argparse help.**
  Rewrite `DOCUMENTATION.md:271-272`, add the multi-repo caveat to the tool
  table row (`:317`) and the client example (`:957`), and correct the
  overlay note (`:685-687`) to say `search.output.default_limit` bounds the
  *client-visible* limit parameter, while single-project result count is the
  pipeline YAML's `limit` step (`chunk_search_graph.yaml` `max_results: 8`).
  Rationale: threading `payload.limit` into `build_search_query` is a
  behavior change to ranked output size — that is retrieval tuning, and the
  constitution routes tuning to YAML, not to param semantics changes made in
  passing ("anything about *how* retrieval ranks … goes in YAML"). If the
  owner wants the behavior instead, that is a separate spec (§7 Q2).
  Regression test: a targeted prose assertion in group 2's table check that
  the `limit` row carries the multi-repo caveat string.
- **D3 — wire `serve.watch.enabled`** *(behavior fix; recommended).* In
  `_cmd_serve` (`__main__.py:891`), replace
  `if getattr(args, "watch", False):` with
  `if getattr(args, "watch", False) or config.serve.watch.enabled:`.
  Rationale: watch-on-by-default is a per-deployment setting — the YAML
  litmus test ("if a behavior could be A/B tested … it belongs in YAML")
  says the key *should* work; deleting it would remove a documented,
  constitution-shaped knob. Docs then become true as written. Tests: unit
  test on `_cmd_serve` dispatch with a config overlay setting
  `serve.watch.enabled: true` and no `--watch` flag (fake watcher via the
  existing serve-test fakes); plus the inverse (flag on, key off).
  Alternative (delete the key) in §4. **Sequencing (§1.3):** compatible with
  the watch-promotion spec's Non-goal 1 — the shipped default stays `false`
  (AC19); D3 only makes overlay opt-in functional, and is the prerequisite
  for that spec's §7.1 default-flip question. If that spec lands first,
  re-anchor D3's doc-side citations: its §3.5 rewrite of the
  `default_config.yaml:145-150` comment and its §3.6 deletions inside
  `DOCUMENTATION.md:376-388` shift the ":411-421" YAML-knobs block, and the
  "CLI --watch overrides this at runtime" wording must be reconciled with
  whichever comment text survives (under D3's OR wiring, "overrides" is
  accurate only in the off→on direction — the flag cannot force watch *off*
  when the key is `true`; prefer "either switch enables" phrasing in the
  reconciled text).
- **D4 — CI fix + doc clarification.** Change `ci.yml:111` to
  `uv run ruff check python/ tests/ benchmarks/` (matches `Makefile:15,34`
  and CLAUDE.md). For the benchmarks pytest suite, amend `CLAUDE.md:72` to
  state it is a local gate not run in CI (adding it to CI is a cost/owner
  decision, §7 Q5). Regression test: none automatable cheaply (a test that
  parses ci.yml against CLAUDE.md's fenced block is possible but brittle);
  the fenced-block command harvester *does* re-verify that the CLAUDE.md
  commands themselves remain parseable.
- **D5 — rewrite the three stale docstrings**
  (`retrieval/config/models.py:505-507`, `mcp_inputs.py:172`, `:220`) to
  name the current six-tool surface / the actual backing tools. Regression
  test: extend group 2 with a repo-grep assertion that the strings
  "fixed 2 tools" and "``search`` MCP tool" / "``lookup`` MCP tool" do not
  appear under `python/pydocs_mcp/` (same shape as
  `test_readme_no_internal_jargon`).
- **D6 — EXTENSIONS.md:222**: drop "planned", reference the registered step
  name `weighted_score_interpolation`. Covered by group 3's blueprint/step
  registry validation plus a one-line prose assertion.
- **D7 — fix `base.py:67-75` docstring example** to construct
  `ChunkFetcherStep(provider=…, filter_adapter=…, name="fetch")` and
  `TokenBudgetStep(formatter=…, budget=2000, name="budget")` (field names
  per `chunk_fetcher.py:113-118`, `token_budget.py:50-56`). Regression test:
  a doctest-style check that every `XxxStep(...)` call in the
  `retrieval/pipeline/base.py` docstring uses only `dataclasses.fields`
  names of the referenced class (small, targeted — not a general docstring
  linter).
- **D8 — `documentation/index.md:19-21`**: reword to "No network calls in
  the default configuration; network is used only if you opt into the
  OpenAI-compatible embedding provider, LLM decision structuring, or the
  LLM reasoning retrieval steps." Regression: prose assertion pinning the
  corrected sentence's key phrase.
- **D9 — add `watch` to the `--gpu` mentions** at `README.md:132-133` and
  `DOCUMENTATION.md:104-105`. Regression: the group-1 alternation expansion
  already validates `serve|index|watch --gpu`; add a prose assertion that
  README's `--gpu` sentence names all three subcommands.
- **D10 — quote the extras install** at `DOCUMENTATION.md:381`
  (`pip install 'pydocs-mcp[watch]'`) — *conditional on sequencing (§1.3):*
  the watch-promotion spec deletes this exact line; if it lands first, skip
  the line edit as moot. The regression lint ships unconditionally either
  way: the group-1 harvester gains a lint that every `pip install …[extra]`
  line in the corpus is quoted. The lint outlives the `[watch]` line — the
  corpus retains extras-install lines for `[graph]`, `[openvino]`,
  `[sentence-transformers]`, `[late-interaction]`, `[ask-your-docs]`
  (`DOCUMENTATION.md:85,:490`, `README.md:195,:304,:323,:412`,
  `examples/ask_your_docs_agent/README.md:68-79`; all already quoted) — and
  is order-safe against that spec's inverted doc-pin tests: it asserts
  quoting only of lines that exist, so zero watch-install lines satisfy it
  vacuously, and the harness pins no `pip install …[watch]` string as
  required prose.
- **D11 — maintainer verification of external-client snippets**
  (`DOCUMENTATION.md:919-952`), vendor-neutral wording preserved in this
  spec per the docs policy. Harness pins JSON validity (group 5) now;
  path-claim verification is §7 Q3.

All README-touching fixes must pass the existing no-internal-jargon audit
(CLAUDE.md §README files) — no PR numbers in replacement text.

### 3.9 YAML config surface

**None.** The harness introduces no new config keys — it is a test suite, and
per the constitution new *tunables* would need YAML, but a doc-conformance
test has nothing to tune. The only YAML-adjacent change is behavioral wiring
of the **existing** `serve.watch.enabled` key (D3), whose default stays
`enabled: false` in `defaults/default_config.yaml:145-153` (unchanged
default, single source: the `WatchConfig` pydantic field at
`models.py:518`).

### 3.10 CI wiring (G6)

Zero pipeline edits for the harness itself: `ci.yml:169-181` runs
`uv run python -m pytest tests/ --ignore=tests/test_parity.py
--cov=pydocs_mcp --cov-fail-under=90` on ubuntu (full OS matrix incl.
Windows on tags/dispatch), so `tests/test_doc_conformance.py` is
auto-collected. Constraints that follow:

- Pure-Python stdlib + already-installed deps only (`yaml`, `pydantic` are
  runtime deps). No subprocess spawning, no `bash`-isms — Windows-safe.
- The harness *imports* `pydocs_mcp.__main__`, `application.tool_docs`,
  `application.mcp_inputs`, `retrieval.config.app_config`,
  `retrieval.pipeline` registries — all core-install imports; the
  `ask_your_docs.cli._build_parser` import is extras-free after the D-list
  refactor (§3.4).
- Doc files are read relative to the repo root, located via
  `Path(__file__).resolve().parents[1]` (existing doc-test precedent).
- The one deliberate ci.yml edit is D4 (benchmarks lint), independent of the
  harness.

### 3.11 Relationship to existing doc tests

The new suite **coexists** with, and does not absorb,
`tests/test_docs_late_interaction.py`,
`tests/test_docs_updated_for_tree_reasoning.py`,
`tests/test_tool_descriptions.py`, `tests/application/test_tool_docs_lint.py`,
`tests/application/test_server_surface.py` — those pin *presence and
structure* of specific features' docs; this suite pins *conformance* of
commands/params/keys generically. Folding them in would churn green tests for
no coverage gain (YAGNI). Where group 2 extends `test_server_surface`'s
territory (key parity), it asserts a superset without duplicating the
existing assertions.

## 4. Alternatives considered

### 4.1 Subprocess `--help` smoke tests instead of in-process argparse parsing

Run `pydocs-mcp <sub> --help` in a subprocess for each documented command.

- Pros: tests the installed entry point end-to-end, including packaging
  (`pyproject.toml:130-133` script wiring); no monkeypatching.
- Cons: only validates that the *subcommand* exists — `--help` exits before
  flag validation, so `search --kind bogus` would pass; slow (one interpreter
  per command, hundreds of invocations); Windows quoting hazards; requires an
  installed package in the test env.
- **Recommendation: rejected.** In-process `parse_args` with the exit/error
  patch validates the full grammar (choices, types, required args) in
  milliseconds and is the mechanism `_build_parser` was explicitly kept for
  (`__main__.py:50-52`).

### 4.2 A markdown-parsing dependency (e.g. `markdown-it-py`) for extraction

- Pros: robust to exotic markdown; less hand-rolled parsing.
- Cons: new dependency in the default install path or a test-only dep to
  manage; the corpus uses plain triple-backtick fences exclusively; the
  state machine is ~15 lines.
- **Recommendation: rejected** — stdlib extraction, per the lean-install
  policy (the ~90MB default footprint is a stated constraint and even
  dev-group additions need justification).

### 4.3 Fix Defect 2 by threading `limit` into the single-project pipeline

Make `build_search_query` (`search_query.py:38-52`) carry `payload.limit`
into `SearchQuery` and have the `limit` step honor it.

- Pros: the flag then does what most users expect; DOCUMENTATION.md becomes
  true as written.
- Cons: changes ranked-output size for every existing client and benchmark
  config; creates *two* competing caps (pipeline `max_results: 8` vs client
  `limit`, vs `search.output.default_limit: 10`) whose precedence needs its
  own design; the constitution routes ranking/output tuning to YAML and
  warns against param-semantics creep. It is A/B-testable → it belongs in a
  YAML-governed design, not a drive-by fix.
- **Recommendation: doc fix now (D2), behavior change deferred** to its own
  spec if the owner wants it (§7 Q2). The argparse help already documents
  the true semantics; DOCUMENTATION.md aligns to it.

### 4.4 Fix Defect 3 by deleting `serve.watch.enabled`

- Pros: smallest diff; no behavior change; docs reworded to "--watch is the
  only switch".
- Cons: removes a documented key users may already have in overlays
  (silently ignored today, loudly absent tomorrow if `WatchConfig` is
  `extra="forbid"` — a breaking overlay change); contradicts the YAML-first
  constitution — "watch on by default for this deployment" is precisely a
  per-deployment setting the YAML layer exists for.
- **Recommendation: rejected — wire it (D3).** One-line OR in `_cmd_serve`,
  key semantics become exactly what two doc surfaces already promise.

### 4.5 One mega doc-linter package under `tests/doc_conformance/`

- Pros: room to grow; per-group files.
- Cons: the requirement names `tests/test_doc_conformance.py`; the harness
  fits one ~450-line file of small functions; a package invites scope creep
  (prose linting, link checking) that this spec explicitly excludes.
- **Recommendation: single file now**; split only if a later iteration
  breaches the file-size budget.

### 4.6 Skip-if-extra-missing for `ask-your-docs` instead of the parser refactor

- Pros: zero production-code change.
- Cons: CI never installs `[ask-your-docs]` (`ci.yml:102-106` comment), so
  the tests would *never* run in CI — a permanent skip is documentation
  theater.
- **Recommendation: refactor (extract `_build_parser`)** — it mirrors the
  `__main__.py` precedent exactly, uses only stdlib `argparse`, and adds no
  import weight.

## 5. Testing & acceptance criteria

Each AC is independently checkable. "Harness" = `tests/test_doc_conformance.py`.

**Harness — CLI conformance (G2):**

1. **AC1.** The harness extracts fenced command blocks from exactly the
   corpus of §3.3 and parametrizes one test per harvested
   `pydocs-mcp`/`ask-your-docs` invocation; test IDs embed `file:line`.
2. **AC2.** Every currently-documented invocation in the corpus parses
   (post-D9/D10 doc fixes); the suite is green.
3. **AC3.** Mutation check (reviewer-executable): temporarily changing a
   documented command to `pydocs-mcp search --kind bogus` or
   `pydocs-mcp indexx .` turns exactly the corresponding parametrized test
   red, with the doc `file:line` and raw command in the failure message.
4. **AC4.** No test in the harness executes a command handler: no index
   files, no `~/.pydocs-mcp` writes, no subprocesses (verifiable by running
   the suite with a read-only HOME and by code review of the
   `parse_args`-only path).
5. **AC5.** `ask_your_docs/cli.py` exposes `_build_parser()` callable
   without the `[ask-your-docs]` extra; `python -c "import
   pydocs_mcp.ask_your_docs.cli"` succeeds in a core-only env (already true
   today, since `theme.py` is pure constants — pinned so it stays true);
   existing `_require_extra` behavior in `main()` is unchanged (its tests
   stay green).
6. **AC6.** The intra-token alternation `serve|index|watch --gpu`
   (`INSTALL.md:80`) expands to three validated invocations.
7. **AC7.** The prose flag-inventory test fails if a backticked `--flag` in
   the corpus is not an option string on any subparser.

**Harness — TOOL_DOCS ↔ schema (G3):**

8. **AC8.** Key parity holds three ways: `TOOL_DOCS` keys ==
   `_TOOL_INPUT_MODELS` keys == tools registered by `_register_tools`, and
   equals the constitutional six.
9. **AC9.** Every kwarg in every TOOL_DOCS `Examples` call is a
   `model_fields` member of that tool's input model (ast-parsed, not
   regex-guessed).
10. **AC10.** The "up to 20" claims and the documented limit defaults are
    read from the models / a default `AppConfig()` — grep of the harness
    shows no hardcoded `20`, `10`, or `50` literals for these assertions
    (single-source-of-truth rule).
11. **AC11.** The `DOCUMENTATION.md:314-321` tool-table row check passes and
    fails on a synthetic extra param inserted into a row (mutation check).

**Harness — YAML keys (G4):**

12. **AC12.** Every fenced ```yaml block in the corpus either safe-loads and
    validates (dotted paths against `AppConfig` introspection, or step/stage
    names against the registries for blueprint blocks) or fails the suite
    with `file:line`.
13. **AC13.** A synthetic doc edit referencing `search.output.bogus_knob` or
    a top-level `retreival:` key turns the suite red — explicitly covering
    the `extra="ignore"` top-level blind spot (`app_config.py:183-187`).
14. **AC14.** Prose dotted-path harvesting validates only tokens whose first
    segment is an `AppConfig` field (no false positives on module paths;
    demonstrated by the suite being green over the current corpus).

**Harness — site integrity + client JSON (G5):**

15. **AC15.** Every MyST include in `documentation/` points at an existing
    file and both marker strings exist verbatim; rewording a root-doc heading
    used as a marker turns the test red (mutation check).
16. **AC16.** The fenced JSON client snippets (`DOCUMENTATION.md:919-952`)
    parse with `json.loads`.

**Defect fixes:**

17. **AC17 (D1).** `pydocs-mcp why "…" --target src/pydocs_mcp/db.py` and
    `get_why(targets=["a/b.py"])` pass validation and reach
    `DecisionService._classify_target`'s path branch; injection-hostile
    targets (`:`, `]]`) remain rejected; new unit tests cover both sides.
    No MCP tool signature changed.
18. **AC18 (D2).** `DOCUMENTATION.md:271-272/317/685-687/957` all carry the
    multi-repo-union caveat consistent with the argparse help
    (`__main__.py:250-257`); no code behavior changed.
19. **AC19 (D3).** With `serve.watch.enabled: true` in an overlay and no
    `--watch` flag, `pydocs-mcp serve` starts the watcher; with the flag and
    `enabled: false` it also starts; both covered by unit tests on
    `_cmd_serve`'s dispatch (fake watcher); the YAML default remains `false`.
20. **AC20 (D4).** `ci.yml`'s ruff check includes `benchmarks/`;
    `CLAUDE.md:72` accurately labels the benchmarks pytest suite's CI status.
21. **AC21 (D5-D7).** The stale 2-tool docstrings and the broken
    `RetrieverPipeline` example are corrected; the harness's grep/docstring
    checks (see §3.8) pin them.
22. **AC22 (D8-D10).** `documentation/index.md`, the `--gpu` mentions, and
    the extras-quoting are corrected; the corresponding harness assertions
    pass. For D10 the AC is satisfied either by the quoting edit **or** by
    the line's prior deletion under the watch-promotion spec (§1.3); the
    group-1 quoting lint must pass over the merged corpus in both orders.

**Gates (G6 + repo CI contract):**

23. **AC23.** Full local gate set green: `ruff check` / `ruff format
    --check` over `python/ tests/ benchmarks/`, `mypy python/pydocs_mcp`,
    `complexipy` ≤15, `vulture`, `pytest tests/ --ignore=tests/test_parity.py
    --cov=pydocs_mcp --cov-fail-under=90`, `uv lock --check`. No coverage
    regression from the touched production lines (D1/D3/cli refactor lines
    are unit-tested per AC5/17/19).
24. **AC24.** The suite runs green on Windows semantics: no `os.sep`
    assumptions, no shell subprocesses, corpus paths built with `pathlib`.
25. **AC25.** The spec's own text and every doc fix remain vendor-neutral
    and jargon-free per the README policy; the README audit grep from
    CLAUDE.md returns no matches on touched files.

## 6. Rollout / migration / back-compat

- **Ordering:** land as one PR in three commits — (1) harness + the doc
  fixes it forces (D2, D6, D8-D10, CLAUDE.md wording), (2) code fixes with
  their unit tests (D1, D3, D5, D7, `ask_your_docs/cli.py` refactor), (3)
  the ci.yml lint-scope fix (D4). Commit 1's harness must be green at each
  commit boundary — i.e. commit 1 includes exactly the doc edits its own
  tests require.
- **Cross-spec sequencing (§1.3):** before branching, survey open /
  just-merged PRs for the same-day watch-promotion spec
  (`2026-07-11-watch-default-install-spec.md`) — the two specs edit the same
  `DOCUMENTATION.md` live-reindex section, the same
  `defaults/default_config.yaml` watch comment, and adjacent doc-pin tests.
  No hard dependency exists in either direction, but the second lander
  rebases: **watch-first** ⇒ drop D10's line edit (the quoting lint ships
  regardless), re-anchor D3's doc citations to the rewritten comment /
  shifted line numbers, and re-run the group-1/group-4 harvests over the
  merged docs; **audit-first** ⇒ the watch spec's §3.6 rewrite must preserve
  D2/D3's corrected wording, keep the group-4 include markers green (its
  edits fall inside a MyST-included region), and its
  `test_readme_watch_mention.py` inversion must coexist with the harness's
  quoting lint (it does — the lint is vacuous over zero watch-install
  lines). The harness itself is the second lander's safety net: group 1
  re-parses every merged command, group 4 re-verifies every include marker.
- **Back-compat, MCP surface:** unchanged — six tools, same signatures
  (`server.py:251-336` untouched except handler docstrings if D5 spills
  over). D1 *widens* accepted input for `get_why.targets` (previously
  rejected values now valid) — a strictly non-breaking relaxation for
  clients.
- **Back-compat, CLI:** no flags added/removed/renamed. D3 changes runtime
  behavior only for deployments that already set `serve.watch.enabled: true`
  in an overlay — which today is a silent no-op; those deployments start
  getting the behavior their config asked for. Called out in the changelog.
- **Back-compat, `ask-your-docs`:** `main()`'s observable behavior is
  identical (`_require_extra` still runs first); only import topology
  changes. The `[ask-your-docs]` extra requirement for actually *running*
  the command is unchanged.
- **Docs site:** all fixes are in the root files the site includes; group 4
  guarantees the include markers survived the edits before the PR merges.
- **Failure mode of the harness itself:** if a future doc edit legitimately
  needs an escape hatch (e.g. a deliberately-broken "don't do this" example),
  the convention is an HTML comment on the preceding line
  (`<!-- doc-conformance: skip -->`) honored by the harvester — shipped in
  the initial version but used zero times in the current corpus, so
  reviewers can grep its usage forever.
- **No index/schema migration**, no packaging change, no new dependency.

## 7. Open questions

1. **(D1 gate)** Confirm `/` is safe inside the `[[…]]` pointer-token
   grammar produced by `application/formatting.py` before relaxing
   `WhyInput` — the `ContextInput` docstring warns about `:` / `]]`; the
   audit found no `/`-sensitivity but the implementer must verify against
   the pointer parser, and add a grammar-level test if any exists.
2. **(D2 direction)** Does the owner want the *behavior* (thread
   `payload.limit` into single-project `SearchQuery`) as a follow-up spec?
   If yes, that spec must also resolve the three-way precedence between the
   pipeline `limit` step (`max_results: 8`), `search.output.default_limit`
   (10), and the client param.
3. **(D11)** Maintainer to verify current external-client MCP config
   conventions (project-level config file vs registration command) and
   update `DOCUMENTATION.md:919-952`; the harness only pins JSON validity.
4. **(Benchmarks docs)** Should a sibling conformance test land under
   `benchmarks/tests/` for `benchmarks/README.md` (~1000 lines, separate
   `pydocs-mcp-eval` package, own parsers, needs
   `PYTHONPATH=benchmarks/src`)? Proposed as follow-up; this spec declares
   it out of scope.
5. **(D4 scope)** Add `PYTHONPATH=benchmarks/src pytest benchmarks/tests/`
   to a CI workflow, or keep it a documented local-only gate? Cost/owner
   call; the doc fix in D4 is correct either way.
6. **(ask-your-docs refactor timing)** If the `cli.py` refactor is deferred
   for any reason, the harness ships with `pytest.importorskip` on those
   commands and a tracking note — but per §4.6 the refactor is small and
   recommended in this PR.
7. **(Stale-docstring scope)** D5/D7 are code docstrings, not user docs —
   included here because they are the same drift class; confirm reviewers
   agree they belong in this PR rather than a spin-off.
8. **(Dead-key reachability)** Is a follow-up "every `AppConfig` leaf field
   has a read site" audit (grep-based, allowlisted) worth building after the
   D3 lesson, or is the risk acceptable given `extra="forbid"` sub-models
   catch user typos?
9. **(Cross-spec ordering)** Which of this spec and
   `2026-07-11-watch-default-install-spec.md` lands first? §1.3 resolves
   each collision under both orders (no hard dependency), but the owner
   should fix an order before implementation so the second PR budgets the
   rebase of the shared `DOCUMENTATION.md` live-reindex section, the
   `default_config.yaml` comment, and the doc-pin tests — and so D10's line
   edit is neither implemented twice nor dropped by both.
