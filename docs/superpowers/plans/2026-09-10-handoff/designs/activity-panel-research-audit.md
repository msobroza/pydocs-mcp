# Ask-your-docs chat page: what each turn shows today, and what gets thrown away

Everything below is read from `origin/main` (2a5592a, released 0.6.0). The runtime claims were checked with three offline probes in `/Users/msobroza/venvs/ayd-openrouter`, using a mock OpenRouter transport. `OPENROUTER_API_KEY` isn't set in this shell, so I made no live calls.

## Summary
- **Final text only.** A turn renders the user's question, a spinner that says "searching your docs…" and goes away, and then the final answer. Tool calls, tool results, reasoning, token counts, the rewritten question and the image-analysis facts all exist during the turn and are dropped.
- **Reasoning is lost before the page ever sees it.** langchain-openai 1.1.9 strips the `reasoning`, `reasoning_content` and `reasoning_details` fields from OpenRouter/vLLM responses, both streamed and non-streamed. Only the reasoning token count survives. Showing reasoning therefore needs a small capture layer; the UI alone can't do it.
- **Tool steps are all there.** `result["messages"]` already holds every call and result, and `astream` can deliver them live. Tool results also carry a structured summary (`meta` fields) from the frozen tool contract, so the display needs no MCP change.
- **The file-size limits are nearly used up.** `app.py` has 7 lines left, `agent.py` 10, `llm_connection.py` 7, and `ask_your_docs_models.py` none. New code has to go in new modules.

## 1. What a turn renders (`app.py`)
1. **History replay.** `st.session_state.messages` and `.history` start empty (347–348). Each rerun replays `for role, text in messages: st.chat_message(role); st.markdown(text)` (350–352). A turn is stored only as plain `(role, text)` pairs.
2. **Attachment chips.** Symbols attached from the Graph page appear as removable buttons (354–364). Images from the last question appear as markdown pills (370–373).
3. **Send loop (439–492):**
   - Two checks can refuse before any model call: a bearer error (445–446) and no model chosen (447–448).
   - Images are collected and checked against the vision policy (450–462). The image store is updated (466–472).
   - The user message is appended to `messages` and drawn (473–476).
   - The assistant turn runs inside `with st.chat_message("assistant"), st.spinner("searching your docs…")` (477). The spinner text never changes during the turn: it covers the rewrite call, every model round and every tool call, and nothing about it persists.
   - The scope snapshot is taken (479) and attachments are woven in and cleared (481–482).
   - `get_agent` (484) is followed by `_answer_question` (485). That function (421–436) calls `reformulate()` and then `ask()`, both inside one `translate_auth_errors` block. The rewritten question is passed to `ask()` but never shown.
   - `st.markdown(answer)` (491) draws the answer, which is then appended to `messages` (492).

## 2. How the agent is called
- **Background event loop.** One cached event loop runs on a daemon thread (90–95). `run()` is `run_coroutine_threadsafe(...).result()` (101–102): it blocks the Streamlit script thread while the coroutine runs on the loop thread.
  - Any `st.*` call made from inside the agent (callbacks, the loop thread) would have no script context and would not render.
  - Live display therefore has to pull events back onto the script thread, for example by calling `run(agen.__anext__())` once per event.
- **`ask()` (`agent.py:440–489`):**
  - It sets three per-question context variables: scope, image store and reinspect budget (463–466).
  - It calls `await agent.ainvoke({"messages": [*history, HumanMessage(content)]})` (480). This is the only call — no streaming, no callbacks.
  - It keeps only `result["messages"][-1].content` (481).
  - History keeps just the bare question (plus an image placeholder) and the answer text (486–487), trimmed to the last 8 messages (488).
- **Agent graphs:**
  - `text_react` and `inline` are plain `create_react_agent` graphs (`text_react.py:34`, `inline.py:36–40`). LangGraph now marks that function deprecated; it still works.
  - `vision_subagent` is a StateGraph: a `vision_extract` node, then the react agent as a nested graph (`vision_subagent.py:77–86`). `auto` picks one of these once, at build time (`auto.py:38–48`).
- **Caching.** The compiled agent is cached per connection and shared across browser sessions (`app.py:177–184`). Anything recorded per turn must go through a context variable or a per-call `config={"callbacks": ...}`, never into the graph itself.

## 3. Data that exists during a turn and is discarded

