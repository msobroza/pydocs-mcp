# Agent activity display for ask-your-docs: scores and final proposal

## Scores

| Criterion | inline-steps | timeline-panel | progressive-disclosure |
|---|---|---|---|
| User comprehension | 7 | 9 | 9 |
| Signal-to-noise (answer stays primary) | 9 | 6 | 9 |
| Honesty about reasoning availability | 8 | 8 | 10 |
| Implementation risk and size | 6 | 5 | 7 |
| Performance | 8 | 7 | 8 |
| Privacy | 7 | 9 | 8 |
| **Total** | **45** | **44** | **51** |

**Winner: progressive-disclosure.**

What decided it:

- **Status-line placement.** I checked `origin/main`. `test_app_connection_dialog.py` checks the sidebar status line with `==` at line 106 and `endswith` at lines 181 and 193. inline-steps and timeline-panel both add a `reasoning:` cell to that line, which would break those tests. They only checked the `status_line()` fixture selector (`_page_fixtures.py:95`). progressive-disclosure puts reasoning in a separate caption and avoids this.
- **Reasoning honesty.** progressive-disclosure alone keeps a per-turn state and a per-model sidebar state apart. It also has an "unknown" state, and it only says "not shared" after two turns in a row, because models skip reasoning on easy prompts.
- **inline-steps** has the tightest labels and the best redaction rule (redact the accumulated text, never each chunk). But its status-line placement breaks tests, and its "no block at all" when there were no tool calls hides that the answer was never grounded.
- **timeline-panel** is best for learning (the CLI equivalent, chip breadcrumbs, refinement diffs) and has the strictest rendering rule (all untrusted output shown as plain text). But it is the noisiest, it streams the answer and so has flicker, and it is the largest to build.

---

## 1. Summary

Each assistant turn gets one `st.status` panel above the answer, with three levels of detail:

- **L0**, always visible: one summary line, such as "Done in 6.4 s · 4 steps · 3 files · reasoning shown".
- **L1**, one click: plain-language steps in order (rephrase → thinking → tool calls → thinking → …), each with an outcome, up to 3 file chips, and notes in words.
- **L2**, per step, only when the session toggle "Show technical details" is on: tool name, the arguments the model proposed, the arguments actually sent after scope pinning, `meta`, a raw preview, and the full reasoning.

Behaviour:

- **While running**, the panel is open and its label changes only when a step starts or ends. When the answer arrives it collapses. On every rerun it is redrawn from a trace stored in session state, so earlier turns keep their panels.
- **Reasoning** is shown only when the endpoint actually returns it. Today langchain-openai 1.1.9 drops it before the app sees it, so a small ChatOpenAI subclass has to recover it. The panel then says which case applies on this turn, and a separate sidebar caption says what to expect from this model.
- **Cost:** no extra model, MCP or probe calls.
- **Privacy:** everything is redacted once, logs contain counts only, and untrusted text is always rendered as plain text.
- **Unchanged:** the MCP surface, the eval path (`ask(on_event=None)` still uses `ainvoke` exactly as today), and `binding.py`.

## 2. The display

### Turn running (panel open)
```
 you   who calls BaseIndexStore.append?
 ◆ ┌ ● Finding callers of BaseIndexStore.append …                    3.2 s ┐
   │   ✓ Rephrased your question as "callers of BaseIndexStore.append"      │  (only if it changed)
   │   Scope: project "example_needle" (pinned by you)                       │  (only if a pin applies)
   │   ▾ Thinking (live) · 1,204 chars                                       │
   │   │ The user wants call sites. First confirm the symbol exists, then    │  st.text, muted, left border
   │   │ ask the reference graph for callers…▍                               │  repaints at most 10/s
   │   ✓ Looked up BaseIndexStore.append                  0.3 s · 1 match    │
   │       [src/store/base.py:39]                                            │
   │   ● Finding callers of BaseIndexStore.append …       1.1 s              │
   └─────────────────────────────────────────────────────────────────────────┘
   Writing the answer…                ← placeholder once the final round starts (v1 does not stream the answer)
```

