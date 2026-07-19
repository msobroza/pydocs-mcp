# How to add a metric

Metrics are rules over parsed facts. Adding one is a small, local change; the
single-source rule (`metrics.py` owns every metric — no second implementation)
keeps it that way.

1. **Write the failing test first.** Add a case to
   `benchmarks/tests/trajectory/test_metrics.py` that pins the formula on a tiny
   hand-built input (a few `ToolEvent` / `LoopEvent` objects or an `Attribution`).
   State the edge case (empty gold, no inspection, missing spans) explicitly.

2. **Implement one function in `metrics.py`.** Keep it a pure function of already
   parsed facts — no file IO, no re-parsing the stream. Give it a formula
   docstring with a `>>>` example (the doctest is the reference oracle) and an
   edge-case sentence. Do not read config literals inline; if a threshold is
   involved it belongs in a `configs/*.yaml` under a version stamp.

3. **Add it to the `TrajectoryMetrics` bundle.** Add the field to the dataclass
   and wire it in `compute_metrics(...)` alongside the others. Everything
   downstream reads the bundle, so this is the only assembly point.

4. **Decide whether it feeds the shaped score.** If it should influence `soft`,
   add a component in `shaped_score.py:score_components`, a weight in
   `configs/score_weights.yaml`, and **bump `score_version`** (any weight/component
   change re-pins every scored record). If it is diagnostic only, stop at
   step 3 — it still ships in the per-trajectory JSON.

5. **Re-pin the goldens.** The derived-record golden in `test_consumers.py` and
   the CLI golden in `test_compute_metrics_cli.py` are byte-for-byte; a metric
   that reaches the record will change them. Update both in the same commit and
   note the version bump.

6. **Document it.** Add the row to
   [`metric-definitions.md`](metric-definitions.md) (formula + edge case). A
   metric without a documented formula is not done.

Do not add a metric as an MCP tool parameter or a CLI flag — the metric layer is
config- and code-driven, and the MCP surface is frozen.