| Data | Where it lives | Status today |
|---|---|---|
| Tool calls (name, arguments, id) | `AIMessage.tool_calls` in `result["messages"]` | Dropped at `agent.py:481` |
| Arguments actually sent after scope pinning | `_intercept` rewrites them (`agent.py:130–147`); only a `logger.debug` records it | The model's `tool_calls` show its own arguments, not the pinned ones actually sent |
| Tool results | `ToolMessage.content` is a list of text blocks. `ToolMessage.artifact` is `{"structured_content": {text, items, meta}}`, because the MCP adapter returns content plus artifact (adapter `tools.py:226–283`, `:533`) | Dropped |
| Tool result summary | `meta.tool`, `project`, `truncated`, `index_stale`, `suggestion`, `resolution` (for `get_references`), `branch`, and the item count (`docs/tool-contracts.md` §2.1–2.4, lines 66–150) | Dropped |
| Tool errors | An MCP error result becomes a `ToolMessage` with `status="error"` (adapter default, `tools.py:~384`) | The model sees it; the user never does |
| `reinspect_images` calls and results | Agent-local tool, not MCP (`reinspect.py:52–92`); its failures are already redacted (83–84) | Dropped |
| Image-analysis facts | A HumanMessage wrapped in `[image analysis]…[/image analysis]` (`vision_subagent.py:56`, `61–64`) | Dropped |
| Rewritten question | `reformulate()` return value (`reformulation.py:34–55`, `app.py:425`) | Never shown; its token usage is also lost |
| Token usage | `AIMessage.usage_metadata`, including `output_token_details.reasoning` (probe 1) | Dropped |
| Reasoning text | Removed by langchain-openai itself (next section) | Never reaches the app |

## 4. Reasoning availability (probe evidence)
**Probe 1** (`probe_reasoning.py`): the mock answers in two shapes, OpenRouter's (`message.reasoning` + `reasoning_details`) and vLLM/DeepSeek's (`reasoning_content`).
- Both `ainvoke` and `astream(stream_mode=["updates","messages"])` produce AI messages with no reasoning in `additional_kwargs` and no reasoning entry in `content_blocks`.
- Only `usage_metadata.output_token_details.reasoning = 25` survives.
- The cause is in langchain-openai's `chat_models/base.py`: `_convert_dict_to_message` (line 161) and `_convert_delta_to_message_chunk` (line 369) copy only `function_call`, `tool_calls` and `audio`.

**Probe 2** (`probe_capture.py`): a `ChatOpenAI` subclass that overrides `_create_chat_result` and `_convert_chunk_to_generation_chunk` recovers the text into `additional_kwargs["reasoning_content"]`.
- This works on both the `ainvoke` and streaming paths, for both field shapes.
- Even then, `content_blocks` does not show a reasoning entry, so the UI must read `additional_kwargs["reasoning_content"]` directly.
- Constraint: the AC-19 test (`test_chat_model_factory.py:81–96`) replaces `langchain_openai.ChatOpenAI` with a spy and checks the exact keyword arguments. The subclass therefore has to be derived from `langchain_openai.ChatOpenAI` at call time (a small class factory), with no extra arguments on the no-block path. `build_chat_model` is at `llm_connection.py:393–424`, and that file has only 7 lines left, so the subclass needs its own module.
- Not verified: whether OpenRouter returns reasoning text for qwen/qwen3.8-27b by default, or only when asked (`extra_body` reasoning or include flags). That needs one live call with the key set. Adding a request flag would change the byte-identical request AC-19 pins, so it would have to be a YAML option, off by default.

**Probe 3** (`probe_subgraph.py`): for the `vision_subagent` shape, `astream(stream_mode="updates")` without `subgraphs=True` produces one update from `react_agent` at the very end, containing all messages. With `subgraphs=True`, each model round and tool step arrives as it happens, labelled with a `react_agent:<id>` namespace.

## 5. Errors, redaction, logs
- **Failure path.** `app.py:486–490` catches every exception, logs only the class name as JSON (489), and calls `_refuse` (399–404): `st.error(redact_bearer(...))`, then `st.info("Your question (not sent): …")`, then `st.stop()`.
- **Problems on a mid-turn failure:**
  - "(not sent)" is wrong at that point: `_refuse`'s docstring describes a before-any-call refusal, but by then the rewrite and some tool calls have already run.
  - The user message is already in `messages` (474) with no reply, while `history` is unchanged.
  - The error box isn't saved, so it disappears on the next rerun.
- **The secrets rules.**
  - G8: secrets never in YAML, argv, logs, the UI or the cache key (`2026-09-05-ask-your-docs-llm-connection-design.md:199`).
  - H4: every auth failure is scrubbed before it reaches `st.error`, a tool-result string, a trace or a caption (same file, line 242).
  - The tools for this are `redact_bearer` (`bearer_tokens.py:394–400`), which masks the cached bearer value and any `Bearer <x>` pattern, `redacted_failure_caption` (403–410) and `translate_auth_errors` (413–432).
- **A new display surface falls under H4.** Tool arguments, tool results (especially error results), reasoning and partial traces must all pass through `redact_bearer` once, when captured. Logs should record event names and counts only, never arguments, results or reasoning.
- **A gap in `redact_bearer`.** It masks only the chat bearer.
  - Today the serve child starts with a minimal environment: the UI path passes no `subprocess_env` (`agent.py:283–289`, `349`), so the child holds no provider key.
  - Once the in-flight 0.6.1 forwards launcher variables to the child, its error text (for example from the embedding provider) could contain a different secret.
  - It happens to coincide here: both chat and embeddings use `OPENROUTER_API_KEY` (`~/pydocs-openrouter/config.yaml:9,22`).
  - Tool-result redaction should also mask the values of forwarded variables.