### Turn finished, collapsed (the default)
```
 ◆ ▸ ✓ Done in 6.4 s · 4 steps · 3 files · reasoning shown
   BaseIndexStore.append is called from two places: IndexRunner.flush
   (src/indexer/run.py:112) and …
   Sources  [src/store/base.py:39] [src/indexer/run.py:112]   Also looked at (1) ▸
```
- Label variants: `· 1 failed` when a tool failed. `Answered without searching · 1.1 s` when no tool was called; this is kept because an ungrounded answer is itself something the user should know.
- "Cited" means the answer text contains the source's path or qualified name. The answer's markdown is never rewritten.

### Turn finished, expanded (L1, with L2 open on one step)
```
 ◆ ▾ ✓ Done in 6.4 s · 4 steps · 3 files · reasoning shown
   │ Model's working notes. They may be incomplete or differ from what it actually did.
   │ ▸ Thinking · 1.4 s · "The user wants call sites. First confirm the symbol…"
   │ ✓ Looked up BaseIndexStore.append                    0.3 s · 1 match
   │     [src/store/base.py:39]                                    ▸ Details
   │ ▸ Thinking · 0.6 s · "Found it. Now callers…"
   │ ✓ Found callers of BaseIndexStore.append             1.1 s · 2 callers
   │     [src/indexer/run.py:112] [tests/test_store.py:20]       ▾ Details
   │     ⓘ Reference graph matches by name (syntactic), so some calls may be missed
   │       tool           get_references
   │       model sent     {"target": "BaseIndexStore.append", "direction": "callers"}
   │       actually sent  {…, "project": "example_needle"}   ← project added by your scope pin
   │       meta           project example_needle · branch main · index up to date · truncated no
   │       result         (st.code, plain text, first 600 of 2,431 chars)
   │ ─ Tokens: 3.1k in · 612 out (412 reasoning) · qwen/qwen3.8-27b   (only when usage was sent)
```

### Reasoning not available
The collapsed label drops "reasoning shown" (or says "reasoning hidden"), and L1 ends with one muted sentence:
```
 hidden:   ◆ ▸ ✓ Done in 5.0 s · 3 steps · 2 files · reasoning hidden
           │ … The model reasoned (412 tokens), but openrouter.ai doesn't return the text.
 none:     │ … This model didn't share any reasoning for this answer.
 unknown:  │ … This endpoint doesn't report whether the model reasoned.
 sidebar:  host · model · auth · vision: yes (static)         ← existing line, unchanged
           Reasoning: shown (seen in answers)  ⓘ              ← new separate caption, no " · ", no "vision:"
```

### Tool error vs turn error
One tool fails and the turn continues (status `complete`):
```
 ◆ ▸ ✓ Done in 3.9 s · 3 steps · 1 failed · 2 files
   │ ✗ Opened src/app/.env:1–40            0.1 s · failed: path outside root boundary
```
The model, network or auth fails partway through (status `error`, expanded, steps kept):
```
 ◆ ▾ ✗ Stopped after 3 steps · 4.0 s · the model endpoint rejected the request
   │ ✓ Rephrased your question as …
   │ ✓ Looked up BaseIndexStore.append       0.3 s · 1 match
   │ ✗ Model call failed                     BearerRejectedError: 401 from llm.internal (token …a1b2)
   ⚠ BearerRejectedError: 401 from llm.internal (token …a1b2)       ← existing st.error, redacted
   Your question was not answered: "who calls BaseIndexStore.append?"
```
- Other labels: `Stopped by you after 2 steps` (Stop, or a rerun mid-turn) and `the agent hit its step limit` (`GraphRecursionError`).
- The "(not sent)" wording of `_refuse` stays only for refusals before any call.

## 3. Friendly labels per tool

