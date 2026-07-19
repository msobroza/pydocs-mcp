# Metric definitions

Every metric is a rule computed from parsed facts — the merged `events.jsonl`,
the attribution result, and the ground-truth eval outcome. No metric is a
judgment call. Each lives in `metrics.py` with a formula docstring and a unit
test; this page is the human-readable index. Notation: `|S|` is set size, `∩`
intersection, `\` set difference.

## Attribution tiers

Attribution (`attribution.py`, ADR 0011) classifies each workspace file a
trajectory touched into three nested tiers, from the tool-call evidence:

- **surfaced** — a tool result named the file (it appeared in `result_ids`).
- **inspected** — the file's content was actually read/returned (not merely
  listed).
- **used** — the file appears in the final patch.

Injected-context surfacings (an `initiator` of injected / exact
`INJECTED_CONTEXT_MARKER` match) are excluded, and machinery events
(`fired_rules`, `suggestion`) are never evidence. First-touch credit records
which tool first surfaced each file.

## Localization metrics

| Metric                     | Formula                                             | Edge case                          |
| -------------------------- | --------------------------------------------------- | ---------------------------------- |
| `gold_file_recall`         | `|surfaced ∩ gold| / |gold|`                        | empty gold → `1.0` (vacuous)       |
| `wasted_read_ratio`        | `|inspected \ used| / |inspected|`                  | no inspection → `0.0`              |
| `hunk_overlap` (per file)  | `|seen_lines ∩ gold_lines| / |gold_lines|`          | empty gold lines → `1.0`           |
| `mean_hunk_overlap`        | mean of per-file `hunk_overlap` over files with spans | no spans → `1.0`                 |
| `tool_calls_to_first_gold` | index (1-based) of the first call surfacing a gold file | never surfaced → `null`        |

`hunk_overlap` only contributes where real line spans exist; files surfaced at
file granularity (no spans) are tracked separately and do not dilute the mean.

## Per-tool evidence yield

`per_tool_yield` groups attribution by the tool that first surfaced each file and
reports, per tool, how many surfaced files reached each tier (surfaced /
inspected / used). It answers "which tool produced evidence that mattered."

## Edit-layer metrics

Computed from the ground-truth outcome (`eval_report.py`), which parses the
swebench-style report into strict pass/fail sets.

| Metric                 | Formula / rule                                                        |
| ---------------------- | -------------------------------------------------------------------- |
| `patch_applies`        | `1` iff the model patch applied cleanly, else `0`.                   |
| `f2p_fraction`         | `|gold_F2P ∩ passed| / |gold_F2P|`; empty gold F2P → `0.0`.          |
| `p2p_regression_count` | count of gold `PASS_TO_PASS` tests that are no longer passing.        |

A missing `PASS_TO_PASS` result is treated as a failure (strict resolve): the
outcome is re-derived from the per-test lists, not read from a `resolved` flag.

## Cost-layer metrics

| Metric              | Rule                                                              |
| ------------------- | ---------------------------------------------------------------- |
| `tokens`            | input/output/… token totals, **deduped by `message.id`** (never re-sum raw usage). |
| `calls_by_tool`     | count of `tool_call` events per tool name.                       |
| `tool_calls`        | total `tool_call` events.                                        |
| `turns`             | distinct turn count across tool + loop events.                   |
| `wall_clock_seconds`| span from first to last tool-call timestamp.                    |
| `cost_usd`          | run cost, taken from the run record (not re-derived from tokens).|

## Shaped score

The six shaped-score **components** are each a goodness-in-`[0,1]` fact:

| Component            | Definition                                              |
| -------------------- | ------------------------------------------------------- |
| `localization_recall`| `gold_file_recall`                                      |
| `evidence_yield`     | `1 - wasted_read_ratio`                                 |
| `patch_applies`      | `1` if the patch applied, else `0`                      |
| `f2p_fraction`       | fraction of gold F2P now passing                        |
| `p2p_clean`          | `1` if `p2p_regression_count == 0`, else `0`            |
| `budget_headroom`    | `1 - min(1, turns / turn_cap)`; `1.0` when no cap recorded |

The per-example `soft` score is the **weight-normalized average**
`Σ(wᵢ·cᵢ) / Σ(wᵢ)`, which lands in `[0,1]` for any non-negative weights. Weights
and `score_version` live in `configs/score_weights.yaml` (see
[`config-reference.md`](config-reference.md)).

## Failure taxonomy

On a non-resolved run the failure label is assigned by a deterministic
**first-match** decision tree (ADR 0012), in this order:

```
infra_error → empty_trajectory → crash_before_first_tool → patch_apply_failed
→ budget_exhausted → resolved → never_ran_tests → localization_miss
→ found_but_misdiagnosed → right_idea_broken_edit → regression_introduced
```

Labels are mutually exclusive; the earlier match wins. `infra_error` is labeled
but **excluded from all score aggregates** (it is a harness fault, not a model
outcome); every other label is included. See `taxonomy.py` for each detector.
