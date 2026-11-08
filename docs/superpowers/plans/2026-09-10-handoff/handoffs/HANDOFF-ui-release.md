
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

## 2026-09-10 — review findings addressed (434b8b99) on feat/ask-your-docs-activity-panel

Worktree scratchpad/ui-release, nothing pushed, no trailers, tree clean. One commit 434b8b99
"fix(ask-your-docs): address review findings", test-first (RED seen for every fix):
- SEC-1 activity_trace_builder.py: `_cut_short` set; stop()/fail() keep `_whole_words` on the open
  thinking step (test_activity_trace: test_a_turn_cut_short_mid_word_never_shows_the_half_arrived_word).
- LEAK-1 page_agent.py: `_refuse_if_closed()` before PageServeSession() and after the graph build
  (test_page_agent: test_a_release_during_a_restart_spawns_no_orphan_child, close_delay_s=0.5).
- XSS-1 activity_view.render_turn_footer: st.error(plain_markdown(failure)) (test_activity_view).
- DC-1 `capture: false` -> build_chat_model(capture_reasoning=False) = stock ChatOpenAI (agent.py passes
  cfg.ui.reasoning.capture); `think_tags` REMOVED from ReasoningUiConfig + default_config.yaml
  (ThinkTagSplitter kept, docstring says not wired).
- DC-2 `editor_link` REMOVED from ActivityUiConfig + YAML (popover not built); extra=forbid rejects both.
- DC-4 new reasoning_caption.py (ReasoningCaption slot = st.empty() in the sidebar; finish_turn(panel,
  answer, caption) calls caption.observe) — the extra at.run() dropped from test_app_activity.
- DC-5 activity_trace.writing_the_answer() + LiveActivityPanel._writing slot ("Writing the answer…").
- C1 _render_live_tool; S1 pyproject comment only (floor 1.59 kept — needs owner OK, see below).
- T1 test_serve_session importorskip moved into the pid_log fixture (2 classification tests run core-only).
- T2 _serve_session_fakes.running_page_loop(): close pages, cancel+gather leftovers, join, loop.close().
- CHANGELOG [Unreleased] Added bullet updated (writing line, caption, capture:false, inert errors).
Gates at 434b8b99: full pytest --cov 4510 passed/3 skipped/1 xfailed, 97.36%; ruff format/check clean;
mypy clean; complexipy ok (snapshot restored); vulture clean; uv lock --check green. test_page_agent +
test_serve_session pass under -W error::PytestUnraisableExceptionWarning -W error::ResourceWarning.

Left (not done):
- DC-3 deferred: listing supported_parameters rung not consulted (ModelListing keeps ids; wiring it would
  add a listing fetch on page load). Recorded in CHANGELOG + reasoning_caption.py comment.
- S1: streamlit>=1.59 floor needs the owner's explicit OK (consequence of item 3).
- A few `_hold` GC warnings remain in the FULL suite, originating from the AppTest page modules (app.py's
  process-global event_loop is never closed) — pre-existing, not from the page-agent fixtures.
- think_tags / editor_link popover / supported_parameters rung = v1.1 candidates.
Exact next step: owner review of `git log origin/main..feat/ask-your-docs-activity-panel`, decide S1,
then a manual run; push / PR only on the owner's word.

## 2026-09-10 — simplify + clean-architecture pass DONE (ded9b556) on feat/ask-your-docs-activity-panel

