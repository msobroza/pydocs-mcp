# Phase 2 evidence — what an external Claude Code headless client emits (loop-side parse targets)

Researcher scope: session-transcript JSONL, headless stdout shapes (`--output-format json` / `stream-json`),
trajectory-id correlation into the MCP server process, format stability across versions, and final-patch
visibility. All observations made 2026-07-18 on this machine (macOS, Darwin 23.6.0) unless labeled otherwise.
Anything not directly observed is labeled **UNVERIFIED** or **DOCS-ONLY**.

Machine context that matters for reading this file:

- Local `claude` on PATH: `/opt/homebrew/bin/claude`, `claude --version` → `2.1.76 (Claude Code)` (command run this session). The on-disk transcripts were written by *newer* embedded CLIs (2.1.111–2.1.205, see §4) — multiple Claude Code builds coexist on one machine, so a capture layer must not assume one version.
- A `grep`/`ls` token-optimizing proxy (RTK, per user config) rewrites some shell commands on this machine; all decisive extraction below was done with `python3 -c json` directly on the files to avoid proxy-mangled output.

---

## D1 premise check — the eval loop IS headless Claude Code

`docs/adr/0007-deterministic-routing-suggestions.md:36-43` (worktree copy, read this session):

> The in-repo rollout harness — the benchmarks agent track — spawns **headless Claude Code** (`claude -p`) per arm, with the indexed arm allowed `mcp__pydocs-mcp__*` through a strict one-server `.mcp.json` that boots `pydocs_mcp serve` (`benchmarks/src/pydocs_eval/agent_track/_runner.py:1-24`, `agent_track/_command.py:39-49,53-135`). The loop is an external client's; the repo only builds commands, corpora, parsers, and judges around it.

Confirmed in code: `benchmarks/src/pydocs_eval/agent_track/_command.py:45` pins `_OUTPUT_FORMAT = "stream-json"` with the comment "stream-json is required for per-event tool_use / usage folding (see _parse.py); --verbose is required by the CLI when stream-json is combined with -p", and `_command.py:83-105` assembles `claude -p <prompt> --output-format stream-json --verbose --model … --max-turns … --allowedTools … [--mcp-config <path> --strict-mcp-config]`. Premise holds.

---

## 1. Session transcript JSONL (on-disk, `~/.claude/projects/<flattened-cwd>/<sessionId>.jsonl`)

### 1.1 Layout

- One directory per working directory, name = cwd with `/` → `-` (e.g. `-Users-msobroza-Projects-pyctx7-mcp`). 63 project dirs exist locally (`ls ~/.claude/projects | wc -l` → 63).
- Main transcript file name **is** the session id: file `7b071a5f-cd0a-4799-8c4d-7f8cf50c6e7a.jsonl` has `sessionId: "7b071a5f-cd0a-4799-8c4d-7f8cf50c6e7a"` in every record (verified by parse).
- Sibling per-session **directory** `<sessionId>/` holds:
  - `subagents/agent-<agentId>.jsonl` — one transcript per subagent (12 such files in `…-dazzling-cerf-8c3cea/85324cdf…/subagents/`, verified). Subagent records carry `agentId` (e.g. `a9ee0196990f05d94`), `isSidechain: true`, and the **parent** `sessionId`.
  - `subagents/workflows/wf_*/…` — workflow journals (`journal.jsonl`) and workflow-subagent transcripts.
  - `tool-results/toolu_*.txt` — oversized tool outputs persisted out-of-line (observed created live this session when a WebFetch result exceeded ~71KB: `…/814c8a47-…/tool-results/toolu_01ASJPG4SzEK6iBEf75RTedg.txt`).

### 1.2 Record types (measured)

Across all 12 top-level transcripts in `-Users-msobroza-Projects-pyctx7-mcp/` (jq type histogram, run this session):

```
3580 assistant   2868 attachment   2101 user   532 last-prompt   451 pr-link
 328 queue-operation   123 system   80 mode   49 file-history-snapshot   1 custom-title
```

