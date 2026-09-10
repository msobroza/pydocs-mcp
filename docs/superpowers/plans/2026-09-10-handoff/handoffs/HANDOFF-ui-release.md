
## 2026-09-10 — item 3 (one serve session per chat page): adversarial design critique (read-only)

Finished: critique of the item-3 design against origin/main 7c2d7ce (no code written, no commits;
worktree scratchpad/ui-release still NOT created). Sources read: app.py, agent.py, binding.py,
serve_spawn.py, serve_child_env.py, tests (_page_fixtures, test_app_caches, test_binding:533,
test_module_line_budgets), streamlit 1.59.1 caching/app_session/websocket_session_manager/testing,
mcp 1.28.1 shared/session.py + client/stdio, langchain-mcp-adapters tools/sessions/callbacks.

Verdict: design holds with these corrections (full corrected design returned to the orchestrator):
- AppTest uses a CONSTANT session_id ("test session id", streamlit/testing/v1/local_script_runner.py:62),
  so scope="session" caches are shared by every AppTest -> the "two page() sessions = 2 builds" test is
  impossible via AppTest; test isolation at the cache layer instead (two fake ScriptRunContexts / call
  caching.clear_session_resource_cache(sid) to simulate disconnect). Existing test_app_caches R7 test
  stays green UNCHANGED (do not rewrite it).
- Drop the asyncio.shield guard + inflight/drain: a late response for a cancelled request is silently
  dropped by ClientSession's default message handler (mcp client/session.py:69-72, 614-619; adapters
  pass no message_handler) -> the "no unknown request ID in caplog" test is vacuous. Keep a thin
  non-shielding guard that only flags transport failures. Item A's PROPOSAL §6 shield clause: drop too.
- Ping: a dead child fails ping instantly (ClosedResourceError from the closed write stream after the
  receive loop ends, shared/session.py:351-458), so only transport-class failures mean dead; a ping
  TIMEOUT must NOT restart (busy child) -> proceed and let the 300 s read timeout bound the turn.
- start/aclose race leaks a child (aclose never takes the lock and a lazy start may be in flight):
  memoize one close task, make start re-check _closed after ready, and aclose await any pending start.