Worktree scratchpad/ui-release, nothing pushed, no trailers, tree clean. Items (4), (3), (A) and the review
fixes were already committed (dceb0bd0 .. 434b8b99); this run added ONE commit, ded9b556
"refactor(ask-your-docs): simplify + clean-architecture pass" (Skill simplify, single pass without the Agent
fan-out, + python-clean-architecture check-quality). Behavior-identical; 8 files under harness/ask_your_docs/:
activity_redaction (_DEFAULT_API_KEY_ENV single source replaces the "OPENAI_API_KEY" literal), activity_events
(_reasoning_of / _message_events shared, nested ternary removed), activity_trace (TurnTrace.file_count,
writing_the_answer in __all__), activity_trace_builder (the two call-id dicts merged), activity_view (PanelItem /
PanelSink types drop a type: ignore, _render_tool_summary shared, _count -> _token_count), page_agent
(_close_logging_failure shared), page_turn (collect_images typed ImagesConfig, clear names), activity_outcomes.
Gates at ded9b556: full `pytest tests/ --ignore=tests/test_parity.py` 4510 passed / 3 skipped / 1 xfailed;
ask-your-docs suite 603 passed; ruff check + format clean; vulture clean; complexipy ok (snapshot restored).
Budgets tight: activity_view.py 398/400, activity_trace_builder.py 297/300.
Declined (not safe or not worth it): LiveActivityPanel.fail(released=) flag split; NoteStep.kind StrEnum;
broad excepts at the documented degrade/never-raise boundaries; isinstance step rendering; a _UsageTally
extraction (would break the builder's 300-line budget); the teaser -> clip_label_text reuse (differs at exactly
61 chars).
Still open from earlier: DC-3 listing rung, S1 streamlit>=1.59 floor needs the owner's OK.
Exact next step: owner review of `git log origin/main..feat/ask-your-docs-activity-panel`, decide S1, manual run;
push / PR only on the owner's word.

## 2026-09-10 — FINAL GATES + real-OpenRouter E2E DONE on feat/ask-your-docs-activity-panel (HEAD ded9b556)