## 6. Sidebar, Graph page, theme
- **Status line.** `render_connection_status_line` (`connection_dialog.py:104–118`) draws one `st.caption` of the form `host · model · auth · vision`, with the bearer error in its tooltip.
  - The auth cell is built at 59–94: `token …last4 HH:MM`, `$VAR set|missing`, `no auth`, or `token unavailable ⚠`, plus `⚠ http` or an origin-changed note.
  - The vision cell (97–101) reads `vision: yes|no (source)` or `vision: ?`.
  - The page calls it at `app.py:274–279`.
  - Test gotcha: `status_line()` in `_page_fixtures.py:94–95` picks the first caption containing both `" · "` and `"vision:"`. A per-turn summary caption must not contain both.
- **Graph page.** `pages/2_Graph.py` feeds the chat through "Add to question" (262–266), which writes `st.session_state["attached"]`; the chat shows the chips (`app.py:354–364`) and weaves them into the question (481). The page shows no agent activity. Line 216 uses `components.v1.html`, which logs a deprecation warning in `~/pydocs-openrouter/ui.log`.
- **Theme.** `theme.py:73–163` injects CSS; the chat rules are at 133–143. There are no rules for expanders or `st.status`. The CLI forces Streamlit's native dark theme (166–180), so in light mode an expander or status box would keep dark chrome unless `theme_css` adds rules for `[data-testid="stExpander"]` and `details summary` using the palette colours.

## 7. Constraints on any change
- **File-size limits** (`test_module_line_budgets.py:12–21`, each file must stay under the limit):

  | File | Lines now | Limit | Lines left |
  |---|---|---|---|
  | `app.py` | 492 | 500 | 7 |
  | `agent.py` | 489 | 500 | 10 |
  | `llm_connection.py` | 492 | 500 | 7 |
  | `ask_your_docs_models.py` | 199 | 200 | 0 |
  | `connection_dialog.py` | 274 | 500 | 225 |

  - Any new YAML option (for example `ask_your_docs.display.*`) needs a new config sub-model file, or space freed in `ask_your_docs_models.py`.
  - `binding.py` is at 499 lines with no limit set.
  - New modules should be added to the limit table. A reasonable split: a pure turn-trace extractor, a Streamlit trace renderer, and the reasoning-capture subclass.
- **Page tests.** They run the real page with AppTest and replace `agent_module.ask`, `build_agent` and `reformulation_module.reformulate` (`test_app_caches.py:62–87`, `test_app_connection_dialog.py:68–84`).
  - Their fakes return a plain `str` from `ask()`. `test_image_attachment.py:94–244` also calls `ask()` directly, and `ask` is a public lazy re-export (`__init__.py:12–28`).
  - So keep `ask() -> str` and add the trace as an optional sink parameter or a sibling function.
  - AppTest in Streamlit 1.63 supports `at.expander` and `at.status`.
  - `FakeLlm` (`_agent_fakes.py:18–34`) emits no tool calls and no reasoning, so a named fake variant is needed for trace tests.
- **Lazy imports.** The launcher must not import streamlit, langgraph or httpx (`test_cli_parser.py:22–54`). `architectures/base.py:3–5` is kept light on purpose. The trace extractor should depend only on langchain-core message types with function-local imports, so it can be tested without Streamlit.
- **Frozen MCP surface.** Showing tool steps is client-side only and uses the existing envelope `meta`. Any options belong in YAML (or a session-only sidebar toggle, like Light mode), never in tool parameters.
- **Streamlit reruns.** Anything not stored in `session_state` disappears on the next interaction.
  - Each assistant turn needs a small, already-redacted trace record stored next to its text: extend the `(role, text)` pairs, or keep a parallel list.
  - The replay loop (350–352) must draw it again.

## 8. What this means for the display design
- **Recording.**
  - Simplest: after `ainvoke`, slice `result["messages"]` for this turn into a trace.
  - Live: iterate `agent.astream(stream_mode=["updates","messages"], subgraphs=True)`, bridged onto the script thread.
  - Either way, record the pinned arguments actually sent. Pulling a pure `_pinned_args(name, args, scope)` out of `_intercept` lets the interceptor and the display share one copy.
- **Layout.** One `st.status` or expander above each answer:
  - Label: "N tool calls · X s · T tokens (R reasoning)". Open while running, collapsed when done, marked as an error on failure, with the steps completed so far kept.
  - Steps in order: the question as searched (only if the rewrite changed it) and the scope pin; image analysis, if any; then for each model round a collapsed, length-capped "Thinking" block and the tool calls with a result summary from `meta` (item count, truncated, index stale, suggestion) plus capped raw text in `st.code`.
  - When the reasoning token count is above zero but no text came back, say so: "reasoned (R tokens); provider did not return the text".

Probe scripts are in `/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/ayd-ui/`:
- `probe_reasoning.py`
- `probe_capture.py`
- `probe_subgraph.py`