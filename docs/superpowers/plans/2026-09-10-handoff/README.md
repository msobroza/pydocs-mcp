# Handoff — 2026-09-10 session (paused: owner low on credits)

Everything needed to continue this session's work from any machine or session. Branches are pushed with draft
PRs; this directory holds the designs, the UI mockup, the OpenRouter runbook and the workflow scripts.

## Shipped (verified by installing from PyPI)

| Release | Commit | What |
|---|---|---|
| pydocs-mcp 0.6.0 | `2a5592a` (#235) | nine-tool surface etc.; `mcp>=1.28.1,<2` cap (mcp 2.x removed `mcp.server.fastmcp`) |
| pydocs-mcp 0.5.2 | `d9d8e05` on `release/0.5.x` (#236) | maintenance: `mcp>=1.28.1,<2` + 0.6.0's security lock bumps |
| pydocs-mcp 0.6.1 | `f9a1535` (#238) | ask-your-docs serve children inherit the shell env (`harness/core/serve_child_env.py`); sealed eval tier; launcher `HARNESS_ASK_YOUR_DOCS_*`; eval floor `>=0.6.0`; tool-contracts §4.1 (26 dirs); #228 |

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
- New pydocs-mcp releases beyond 0.6.1 need the owner's explicit word. Merges of the draft PRs below also need it.

## Tracks in flight

| Track | Branch / PR | State | Next |
|---|---|---|---|
| Eval changelog → eval 0.2.0 (items 8+5) | `docs/eval-changelog-020` (draft #240) | 6 commits, eval suite green; review + gates were running | rebase onto `main` (#237 also edited `CLAUDE.md`), finish review/gates, merge, tag `eval-v0.2.0`, verify PyPI (retry `uv pip install --no-cache` if the simple index lags) |
| Qwen3 query instruction (item 7) | `feat/embedding-query-instruction` (draft #239) | `4b006c9`, `ff899ce` + WIP `d1e7e0b` (unreviewed) | finish per the final design (`embedding.query_prefix`), review, simplify, RepoQA benchmark — the bench index cache does not hit on RepoQA, so budget the re-embed — gates |
| UI release (items 4, 3, activity panel) | `feat/ask-your-docs-activity-panel` (created by the workflow once implementation starts) | item-3 design done | implement per `workflows/ayd-ui-release-*.js`: file watcher → prep refactors → one serve session per page → activity panel |
| Model parameters v2 (design) | none | **design done** — `designs/model-params-v2-proposal.md` + `-mockup-spec.json` (8 states) | owner decisions D0–D10 in its §10 (notably D1 drop presets, D2 drop the two penalties = narrows P1's seven params, D3 fold a sent-settings fingerprint into arm identity); then implement per its §9 on a new branch; refresh the mockup |
| Queued | — | — | keyless OpenAI-compatible embedding endpoints (`OpenAIEmbedder` requires its key env var even for a keyless vLLM); owner undecided on a fresh-install (no-lock) CI job |

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
- `workflows/*.js` — the four workflow scripts as last run.
