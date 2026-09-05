# Trajectory instrumentation — reference docs

The trajectory layer turns a captured agent rollout into recomputable metrics,
a shaped score, a failure label, and natural-language feedback. It has two
halves that meet at a file-format contract (never a Python import):

- **Product recorder** (`python/pydocs_mcp/observability/`) writes a raw
  `server_events.jsonl` per trajectory when `trace.enabled` is set.
- **Eval layer** (`benchmarks/src/pydocs_eval/trajectory/`) reads the raw
  server events plus the loop-side stream-json and run record, merges them into
  one ordered `events.jsonl`, and computes every derived output from it.

Everything downstream of the merged stream is a pure function of immutable
inputs, so deleting the derived outputs and rerunning regenerates them
byte-for-byte.

## Contents

- [`schema-reference.md`](schema-reference.md) — the merged `events.jsonl` record
  types, field-by-field, with annotated example records.
- [`metric-definitions.md`](metric-definitions.md) — every metric with its exact
  formula and edge-case behavior.
- [`config-reference.md`](config-reference.md) — the tunable weights, thresholds,
  and the `score_version` / `taxonomy_version` stamps.
- [`adding-a-metric.md`](adding-a-metric.md) — the short how-to for adding one.

## The command

```
pydocs-eval-compute-metrics <trace-dir> [--out <dir>]
```

`<trace-dir>` holds one subdirectory per trajectory; each subdir carries the
merged `events.jsonl` and a `facts.json` (the gold + eval facts — see
[`config-reference.md`](config-reference.md#factsjson-per-trajectory-inputs)).
The command writes, under `--out` (default `<trace-dir>/derived`):

- `trajectories/<trajectory_id>.json` — the per-trajectory derived record
  (machine-readable canonical JSON).
- `aggregate.json` — the run rollup plus a per-trajectory index.
- `report.txt` — the same numbers as a human-scannable table.

The trace inputs are treated as immutable; outputs go to a separate directory so
a rerun never mutates the captured trace.