These live in a pure `activity_labels.py`. The running form ends in "…" and the done form is past tense. Argument values are truncated to 60 characters.

| Tool | Label | Outcome | Notes |
|---|---|---|---|
| search_codebase | Searched {project code / dependencies / all code} for "{query}" [in {package}]; kind api → "symbols", decision → "decisions" | {n} matches in {k} files | |
| get_symbol | summary → Looked up `T`; tree → Outlined `T`; source → Read the source of `T` | path:start–end | |
| get_context | Gathered context for A, B (+n more) | {n} symbols | |
| get_references | callers → Found callers of `T`; callees → Found what `T` calls; inherits → Checked the class hierarchy of `T`; impact → Estimated what changing `T` affects; governed_by → Found decisions governing `T` | {n} callers / edges | syntactic: "matched by name, may miss some"; unavailable: "Reference graph not available for this language" |
| get_why | Looked for design decisions about "{query}" / {targets} | {n} decisions | commit SHAs as text, not links |
| get_overview | Got an overview of {package or "the workspace"} | {project} · {branch} | |
| grep | Searched file text for `/pat/` in {glob, path, or "the project"} | {n} matching lines in {k} files | |
| glob | Listed files matching `{pattern}` | {n} files | |
| read_file | Opened `{path}:{a}–{b}` | {n} lines | |
| reinspect_images (agent-local) | Looked at image {name} again | done / budget used up | |
| vision_extract (node) | Analyzed {n} attached image(s) | first facts line | |
| reformulate (outside the graph) | Rephrased your question as "…" | — | only when it differs after normalizing |
| narration (text before tool calls) | shown as a plain-text Note | — | only rationale from models that don't reason |
| unknown tool | Called `{name}` | first line of text | |

**Notes from `meta`** are always words plus ⓘ, never colour alone:
- truncated → "Results were cut off at the limit"
- index_stale → "The index is older than your checkout"
- suggestion → "Tool hint: …"
- branch different from the pin → "Answered from branch X"
- `status="error"` → "failed: {redacted first line}"

**Citations** come from `items[]` rows that have a `path`:
- Deduplicated per turn by `(path, start_line)`; each step shows 3 chips plus "+N".
- A chip opens an `st.popover` with the span, package, qualified name, and any snippet already captured (no new call).
- An editor link appears only if `editor_link` is set.

## 4. Reasoning capture and the availability signal

**Evidence** (from the probes in `scratchpad/ayd-ui/`, rechecked where marked):
- OpenRouter's qwen/qwen3.8-27b returns reasoning with no request flag. The recorded `rec/A.body`, `rec/B.body` and `rec/C.body` carry it in the `reasoning` field (not `reasoning_content`), and each also has `usage…reasoning_tokens`. I re-grepped these bodies for this report. Probe A also measured 90 characters and 22 reasoning tokens.
- The model listing for qwen/qwen3.8-27b includes `reasoning`, `include_reasoning` and `reasoning_effort` in `supported_parameters` (`models.json`, rechecked).
- Stock `ChatOpenAI` 1.1.9 drops these fields in both streaming and non-streaming mode (`replay.py`).
- The OpenAI block translator never emits a reasoning block, so the UI must read `additional_kwargs`, not `content_blocks`.
- Serialising the enriched message keeps reasoning out of the next prompt (`replay.py`).
- Nesting an expander or popover inside `st.status` inside `st.chat_message` works on Streamlit 1.63, including after a rerun (`nest_probe*.py`, `run_apptest.py`).
- The order of events is thinking → tools → thinking → answer (`events_run.log`). `subgraphs=True` is required for `vision_subagent` to stream live.

