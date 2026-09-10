
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
