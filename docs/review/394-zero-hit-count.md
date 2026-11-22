# #394 — the $0 zero-hit count, and the table prepared for #366

**Status: prepared, not posted.** Nothing below has been written to any GitHub issue. The section "Text for #366" is the comment to post when #394 is closed.

**Verdict: BELOW the floor in every complete arm.** Highest arm: 9 of 20, floor 12, BELOW. Latest complete ladder arm (step 3): 4 of 20, floor 12, BELOW. After the API zero-hit fallback (ADR 0027): 0 of 20 in both complete arms.

Under the owner's decision of 2026-11-22 (question 1, option 1) this means: #394 closes unmerged, the table goes to #366, no paid arm runs, and arm C is not built.

## Text for #366

> **#394 (pointer density, the `zero_hit` retarget): closed unmerged on a $0 count, 2026-11-22.** No arm was run and nothing was spent.
>
> **Rule, fixed before counting (owner decision, 2026-11-22).** Count search zero hits on the stored `repoqa-qa/small_dev` traces first. One zero hit can cost at most one Turn, so the largest effect a zero-hit Pointer arm can have on the mean is (share of tasks with a zero hit) × 1 Turn. The floor is the share at which that reaches the A/A edge of the slice (about ±0.6 Turns at 20 tasks): **12 of 20 tasks**. Below the floor, #394 closes unmerged with this table and no paid arm.
>
> **Definition.** A search zero hit is a `search_codebase` call that did not fail and returned no rows (`hit_count == 0`, empty `result_ids`), read from the call record in the trace, never from a fired rule. A zero-hit task has at least one. A `grep` or `glob` with no match is not one.
>
> **Ladder arms** (20 tasks each; all before ADR 0027 and before `system_v3`; chat `qwen/qwen3.8-27b`, 12-Turn budget):
>
> | Arm | Commit | Zero-hit tasks | Zero-hit search calls | Kind | `get_overview` was the next call | `needle cited` on zero-hit tasks | Largest possible effect on the mean | Floor 12 of 20 |
> |---|---|---|---|---|---|---|---|---|
> | A1 `runs/aa-374/baseline` (2026-09-30) | `60d5a00a` | 5 of 20 | 5 | `api` ×5 | 1 of 5 | 5 of 5 | 0.25 Turns | BELOW |
> | A2 `runs/aa-374/candidate` (2026-09-30) | `60d5a00a` | 9 of 20 | 9 | `api` ×9 | 0 of 9 | 7 of 9 | 0.45 Turns | BELOW |
> | step 4 `runs/step4-376/candidate` (2026-09-30) | `7f30e8ec` | 4 of 20 | 4 | `api` ×4 | 1 of 4 | 4 of 4 | 0.20 Turns | BELOW |
> | step 3 `runs/step3-375/candidate` (2026-10-03) | `8fd6a23a` | 4 of 20 | 4 | `api` ×4 | 0 of 4 | 3 of 4 | 0.20 Turns | BELOW |
>
> - `runs/step4-376/baseline` is the reused A1 baseline (byte-identical traces), counted once as A1.
> - Counting the two failed searches (a validation error on `project=`) as well gives 5, 10, 5 and 4 tasks: still below.
> - A1 and A2 are the same commit and settings: the count itself moves by four tasks between identical arms.
>
> **What the rows show.**
> - Every one of the 22 search zero hits is a `kind="api"` request, each the task's opening search in Turn 1. No search of any other kind returned zero rows (63 calls).
> - The `get_overview` Pointer was followed in 2 of 22 zero-hit tasks, the only two where the miss was sent alone in its Turn. In the other 20 the model had sent a `grep` or a second search in the same Turn and continued from that result.
> - The gold file reached the model in all 22 zero-hit tasks (15 in the same Turn as the miss, 7 later). 19 of 22 answers cite the Needle; the three misses are all on the two generated-code `mlc-llm` needles, which no arm cites, zero hit or not.
> - Mean Turns after the first zero hit: 4.60, 4.33, 3.25, 3.50. Zero-hit tasks take more Turns than the others, but that gap belongs to the API zero hit itself, not to the Pointer line the arms would have changed.
>
> **Today's product.** Since ADR 0027 (2026-11-08, default on) a `kind="api"` zero hit is answered in the same response with the `kind="any"` rows, and the overview Pointer renders only when that fallback is empty too. So the counts above are an upper bound. Supporting traces, same 20 tasks and chat model, from the three-seed before/after of 2026-11-08 and the fallback experiment of 2026-11-09 (not ladder arms, not the floor test):
>
> | Slice | Commit | `system_v3` | ADR 0027 | Zero-hit tasks per 20-task arm |
> |---|---|---|---|---|
> | Three-seed, baseline | `7ee99fd4` | no | before | 5, 2, 3 |
> | Three-seed, candidate | `9b9de5a3` | yes | before | 5, 4, 8 |
> | Fallback experiment, baseline | `9b9de5a3` | yes | before | 7, 2, 4 |
> | Fallback experiment, candidate | `0a6496b1` | yes | **after** | **0, 0** (two complete seeds; 11 API zero hits, all answered with fallback rows) |
>
> A third candidate seed and two later runs are partial and were not pooled.
>
> **Consequences.**
> - #394 is closed unmerged. Arms B and C were not run; arm C (a new printed line behind a new flag, with an ADR 0007 amendment) is not built.
> - No product default changes. `callers` stays on `search_hit_code`; no `pointer_hits` knob.
> - Ledger line 15 ($1.00–2.00) is not spent. #394 no longer blocks #396.
> - No 100-task `repoqa-qa/dev` arm exists yet. The matching floor there is about 27 of 100 tasks, recorded as the rule for a future slice.
>
> **Check.** The count script reproduces the stored verdict figures of all four arms from the same files (penalised Turns-to-answer 4.200 / 3.850 / 3.600 / 3.150; `needle cited` 0.850 / 0.900 / 0.900 / 0.900) and ADR 0027's own count for the three-seed candidate arms (17 zero hits over 60 tasks).