**Capture** (`reasoning_capture.py`):
- `reasoning_text_from_payload` reads `reasoning_content`, then `reasoning`, taking the first match. If neither is present it falls back to the text or summary entries in `reasoning_details`.
- `reasoning_is_redacted` returns true when a `reasoning.encrypted` detail is present.
- `reasoning_chat_model_class(base)` is a cached subclass that overrides `_create_chat_result` and `_convert_chunk_to_generation_chunk` and writes `additional_kwargs["reasoning_content"]` and `["reasoning_redacted"]`.
- `build_chat_model` resolves `ChatOpenAI` at call time and passes the same kwargs, so AC-19's spy still sees an identical constructor call. If the spy is not a class, it falls back to the base class.
- If upstream renames a private method, capture degrades: one JSON log line `reasoning_capture_unavailable`, and the badge shows "unknown". The contract test makes the rename fail CI loudly.
- `ThinkTagSplitter` for inline `<think>` tags is opt-in.
- v1 changes nothing on the wire: no `extra_body`, and no `stream_usage` override (see risk 3).

**Display:**
- One Thinking block per model round, taken only from chunks where `langgraph_node == "agent"`. That keeps vision and reinspect tokens out.
- Rendered with `st.text` (plain text, never markdown or HTML).
- The disclaimer appears once per turn.
- `max_chars` keeps the head and tail of long text.

**Availability ladder** (`reasoning_capability.py`, keyed by `connection_key` in session state):
1. **YAML:** `availability` or `display: hidden` → off.
2. **Observed this session:**
   - `shown` sticks once seen.
   - `hidden`: reasoning tokens > 0 or an encrypted detail, but no text.
   - `not shared`: two consecutive turns with usage reported and 0 reasoning tokens.
3. **Listing metadata:** `supported_parameters` containing `reasoning` or `include_reasoning` → "supported, not seen yet". This signal is positive-only.
4. **Default:** "unknown until the first answer".

It never spends a probe call.

## 5. YAML settings (`ask_your_docs.ui.*`) and defaults

```yaml
ask_your_docs:
  ui:
    activity:
      enabled: true              # false = today's spinner + answer
      live: true                 # false = trace built after ainvoke (same builder; test oracle)
      technical_details: false   # default for the session-only sidebar toggle
      collapse_when_done: true   # failed/stopped turns always stay expanded
      result_preview_chars: 600  # ge=0 le=5000
      args_max_chars: 2000       # ge=40 le=20000
      max_steps_shown: 40        # then "+N more steps"
      history_keep: 20           # older turns keep the L0 label + citations only
      editor_link: null          # e.g. "vscode://file/{root}/{path}:{start_line}"
    reasoning:
      capture: true              # read fields the endpoint already returns; no request change
      display: collapsed         # collapsed | expanded | hidden
      max_chars: 20000           # per turn, head + tail
      think_tags: off            # off | on
      availability: null         # true | false | null (= ladder)
```

- Pydantic models with `extra="forbid"`; each default is defined once and mirrored in `defaults/default_config.yaml`.
- Poll interval (50 ms) and repaint throttle (100 ms) are module constants, not settings.
- Anything that changes the request, such as reasoning effort, is left out and would go under `ask_your_docs.llm` if the owner decides to add it.

## 6. Implementation plan

Line counts below are from `origin/main`. Budgets are the entries in `test_module_line_budgets.py`.

**Prep commit (no behaviour change):**
- `ask_your_docs_models.py` is at 199 of 200 lines. Move `ImagesConfig` into a sibling module and re-export it.
- Pull `pinned_args(name, args, scope) -> dict` out of `_intercept` into `scope_pin.py` (about 35 lines). `agent.py` is at 489 of 500, so this also frees room there.

**New modules** (each added to `_BUDGETS`; only `activity_view.py` imports Streamlit):

