**Streaming agent activity in the ask-your-docs UI**

Offline tests settle both halves of the request. For tool calls, use `agent.astream(stream_mode=["messages","updates"], version="v2", subgraphs=True)` and drain it into widgets from the Streamlit script thread; it gives everything needed. For reasoning, streaming alone will not work: the installed langchain-openai throws away the reasoning text qwen/qwen3.8-27b sends back through OpenRouter, so a small subclass of the chat model is needed first. The fake-model prototypes run clean, including a headless Streamlit test. Nothing live was called (no cost) and no repo was edited.

**Installed versions** (`/Users/msobroza/venvs/ayd-openrouter`, Python 3.12.12): pydocs-mcp 0.6.0, langgraph 1.2.11, langgraph-prebuilt 1.1.0, langchain-core 1.6.2, langchain-openai 1.1.9, langchain-mcp-adapters 0.3.2, streamlit 1.63.0, openai 1.109.1, mcp 1.30.0. The `langchain` package itself is not installed.

**How the app works today (origin/main)**
- `ask()` in `agent.py` calls `agent.ainvoke(...)` and keeps only `result["messages"][-1].content`.
- `app.py` runs that under `st.spinner` through `run()`, a blocking call on the cached background event loop. Every step between question and answer is thrown away.
- With the live config (`architecture: auto`, `vision: true`, `preferred_architecture: inline`) the agent is a flat `create_react_agent` graph.
- `vision_subagent` nests that same graph inside a node, so the stream needs `subgraphs=True` to see inside it.

**1. Reasoning is dropped before the UI can see it**

In langchain-openai 1.1.9, `_convert_delta_to_message_chunk` keeps only role, content, function_call and tool_calls. The non-streaming path `_create_chat_result` drops the field too.

I fed a canned OpenRouter stream through a fake HTTP transport (`proto_openai_reasoning.py`):
- **Stock `ChatOpenAI`:** `reasoning_content` is None, both streaming and non-streaming.
- **A roughly 15-line `ReasoningChatOpenAI` subclass:** it overrides both methods and copies `delta.reasoning` (or `reasoning_content`) into `additional_kwargs["reasoning_content"]`. Reasoning then arrives token by token, merges into the final message, and tool-call chunks and usage are unchanged.

Two related points:
- **Read `additional_kwargs["reasoning_content"]` directly.** Don't rely on `content_blocks`: chunks tagged as coming from OpenAI go through the OpenAI block translator, which never adds a reasoning block, so none appeared in the test.
- **Token counts:** `ChatOpenAI` only turns on `stream_usage` for the default OpenAI URL. Pass `stream_usage=True` in `build_chat_model` if you want a token or reasoning-token badge; the test confirmed the reasoning-token count comes through then.

The fix belongs at the single `ChatOpenAI` construction site, `build_chat_model` in `llm_connection.py`.

**2. Event order with a fake model**

The fake turn: reasoning, then three parallel tool calls (search 0.30s, overview 0.10s, grep fails), then reasoning plus the line "Let me open APIRouter.", one `get_symbol` call, and the final answer. Full log is in `events_run.log`.

| Strategy | What arrives |
|---|---|
| S1: `astream(["updates","messages"])`, v1 tuples | `messages[agent]`: reasoning chunks, text chunks, tool-call argument fragments, end-of-turn marker. Then `updates{agent}`: the complete message with parsed tool calls and full reasoning. |
| S2: `astream([...,"tasks"], version="v2")` | Same order as S1, as dicts `{"type","ns","data"}`, plus "tasks" start/end events. Each tool's "tasks" start includes its call id, name and args. |
| S3: `astream_events(version="v2")` | Same information, but noisier (about 40 internal chain events per run). `on_tool_start` gives only the args and a run_id, not the tool call id, so parallel calls with the same name have to be paired by run_id. |
| S4a: nested graph, `subgraphs=False` | No live activity. Everything arrives as full messages at the very end (1173 ms). |
| S4b: nested graph, `subgraphs=True` | Full live stream, with namespace `("react_agent:<id>",)`. |
| S5: `astream_events(version="v3")` | Marked experimental (warning shown). It produced only values and messages events with no tool-call events. Don't use it. |