Notes:
- **No `summary`-type records exist anywhere** under `~/.claude/projects/` on this machine (recursive grep for `"type":"summary"` → zero files). The compact-continuation role is played by `last-prompt` records (`{type, lastPrompt, leafUuid, sessionId}`). Community write-ups describing a `summary` type describe older CLIs; do not build a parser that requires it.
- An old observer-session transcript additionally showed a `progress` type; `mode`/`pr-link`/`custom-title`/`file-history-snapshot` appear only in some versions (§4).

### 1.3 Fields per record (measured, v2.1.205 file `e40dbc30-…jsonl`, 141 records)

Top-level key sets by record shape (jq `keys` histogram):

- **assistant**: `attributionPlugin, attributionSkill, cwd, entrypoint, gitBranch, isSidechain, message, parentUuid, requestId, sessionId, timestamp, type, userType, uuid, version` (+ `apiErrorStatus, error, errorDetails, isApiErrorMessage` on API-error records).
- **user (tool_result)**: `cwd, entrypoint, gitBranch, isSidechain, message, parentUuid, promptId, sessionId, sourceToolAssistantUUID, timestamp, toolUseResult, type, userType, uuid, version`.
- **user (human prompt)**: adds `origin, permissionMode, promptSource`, no `toolUseResult`.
- **attachment**: `attachment, cwd, entrypoint, gitBranch, isSidechain, parentUuid, sessionId, timestamp, type, userType, uuid, version` — hook outputs (`hook_success` with `hookName`, `stdout`), system-reminder payloads, etc.
- **system**: `subtype` (e.g. `stop_hook_summary`), `hookCount/hookInfos/hookErrors`, `level`, `toolUseID`, plus the common envelope.
- **last-prompt**: `{lastPrompt, leafUuid, sessionId, type}` — no uuid/timestamp envelope.
- **queue-operation**: `{content, operation ("enqueue"), sessionId, timestamp, type}`.
- **file-history-snapshot**: `{messageId, snapshot: {messageId, trackedFileBackups, timestamp}, isSnapshotUpdate}` — checkpoint bookkeeping, NOT a diff (sample measured 247 bytes with empty `trackedFileBackups`).

`entrypoint` values across all local pyctx7 transcripts: `claude-desktop` (41,277 records), `claude-vscode` (1,007). A headless run's value was not observed (no local headless transcript) — **UNVERIFIED** what `-p` stamps there, though the SDK-spawned process env showed `CLAUDE_CODE_ENTRYPOINT=sdk-cli` (§3.3).

### 1.4 Trimmed REAL records (redacted; from `…-hardcore-banach-7dae8e/7b071a5f….jsonl`, v2.1.197, 262 lines, 0 malformed)

**(a) assistant record carrying an MCP tool_use** (line 43; content trimmed):

```json
{"type":"assistant","uuid":"18d8559c-309b-47ef-a33b-e06e6ed27062",
 "parentUuid":"040ae0a9-e59e-4b78-be3e-a8bb3b11eaa8",
 "sessionId":"7b071a5f-cd0a-4799-8c4d-7f8cf50c6e7a",
 "timestamp":"2026-07-07T08:16:56.759Z","version":"2.1.197",
 "cwd":"/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/hardcore-banach-7dae8e",
 "gitBranch":"claude/laughing-bassi-a81b6f","requestId":"req_011CcnM9HpiLgc2tn44AsVi8",
 "isSidechain":false,"userType":"external","entrypoint":"claude-desktop",
 "message":{"role":"assistant","content":[
   {"type":"tool_use","id":"toolu_019Z5vTuamj53ZttVEk7k4jc",
    "name":"mcp__plugin_claude-mem_mcp-search__search",
    "input":{"query":"DecisionPipeline","limit":20}}],
  "usage":{"input_tokens":"…","cache_creation_input_tokens":"…","cache_read_input_tokens":"…",
           "output_tokens":"…","server_tool_use":{},"service_tier":"standard",
           "cache_creation":{},"inference_geo":"…","iterations":[],"speed":"standard"}}}
```

**(b) paired user record carrying the MCP tool_result** (line 44; 4,403-char text trimmed):