- Never set_exception(CancelledError) on ready (reads as the caller's own cancellation + "Future
  exception was never retrieved"); cancel ready instead; consume the owner's exception in a done-callback;
  unwrap BaseExceptionGroup from anyio task groups before classifying/logging.
- Owner task must run in an EMPTY context: loop.create_task(coro, context=contextvars.Context()).
- Do NOT touch binding.py (eval binding must stay byte-identical; 499/500 lines; no session_kwargs there).
- Move the 0.6.1 UI env regression guard (test_build_agent_ui_path_spawns_child_with_parent_env) onto
  the new production opener, since the page no longer uses build_agent's default spawn path.
- Streamlit floor: uv.lock already resolves 1.59.1; bump pyproject streamlit>=1.59 in the same hand
  edit as A4's langchain-openai<2 cap, then ~/.local/bin/uv lock --check.

Left: everything (items 4, 3, A) — nothing implemented yet.
Exact next step: create the worktree + venv (commands in the orchestrator prompt), then implement item 4
(port a5c748a using FakeStreamlitRun from tests/harness/ask_your_docs/_launcher_fakes.py), commit; then
item 3 per the corrected design (TDD step 0 = streamlit scope="session"/on_release probe under AppTest).

## 2026-09-10 — stage 1 DONE (items 4, prep, 3) on feat/ask-your-docs-activity-panel

Worktree: scratchpad/ui-release (branch off origin/main 6ca3a61, NOT f9a1535 — origin/main had moved;
v0.6.1 + #237 + #240 are all in the base). Nothing pushed. Commits (no trailers, existing identity):
- dceb0bd fix: launcher passes --server.fileWatcherType none before app path / passthrough
  (cli.py; tests/harness/ask_your_docs/test_cli_file_watcher.py reuses FakeStreamlitRun).
- 66c562f refactor: ImagesConfig -> retrieval/config/ask_your_docs_image_models.py (re-exported);
  scope_pin.pinned_args extracted from agent._intercept (test_scope_pin.py).
- 7ee985f refactor: sidebar scope pickers -> scope_pickers.render_scope_pickers (app.py budget;
  test_scope_pickers.py; pickers had no tests before).
- 6bd29a1 feat: one MCP serve session per chat page — serve_session.py (PageServeSession,
  page_serve_opener, is_serve_transport_failure), page_agent.py (PageAgentHandle, release_page_agent,
  close_all_page_agents, restart_notice); app.py page_agent() = cache_resource(scope="session",
  max_entries=1, on_release=...); AppTest seam session_state["serve_tools_opener"] (default-seeded
  FakeServeToolsOpener in _page_fixtures.page()); streamlit>=1.59 (pyproject + uv.lock line, lock
  --check green); CHANGELOG [Unreleased] Changed/Fixed; example README sentence. binding.py untouched.
Gates: tests/harness + tests/test_config_ask_your_docs.py 556 passed / 2 skipped; ruff check+format
clean; complexipy (new modules) ok, snapshot restored; vulture clean; mypy excludes the harness dir.
Line budgets now tight: app.py 499/500, binding.py 499/500, llm_connection.py 492/500.

Left: item A (activity panel, PROPOSAL.md) incl. A4 (langchain-openai<2 cap in pyproject + the uv.lock
requires-dist line, then `~/.local/bin/uv lock --check`; contract test for the two private ChatOpenAI
methods). A must first move _answer_question out of app.py (499/500). Drop the PROPOSAL §6
"asyncio.shield around in-flight tool calls" clause (serve_session has no shield by design). Map a
failure after handle.closed (released mid-turn) to "stopped", not "error" (critique issue 12). Add the
CHANGELOG "### Added" bullet for the panel under [Unreleased].
Exact next step: in scratchpad/ui-release, start item A with the reasoning_capture TDD (PROPOSAL §6
order step 2), keeping cli.py free of streamlit/langgraph/httpx.

## 2026-09-10 — stage 2 DONE (item A part 1: pure modules + config) on feat/ask-your-docs-activity-panel

Worktree scratchpad/ui-release, nothing pushed, no trailers. One commit per module, test-first (RED seen
for each: ModuleNotFoundError at collection, then green):
- 63415aa reasoning_capture.py (+A4): cached ChatOpenAI subclass overriding _create_chat_result +
  _convert_chunk_to_generation_chunk -> additional_kwargs reasoning_content / reasoning_redacted;
  ThinkTagSplitter (opt-in). llm_connection.build_chat_model returns reasoning_chat_model_class(ChatOpenAI)
  (kwargs unchanged, AC-19 spy green). pyproject + uv.lock requires-dist: langchain-openai>=0.2,<2
  (uv lock --check green). Tests: test_reasoning_capture.py (contract test for both private methods,
  stock-drops canary, A/B/C replays via _reasoning_fakes.FakeChatCompletionsEndpoint; fixtures in
  tests/harness/ask_your_docs/fixtures/openrouter_reasoning/ = response bodies only, no secrets).
- 4f36628 reasoning_capability.py: TurnReasoning (shown/hidden/none/unknown/off), ladder
  ReasoningLadderState + observe_turn + reasoning_availability, sidebar text, turn sentence.
- 28e2a23 retrieval/config/ask_your_docs_ui_models.py (ActivityUiConfig, ReasoningUiConfig,
  AskYourDocsUiConfig) as AskYourDocsConfig.ui + default_config.yaml ui: block; tests/test_config_ask_your_docs_ui.py.
- ca7e835 activity_events.py (ReasoningDelta/RoundEnded/ToolFinished/VisionAnalyzed,
  events_from_stream_part, events_from_messages); [image analysis] markers single-sourced in
  attachments.woven_image_analysis / image_analysis_facts (vision_subagent delegates). Named fakes
  FakeReasoningToolLlm + FakeActivityToolset (+ ACTIVITY_SCRIPT) in _agent_fakes.py.
- 8fffb7b activity_labels.py + activity_outcomes.py (split for the 300-line budget); scope_pin
  CODE_SCOPE_WORDS shared with agent.scope_prefix (same bytes).
- 6df909c activity_trace.py (TurnTrace, steps, TraceLimits, turn_summary_label, trim_history,
  current_step_label) + activity_trace_builder.py (TraceBuilder, trace_from_messages); Citation.redacted.
- b9f80e5 CHANGELOG [Unreleased] Changed bullet for the langchain-openai<2 cap.
Gates at 6df909c: tests/harness + tests/test_config_ask_your_docs.py + tests/test_config_ask_your_docs_ui.py
704 passed / 2 skipped; ruff check+format clean; complexipy ok (snapshot restored); vulture clean
(full package); uv lock --check green. Budgets: all new modules in _BUDGETS; builder 294/300,
llm_connection 494/500, app.py still 499/500.

Deviations from PROPOSAL: labels and trace each split in two modules (budgets); trace_from_messages
lives in activity_trace_builder (events has events_from_messages); think_tags is a bool (YAML 1.1
off/on trap); live reasoning snapshots show text only up to the last whitespace (a half-typed secret
is never shown); "Answered from branch X" note not implemented (no branch pin exists yet);
reasoning.capture / think_tags are NOT yet wired (capture is always on in build_chat_model;
TraceLimits treats capture:false like display:hidden).

Left (stage 3): activity_stream.py (stream_turn over astream v2 messages+updates subgraphs=True; NO
asyncio.shield); activity_view.py (answer_with_activity, drain_turn_future, render_saved_activity,
render_sources via activity_outcomes.split_cited, render_reasoning_caption; move _answer_question out of
app.py first); agent.ask(on_event=None) branch (None = today's ainvoke, byte-identical); app wiring
(replay loop enumerate + saved traces, A1 persist failed/stopped turns as ("assistant","") + trace,
failure after handle.closed -> builder.stop not fail, ReasoningLadderState per connection_key,
technical-details toggle, trim_history with history_keep); redact = redact_bearer + masking every
configured api_key_env value; one {"event":"turn_activity"} JSON log with counts only; theme
danger/warn tokens + 4.5:1 contrast test; TDD items 7, 8, 10, 11; wire reasoning.capture/think_tags;
CHANGELOG "### Added" bullet for the panel; README note that reasoning may be unfaithful or quote files.
Exact next step: in scratchpad/ui-release, TDD activity_stream.py (PROPOSAL §6 order step 4) against
FakeReasoningToolLlm/FakeActivityToolset, then activity_view + app wiring.

## 2026-09-10 — stage 3 DONE (item A part 2: stream, view, page wiring, docs) on feat/ask-your-docs-activity-panel

Worktree scratchpad/ui-release, nothing pushed, no trailers, tree clean. Test-first (RED seen for each):
- 176e4d66 activity_stream.py (stream_turn: astream messages+updates+values, v2, subgraphs=True, returns the
  root state's messages = what ainvoke returns; invoke_turn = the live:false path). NO asyncio.shield (the
  held serve session owns cancellation). agent.ask(on_event=None, live=True): None keeps the plain ainvoke.
  Fakes: activity_react_graph, nested_vision_graph, FakeRecordingGraph, FakeActivityGraphBuilder; a script
  round {"error": ...} makes FakeReasoningToolLlm raise. Tests: test_activity_stream.py, test_ask_activity.py.
- d2153df3 activity_redaction.py (turn_redactor = bearer + values of api_key_env and OPENAI_API_KEY, >= 8
  chars), activity_trace.turn_activity_record (counts only), theme danger/warn tokens + panel CSS
  (test_theme_contrast.py: >= 4.5:1 on bg and surface, both palettes).
- 277e86b2 activity_view.py (LiveActivityPanel drain/finish/fail + renderers; Stop/rerun = BaseException ->
  saved as stopped; failure after handle.closed -> stopped) and page_turn.py (collect_images, refuse,
  AskTurn, TurnRunners, answer_question moved out of app.py; A1 persistence as ("assistant","") + trace;
  ladder per connection_key; one INFO turn_activity log). app.py 453/500 lines, still the ONE error
  boundary; sidebar: reasoning caption (separate caption, status line unchanged) + "Show technical details"
  toggle (key ayd_technical_details). test_app_activity.py (TDD 7 + 8), test_activity_view.py (stop /
  released), test_cli_parser lazy-import guards (TDD 11). test_app_connection_dialog: the send-failure test
  now expects the caption 'Your question was not answered: "..."' ("(not sent)" stays for refusals).
- 030bf4b9 CHANGELOG [Unreleased] Added + Changed bullets; README + example README (panel, YAML, the
  reasoning-may-be-unfaithful / may-quote-files / repo-file-secrets-undetectable note).
Gates at 030bf4b9: full `pytest tests/ --ignore=tests/test_parity.py` 4499 passed / 3 skipped / 1 xfailed
(coverage flag not run); ruff check + `ruff format --check python/ tests/ benchmarks/` clean; complexipy ok
(snapshot restored); vulture clean; `~/.local/bin/uv lock --check` green; README audit grep clean.

Deviations / left (none blocking):
- ask_your_docs.ui.reasoning.capture and think_tags are still NOT wired into build_chat_model
  (llm_connection.py is 494/500 lines); capture:false only hides reasoning (same as display: hidden).
- Sidebar ladder gets listing_entry=None (ModelListing keeps ids only), so "supported, not seen yet"
  only appears via reasoning.availability: true.
- Citation chips are code spans (no st.popover); editor_link is validated but not rendered; the
  "Writing the answer…" placeholder and the phase-1.1 CLI equivalent / "Show in graph" are not built.
- A real Stop press is covered only by the simulated BaseException test (AppTest cannot press Stop).
Exact next step: owner review of the branch (git log origin/main..feat/ask-your-docs-activity-panel), then
a manual run (`harness-ask-your-docs --workspace ~/pydocs-index` with the example_needle overlay) to eyeball
the panel; push / PR only on the owner's word.