Details that matter for the UI:
- **Tool results arrive one at a time.** Each tool's `ToolMessage` arrives in "updates" and "messages" as soon as that tool finishes: grep at 354 ms (error), overview at 426 ms, search at 622 ms.
- **The next model turn waits for the slowest tool.** It started at 623 ms.
- **Failed tools are marked.** They arrive as `ToolMessage(status="error")`, which is also what the MCP adapter produces for `isError=True`.
- **Tool results are content blocks, not strings.** Content is `[{"type":"text","text":...}]`, so use `msg.text`.
- **Tool start = the moment the "agent" update with tool calls arrives.** It lands within 1 ms of the "tasks" start events, so "tasks" is optional for `create_react_agent`. It is still useful to time non-agent nodes, such as `vision_extract` as an "Analyzing image" step.
- **Timing:** stamp `perf_counter` on the loop thread as events arrive. Tool duration is result arrival minus start; model time to first token is the first chunk after the "agent" task starts.

**3. Recommended event handling**
- **Filter what counts as answer text.** Take text and reasoning only from `AIMessageChunk`s where `metadata["langgraph_node"] == "agent"`. Otherwise tokens from the vision call in `vision_extract` or from the `reinspect_images` tool leak into the answer. Tagging those models with `"nostream"` also works; langgraph 1.2.11 honours it.
- **Text before tool calls is narration, not the answer.** Stream it live, and if that turn ends with tool calls, move it into the activity trace.
- **The final answer** is the last "agent" update with no tool calls. `ask()` can return it and update history as it does now.
- **Keep the eval harness unchanged.** Add an event callback to `ask()` (or a separate streaming version) so the binding keeps its current `ainvoke` path.
- **Scope pinning:** the tool args shown are what the model proposed. `_intercept` adds the pinned project/package afterwards, so show a "scope pinned" chip or pass the effective args through a context variable.

**4. Streamlit 1.63 layout (verified headless with AppTest, no errors, including after a rerun)**
- **Keep the agent on the existing background loop.** A producer coroutine turns stream parts into small event records and puts them on a thread-safe `queue.Queue`. The script thread reads the queue every 50 ms and updates placeholders. Widgets are only touched from the script thread.
- **Inside `st.chat_message("assistant")`:**
  - `st.status("Thinking…", expanded=True)` replaces the spinner. The label changes to "Calling search_codebase…" while a tool runs. At the end it collapses to "Worked for 1.2s · 4 tool calls · 1 failed", with state "complete", or "error" if something failed.
  - Inside the status, reasoning streams into an `st.empty()` as a caption.
  - Each tool gets its own `st.empty()` slot, updated in place: status icon, tool name, args and duration, then an `st.expander("result")` with the first 600 characters of the result in `st.code`.
  - An `st.expander` or `st.popover` nested inside `st.status` both render without error in 1.63. `st.badge` is also available.
- **Answer:** an `st.empty()` below the status that streams markdown with a cursor.
- **Don't hand the agent's async generator to `st.write_stream`.** It converts it by creating a new event loop on the script thread, not the cached loop that owns the MCP client and HTTP connection pools.
- **Stop/rerun:** call `future.cancel()` in a `finally` so a stop or rerun mid-stream doesn't leave the agent running.
- **Errors:** the producer should not turn exceptions into text. After the queue is drained, call `future.result()` inside `translate_auth_errors(bearer)` so the existing redaction path still applies.
- **History:** save the trace in session state (`messages` is currently just `(role, text)`) and redraw it as a collapsed `st.status` on every rerun. The prototype does this.

**5. Open items**
- The live model was not called. It is unconfirmed whether qwen/qwen3.8-27b on OpenRouter returns reasoning by default or needs `extra_body={"reasoning": {...}}`. One cheap live call would settle it.
- Some providers put reasoning inline as `<think>…</think>` in the answer text. A small splitter would be a sensible fallback.

Files are in /private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/ayd-ui:
- fakes.py
- proto_events.py
- events_run.log
- proto_openai_reasoning.py
- proto_page.py
- run_apptest.py