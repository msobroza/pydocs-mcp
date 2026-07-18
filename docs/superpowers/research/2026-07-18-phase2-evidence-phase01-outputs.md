# Phase 2 evidence — what Phases 0–1 actually shipped that Phase 2 must consume

- **Date:** 2026-07-18 · **Researcher scope:** Phase 0/1 outputs (ADRs 0005–0008, tool-contracts, implementing modules)
- **Worktree:** `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/phase-2-instrumentation-spec-498def`, branch `claude/phase-2-instrumentation`, HEAD `cebf08c` (= origin/main; includes Phase 0 merge `f4a8f2e` #198 and Phase 1 merge `a129136`, verified via `git log --oneline -3 a129136`).
- **Method:** static reads of the files cited below plus one runtime probe in the worktree venv (`.venv/bin/python`, editable install). Every claim carries a file:line cite or a command output. Anything not directly observed is labeled UNVERIFIED.
- All file paths repo-relative unless absolute.

---

## 1. ADR 0007's finding: where the agent loop lives (conditions Phase 2 D1 capture architecture)

Decisive sentences, quoted from `docs/adr/0007-deterministic-routing-suggestions.md`:

> **Where the agent loop actually lives.** The repo contains exactly two tool-driving loops. The ask-your-docs agent is a LangGraph ReAct loop (`python/pydocs_mcp/ask_your_docs/architectures/text_react.py:32-34`) over all nine tools via a stdio `MultiServerMCPClient` that spawns `pydocs_mcp serve` (`ask_your_docs/agent.py:205-213`) — it is a chat product and a prompt-optimization subject (`benchmarks/src/pydocs_eval/optimize/ask_binding.py:1-13`), not an evaluation harness. The in-repo rollout harness — the benchmarks agent track — spawns **headless Claude Code** (`claude -p`) per arm, with the indexed arm allowed `mcp__pydocs-mcp__*` through a strict one-server `.mcp.json` that boots `pydocs_mcp serve` (`benchmarks/src/pydocs_eval/agent_track/_runner.py:1-24`, `agent_track/_command.py:39-49,53-135`). The loop is an external client's; the repo only builds commands, corpora, parsers, and judges around it (the retrieval track bypasses the tool surface entirely, `systems/pydocs.py:48-51`). So option (b) has no host: there is no in-repo eval loop to put orchestration into.

(`docs/adr/0007-deterministic-routing-suggestions.md:29-43`)

And in the options analysis:

> **(b) Agent-loop-side orchestration.** Buried: the rollout loop is headless Claude Code — an external client the repo cannot orchestrate — and the only in-repo loop is the ask-your-docs chat product (`agent_track/_command.py:53-135`), so there is nowhere to put the code.

(`docs/adr/0007-deterministic-routing-suggestions.md:96-100`)

**Confirmed against the current source** (not just the ADR): `benchmarks/src/pydocs_eval/agent_track/_command.py:53-105` builds `["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", arm.model, "--max-turns", str(arm.max_turns), "--allowedTools", allowed, ...]`; the indexed arm adds `--mcp-config <path> --strict-mcp-config` (lines 97-104); `render_mcp_config` (117-135) emits a one-server `.mcp.json` launching `<python> -m pydocs_mcp serve <corpus_dir>`. The bare arm's tool grant is `"Read Grep Glob Bash"`, indexed adds `mcp__pydocs-mcp__*`, judge arm gets `""` (`_command.py:39-41,108-114`). Output format is `stream-json` "required for per-event tool_use / usage folding (see _parse.py)" (`_command.py:43-45`).

**Phase 2 D1 implication (stated in ADR terms, verified here):** any trace capture must sit either (a) server-side inside `pydocs_mcp serve` (the process the harness controls), (b) in the harness's parse of Claude Code's `stream-json` event stream (`agent_track/_parse.py` already folds per-event tool_use/usage into `AgentRunResult`, `agent_track/_types.py:48-64`), or (c) in the ask-your-docs LangGraph loop for the ask track. There is no in-repo agent loop for the eval rollouts.

---

## 2. Exact marker formats Phase 1 shipped

### 2a. `[suggestion: ...]` body prefix — `python/pydocs_mcp/application/suggestions.py`

```python
SUGGESTION_PREFIX = "[suggestion:"                                      # suggestions.py:23
GREP_ZERO_HIT_SUGGESTION = (
    '[suggestion: no exact matches — for conceptual queries, try search_codebase(query="...")]'
)                                                                        # :25-27
GREP_TRUNCATED_SUGGESTION = (
    "[suggestion: output cut by head_limit — narrow with path= or glob=, or raise head_limit=]"
)                                                                        # :28-30
SEARCH_ZERO_HIT_SUGGESTION = "[suggestion: zero hits — orient with get_overview()]"  # :31
```

The module docstring pins the R7 semantics: "a transcript line starting with the fixed ``[suggestion:`` prefix is always a harness-initiated nudge, never model-earned routing" (`suggestions.py:7-9`).

Body attachment differs per rule:

- **grep** (both rules): suffix appended to the text body — `return f"{body}\n{suffix}", items, {**extras, "suggestion": suffix}` (`application/file_tools.py:391-408`, `_with_grep_suggestion`). Zero-hit and truncation are mutually exclusive; at most one rule fires per response (`file_tools.py:394-396`). With the flag off the response is byte-identical to pre-suggestion output.
- **search_codebase** zero-hit: the body gets the pre-existing `[[next:overview:]]` pointer token (rendered by the envelope as `→ get_overview()`), while `meta.suggestion` mirrors the fixed `SEARCH_ZERO_HIT_SUGGESTION` string — the `[suggestion: ...]` text itself is NOT in the body for search (`application/tool_router.py:110-125`).
- **get_why** zero-hit: same shape — body is `"No decisions found."` + pointer token (flag-gated, `application/decision_service.py:243-245`), extras carry `{"suggestion": SEARCH_ZERO_HIT_SUGGESTION}` via `_zero_hit_extras` (`decision_service.py:215-227`). The `tool` arg names the calling surface (`get_why` vs `search_codebase(kind="decision")`) for the fired-rule log (`decision_service.py:220-222`).

### 2b. `meta.suggestion` envelope field

- Declared as `SuggestionMetaModel(MetaModel)` with `suggestion: str | None = None` (`application/tool_response.py:57-67`). The subclass is mandatory because `MetaModel` has no `model_config` — pydantic default `extra="ignore"` silently drops undeclared extras at server-side envelope validation (`tool_response.py:61-64` docstring; ADR 0007 pitfall).
- Wired to exactly three envelopes: `SearchEnvelope` (search_codebase), `WhyEnvelope` (get_why), `GrepEnvelope` (grep) at `tool_response.py:165-198`; all other six tools keep plain `MetaModel` (`ReferencesEnvelope` keeps `ReferencesMetaModel.resolution`, `:183-186`). `ENVELOPE_MODELS` registry maps the nine frozen names (`:215-225`).
- Contract: `docs/tool-contracts.md` §2.3 (lines 116-135): "`meta.suggestion: str | null` — the deterministic routing suggestion that fired for this response, or `null` when none did. The value is fixed server rendering under the deterministic `[suggestion:` prefix ... Each rule is individually flaggable ... (`output.suggestions.{grep_zero_hit,grep_truncated,search_zero_hit}`, all default on); with every flag off the field is always `null` and bodies carry no suggestion line." Migration row 7 at `tool-contracts.md:451`. Owner ratified the amendment 2026-07-18 (`docs/adr/0007:137-139`).

### 2c. `INJECTED_CONTEXT_MARKER` — `python/pydocs_mcp/application/session_start_context.py:36-38`

Exact string (runtime-verified byte-for-byte, see §7 probe):

```
[pydocs-mcp session-start-context: harness-injected at session start; not model-retrieved]
```

- Wire-format constant: "the Phase 2 attribution matcher excludes injected content by EXACT match on this first line — rewording it is a cross-phase breaking change" (`session_start_context.py:32-35`).
- It is ALWAYS the first line of the session-start pack (`build_session_start_context`, `session_start_context.py:80-81`: `head = f"{INJECTED_CONTEXT_MARKER}\n{tool_docs.SESSION_START_PREAMBLE}"`); the head is never trimmed under budget pressure (`_fit_to_budget` docstring, `:92-99`).
- Two sibling truncation-note constants (also fixed machinery): `CARD_TRUNCATED_NOTE = "[session-start-context: overview card truncated to fit the token budget]"` and `INVENTORY_TRUNCATED_NOTE = "[session-start-context: version inventory truncated to fit the token budget]"` (`:42-45`).
- Pinned by tests: `tests/application/test_session_start_context.py:107-127,194,208` (byte-equality + first-line assertions) and `tests/test_cli.py:359-361` (CLI stdout first line).

### 2d. Fired-rule structured log

- One emitter: `log_suggestion_fired(tool, rule)` → `log.info(json.dumps({"event": "suggestion_fired", "tool": tool, "rule": rule}))` (`suggestions.py:34-36`). Docstring: "One structured line per fired rule — the Phase 2 attribution input."
- **Logger name:** `pydocs_mcp.application.suggestions` (module `__name__`; runtime-verified, §7). Level INFO. The JSON is the *message string* of a stdlib logging record — not a structured-logging handler; a Phase 2 consumer either attaches a handler to that logger or greps stderr for `{"event": "suggestion_fired"`.
- JSON fields: exactly `event` (always `"suggestion_fired"`), `tool` (`"grep" | "search_codebase" | "get_why"`), `rule` (`"grep_zero_hit" | "grep_truncated" | "search_zero_hit"`). Verified emission sites: `file_tools.py:401,404`; `tool_router.py:120`; `decision_service.py:226`. Test pins: `tests/application/test_file_tools.py:432-433`, `test_decision_service.py:458`, `test_error_empty_contract.py:182`.

### 2e. Descriptions-artifact startup log line

- Emitted by `server._apply_descriptions_source` on logger **`"pydocs-mcp"`** (`server.py:54` `log = logging.getLogger("pydocs-mcp")`; emission at `server.py:546`): `log.info("descriptions artifact %s source=%s", artifact_hash[:12], source)`.
- `source` values: `"packaged"` | the override path string | `"pre-applied"` (constants `PACKAGED_SOURCE = "packaged"`, `PRE_APPLIED_SOURCE = "pre-applied"` in `application/description_override.py:43-44`). The `pre-applied` branch fires when no override won but the live surface differs from the packaged hash — i.e. the benchmarks overlay wrapper rebound attributes before `server.run` (`server.py:539-545`, comment: "Claiming ``packaged`` next to the overlay's hash would mislead Phase 2 attribution").
- Format pinned by tests: `tests/test_serve_descriptions_override.py:233` (`r"descriptions artifact [0-9a-f]{12} source=packaged"`), `:247` (override path), `:282` (`source=pre-applied`). `server.py:516-527` docstring: "The log format is pinned by test: Phase 2 attribution parses it from the startup log, not the wire."

---

## 3. `current_artifact_hash()` — exact contract

`python/pydocs_mcp/application/description_source.py`:

- **Signature:** `def current_artifact_hash() -> str` (`:450`). Zero args; reads the LIVE `tool_docs` module attributes on demand (function-local import, `:463`).
- **What it hashes** (`_artifact_hash`, `:490-498`): rebuilds the 11-section mapping `{SERVER_INSTRUCTIONS, "TOOL: <name>"×9 (contract order), SESSION_START_PREAMBLE}` from the live attributes, computes `surface = normalize(render_sections(sections))` (normalize = one parse→render pass, the load-bearing one-normalization-pass rule, `:297-305` and module docstring `:13-20`), then `sha256(f"renderer:v{RENDERER_VERSION}\n{surface}".encode("utf-8")).hexdigest()`.
- **`RENDERER_VERSION = 1`** (`:46`), bump-on-renderer-change semantics documented at `:43-45`.
- Truthful under BOTH writers — `apply_source` and the legacy benchmarks wrapper rebinding attributes directly (`:453-457` docstring). Caveat frozen by ADR 0006: hash == wire only because all rebinding happens BEFORE MCP registration (`server.py:522-527`; registration captures `TOOL_DOCS[name]` at `server.py` `_register_tools`).
- **Sibling:** `packaged_artifact_hash()` (`:472-487`) fingerprints what the packaged doc *would* serve; `server.py:539` compares the two to detect pre-applied overlays.
- **Where a Phase 2 trace capturer gets it** (three sanctioned routes):
  1. **In-process import** — `from pydocs_mcp.application.description_source import current_artifact_hash` any time after rebinding (used by the CLI paths and testable directly).
  2. **Return values** — `apply_source(path) -> str` returns the new hash (`:414-447`); `apply_descriptions_override(...) -> tuple[str, str]` returns `(artifact_hash, source)` (`description_override.py:97-122`).
  3. **The startup log line** (§2e) from the serve subprocess — ADR 0006 §6: "Phase 2 attribution reads it from the startup log / run config instead" (`docs/adr/0006:158-159`); explicitly NOT in the response envelope (meta field names frozen).
- Runtime probe (this worktree): `current_artifact_hash() == packaged_artifact_hash()` → `True`; first 12 hex chars `eeb66ef59a4b` (value is a function of the packaged `descriptions.md` bytes at HEAD cebf08c; do not treat as stable across doc edits).

---

## 4. Run-config identity surfaces: what uniquely identifies a candidate/run today

### 4a. Product side (what exists)

- **Descriptions override precedence** (ADR 0006 §3, implemented): `--descriptions` CLI flag > env `PYDOCS_SERVE__DESCRIPTIONS_PATH` > user-YAML `serve.descriptions_path` > packaged. Env var name hardcoded as `DESCRIPTIONS_PATH_ENV_VAR = "PYDOCS_SERVE__DESCRIPTIONS_PATH"` (`description_override.py:39`); resolution in `resolve_descriptions_override` (`:66-94`; CLI flag wins before env is inspected; SET-but-EMPTY env raises `EmptyDescriptionsEnvError` `:49-63`). `server.run(..., descriptions_path=...)` carries the flag (`server.py:549-561`); `__main__.main()` pre-applies the env override before parser build unless a `--descriptions` flag is in argv (`__main__.py:1519-1560`). Universal strictness: explicit-but-missing/invalid source hard-fails; only no-override-at-all serves packaged.
- **AppConfig fields for the Phase 1 flags** (`python/pydocs_mcp/retrieval/config/models.py`):
  - `SuggestionsConfig` (`:420-434`): `grep_zero_hit: bool = True`, `grep_truncated: bool = True`, `search_zero_hit: bool = True`; mounted at `OutputConfig.suggestions` (`:444`) → YAML `output.suggestions.*` (`defaults/default_config.yaml:131-143`, hypotheses as YAML comments).
  - `SessionStartContextConfig` (`:674-690`): `enabled: bool = False`, `budget_tokens: int = Field(default=2000, ge=1)`; mounted at `ServeConfig.session_start_context` (`:710-712`) → YAML `serve.session_start_context.*` (`default_config.yaml:206-208`).
  - `ServeConfig.descriptions_path: str | None = None` (`:716`; YAML `serve.descriptions_path`, `default_config.yaml:199` with the precedence comment at 195-198).
  - AppConfig layering (verified in ADR 0006 evidence): `[init_settings, env_settings, user-YAML, shipped defaults]` — env outranks user YAML.
- **Index identity:** `index_metadata` table holds project + embedder identity incl. `git_head` (`db.py:145-151` per ADR 0008 evidence); envelope `meta.indexed_git_head`/`live_git_head`/`index_stale` per response (`tool-contracts.md:91-99`).
- **No run-config lockfile exists product-side.** Nothing dumps the effective AppConfig, the artifact hash, and the flag states into a single per-run record. The closest artifacts are (a) the startup log line (hash + source only) and (b) `SearchBackendConfig.compute_identity()`-style identity strings folded into the *pipeline hash* for caching (`models.py:731-733`) — an index-cache concern, not a rollout-identity record.

### 4b. Benchmarks side (what exists)

- **`OptimizeRunConfig`** (`benchmarks/src/pydocs_eval/optimize/run_config.py:216-241`): `artifact`, `optimizer`, `ladder`, `fitness` (weights + judge_parity_floor), `accept_margin`, `budget` (`OptimizationBudget`: max_trials 20 / max_usd 40.0 / wall_timeout 14400 / max_judge_calls 200, `optimize/_types.py:17-48`), `llm: CritiqueLlmConfig | None` (`provider`, `model_name`, `temperature: float = 0.7`, `run_config.py:95-108`), `dataset`, `ask_rubric` (incl. `AskRunnerSettings`: `model`, `architecture`, `base_url`, `workspace`, `task_timeout_seconds`, `:111-126`), `config_search` (incl. per-section `seed: int | None` falling back to top-level, `:195-199`), and **`rng_seed: int = DEFAULT_RNG_SEED`** with the comment "recorded in provenance so two runs with identical config + ledger are identical modulo LLM nondeterminism (spec §3.6)" (`:238-241`). Registry-key validation at load (`load_run_config`, `:270-287`).
- **`Provenance`** (`optimize/_types.py:65-79`): `seed_fingerprint`, `dataset_revision`, `model_ids: tuple[str, ...]`, `optimizer`, `rubric_hash: str | None` — "Recorded so a landed proposal is reproducible months later."
- **`TrialsLedger`**: append-only JSONL, resume key `(fingerprint, split, objective_hash)` (`optimize/trials_ledger.py:1-48`); candidate `fingerprint` hashes the normalized artifact surface (ADR 0005 evidence; `orchestrator.py:120-137`).
- **Agent track**: `ArmConfig` (`agent_track/_types.py:83-106`): `name`, `model = DEFAULT_MODEL` ("claude-sonnet-5", `:21`), `max_turns = 40`, `mcp`, `no_tools`. `AgentTrackConfig` (`:117-131`): `judge_model`, `max_tasks 48`, `max_usd 25.0`, `task_timeout_seconds 900.0`, `rng_seed 0` (fixes judge-label randomization + report bootstrap), `output_dir ~/.cache/pydocs-mcp/agent-track`.

### 4c. What Phase 2 R2 must ADD (gap analysis, all directly observed absences)

- **No sampling parameters for the rollout arms.** `build_claude_command` passes only `--model` and `--max-turns` (`_command.py:83-105`); temperature/top_p/seed are not settable and not recorded for headless Claude Code arms. LLM nondeterminism is explicitly unpinned ("identical modulo LLM nondeterminism", `run_config.py:239-241`).
- **No provider/base_url stamping for the agent track** (ask track has `base_url` in `AskRunnerSettings`; the claude CLI arms have nothing).
- **No unified run-record/lockfile** joining {descriptions artifact hash, AppConfig flag states (`output.suggestions.*`, `serve.session_start_context.*`), arm model/max_turns, judge model, rng_seed, dataset revision, corpus/index identity (`indexed_git_head`)}. The pieces exist in four places (startup log, AppConfig YAML, `OptimizeRunConfig`/`AgentTrackConfig`, envelope meta) but nothing stamps them together per run.
- **Artifact-hash consumption is log-parse only** — ADR 0006 action item 6's second half ("consume `current_artifact_hash()` from the startup log / run config for per-run attribution") is Phase 2's; the wrapper-migration half is DONE (`_overlay_server.py:13-19` — the wrapper now routes overlays through the product's `apply_source`, staging a bridged document via tempfile, `:113-141`).
- **Caps that exist but aren't stamped into results:** `max_usd`/`max_turns`/`task_timeout_seconds` are enforced but `RunMetrics` (`agent_track/_types.py:31-46`) records outcomes (cost_usd, wall_seconds, turns, tool_calls, distinct_files_read, cache tokens, answer), not the caps in force.

---

## 5. "Harness-initiated tool call" marking (Phase 2 R7)

**What exists:** only *output-side* markers —

1. the `[suggestion:` body prefix + `meta.suggestion` field marking server-initiated routing *nudges* (§2a-2b), and
2. `INJECTED_CONTEXT_MARKER` marking harness-*injected context* at session start (§2c), and
3. the fired-rule log line attributing each nudge (§2d).

**What does NOT exist:** no mechanism anywhere marks a *tool call* as harness-initiated vs model-initiated. Repo-wide grep for `harness-initiated|harness_initiated` in `python/`, `benchmarks/`, and `docs/tool-contracts.md` hits exactly one line — the `suggestions.py:8` docstring describing the suggestion prefix. ADR 0007's R7 constraint ("its actions marked as harness-initiated", `0007:23-24`) was satisfied in Phase 1 purely by the suggestion markers because Phase 1's rules never *issue* tool calls — they only append text. In the agent track, every tool call in a transcript is issued by headless Claude Code (the external client); the harness issues zero tool calls of its own. **Phase 2 must build the harness-initiated-call distinction from scratch** — e.g., transcript-level provenance when a Phase 2 capturer replays/issues calls, since there is no existing envelope field, log event, or stream-json annotation for it. (The stream-json `tool_use` events the parser folds, `agent_track/_parse.py` per `_command.py:43-45`, are all model-initiated by construction today.)

---

## 6. The session-start-context pack

**Exact content structure** (`application/session_start_context.py:54-122`), in order, sections joined by `"\n\n"` (`_join_sections`, `:88-89`):

1. **Head** (never trimmed): line 1 = `INJECTED_CONTEXT_MARKER` (§2c), then `tool_docs.SESSION_START_PREAMBLE` — read as a module attribute at build time (never a from-import snapshot) so an `apply_source` override reaches later packs (`:77-80`).
2. **Overview card**: `format_overview_card(await overview.build(package))` — the SAME renderer/snapshot `get_overview` serves (`application/formatting.py` `format_overview_card`; `OverviewService.build`), `.rstrip("\n")` (`:71`).
3. **Version inventory**: `"## Installed packages"` heading + one `f"{pkg.name} {pkg.version}"` row per package from `uow.packages.list()`, sorted by name (`:74-76,100`).

**Budget enforcement** (`_fit_to_budget`, `:92-122`): REAL tokens via `retrieval/llm_clients/model_budget.count_tokens` with model name `""` → o200k_base fallback, matching ADR 0008's measurement encoding (`:48-51`). Trim order: overview-card lines dropped from the end first (binary search for largest fitting prefix, `_largest_fitting` `:125-141`), with `CARD_TRUNCATED_NOTE` appended; then inventory rows with `INVENTORY_TRUNCATED_NOTE`; the head is the floor — a budget below it returns head + notes rather than an unmarked fragment (`:96-99,122`).

**Builder signature:** `async def build_session_start_context(*, uow_factory, overview, budget_tokens, package: str = "") -> str` (`:54-60`).

**CLI subcommand:** `pydocs-mcp session-start-context [package]` — parser at `__main__.py:490-506` (help text: "Print the session-start context pack (marker + preamble + overview card + version inventory)"; corpus selectors only, budget/flag are YAML-only by design, `:485-489`); runner `_run_session_start_context` (`:991-1015`) applies the descriptions override first (`apply_descriptions_override(cli_path=None, configured_path=config.serve.descriptions_path)`, `:1010`) then prints the pack; dispatch entry `"session-start-context": _cmd_session_start_context` (`:1514`). Invoking the subcommand does NOT require `serve.session_start_context.enabled` — "invoking the subcommand IS the consent" (`:997-998`).

**How an external client receives it (turn-0 injection mechanics):** two channels, one builder (ADR 0008 §Decision 5):

- **External harnesses (the Phase 2-relevant channel):** run the CLI subcommand (or call the builder as product API) and compose the printed pack into their own prompts — the MCP surface is untouched (no tenth tool, no MCP resource; nothing registered, per ADR 0008 evidence `0008:101-107`). Injection is the *harness's* job; the product only guarantees deterministic content + the marker.
- **ask-your-docs (in-repo channel):** `build_session_start_context_for_agent_prompt(workspace, config_path)` (`ask_your_docs/session_start_injection.py:14-60`) returns `None` when `serve.session_start_context.enabled` is false (byte-identical prompt assembly), else applies the descriptions override for channel parity (`:36-42` — "the two channels must describe the same surface"), builds the pack for the FIRST workspace bundle (same `services[0]` rule as the MCP server, `:20-23`), and `agent.py` appends it to the assembled system prompt: `return f"{assembled}\n{session_start_context}"` (`agent.py:187-189`; call site gated at `:248-251`).

**Flag/budget defaults:** `serve.session_start_context.enabled: false`, `budget_tokens: 2000` (`models.py:689-690`; `default_config.yaml:206-208`). Default-off keeps Phase 1 behavior-neutral on this feature; ADR 0007's two grep rules are the phase's deliberate default-on exceptions (`0008:190-194`).

---

## 7. Runtime verification probe (executed this session)

Command (in the worktree venv):

```
.venv/bin/python -c "from pydocs_mcp.application.description_source import current_artifact_hash, packaged_artifact_hash, RENDERER_VERSION, CANONICAL_HEADERS; ..."
```

Output (verbatim):

```
renderer_version: 1
current==packaged: True
current12: eeb66ef59a4b
headers: 11
marker: [pydocs-mcp session-start-context: harness-injected at session start; not model-retrieved]
prefix: [suggestion:
logged: {"event": "suggestion_fired", "tool": "grep", "rule": "grep_zero_hit"} | logger name: pydocs_mcp.application.suggestions
```

Also verified on disk: `python/pydocs_mcp/defaults/descriptions.md` exists as packaged data (10.9K, alongside `default_config.yaml`; `ls` output) and `tool_docs.py` populates `SERVER_INSTRUCTIONS, TOOL_DOCS, SESSION_START_PREAMBLE = attribute_views(load_packaged())` at import (`application/tool_docs.py:41`).

---

## 8. Miscellany Phase 2 should know (verified)

- **The 11 canonical sections** of the description document: `SERVER_INSTRUCTIONS`, `TOOL: <name>` ×9 in contract order, `SESSION_START_PREAMBLE` (`description_source.py:95-99`); closed header regex also admits benchmarks-only `SYSTEM_PROMPT`/`REWRITE_PROMPT` keys which the product's allowed-set firewalls out (`:108-110`, module docstring `:22-30`).
- **Error taxonomy** for bad candidate documents (all subclass `DescriptionSourceError(PydocsMCPError, ValueError)`): `HeaderCollisionError`, `MissingSectionError`, `MissingMarkerError`, `TokenBudgetExceededError`, `StrayContentError`, `DuplicateSectionError` (`description_source.py:113-200`). Strict parse (`allowed=` given) is the product loader path; lenient parse is the optimizer-firewall path (`:219-244`).
- **Lint constants** (canonical home `description_source.py:54-63`, re-exported by `tool_docs.py:27-38`): `REQUIRED_MARKERS` five colon-free substrings, `CHARS_PER_TOKEN=4`, `PER_TOOL_TOKEN_BUDGET=500`, `TOTAL_TOKEN_BUDGET=3600`; budgets cover TOOL sections only (`:339-354`).
- **Overlay wrapper migration status:** DONE for binding parity — `benchmarks/src/pydocs_eval/optimize/_overlay_server.py` routes overlays through `apply_source` (`:13-19,113-141`), so `current_artifact_hash` and the `source=pre-applied` startup label stay truthful for overlay runs. Phase 2 still owes the attribution-consumption half of ADR 0006 action item 6.
- **`meta.resolution` precedent** (`tool-contracts.md:102-114`, `tool_response.py:51-54`) is the sanctioned additive-meta recipe Phase 2 must follow if it ever needs another meta field — but note ADR 0006 already REJECTED putting the artifact hash in the envelope (frozen meta names; startup log instead).
- **UNVERIFIED items inherited from the ADRs** (labeled there, restated here): GEPA `Candidate = dict[str, str]` mapping is web-derived, not release-pinned (`0005:72-75`); MCP-client honoring of `tools/list_changed` and resource auto-load are unverified (`0006:71-75`, `0008:105-107`); the 0-hit wire bytes in ADR 0007 were asserted from code + golden tests, not observed live (`0007:82-86`). This session did not re-verify any of them.
