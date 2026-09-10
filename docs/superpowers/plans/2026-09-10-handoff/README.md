# Handoff — 2026-09-10 session (paused: owner low on credits)

Everything needed to continue this session's work from any machine or session. Branches are pushed with draft
PRs; this directory holds the designs, the UI mockup, the OpenRouter runbook and the workflow scripts.

## Shipped (verified by installing from PyPI)

| Release | Commit | What |
|---|---|---|
| pydocs-mcp 0.6.0 | `2a5592a` (#235) | nine-tool surface etc.; `mcp>=1.28.1,<2` cap (mcp 2.x removed `mcp.server.fastmcp`) |
| pydocs-mcp 0.5.2 | `d9d8e05` on `release/0.5.x` (#236) | maintenance: `mcp>=1.28.1,<2` + 0.6.0's security lock bumps |
| pydocs-mcp 0.6.1 | `f9a1535` (#238) | ask-your-docs serve children inherit the shell env (`harness/core/serve_child_env.py`); sealed eval tier; launcher `HARNESS_ASK_YOUR_DOCS_*`; eval floor `>=0.6.0`; tool-contracts §4.1 (26 dirs); #228 |
| pydocs-mcp-eval 0.2.0 | `6ca3a61` (#240) | own `benchmarks/CHANGELOG.md`; `pydocs-mcp>=0.6.0` floor; sdist ships the changelog, no tests; preflight honest about its source-checkout default |

Verified end to end: example_needle indexed with OpenRouter `qwen/qwen3-embedding-4b`, the Streamlit UI answering with
`qwen/qwen3.8-27b` from the PyPI 0.6.1 build with no workaround (`openrouter-example-needle-runbook.md`).

## Owner decisions (binding)

- Recommendation list items: 1 keep `[ask]` out of `[all]`; 2 fold #228 (done); 3 one serve session per chat page;
  4 port the file-watcher default (local commit `a5c748a`); 5 publish pydocs-mcp-eval 0.2.0 (**approved**); 7 Qwen3
  query instruction + paid RepoQA comparison ($5 cap); 8 `benchmarks/CHANGELOG.md`; 9 §4.1 fix (done). Not picked: 6, 10.
- Activity panel (`designs/activity-panel-proposal.md`, mockup `designs/ui-mockup.html`) — **approved**, with
  A1 keep failed/stopped turns in history · A2 usage badge only when the provider sends usage · A3 no answer
  streaming in v1 · A4 cap `langchain-openai<2` + contract test for the two overridden private methods.
- Model parameters: P1 seven params · P2 typed, factory-built `extra_body` phase ratified as a separate later change ·
  P3 rewrite step pins temperature 0 · P4 eval refuses file/env-sourced params · P5 raise the `langchain-openai` floor ·
  P6 tiny paid `max_completion_tokens` check. Plus: **hide** (mask) any parameter the model/provider can't honour;
  keep the dialog simple; must work for OpenRouter, OpenAI, vLLM, LiteLLM and generic OpenAI-compatible servers;
  one **Thinking** control mapped per provider.
- **Params v2 decisions — all recommendations accepted:** D1 raw fields with hints, no presets · D2 drop both penalties
  (five params: Thinking, Temperature, Max output tokens; Top p and Seed under More) · D3 fold a sent-settings
  fingerprint into arm identity · D4 exactly two `extra_body` routes (OpenRouter `reasoning` object; vLLM
  `chat_template_kwargs.enable_thinking` only when `provider: vllm`), shipped as a separate PR after phase 1 · D5 no
  vLLM access available → **hide Thinking Off on vLLM** until someone verifies it · D6 done (below) · D7 hidden saved
  values are not sent, no caption · D8 Thinking "On" = `medium` · D9 `langchain-openai>=0.2.14,<2` · D10 document
  `drop_params` and log one line when LiteLLM is detected.
- **D6 result (paid, ~$0.0006):** OpenRouter honours `max_completion_tokens` even for models whose listing reports only
  `max_tokens` (mistral-nemo, l3-lunaris-8b, qwen3.8-27b: cap 20 → `finish_reason=length`, 20 tokens). So Max output
  tokens is never masked on OpenRouter. `reasoning: {enabled: false}` gave 0 reasoning tokens on qwen3.8-27b, which
  confirms the D4 OpenRouter route.
- **Fresh-install CI:** nightly no-lock install job **and** a pre-publish wheel smoke gate in `release.yml` (in flight).
- **#239:** force-push the agent-rewritten history (approved) once its workflow finishes.
- New pydocs-mcp releases beyond 0.6.1 need the owner's explicit word. Merges of the draft PRs below also need it.
- **The owner tests the UI release first:** once the UI workflow and the light-mode fix are done, run the app from the `feat/ask-your-docs-activity-panel` worktree on port 8512 (`.venv/bin/harness-ask-your-docs --workspace ~/pydocs-openrouter/index --config ~/pydocs-openrouter/config.yaml --port 8512 -- --server.headless true`), give the owner the link and a short checklist (activity panel states, one serve child per page, light and dark via Streamlit's theme menu), and merge nothing until they say OK.

## Tracks in flight

| Track | Branch / PR | State | Next |
|---|---|---|---|
| Eval changelog → eval 0.2.0 (items 8+5) | #240 merged `6ca3a61` | **DONE** — `eval-v0.2.0` published; verified by a fresh PyPI install (`pydocs-mcp-eval[retrieval]==0.2.0` → pydocs-mcp 0.6.1) | — |
| Qwen3 query instruction (item 7) | `feat/embedding-query-instruction` (draft #239, force-pushed after a rebase onto `5461d8e`) | implemented (10 commits), simplified, gates green; **paid RepoQA small_test done (~$0.44 of $5): no measurable gain** (MRR 0.740→0.751, 2 wins / 2 losses / 26 ties, recall@5 and @10 identical; an identical re-run matched the baseline exactly) → the knob ships with default `None` and is not recommended. Opus re-review done (the first reviewers failed on the Fable limit): 2 minor findings fixed (`dfa4bf20`, `0d4bfa44`), full gates green (4324 passed, 97.42%); head `0d4bfa44`, **marked ready for review** | owner review + merge word. Open, owner-gated: content-keyed bench cache (design §8.0), AsyncOpenAI timeout/retries |
| UI release (items 4, 3, activity panel) | `feat/ask-your-docs-activity-panel` (**draft #244**, head `ded9b556`, 17 commits; merges clean with `5461d8e`) | **implemented, reviewed, gates green** — 3 review lenses (retried after Fable-limit failures): 12 findings, 11 fixed (`434b8b99`), simplify `ded9b556`; 4510 passed, 97.36%; real OpenRouter e2e: one serve child per page (was 22 spawns), key never shown, $0.0124; each e2e turn reported "1 failed" tool step (under investigation) | light-mode workflow `ayd-theme-native-fix` running on this branch (run `wf_c155c8dd-b2a`) → push → owner test on :8512. **Owner decision S1:** `streamlit>=1.43` → `>=1.59` (needed for `cache_resource(scope="session", on_release=...)`). DC-3 (listing-metadata reasoning caption) deferred + documented |
| Model parameters v2 (design) | none | **design done, decisions accepted** (see Owner decisions) — `designs/model-params-v2-proposal.md` + `-mockup-spec.json` (8 states) | implement per its §9 on a new branch **after the UI release merges** (both touch the connection dialog and `llm` wiring); D4 routes as a follow-up PR; refresh the mockup |
| Fresh-install CI | `ci/fresh-install-gate` (draft #243, head `fa8e1e1`) | **CI all green**, incl. the new `Fresh install` job on Linux 3.11 + 3.13 (unpinned resolve: mcp 1.30.0, langchain-openai 1.1.9, streamlit 1.63.0, openai 1.109.1) | owner's word to merge; `release.yml` `smoke-wheel` is first exercised by the next `v*` tag |
| Light-mode theme bug (owner report) | goes on `feat/ask-your-docs-activity-panel` after the UI workflow ends | reproduced on 0.6.1: chat text ~1.1:1 contrast, black inline-code chips, dark code-block syntax, dark dropdown/radio bits | root cause: launcher pins Streamlit's native theme to dark (`theme.streamlit_theme_flags`), the Light toggle only swaps the partial CSS overlay `theme_css()`; preferred fix (verified on Streamlit 1.63): launch with native `[theme.light]` + `[theme.dark]` (+ `.sidebar`) palettes instead of pinning dark, so Streamlit colors every native element; custom CSS follows `st.context.theme.type` and only adds accents; the in-app toggle can't switch Streamlit's theme (no API) → **owner chose Streamlit's own theme menu, shipped with the UI release**; run `workflows/ayd-theme-native-fix.js` on that branch after the UI workflow; keep a ≥4.5:1 contrast test for both palettes and verify with screenshots in both modes |
| get_symbol target resolution | `feat/get-symbol-resolution` (worktree `<scratch>/symbol-resolve`, local only) | design workflow running (`wf_304908cc-3d5`: 5 readers → 3 designs → judge → 2 critics → committed spec) | implement test-first per the spec → review → simplify → gates; push/PR on the owner's word. Contract hazard: §2.3 lists only three suggestion-emitting tools, so a `get_symbol` `meta.suggestion` would be an owner-gated amendment |
| Queued | — | — | keyless OpenAI-compatible embedding endpoints (`OpenAIEmbedder` requires its key env var even for a keyless vLLM) |

## How to resume

- **Same Claude session:** re-invoke `Workflow({scriptPath, resumeFromRunId})`; finished agents replay from cache.
  Run ids: `wf_33bb79c1-4dc` (eval changelog), `wf_57ed9d3c-064` (query instruction), `wf_4ab1322a-f58` (UI release),
  `wf_de434927-ec4` (params v2).
- **New session:** cache resume is unavailable. Copy a script from `workflows/`, change its scratch-directory constant
  `S` to a fresh directory, delete the stages already finished (see the track table and each branch's commits), and run it.
  Each remaining agent appends a dated entry to `~/pydocs-handoffs/2026-09-10/HANDOFF-<track>.md` on the owner's machine;
  the summaries are also synced into this PR as the work completes.
- Repo rules that bit this session: commits authored by the owner with no trailers; run the full `ci.yml` gate set before
  pushing; restore `complexipy-snapshot.json` before staging; relock only with `~/.local/bin/uv` (prefer hand-editing a
  single `uv.lock` specifier line); the Bash tool runs zsh (`$f:uv.lock` applies the `:u` modifier; `set -- $n` does not
  word-split); fresh PyPI uploads can lag in the simple index for a few minutes.

## Files

- `designs/activity-panel-proposal.md`, `-mockup-spec.json`, `-research-{audit,streaming,reasoning}.md` — approved activity panel.
- `designs/model-params-v1-proposal.md`, `-mockup-spec.json` — first params design (superseded by v2 rules above).
- `designs/model-params-v2-proposal.md`, `-mockup-spec.json` — **v2 design** (Thinking + Temperature + Max output tokens; Top p and Seed under More; unsupported controls hidden; five provider profiles).
- `handoffs/HANDOFF-*.md` — mirror of the per-track notes the workflow agents append on the owner's machine.
- `designs/serve-child-env-0.6.1-design.md` — the shipped 0.6.1 fix, for reference.
- `designs/ui-mockup.html` — clickable mockup of both UI proposals.
- `openrouter-example-needle-runbook.md` — how the OpenRouter test was set up (local paths are the owner's machine).
- `workflows/*.js` — the four workflow scripts as last run, plus `ayd-theme-native-fix.js` (ready to run for the light-mode fix).
