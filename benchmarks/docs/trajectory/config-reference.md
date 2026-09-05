# Config reference

All trajectory tuning is data, not code — versioned so any labeled or scored
output is always traceable to the config that produced it. Editing a value is a
**version bump**, never a silent in-place change, so old traces stay
byte-reproducible.

## `configs/score_weights.yaml` — shaped-score weights

```yaml
score_version: 1
weights:
  localization_recall: 0.20  # gold-file recall
  evidence_yield: 0.10       # 1 - wasted-read ratio
  patch_applies: 0.20        # 1 if the model patch applied cleanly, else 0
  f2p_fraction: 0.30         # fraction of gold FAIL_TO_PASS tests now passing
  p2p_clean: 0.10            # 1 if no PASS_TO_PASS regression, else 0
  budget_headroom: 0.10      # 1 - min(1, turns / turn_cap); 1.0 when no cap
```

- `score_version` is stamped into every derived record. **Any weight edit must
  bump it.**
- Weights are sane-but-**uncalibrated**; calibration and threshold ablation are
  deferred to a later phase.
- The soft score is `Σ(wᵢ·cᵢ) / Σ(wᵢ)`, so the weights need not sum to 1; a
  non-positive weight total is rejected at load time.

## `configs/taxonomy.yaml` — failure-taxonomy detectors

```yaml
taxonomy_version: 1
test_runner_patterns:      # never_ran_tests detector — regex over bare Bash commands
  - "\\bpytest\\b"
  - "\\bpython\\b.*\\b-m\\b\\s+pytest\\b"
  - "\\bpython\\b.*\\b-m\\b\\s+unittest\\b"
  - "\\btox\\b"
  - "\\bunittest\\b"
```

- `taxonomy_version` is stamped onto every labeled output. **Any reordering of
  the decision tree, new label, or pattern-set change bumps it.**
- `test_runner_patterns` are the anchored regexes that decide whether test
  execution was observed (word boundaries keep `pytest_helper` from counting).

## `facts.json` — per-trajectory inputs

Each trajectory subdir under the trace-dir carries a `facts.json` naming the
gold and eval facts the metric layer needs. Required keys:

| Key               | Type              | Meaning                                             |
| ----------------- | ----------------- | --------------------------------------------------- |
| `trajectory_id`   | `str`             | Correlation id; names the per-trajectory output.    |
| `instance_id`     | `str`             | The task instance the rollout targeted.             |
| `workspace_root`  | `str`             | Root used to normalize surfaced paths.              |
| `gold_files`      | `[str]`           | Files the gold patch edits.                         |

Optional keys (sensible defaults when absent):

| Key                 | Type                | Default | Meaning                                          |
| ------------------- | ------------------- | ------- | ------------------------------------------------ |
| `gold_line_map`     | `{str: [int]}`      | `{}`    | Gold-edited line numbers per file (hunk overlap).|
| `final_patch_files` | `[str]`             | `[]`    | Files the model's final patch touched.           |
| `gold_f2p`          | `[str]`             | `[]`    | Gold `FAIL_TO_PASS` test names.                  |
| `gold_p2p`          | `[str]`             | `[]`    | Gold `PASS_TO_PASS` test names.                  |
| `turn_cap`          | `int \| null`       | `null`  | Turn budget (drives `budget_headroom`).          |
| `patch_bytes`       | `int`              | `0`     | Size of the model patch (degenerate detectors).  |
| `cost_usd`          | `float`            | `0.0`   | Run cost.                                         |
| `report`            | `{instance: block}` | absent  | swebench-style eval report → ground-truth outcome.|
| `outcome_kind`      | `str`               | absent  | Degenerate outcome when no `report`: one of `infra`, `patch_apply_failed`, `no_report`. |

When `report` is present it wins; otherwise `outcome_kind` selects a degenerate
ground-truth outcome. An `infra` outcome is excluded from score aggregates; a
`patch_apply_failed` outcome is a graded (counted) task failure.

## Output layout

`pydocs-eval-compute-metrics` writes, under `--out` (default
`<trace-dir>/derived`):

- `trajectories/<trajectory_id>.json` — the derived record (canonical JSON).
- `aggregate.json` — `run` (FitnessReport-compatible rollup), `infra_excluded`,
  `n_trajectories`, `score_version`, `taxonomy_version`, and a per-trajectory
  index.
- `report.txt` — the human-scannable table.

All JSON output is canonical (sorted keys, stable float repr), so a delete +
rerun is byte-identical.
