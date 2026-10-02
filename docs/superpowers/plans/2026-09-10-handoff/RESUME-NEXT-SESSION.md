# Resume here — pydocs-mcp work paused 2026-09-11 (owner out of credits)

All workflows were stopped cleanly. Every finished stage is committed. Interrupted stages were saved as local
`wip:` commits: verify their tests before building on them, or `git reset --soft HEAD~1` and redo the stage.
Nothing below is merged. Nothing is pushed unless marked **pushed**.

## Set up a new session

- **Worktrees.** The old scratch worktrees lived in a `/private/tmp` scratchpad that gets wiped on restart. The branch refs are safe in the main repo `.git`. Recreate what you need anywhere:
  `git -C /Users/msobroza/Projects/pyctx7-mcp worktree prune && git -C /Users/msobroza/Projects/pyctx7-mcp worktree add <dir> <branch>`
- **Venvs.**
  - Core: `~/.local/bin/uv sync --frozen --group dev --python cpython-3.11-macos-aarch64-none`.
  - UI, model-params and bench work: add `--all-extras`.
  - Benchmarks tests also need `uv pip install unidiff rapidfuzz matplotlib seaborn gepa==0.1.4`.
- **Workflow scripts.** They are in PR #241, under `docs/superpowers/plans/2026-09-10-handoff/workflows/`. In a new session there is no cache resume, so edit each script's scratch constant `S` and delete the stages that are already done.
- **Stage plans and agent reports.** They are in the old session's journals, which live under `~` and survive restarts:
  `~/.claude/projects/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/subagents/workflows/<run-id>/journal.jsonl`. Each one has a `{"type":"result"}` line per finished agent. The first result is the plan.
- **Rules.**
  - Commits are authored by msobroza only, with no Co-Authored-By trailers.
  - Don't push or open PRs without the owner's word.
  - Publish releases only on the owner's explicit word.
  - Relock only with `~/.local/bin/uv`.
  - Restore `complexipy-snapshot.json` before staging.
  - Never print `OPENROUTER_API_KEY` (it's in the repo `.env`).
  - Run the full CLAUDE.md "Tests & Lint" gate set before any push.
- **Main moved.** `main` is now `8c90bd55`: #245, and #225, the multilanguage reference graph with its contract §2.2/§3.5/§4.1/§5.1 amendments. Bring PR branches up to date with a **merge commit**, not a rebase plus force-push.

## Tracks

| # | Track | Branch @ head | State | Next step |
|---|---|---|---|---|
| 1 | Fresh-install CI | `ci/fresh-install-gate` @ `e2958a8c` (**pushed**, #243) | main merged in; GitHub CI all green, incl. Fresh install 3.11 + 3.13; mergeable | owner merges |
| 2 | UI release (activity panel, one serve session per page, light/dark fix, tool icons + markdown hardening) | `feat/ask-your-docs-activity-panel` @ `9b100f55` (local merge of main; #244 is at `07525986` **pushed**) | merge commit made; its verification was stopped | hunk-check the merge against both parents (`5461d8e`→`07525986` and `5461d8e`→`8c90bd55`), run the full gates, fast-forward push to #244, watch CI. Script: `workflows/ui-release-merge-main.js` (verify stage) |
| 3 | Model settings v2 (Thinking / Temperature / Max output tokens, Top p / Seed under More; hidden per provider) | `feat/ask-your-docs-model-params` @ `76493110` (local; stacked on `07525986`) | stages S1–S4 committed (`307be872`…`0e5563d0`); **S5 interrupted → WIP `76493110`** | finish S5 per plan (journal `wf_86554322-722`); review (design / providers / security-UX), fix, simplify, gates, live check ≤ $0.05; merge the updated #244 (`9b100f55`) in; then the **owner tests on port 8512**, running from this branch |
| 4 | get_symbol target resolution (`src.` strip, unique bare name, closest-name hints) | `feat/get-symbol-resolution` @ `6aedaa7e` (local) | all 5 stages committed (`714f8eb4`…`6aedaa7e`); review was mid-run (one minor finding so far) | 3-lens review, fix, simplify, gates, free live replay of the spec §3 table; record owner OD-1 = (a) in the spec; merge main in (merge-tree clean); then ask the owner to push/PR. Script: `workflows/get-symbol-resolution-impl.js` |
| 5 | Member module ids root cause (OD-2) | `fix/member-module-ids` @ `4c9f4b81` (local) | spec `272e43af`; S1 `d7caf32f` (members follow the package-root rule), S2 `4c9f4b81` (one project re-read on upgrade) | remaining stages per plan (journal `wf_13633072-9af`); OD-B as the LAST, self-contained commit; review, gates, free live upgrade check |
| 6 | Tool-surface bug fixes (refs on modules, class source, grep glob, overview) | `fix/tool-surface-bugs` @ `b962bc98` (local, on main `8c90bd55`; spec `73875867`) | plan done (6 stages; journal `wf_4f0e9f6c-1b7`); **S1 interrupted → WIP `b962bc98`** | re-verify spec facts that touch #225's files first; then S1–S6, review, gates, free replay. Script: `workflows/tool-surface-bugs-impl.js` |
| 7 | Qwen3 code-instruction arm (on #239) | `feat/embedding-query-instruction` @ `7e129ded` (local, 2 ahead of **pushed** `0d4bfa44`) | pre-registered `61311e29` + audit fix `7e129ded`; **paid run interrupted** (partial evidence in `~/pydocs-handoffs/2026-09-10/qcode-evidence/`) | check OpenRouter usage first. Budget: $5 owner cap, $0.44 spent before this arm, $2 ceiling for this arm. Re-run the sweep (fresh baseline + code arm, small_test; full test only if paired wins > losses), verify, record, then merge main into #239 (conflicts) and push |
| 8 | Handoff notes | `docs/handoff-2026-09-10` (**pushed**, draft #241) | up to date | keep syncing |

## Open owner decisions

- **OD-B** (track 5): make `python_package_root` use `abspath` instead of `resolve()`, so symlinked roots keep correct ids. Recommended: yes. It is built as the last droppable commit.
- **Merges:** #243 is ready now. #239 and #244 need the steps above. The owner tests the UI release + model settings on port 8512 before any merge.
- **Also running separately** (the owner's own session): "Fix embed skip set never populated in ingestion" (task_5905f626).