```json
{"type":"user","sessionId":"7b071a5f-…","parentUuid":"18d8559c-…",
 "sourceToolAssistantUUID":"18d8559c-…","promptId":"…","version":"2.1.197",
 "message":{"role":"user","content":[
   {"type":"tool_result","tool_use_id":"toolu_019Z5vTuamj53ZttVEk7k4jc",
    "content":[{"type":"text","text":"Found 45 result(s) matching \"DecisionPipeline\" (20 obs, 5 sessions, 20 prompts)\n\n### May 25, 2026\n…"}]}],
 "toolUseResult":[{"type":"text","text":"Found 45 result(s) matching \"DecisionPipeline\" …"}]}
```

**(c) human-prompt user record** (line 10, trimmed): keys include `origin, permissionMode, promptId, promptSource`; `message: {"role":"user","content":"I would like that the refactoring of DecisionPipeline was done"}` (content may be a plain string, not a block list).

### 1.5 MCP calls in the transcript — the answers Phase 2 needs

- MCP tools appear as `tool_use` blocks with `name = "mcp__<server>__<tool>"` and the **raw input args object** (measured names on this machine include `mcp__plugin_claude-mem_mcp-search__search`, `mcp__ccd_session__spawn_task`, 25 distinct `mcp__…` names counted machine-wide). This matches the harness's own `_MCP_TOOL_PREFIX = "mcp__"` assumption (`benchmarks/src/pydocs_eval/agent_track/_parse.py:32`).
- **The tool result carries the full MCP envelope text, twice**: once inside `message.content[].content` (list of `{"type":"text","text":…}` blocks — the MCP content blocks verbatim, 4,403 chars in the sample) and once duplicated in the top-level `toolUseResult` field. For built-in tools `toolUseResult` is instead a structured object (Bash sample: `{"stdout":"","stderr":"","interrupted":false,"isImage":false,"noOutputExpected":false}`). So loop-side capture CAN recover the exact rendered envelope the model saw — including pydocs-mcp's response envelope — from the transcript alone.
- **Oversized results caveat**: outputs beyond a size threshold are spilled to `<sessionId>/tool-results/toolu_*.txt` (observed live this session); a parser must treat the inline text as possibly elided and check the sidecar dir. Threshold **UNVERIFIED**.

### 1.6 Streaming duplication — dedupe by `message.id` (measured)

In `e40dbc30….jsonl` (v2.1.205): **51 assistant records but only 20 distinct `message.id` values** (max 5 records for one API message — one record per content block, e.g. thinking + text + 3 tool_use). The `message.usage` object is **byte-identical across all records of the same message id** (verified for a 3-record message). Consequences:

- Per-turn token accounting MUST dedupe usage by `message.id`, else input/cache tokens are over-counted several-fold.
- Caveat for the existing harness: `_parse.py:100-135` sums `cache_read/creation` across **every** usage block without message-id dedupe. Whether the *stdout* stream-json duplicates usage the same way the on-disk transcript does is **UNVERIFIED** (no paid run made), but the risk is real and cheap to guard against.
- Thinking blocks appear as `{"type":"thinking", …, "signature":…}` content blocks (15/15 signed in the sample file).
- `usage` fields observed (v2.1.205): `input_tokens, output_tokens, cache_creation_input_tokens, cache_read_input_tokens, cache_creation{ephemeral_1h_input_tokens, ephemeral_5m_input_tokens}, server_tool_use{web_search_requests, web_fetch_requests}, service_tier, inference_geo, iterations[], speed`. The last four are new vs 2.1.156-era records — treat unknown usage keys as open-world.

---

## 2. Headless stdout shapes (`claude -p`)

No paid headless run was made this session (standing rule: paid runs need explicit user go; the task directed help/docs instead). Evidence = `claude --help` output (run), official docs (fetched), and the repo's own contract fixtures (hand-written, labeled as such).

### 2.1 `--output-format json` (single result envelope)