| Module | Main functions | Target / budget |
|---|---|---|
| `reasoning_capture.py` | `reasoning_text_from_payload`, `reasoning_is_redacted`, `reasoning_chat_model_class`, `ThinkTagSplitter` | 170 / 250 |
| `reasoning_capability.py` | `ReasoningAvailability` ladder, `observe_turn`, `reasoning_sidebar_text`, `reasoning_turn_sentence` | 110 / 200 |
| `activity_events.py` | `ActivityEvent`, `events_from_stream_part(part, t)`, `trace_from_messages(msgs)` (langchain-core imported inside functions; `AGENT_NODE_NAMES = {"agent", "model"}`) | 190 / 300 |
| `activity_labels.py` | `tool_step_label`, `summarize_tool_result(meta, items, text)`, `citations_from_items` | 200 / 300 |
| `activity_trace.py` | `TurnTrace`, `ToolStep`, `ThinkingStep`, `Citation`; `TraceBuilder.apply(event)` (redaction of accumulated buffers, caps, reasoning state); `trim_trace` | 220 / 300 |
| `activity_stream.py` | `async stream_turn(agent, payload, sink) -> list[BaseMessage]` using `astream(stream_mode=["messages","updates"], version="v2", subgraphs=True)`; `asyncio.shield` around in-flight tool calls | 110 / 200 |
| `activity_view.py` | `answer_with_activity(req, *, submit, run, ui, redact)`, `drain_turn_future(future, q, panel)`, `render_saved_activity`, `render_sources`, `render_reasoning_caption` | 280 / 400 |
| `retrieval/config/ask_your_docs_ui_models.py` | `ActivityUiConfig`, `ReasoningUiConfig`, `AskYourDocsUiConfig` | 90 / 200 |

**Edits to existing files:**
- **`agent.py`:** `ask(..., on_event: Callable[[ActivityEvent], None] | None = None)` with one branch. With `None`, the `ainvoke` path is unchanged byte for byte. The return type stays `str`.
- **`app.py`** (492 of 500):
  - `_answer_question` moves out into `activity_view`.
  - The replay loop becomes `enumerate` plus `render_saved_activity(activity.get(i))`.
  - `except` stays in `app.py` as the page's one error boundary.
  - The sidebar calls `render_reasoning_caption`.
- **`llm_connection.py`** (492 of 500): a two-line class selection.
- **`theme.py`:** `danger` and `warn` tokens plus about 15 CSS rules (expander and status chrome in both palettes, reasoning container, chips).
- **`connection_dialog.py`:** untouched.

**Threading and privacy rules:**
- Only the script thread touches widgets. The agent runs on its loop and reports through `queue.Queue.put_nowait`.
- Never use `st.write_stream(async_gen)`: it would run the generator on a second event loop.
- `finally: future.cancel()`.
- `redact` = `redact_bearer` plus masking of the values of every configured `api_key_env` variable. The composition root passes a tuple of env names, which 0.6.1 can extend.
- One log record per turn: `{"event":"turn_activity", …}`, counts only.

**TDD list** (write the failing test first; named fakes `FakeReasoningToolLlm` and `FakeActivityToolset` in `_agent_fakes.py`; fixtures A/B/C bodies only, checked for secrets):
1. `test_reasoning_capture`:
   - A, B and C replayed through `httpx.MockTransport`, streamed and not, both field names.
   - A canary that stock `ChatOpenAI` still drops the fields.
   - Encrypted detail → redacted flag.
   - The overridden private methods exist.
   - Tag split across chunks, and a closing tag with no opening tag.
   - AC-19 unchanged.
2. `test_scope_pin`: `pinned_args` matches the old `_intercept` behaviour table.
3. `test_activity_events`:
   - Order matches `events_run.log`; parallel calls paired by `tool_call_id`.
   - `vision_extract` tokens excluded; narration separated from the answer.
   - `subgraphs=True` streams the nested graph.
4. `test_activity_labels`: parametrised over the 9 tools, `reinspect_images`, `vision_extract` and an unknown tool; each meta note; `resolution="unavailable"`.
5. `test_activity_trace`:
   - A secret split across two deltas is masked, in args, results, reasoning and errors.
   - Caps and `history_keep`.
   - All 5 reasoning states.
   - Tool error → step failed but turn done; cancel → `stopped`.
   - Live builder and `trace_from_messages` agree.