No new commits this run (verification only). 17 commits origin/main..HEAD (dceb0bd0 .. ded9b556), all authored
msobroza, zero trailers, tree clean. Branch base 6ca3a613; origin/main is now 5461d8e (#242) — `git merge-tree`
with origin/main is CLEAN (no rebase done; none needed to open a PR).
Gates (worktree .venv, py3.11 aarch64): uv lock --check OK; ruff check + format --check OK (1296 files); mypy OK
(272 files); complexipy OK (snapshot restored); vulture OK; smoke_check_benchmark_imports OK; pytest tests/
--cov 4510 passed / 3 skipped / 1 xfailed, 97.36%; cargo fmt/clippy/test OK (13 passed); pip-audit (requirement
mode SIGABRTs under sandbox -> ran scratchpad/audit-venv pip-audit --disable-pip --no-deps on the same uv export,
CI's two ignores) "No known vulnerabilities found, 2 ignored"; README audit grep clean.
benchmarks/tests (local-only gate): 2150 passed / 22 failed — ALL environment (worktree venv lacks the eval
package deps unidiff/rapidfuzz/gepa/seaborn; subprocess import-isolation tests don't see the --target overlay);
branch touches nothing under benchmarks/ or scripts/. Re-run in a venv with benchmarks' deps to confirm green.
Core-only venv (scratchpad/core-venv-ui, editable to ui-release, no streamlit/langgraph/langchain): cli import
loads none of streamlit/langgraph/httpx/langchain_*; serve_spawn/serve_session/activity_*/page_agent import
core-only; harness.core loads no langchain; `harness-ask-your-docs --help` prints the install hint (rc 0);
ask-your-docs tests core-only 354 passed / 47 skipped.
E2E (scratchpad/ui_e2e.py, AppTest on the real app.py, OpenRouter qwen/qwen3.8-27b, ~/pydocs-openrouter):
question asked twice in one session -> both answers name MaxSimScorer; each turn 5 tool steps + 5 thinking
steps, reasoning "shown"; serve opener entered ONCE, one serve child pid across both questions, 1 "MCP ready"
line (old ui.log: 22); zero children after close_all_page_agents; key absent from all 67 elements + traces +
stderr; cost $0.0124. Observation: each status label says "1 failed" (one tool call per turn errored and the
agent recovered) — worth a look during review, not blocking.
Still open (owner): S1 streamlit>=1.59 floor OK; DC-3 listing rung deferred (v1.1).
Exact next step: owner review of `git log origin/main..feat/ask-your-docs-activity-panel`, decide S1, then push +
PR only on the owner's word.

## 2026-09-10 — Light-mode readability fix DONE on feat/ask-your-docs-activity-panel (HEAD 17643add)

Owner bug (0.6.1): in Light mode, chat text ~1.1:1, code chips and syntax highlighting stayed dark, sidebar
dropdowns and radios stayed dark. Root cause: streamlit_theme_flags() pinned the native theme to dark and the in-app
toggle only swapped a partial CSS overlay. Four commits, test-first, no trailers, author msobroza:
- 32f2049d — streamlit_theme_flags() emits [theme.light] + [theme.dark] (page: primary/bg/secondaryBg/text/
  link/codeText/codeBackground/border; sidebar: bg/secondaryBg) from THEMES; no --theme.base, no top-level
  --theme.*. Light accent #0B7A66 -> #096B5A (it was 3.95:1 on its wash chip). RED 6 failed -> GREEN 45 passed.
  Headless `streamlit run` with the flags on 1.59.1: health ok, no errors.
- 160f896c — render_appearance_toggle + ui_light/ui_light_widget keys + app.py / pages/2_Graph.py call sites
  removed; palette_for_theme_type() + current_palette() follow st.context.theme.type (dark when None);
  #MainMenu no longer hidden (deploy/status/toolbarActions/decoration/footer still hidden). RED: collection
  ImportError + test_the_theme_menu_stays_reachable -> GREEN (AppTest: neither page renders a toggle).
- 0f4c3bd4 — theme_css keeps brand/accent/bubble/border/danger/warn only; no bg/surface/recessed/text/muted
  hex, no widget re-theming. Muted = MUTED_TEXT_OPACITY (.72) over native text; user bubble = translucent
  neutral lift. Graph legend drops inline text colours (deviation: outside theme_css, same bug class).
  RED 12 failed -> GREEN.
- 17643add — examples README (switch via top-right menu -> System / Light / Dark) + CHANGELOG [Unreleased] Fixed.
Gates: tests/harness 768 passed / 2 skipped (baseline 709); ruff check+format, mypy (272), vulture, complexipy
OK (snapshot restored); README audit grep clean. Full `pytest tests/` NOT re-run this pass.
Deviations: venv/lock is Streamlit 1.59.1, not 1.63 — verified there that both theme sections + nested sidebar
flags exist and that the main menu shows the System/Light/Dark radio inline (no separate Settings dialog), shown
only when >1 theme is available. The agraph canvas colours (inside its iframe) still use current_palette(), so
they can lag one rerun after a theme switch.
Left: manual browser check of both themes + the switch; full `pytest tests/` + CI gate set before push.
Exact next step: owner runs `harness-ask-your-docs`, flips top-right menu -> Light / Dark, eyeballs chat, code,
sidebar, graph page; then push + PR only on the owner's word.

## 2026-09-10 (late) — theme review + visual check (branch feat/ask-your-docs-activity-panel)
Review of 32f2049d..17643add: correct; no toggle leftovers (only historical docs/superpowers plans mention it);
theme_css paints no text/ground colours; lazy streamlit import kept; theme.py ~210 lines, app.py 445.
Visual check DONE in a real browser (Streamlit 1.59.1, port 8511, OpenRouter qwen3.8-27b, one question asked):
- Light: chat text #17242F on #F4F6F8; inline code #096B5A on #E9EEF2; code block light ground + light syntax
  tokens; sidebar #FFFFFF with #17242F labels/radios. Dark: text #DEE4EA on #0E141B; inline code #34D3B7 on
  #0A0F14; code block dark; sidebar #161E27 with #DEE4EA labels/radios. Main menu shows System/Light/Dark.
- CONFIRMED BUG found + fixed: a menu switch does not rerun the script, so theme_css(current_palette()) kept the
  LIGHT accent #096B5A (~2.9:1) on the dark canvas for the brand "docs" + active nav until the next rerun.
  Fix 8011ecb9 (test-first: RED 29 failed -> GREEN): theme_css() takes no palette and emits accent/wash/border/
  danger/warn as CSS light-dark(<light>, <dark>) (Streamlit sets color-scheme on .stApp + sidebar per theme);
  current_palette() now only feeds the graph canvas iframe. Re-verified in the browser: Dark->Light and
  Light->Dark flip the brand/nav accent instantly with no rerun, on the chat and graph pages.
Gates after 8011ecb9: tests/harness 778 passed / 2 skipped; ruff check+format, mypy (272), vulture, complexipy OK
(snapshot restored). No push, no PR, no trailers. Server stopped.
Observation (Streamlit-native, not ours): the menu choice did not always survive a full page navigation in the
preview browser (came back Light once) — consistent and readable either way.
Left: graph canvas node/edge colours (iframe) still lag one rerun after a switch (documented); full
`pytest tests/` + full CI gate set (coverage, uv lock --check, pip-audit) not run this pass.
Exact next step: run the full CI gate set from CLAUDE.md in the ui-release worktree; then push + PR only on the
owner's word.

## 2026-09-10 (final) — full CI gate set GREEN on feat/ask-your-docs-activity-panel (HEAD 8011ecb9)
No new commits this pass: the Light-mode fix was already complete (32f2049d, 160f896c, 0f4c3bd4, 17643add,
8011ecb9 — in-app toggle removed, both native palettes via [theme.light]/[theme.dark](+.sidebar) flags, #MainMenu
reachable, theme_css paints no text/ground colours and uses light-dark() for accents). 22 commits
origin/main..HEAD, all msobroza, 0 Co-Authored-By trailers, tree clean.
Re-verified: all 20 streamlit_theme_flags() keys are valid config options on the venv's Streamlit 1.59.1;
client.toolbarMode left at its default (auto), so the main menu keeps its System/Light/Dark picker; no
toggle leftovers outside tests/CHANGELOG/docstrings; README audit grep clean.
Gates (worktree .venv, HOME=mktemp): ruff format --check OK (1300 files); ruff check OK (python/ tests/ benchmarks/
scripts/); mypy OK (272); complexipy OK (snapshot restored); vulture OK; uv lock --check OK; pytest tests/
--ignore=tests/test_parity.py --cov: 4579 passed / 3 skipped / 1 xfailed, coverage 97.36%.
Not run this pass: cargo checks, pip-audit, benchmarks/tests (branch touches none of that; last pass green/env-only).
Left (owner): manual eyeball of both themes on 1.63 if desired; S1 streamlit floor; DC-3 listing rung (v1.1).
Exact next step: owner review of `git log origin/main..feat/ask-your-docs-activity-panel`; push + PR only on the
owner's word.

## 2026-09-10 (icons) — a Material icon per tool in the activity panel (48a08abf)
Owner request: "a different icon for each tool that I call". One commit on feat/ask-your-docs-activity-panel
(no push, no PR, no trailers). Line format: "<icon> <status glyph> <label><em-space><duration · outcome>",
e.g. ":material/search: ✓ Searched all code for "routing"  0.4 s · 2 matches in 2 files". Icon-first because every
step kind (tool / thinking / vision) then starts with its icon (one column of step types), the glyph stays next to
the sentence whose tense it matches, and a markdown line never starts with model text (no "1."/"-" list injection).
- TOOL_ICONS (+ THINKING_ICON psychology, VISION_ICON image, UNKNOWN_TOOL_ICON build, NOTE_ICONS {vision}) live in
  activity_labels.py; reinspect_images shares VISION_ICON. Parity test is driven by
  description_source.FROZEN_TOOL_NAMES (a tenth tool fails until it gets a distinct icon); every icon passes
  streamlit.string_util.is_material_icon + validate_material_icon (ALL_MATERIAL_ICONS, 1.59.1).
- New activity_markdown.py (budget 200; 77 lines): plain_markdown (moved from activity_view), iconed_markdown,
  tool_step_markdown, thinking_teaser_markdown. SECURITY finding: Streamlit 1.59's StreamlitMarkdown does
  replaceAll(":material/", ":material_") and emoji ":name:" detection on the RAW string before backslash
  escapes, so "\:" does NOT defuse; plain_markdown now inserts U+200B after a shortcode-opening colon
  (Streamlit's own validate_material_icon trick). This also closes the same pre-existing gap in status labels.
- Tool lines, the vision note, the live thinking line/caption and the thinking expander label moved from st.text to
  st.markdown (only markdown shows icons); other notes (rephrase/scope/narration) stay st.text. The label/outcome
  gap is an em space because markdown collapses the old three ASCII spaces. activity_view.py 398 -> 388 lines.
- RED: labels module ImportError (THINKING_ICON) + 5 failed (view live/final x2, escape unit, page done+rerun,
  page hostile-arg). GREEN: 19 icon tests pass; tests/harness 798 passed / 2 skipped; ruff check+format OK;
  mypy OK; complexipy OK (snapshot restored); vulture OK; README audit grep clean.
- Visually verified on a scratch Streamlit 1.59.1 page (browser): all 12 icons render as monochrome glyphs;
  ":material/bolt:", ":smile:", "**x**" from args render literally. CHANGELOG [Unreleased] Added + example README
  panel paragraph each gained one clause.
Not run this pass: full pytest tests/ + coverage gate, uv lock/pip-audit (branch touches no deps).
Exact next step: owner eyeball of the icons in the real page (both themes); push + PR only on the owner's word.

## 2026-09-10 — REVIEW of 48a08abf (Material icon per tool): injection / correctness / tests
Method: code read of the diff + Streamlit 1.59.1 frontend bundle (StreamlitMarkdown.CwaSe4JX.js) + a live render of
real tool_step_label/iconed_markdown/plain_markdown output on a scratch page (probe in scratchpad/icon-probe/, server stopped).
Verified findings:
- MAJOR (icon/image injection survives): plain_markdown does not escape "&", and micromark decodes HTML named entities
  in text nodes AFTER Streamlit's raw-string checks. Query "A &colon;material&lowbar;bolt&colon; B &colon;streamlit&colon; C"
  rendered a bolt ICON in the tool line, the thinking expander label and st.error, and the Streamlit LOGO <img> in
  the thinking label + st.error. Fix: add "&" to _MARKDOWN_SPECIALS ("\&" is a CommonMark escape) + regression test.
- MAJOR (new clickable links): tool lines, vision note and finished live-thinking line moved st.text -> st.markdown, and
  remark-gfm literal autolinks are on, so "https://evil.example/x" / "www.evil.example" / emails in args or reasoning
  render as <a target=_blank>. Rendered live in the tool line, thinking label and st.error (latter two pre-existing).
  Fix: defuse autolink literals in plain_markdown (escape "@", break "://" and "www." e.g. with U+200B) + tests.
- MINOR (not "as typed"): the U+200B lookahead ":(?=[\w+/-]+:)" runs AFTER escaping, so "\_" blocks it; model text
  ":material/thumb_up:" displays as ":material_thumb_up:" (Streamlit's raw replaceAll). No icon rendered (escaped
  "_" splits the text node). Fix: defuse shortcodes on the raw text before escaping, or allow "\\" in the lookahead.
OK: icon order/prefix, FROZEN_TOOL_NAMES parity (true contract single source), unknown->build, reinspect_images->image,
thinking expander + live caption share the helper; citation chips are code spans (unchanged).
Next: fix the two MAJORs (+ tests) before the owner eyeball; push/PR only on the owner's word.

## 2026-09-10 23:25 — UX/visual review of the per-tool icons (48a08ab), review only, no commits
Real page (port 8511, qwen3.6-27b via OpenRouter, ONE question) in the Browser pane, light AND dark (Streamlit
menu theme switch, restored to System afterwards); scratch probe page on 8512 for hostile labels + all 12 icons x 3 states.
- VISUAL PASS: psychology / manage_search / search render on real thinking + grep + search lines and on the thinking
  expander label; icons are 16px, top-aligned with their line, and take the text colour in both themes
  (light rgb(23,36,47) = text; dark rgb(222,228,234) = text on rgb(14,20,27)). All 12 icons distinct on the probe;
  ✗ failed / ● running lines read clearly (probe, light only — the real run had no failed step and the live
  status box was collapsed while running).
- FINDING (major): Streamlit text directives are NOT defused — ":red[x]" colours the line (rgb(189,64,67)),
  ":blue-background[x]" / ":violet-badge[x]" tint it, ":small[x]" shrinks it to 14px, ":rainbow[x]" gradients it,
  on tool lines AND the thinking expander label. _SHORTCODE_START only matches ":name:" forms. Fix: also defuse
  ":" before "<name>\[" (post-escape), + regression test.
- FINDING (major): GFM autolinks — a bare https://…, www.… or name@host in a tool arg becomes a clickable <a>
  (verified hrefs https://evil.example/login, http://www.evil.example/, mailto:admin@evil.example). New on tool lines
  (were st.text). Fix: break "://", "www." and "@" with U+200B in plain_markdown, + regression test.
- FINDING (minor): the ZWSP rule also fires on ordinary colon text (std::string::npos, 10:30:00), so copying a
  grep pattern out of the panel yields an invisible U+200B.
Servers on 8511/8512 stopped. Next: fix the two majors (+ tests) before the owner eyeball; push/PR only on owner's word.

## 2026-09-10 23:40 — tool-icon review fixes committed (07525986 on feat/ask-your-docs-activity-panel, NOT pushed)
All 6 findings re-verified at string level before fixing (all reproduced); fixed test-first in activity_markdown.plain_markdown
(the single choke point, so the pre-existing expander / st.status / st.error / caption sites are covered too):
- MAJOR entity: "&" added to _MARKDOWN_SPECIALS ("\&" CommonMark escape) -> &colon;/&lowbar; never decoded.
- MAJOR autolinks (2 duplicate findings): U+200B after "://" colon, between "www" and "." (case-insensitive), before "@".
- MAJOR directives: U+200B after a colon opening ":name[" (":red[", ":blue-background[", ":small[", badges, rainbow).
- MINOR underscore shortcode: the single _INERT_TRIGGER regex now runs on RAW text before escaping -> ":material/thumb_up:"
  keeps its "/" (renders as typed).
- MINOR colon over-defusing: kept deliberately (Streamlit's emoji name set lives only in its JS bundle) — WHY comment +
  pin test (std::string::npos, 10:30:00, key:value:other get U+200B; "a: b" untouched).
Tests: new tests/harness/ask_your_docs/test_activity_markdown.py (entity/autolink/directive/underscore/colon pins + thinking
teaser); test_activity_view.py gains a parametrized AppTest hostile-arg+reasoning page test (entity, https, www, email,
:red[x]) and underscore cases; the failure-caption expectation now carries "http:​//h" (bare URL would autolink).
Gates: harness suite 671 passed; ruff check/format, mypy, complexipy, vulture green; snapshot restored before staging.
Live re-check (scratch probe2.py on port 8513, Streamlit 1.59.1, server stopped): only trusted search/psychology icons,
0 Streamlit logos, 0 <a>, 0 coloured/badge spans across tool line, thinking expander label and st.error.
Icon order unchanged: "<icon> <status glyph> <label>" (icon first keeps the line from ever starting with model text).
Next: owner eyeball; push/PR only on the owner's word.

## 2026-09-10 — tool icons: full CI gate re-verification (no code change)
- Icon feature 48a08abf + review fixes 07525986 on feat/ask-your-docs-activity-panel (draft PR #244), unpushed.
- Full ci.yml set re-run green: ruff format/check, mypy (272 files), complexipy 15 (snapshot restored), vulture 80, pytest 4624 passed / 3 skipped / 1 xfailed, cov 97.36%, uv lock --check, README audit clean.
- Verdict GO for the owner's push/PR-update word; nothing pushed.

## 2026-09-11 — merged origin/main (8c90bd55) into feat/ask-your-docs-activity-panel: 9b100f55 (NOT pushed)
- Merge commit 9b100f55 (parents 07525986 + 8c90bd55), --no-ff, no rebase, no trailers; PR #244 will fast-forward on push.
- Branch base was 6ca3a613 (#240), so the merge also brings #242 (lock advisories) as well as #245 + #225.
- Only textual conflict: CHANGELOG.md. It is now one [Unreleased] section: main's headline, then Added (main, branch),
  Changed (main, branch), Deprecated (main), Fixed (branch). Released sections byte-identical to origin/main.
- README.md, default_config.yaml and pyproject.toml auto-merged. The only differences from main are the branch's own
  (README activity-panel paragraph, ask_your_docs.ui block, two harness pins). CLAUDE.md, DOCUMENTATION.md and
  tests/test_pyproject_extras.py match main exactly. README jargon audit clean.
- uv.lock: started from origin/main's lock, then ~/.local/bin/uv lock. The diff against main is exactly the 2 requires-dist lines
  (langchain-openai >=0.2,<2; streamlit >=1.59). uv lock --check passes.
- Venv rebuilt (cpython-3.11 aarch64), re-synced --frozen --all-extras --inexact (tree-sitter-java, pylate 1.6, etc.).
- Tests (HOME=mktemp): harness + pyproject_extras + config/default_config/watch suites 1010 passed / 2 skipped;
  analyzer + multilang treesitter suites 166 passed. Full ci.yml gate set NOT re-run after the merge.
- Next: full CI gate re-run on the merged tree, then push only on the owner's word.