- Docs (fetched this session, https://code.claude.com/docs/en/headless): "`json`: structured JSON with result, session ID, and metadata"; cost example `claude -p … --output-format json | jq -r '.result'`; session capture example `session_id=$(claude -p "Start a review" --output-format json | jq -r '.session_id')`; "the response payload includes `total_cost_usd` and a per-model cost breakdown". With `--json-schema`, structured output lands in a `structured_output` field.
- Repo fixture (`benchmarks/tests/agent_track/fixtures/claude_result.json` — **hand-written fixture, not captured CLI output**, but preflight-validated design): `{type:"result", subtype:"success", is_error, duration_ms, duration_api_ms, num_turns, result, session_id, total_cost_usd, usage{input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens}}`.
- Repo parser reads `total_cost_usd` (top-level, with a nested-`result` fallback), `num_turns`, `result` (`_parse.py:64-97`).
- So: **final answer text, total usage, cost, duration, and session_id ARE loop-side** without touching the transcript; per-turn usage is not in this envelope.

### 2.2 `--output-format stream-json` (NDJSON event stream)

- `claude --help` (v2.1.76, run): `--output-format … "stream-json" (realtime streaming)`; `--include-partial-messages … only works with --print and --output-format=stream-json`; `--input-format stream-json`; `--replay-user-messages`.
- Docs (same fetch): each line is one JSON event; first event is `system/init` (`session_id`, `model`, `tools`, MCP servers, plugins, optional `capabilities` array — capabilities requires ≥2.1.205); `assistant`/`user` events carry `message` with content blocks; subagent messages appear with `parent_tool_use_id` set (main-conversation messages carry `null`); by default only subagent tool_use/tool_result are forwarded — `--forward-subagent-text` (≥2.1.211) adds subagent text/thinking; `system/api_retry` events carry `attempt, max_retries, retry_delay_ms, error_status, error, uuid, session_id`; **"The last line of the stream is a `result` message with the final response text, cost, and session metadata"** (truncation of that last line fixed in 2.1.208). With `--include-partial-messages`, `stream_event` events wrap raw API deltas (`.event.delta.text`).
- The repo already consumes exactly this: `_runner.py:53` (`_RESULT_EVENT_TYPE = "result"`) takes the final result line from the same stream (no second invocation), `_parse.py` folds tool_use/usage events.

### 2.3 Headless flags that matter for capture (all verified in `claude --help` this session)

`--session-id <uuid>` ("Use a specific session ID … must be a valid UUID"), `--no-session-persistence` ("sessions will not be saved to disk … only works with --print" → **by default, `-p` runs DO persist an on-disk transcript**), `--mcp-config <configs...>`, `--strict-mcp-config`, `--permission-mode`, `--json-schema`, `--fork-session`, `--resume`. Docs add `--bare` (skips hooks/skills/plugins/CLAUDE.md/auto-MCP; "recommended mode for scripted and SDK calls", future default for `-p`) — not present in local 2.1.76 help, so **version-gated**.

---

## 3. Correlation — getting a trajectory_id into the MCP server process

### 3.1 `.mcp.json` `env` block: SUPPORTED (docs + doc examples)

Official MCP docs page (fetched this session, https://code.claude.com/docs/en/mcp; quotes from the persisted fetch):

- Server entries support `"env"`: e.g. `{"mcpServers": {"database-tools": {"command": …, "args": […], "env": {"DB_URL": "${DB_URL}"}}}}` and stdio add-json example `'{"type":"stdio","command":…,"args":[…],"env":{"CACHE_DIR":"/tmp"}}'`.
- "Environment variable expansion in `.mcp.json` … `${VAR}` … `${VAR:-default}` … can be expanded in: `command`, `args`, **`env`: environment variables passed to the server**, `url`, `headers`."
- The repo's current `render_mcp_config` (`_command.py:117-135`) emits only `command` + `args` — adding a per-rollout `"env": {"PYDOCS_TRAJECTORY_ID": …}` is a pure additive change to that function, and `--strict-mcp-config` (already passed for the indexed arm, `_command.py:100-104`) guarantees the arm sees only that one server.

### 3.2 session_id is available on BOTH sides

- Transcript side: every envelope record carries `sessionId` (§1.3-1.4); the transcript filename is the sessionId.
- Loop side: result envelope has `session_id` (docs example jq `.session_id`); stream-json `system/init` carries `session_id` as the first event; and the loop can *choose* the id up-front with `--session-id <uuid>` (help, v2.1.76), making trajectory_id = session_id a viable design (one process per rollout).

### 3.3 Does Claude Code set a session-identifying env var in spawned MCP servers?

Empirical (this session): dumped the environment of a live Claude-Code-spawned stdio MCP server process (`ps eww` on pid 8137, `bun …/claude-mem/12.3.7/scripts/mcp-server.cjs`, child of a running `claude --output-format stream-json …` SDK process; values redacted where sensitive):

```
CLAUDE_PROJECT_DIR=/Users/msobroza
MCP_CONNECTION_NONBLOCKING=true
CLAUDE_PLUGIN_ROOT=…/claude-mem/12.3.7        (plugin-provided server only)
CLAUDE_ENV_FILE=/Users/msobroza/.claude/session-env/aa87b285-ccb5-45ee-aea2-5d70c500ca37/sessionstart-hook-2.sh
CLAUDE_PLUGIN_DATA=…                          ANTHROPIC_BASE_URL=…
CLAUDE_AGENT_SDK_VERSION=0.1.77               CLAUDE_CODE_ENTRYPOINT=sdk-cli
```

- `CLAUDE_PROJECT_DIR` is **documented**: "Claude Code sets `CLAUDE_PROJECT_DIR` in the spawned server's environment to the project root" (MCP docs fetch, §"line 104" of persisted output).
- **No `CLAUDE_SESSION_ID`-style variable exists.** The only session-identifying trace is the session UUID embedded in the `CLAUDE_ENV_FILE` *path* (`~/.claude/session-env/<sessionId>/…`, dir naming verified against `ls ~/.claude/session-env/`). That is an undocumented implementation detail observed on SDK version 0.1.77 — usable as a fallback heuristic at best; **treat "Claude Code provides a session env var to MCP servers" as UNCONFIRMED/absent** and rely on the explicit `.mcp.json` `env` injection instead.

**Recommended correlation design (evidence-backed):** loop generates trajectory_id → passes `--session-id <trajectory-uuid>` (ties transcript + result envelope) AND writes it into the per-rollout `.mcp.json` `env` map (ties the server-side trace). Both mechanisms are documented/flag-verified; neither depends on undocumented behavior.

### 3.4 Approval caveat

Docs: "Claude Code prompts for approval before using project-scoped servers from `.mcp.json` files" — but the harness path passes an explicit `--mcp-config` + `--strict-mcp-config` and pre-allowlists `mcp__pydocs-mcp__*` via `--allowedTools` (`_command.py:40,94-104`), which is the existing, working pattern.

---

## 4. Stability — which artifact is the least-fragile parse target?

### 4.1 On-disk transcript: undocumented and visibly drifting

The transcript format is **not documented** anywhere in the fetched official pages (the headless page never mentions the on-disk JSONL; sessions are exposed via `--resume`/SDK APIs instead). Measured drift across four local versions (same project, `keys` union per record type):

| version (file) | drift observed |
|---|---|
| 2.1.111 (`dcde9160…`) | has `slug` on user+assistant; types incl. `pr-link` |
| 2.1.156 (`3ca5f74a…`) | `slug` gone; `mode` type appears |
| 2.1.197 (`1594e0c7…`) | assistant gains `attributionMcpServer, attributionMcpTool, attributionSkill`; user gains `promptSource` |
| 2.1.205 (`e40dbc30…`) | attribution keys **renamed** → `attributionPlugin, attributionSkill`; assistant gains error fields (`apiErrorStatus, error, errorDetails, isApiErrorMessage`); `custom-title` type appears; `mode`/`system` absent in sample |

Also: an observer-session file showed `progress` records; `file-history-snapshot` appears only in some files; no `summary` type anywhere (§1.2). Core spine stable across all four: `uuid, parentUuid, sessionId, timestamp, type, message, cwd, gitBranch, version, isSidechain, userType` + `toolUseResult` on tool-result user records.

### 4.2 stream-json stdout: documented, feature-detectable, version-noted

The headless docs page documents the event vocabulary (init/assistant/user/result/system-subtypes/stream_event) **with explicit min-version annotations** (e.g. result-line truncation fixed 2.1.208; `capabilities` array ≥2.1.205 "Check it to feature-detect instead of comparing version strings"). It is the shape the repo's harness already parses tolerantly (`_parse.py` skips malformed lines).

### 4.3 Agent SDK: the only *contractual* surface

Python Agent SDK docs (fetched this session, https://code.claude.com/docs/en/agent-sdk/python): `query()` / `ClaudeSDKClient.receive_response()` yield typed objects — `SystemMessage, UserMessage, AssistantMessage, ResultMessage, StreamEvent, HookEventMessage, TaskNotificationMessage`; `ResultMessage` is a dataclass with `total_cost_usd, usage, num_turns, session_id, result, is_error, duration_ms, subtype`; sessions are persisted and listable (`list_sessions()`, `get_session_messages()`), with an optional `SessionStore` interface for mirroring transcripts to external backends. A versioned pip package (`CLAUDE_AGENT_SDK_VERSION=0.1.77` observed live in §3.3) is a semver contract in a way the on-disk JSONL is not.

### 4.4 Recommendation (ranked by fragility, with evidence)

1. **Primary: stream-json stdout of the `claude -p` run the loop already owns.** Documented event set with min-version notes and a feature-detect field; the final `result` line gives answer/cost/usage/session_id in the same stream (`_runner.py` already does exactly this); no filesystem coupling; works with `--no-session-persistence`.
2. **Secondary/enrichment: the on-disk transcript** keyed by the loop-chosen `--session-id` — the only place with per-record `uuid/parentUuid` threading, full MCP result envelopes (§1.5), subagent transcripts, and duplicated `toolUseResult`. Parse it open-world (ignore unknown keys/types, dedupe by `message.id`), pin only the stable spine of §4.1, and record the `version` field alongside every captured trace.
3. **If/when the loop is rewritten in Python: the Agent SDK** is the least-fragile programmatic stream (typed, versioned package), at the cost of replacing the current subprocess design. Not required for Phase 2; noted for the roadmap.

---

## 5. Final patch visibility

- The **result envelope** contains `result` (answer text), cost/usage/turn metadata — nothing patch-like (docs field lists + fixture, §2.1).
- The **stream/transcript** contains the *ingredients* of edits — `Edit`/`Write` tool_use inputs (`old_string`/`new_string`, `file_path`) and their tool_results — but no consolidated final diff. `file-history-snapshot` records are checkpoint bookkeeping (`trackedFileBackups` map; sample empty), not diffs (§1.3).
- Docs offer no patch output either; the commit example has Claude run `git commit` itself via Bash.
- **Conclusion: in an SWE-bench-style rollout the final patch = `git diff` of the workspace, captured by the loop after the process exits.** Phase 2 must snapshot the workspace (e.g. `git add -N . && git diff HEAD` or `git diff` + status for untracked) itself; reconstructing edits from transcript Edit-inputs would be a lossy re-implementation (rejected). Consistent with the current read-only harness scaffold ("Do not edit any files", `_command.py:141-146`) which sidesteps patches today — the patch-capture step is NEW work for any write-task dataset.

---

## Appendix: commands behind the numbers (all run 2026-07-18)

- `claude --version` → `2.1.76 (Claude Code)`; `claude --help` (flag quotes in §2.3).
- jq/python histograms over `~/.claude/projects/-Users-msobroza-Projects-pyctx7-mcp/*.jsonl` (record types, §1.2; key sets per version, §4.1; 51-records/20-message-ids dedupe measurement, §1.6).
- python extraction of lines 10/43/44 of `…-hardcore-banach-7dae8e/7b071a5f-cd0a-4799-8c4d-7f8cf50c6e7a.jsonl` (262 lines, 0 malformed; §1.4-1.5).
- `ps -axww -o pid,ppid,command` + `ps eww 8137` (live MCP server env, §3.3, redacted).
- WebFetch: `https://code.claude.com/docs/en/headless` (full page), `https://code.claude.com/docs/en/mcp` (persisted to `…/tool-results/toolu_01ASJPG4SzEK6iBEf75RTedg.txt`), `https://code.claude.com/docs/en/agent-sdk/python`.
- Repo cites: `docs/adr/0007-deterministic-routing-suggestions.md:36-43`; `benchmarks/src/pydocs_eval/agent_track/_command.py:23-49,83-135`; `_parse.py:15-32,64-135`; `_runner.py:51-53,153-169`; fixtures `benchmarks/tests/agent_track/fixtures/claude_result.json` + `claude_stream.jsonl` (hand-written).