6. `test_reasoning_capability`: ladder precedence; `shown` sticks; `not shared` needs two turns; metadata is positive-only.
7. `test_app_activity` (AppTest):
   - Running → complete and collapsed, with the right label.
   - A rerun replays the collapsed panel.
   - Failed turn: state `error`, steps kept, persists across a rerun, no orphaned user message.
   - `enabled: false` gives today's DOM.
   - A sentinel bearer is absent from every markdown, code, caption and text element.
   - The reasoning caption contains neither `" · "` nor `"vision:"`, and the existing status-line tests stay green.
8. **Log test:** `caplog` contains only the `turn_activity` keys.
9. **Config tests:** `extra="forbid"`, bounds, and YAML/model default parity.
10. **Theme test:** contrast of at least 4.5:1 for `danger` and `warn`.
11. **`test_cli_parser` lazy-import guard:** the launcher still imports no streamlit, langgraph or httpx.
12. **Budget entries** for the new modules.

**Order of work:**
1. The prep commit.
2. Capture plus AC-19 green.
3. Pure modules (events, labels, trace, capability).
4. Stream.
5. View plus app wiring.
6. Theme.
7. Phase 1.1: the "Try it" CLI equivalent in L2 (checked against the real argparse parser), and "Show in graph".

**How this fits the planned "one serve session per page" change:**
- The panel never reaches for the agent, loop or serve child. `stream_turn(agent, payload, sink)` takes the agent as a parameter, and `answer_with_activity` receives `submit` and `run` callables from the page. So it doesn't care whether the agent is `cache_resource`-shared (today) or owned by the page session (planned).
- Traces (`st.session_state.activity`) and the reasoning ladder are already per browser session. With one serve session per page, cancelling mid-turn only affects that page's own stdio session, which removes today's cross-session risk.
- Either way, `asyncio.shield` on the in-flight tool call means a cancelled turn never leaves a late response on the stdio stream.
- Nothing depends on `serve_spawn` internals. The env-name tuple for redaction is the only integration point.

## 7. Risks and open questions for the owner

1. **Private langchain-openai overrides.** The pin `langchain-openai>=0.2` has no upper bound. Mitigations are the contract test, the canary, and degrading without breaking chat. Do you want a `<2` cap?
2. **Persisting failed and stopped turns** (as `("assistant", "")` plus a trace) changes today's behaviour, where errors vanish on rerun. Needs approval.
3. **Token badges** need usage in the stream. `stream_usage=True`, even as a class-field default, adds `stream_options` to the request and so changes the wire request. v1 shows tokens only when the provider sends usage anyway, which OpenRouter does. Should `stream_usage` be enabled?
4. **Streaming changes how the model is called.** LangGraph's messages mode makes the model stream. Some OpenAI-compatible servers stream tool calls badly; `live: false` is the fallback. Local vLLM, llama.cpp and Ollama reasoning fields come from docs, not probes.
5. **Reasoning can be unfaithful or leak file contents.** Mitigations: plain text, the disclaimer, collapsed by default, `display: hidden`. Secrets inside repository files that aren't among the known values can't be detected; say so in the README.
6. **Answer streaming is deferred.** Text from a round that ends in tool calls is narration, so streaming the answer before the round ends causes flicker. Is v1 without it acceptable?
7. **`create_react_agent` is deprecated.** The node rename (`agent` → `model`) is covered by `AGENT_NODE_NAMES`.
8. **Line budgets.** `ask_your_docs_models.py` 199/200, `app.py` 492/500, `llm_connection.py` 492/500, `agent.py` 489/500. These force the prep moves. Alternatively, raise the 200 budget.
9. **Open decisions:** should a request-side reasoning pass-through under `ask_your_docs.llm` ever exist? `editor_link` `{root}` for dependency files that live outside the project. The "Show in graph" handoff (v1.1).

I didn't edit either repo. The only file I created is the JSON spec of the mockup states for the later HTML mockup: /private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/ayd-ui/mockup_spec.json