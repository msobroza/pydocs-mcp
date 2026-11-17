## Before/after measurement

- split: `repoqa-qa/small_test` — 2 task(s) per arm, 0 and 0 measured
- baseline: `<short>` <subject>
- candidate: `<short>` <subject>
- model: `qwen/qwen3.8-27b` @ `https://openrouter.ai/api/v1`, 12 agent turn(s) per task
- estimated spend: $0.00 (the plan's pre-run estimate; the spend rows below are measured)
- outcomes, baseline: no task (near cap: 0)
- outcomes, candidate: no task (near cap: 0)
- settings, baseline: architecture `text_react`, scaffold `citing`, guidance none, digest `f320c3b7c1df918c88449615e90cb1bc09282729bf4321be4675a42845d68c3a`
- settings, candidate: architecture `text_react`, scaffold `citing`, guidance none, digest `f320c3b7c1df918c88449615e90cb1bc09282729bf4321be4675a42845d68c3a`

| Metric | baseline | candidate | delta (95% CI) | p | pairs |
|---|---|---|---|---|---|
| budget-exhausted rate ↓ | n/a | n/a | n/a | n/a | 0 |
| answered-within-budget rate ↑ | n/a | n/a | n/a | n/a | 0 |
| outcome: unrecorded · | 0 | 0 | 0 | n/a | n/a |
| outcome: timeout ↓ | 0 | 0 | 0 | n/a | n/a |
| outcome: budget_exhausted ↓ | 0 | 0 | 0 | n/a | n/a |
| outcome: exhausted_finalized ↓ | 0 | 0 | 0 | n/a | n/a |
| outcome: starved_reply ↓ | 0 | 0 | 0 | n/a | n/a |
| outcome: unanswered_empty ↓ | 0 | 0 | 0 | n/a | n/a |
| outcome: answered ↑ | 0 | 0 | 0 | n/a | n/a |
| near cap ↓ | 0 | 0 | 0 | n/a | n/a |
| finalize format failures ↓ | n/a | n/a | n/a | n/a | n/a |
| turns-to-answer (penalised, exhausted = cap+1) ↓ | n/a | n/a | n/a | n/a | 0 |
| turns-to-answer (answered only) ↓ | n/a | n/a | n/a | n/a | 0 |
| turns after needle ↓ | n/a | n/a | n/a | n/a | 0 |
| turns (per task) ↓ | n/a | n/a | n/a | n/a | 0 |
| tool calls (per task) ↓ | n/a | n/a | n/a | n/a | 0 |
| calls after first gold (total) ↓ | n/a | n/a | n/a | n/a | n/a |
| calls after first gold (per task) ↓ | n/a | n/a | n/a | n/a | 0 |
| calls after first gold (p90) ↓ | n/a | n/a | n/a | n/a | n/a |
| calls after first gold read (total) ↓ | n/a | n/a | n/a | n/a | n/a |
| calls after first gold read (per task) ↓ | n/a | n/a | n/a | n/a | 0 |
| calls after first gold read (p90) ↓ | n/a | n/a | n/a | n/a | n/a |
| tool calls to first gold read ↓ | n/a | n/a | n/a | n/a | 0 |
| needle cited ↑ | n/a | n/a | n/a | n/a | 0 |
| gold-site coverage at stop ↑ | n/a | n/a | n/a | n/a | 0 |
| cited-path precision ↑ | n/a | n/a | n/a | n/a | 0 |
| answers over the judge cap ↓ | n/a | n/a | n/a | n/a | n/a |
| needless-call rate ↓ | n/a | n/a | n/a | n/a | 0 |
| — resurfacing calls ↓ | 0 | 0 | 0 | n/a | n/a |
| — zero-yield calls ↓ | 0 | 0 | 0 | n/a | n/a |
| — fan-out-where-batch calls ↓ | n/a | n/a | n/a | n/a | n/a |
| — tool-mismatch calls ↓ | 0 | 0 | 0 | n/a | n/a |
| pointer-followed rate ↑ | n/a | n/a | n/a | n/a | 0 |
| parallel calls per turn ↑ | n/a | n/a | n/a | n/a | 0 |
| batch-versus-fan-out ratio ↑ | n/a | n/a | n/a | n/a | 0 |
| gold-reached rate ↑ | n/a | n/a | n/a | n/a | 0 |
| visible gold rate ↑ | n/a | n/a | n/a | n/a | 0 |
| tool calls to first gold ↓ | n/a | n/a | n/a | n/a | 0 |
| tool calls to first visible gold ↓ | n/a | n/a | n/a | n/a | 0 |
| visible-hit rate per search call ↑ | n/a | n/a | n/a | n/a | 0 |
| calls to full coverage ↓ | n/a | n/a | n/a | n/a | 0 |
| trajectory (union) recall@1 ↑ | n/a | n/a | n/a | n/a | 0 |
| trajectory (union) recall@5 ↑ | n/a | n/a | n/a | n/a | 0 |
| trajectory (union) recall@10 ↑ | n/a | n/a | n/a | n/a | 0 |
| best search call recall@1 ↑ | n/a | n/a | n/a | n/a | 0 |
| best search call recall@5 ↑ | n/a | n/a | n/a | n/a | 0 |
| best search call recall@10 ↑ | n/a | n/a | n/a | n/a | 0 |
| first search call recall@1 ↑ | n/a | n/a | n/a | n/a | 0 |
| first search call recall@5 ↑ | n/a | n/a | n/a | n/a | 0 |
| first search call recall@10 ↑ | n/a | n/a | n/a | n/a | 0 |
| best search call MRR ↑ | n/a | n/a | n/a | n/a | 0 |
| first search call MRR ↑ | n/a | n/a | n/a | n/a | 0 |
| search calls ↓ | n/a | n/a | n/a | n/a | 0 |
| reformulations ↓ | n/a | n/a | n/a | n/a | 0 |
| tool calls (total) ↓ | 0 | 0 | 0 | n/a | n/a |
| distinct tools used · | n/a | n/a | n/a | n/a | 0 |
| tool calls used ↑ | n/a | n/a | n/a | n/a | 0 |
| used/total call ratio ↑ | n/a | n/a | n/a | n/a | 0 |
| tokens in (total) ↓ | n/a | n/a | n/a | n/a | n/a |
| tokens out (total) ↓ | n/a | n/a | n/a | n/a | n/a |
| reasoning tokens (total) ↓ | n/a | n/a | n/a | n/a | n/a |
| cached tokens (total) ↓ | n/a | n/a | n/a | n/a | n/a |
| estimated USD (total) ↓ | n/a | n/a | n/a | n/a | n/a |
| reported USD (total) ↓ | n/a | n/a | n/a | n/a | n/a |
| tokens in (per task) ↓ | n/a | n/a | n/a | n/a | 0 |
| tokens out (per task) ↓ | n/a | n/a | n/a | n/a | 0 |
| reasoning tokens (per task) ↓ | n/a | n/a | n/a | n/a | 0 |
| cached tokens (per task) ↓ | n/a | n/a | n/a | n/a | 0 |
| estimated USD (per task) ↓ | n/a | n/a | n/a | n/a | 0 |
| reported USD (per task) ↓ | n/a | n/a | n/a | n/a | 0 |
| uncached tokens in (total) ↓ | n/a | n/a | n/a | n/a | n/a |
| uncached tokens in (per turn) ↓ | n/a | n/a | n/a | n/a | 0 |
| wall seconds (per task) ↓ | n/a | n/a | n/a | n/a | 0 |
| description tokens ↓ | 10 | 10 | 0 | n/a | n/a |

`↓` marks a metric that is better lower, `↑` one that is better higher, `·` one that is neither (so it carries no p). The change succeeds when the needless-call rate goes DOWN while tool calls to first gold stay flat or improve.

Every task ends in exactly ONE outcome, decided in this order: `timeout` (the eval's per-task timeout killed it), `budget_exhausted` (the turn budget ran out and no answer came back), `exhausted_finalized` (it ran out and one final reply still answered), `starved_reply` (an empty reply the endpoint cut at its token limit while the model could think), `unanswered_empty` (an empty answer for no reason above) and `answered`; `unrecorded` marks an arm written before outcomes were recorded whose outcome could not be back-filled. The `outcome:` rows count each; `budget-exhausted rate` counts both exhausted outcomes, `answered-within-budget rate` counts `answered` alone, and `near cap` counts tasks within one turn of the budget.

`turns-to-answer (penalised, exhausted = cap+1)` is the headline: an answered task counts its own turns, and every unanswered one — exhausted, finalized after exhaustion, starved, timed out or empty — counts the budget + 1 = 13 turns, since it never answered within the budget. `unrecorded` tasks drop out of both turn-to-answer means and stay in the tally. `turns-to-answer (answered only)` averages the answered tasks alone, and `turns (per task)` is the raw count of model replies, penalty-free. `turns after needle` counts the replies after the turn whose call first surfaced a gold file; the `calls after first gold` rows count the calls after that call, and the `... read` rows the calls after the first `read_file` or `get_symbol` that returned one.

Each arm's cell is its mean with a 95% percentile-bootstrap interval (1000 resamples, seed 0). `delta` is the PAIRED change, candidate minus baseline, over the `pairs` tasks both arms measured — so it can differ from the difference of the two columns, which average each arm's own defined tasks. `p` is one-sided for the candidate being better in that row's own direction (Wilcoxon signed-rank; McNemar's exact two-sided p for the 0/1 rates). A count or total row is a whole-arm figure and a `(p90)` row each arm's p90 over the tasks that defined it; neither carries a test.

`n/a` means undefined, not zero: a rate over opportunities the server created is undefined when there were none, a retrieval number is undefined when the trajectory never searched, and such trajectories are dropped from the mean rather than counted as zero.

The spend rows are MEASURED, not assumed. Reasoning tokens are the thinking slice of tokens out and cached tokens the reused slice of tokens in, so neither is added to its parent. Usage is counted once per model message id, so a message an endpoint re-sent on a retry is billed once. `estimated USD` prices the measured tokens with the run's `--usd-per-1m-*` flags (reasoning at the output rate, since the endpoint bills it as completion); `reported USD` is the endpoint's own quote, `n/a` when it quoted none.

Used calls are counted under the `not_needless` definition: an answering run leaves no patch to attribute a row to, so a call counts when no needless-call component charged it — a weaker claim than attributed evidence.