## Method, in short

- **Sources.** The four ladder arm directories as copied from the owner's machine (`~/pydocs-handoffs/2026-09-24/ask-turn-efficiency/runs/…`), read from `/workspace/claude-logs/experiments-2026-11-22/traces/ladder-arms/`. The traces are not in this repository and must not be added to it. Supporting arms: `/workspace/claude-logs/te-context/merge/paid/runs/paid-20261108T0624Z/` and `/workspace/claude-logs/te-context/track-49/m1/paid/runs/m1-20261109T0134Z/`.
- **Per task.** `arm.json` gives the task, its outcome, Turns and stored answer; `traces/<id>/server_events.jsonl` gives every tool call; `model_turns.json` maps calls to Turns.
- **Next call after the first zero hit.** The calls of the first later Turn. Calls sent in the same Turn as the miss were issued before the model saw it and are listed apart.
- **`needle cited`.** The eval suite's own rule, `score_needle_citation` (`benchmarks/src/pydocs_eval/judge/needle_citation.py`), loaded by file path and applied to the stored answers.
- **Gold file reached.** The eval's file-level rule (`benchmarks/src/pydocs_eval/trajectory/gold_reach.py`): some call returned a row whose path is the gold file.
- **Cost.** $0: local JSON parsing only.

Full evidence — per-task rows for all 80 task runs, every source path, the checks and the caveats — is in `/workspace/claude-logs/experiments-2026-11-22/ZERO-HIT-COUNT.md`, with the scripts beside it in `count-scripts/`.

## Caveats the reader should keep

1. The ladder arms predate `system_v3` and ADR 0027. They bound today's rate from above; the arms that carry `system_v3` agree (at most 8 of 20 before the fallback, 0 of 20 after).
2. No ladder arm of the current accepted state is in the handoff. The post-fallback evidence is two complete arms of the fallback experiment.
3. The Turn gap between zero-hit tasks and the others is observational. It does not estimate what changing the Pointer would do.
4. "Gold file reached" says the file was returned to the model, not that the function was read. `needle cited` is the stricter row.
