# Merged-stream schema reference (`events.jsonl`)

`events.jsonl` is the canonical, ordered merge of the product recorder's raw
server events and the loop-side stream-json. It is JSON Lines: one JSON object
per line, each carrying an `_event` discriminator. `schema_version` is `1`; any
breaking field change bumps it, and a version-1 reader returns `None` for an
unknown `_event` rather than crashing.

The example records below are drawn from the synthetic fixtures under
`benchmarks/tests/trajectory/fixtures/` — they contain no real data, so nothing
is redacted.

## Record types

Three `_event` values appear in a merged stream:

| `_event`            | Meaning                                                        |
| ------------------- | -------------------------------------------------------------- |
| `trajectory_header` | Exactly one, first line. Identity + versions for the run.      |
| `tool_call`         | One MCP tool call recorded server-side (ordered by `seq`).     |
| `loop_event`        | One loop-owned record: assistant text, bare tool use, or result. |

### `trajectory_header`

```json
{"_event": "trajectory_header", "artifact_hash": "0000…0000", "claude_cli_version": "2.1.76",
 "dataset_revision": "widgetlib-fixture@1", "mcp_version": "1.28.1",
 "pydocs_mcp_version": "0.6.0",
 "run_config": {"instance_id": "widgetlib__pricing-discount", "model": "claude-haiku-4-5-20251001"},
 "schema_version": 1, "trajectory_id": "10000000-0000-4000-8000-000000000001"}
```

| Field                | Type          | Notes                                                        |
| -------------------- | ------------- | ------------------------------------------------------------ |
| `trajectory_id`      | `str`         | The correlation key joining server events, loop stream, run. |
| `artifact_hash`      | `str`         | Hash of the optimizable description surface (Phase 1).       |
| `pydocs_mcp_version` | `str`         | Recorder's product version.                                  |
| `mcp_version`        | `str`         | MCP protocol library version.                                |
| `claude_cli_version` | `str`         | The loop CLI version.                                        |
| `dataset_revision`   | `str \| null` | The task-corpus revision, when known.                        |
| `run_config`         | `dict`        | Sampling/model/instance metadata for the run.                |
| `schema_version`     | `int`         | `1`.                                                         |

### `tool_call`

One indexed MCP tool call. Annotated:

```json
{"_event": "tool_call",
 "tool": "search_codebase",            // which of the nine tools
 "args": {"kind": "docs", "query": "discount"},
 "seq": 1,                             // monotonic server-side order key
 "ts": 1001.0,                         // recorder wall-clock (seconds)
 "turn": 1,                            // loop turn the call belongs to
 "latency_ms": 41.0,                   // server-measured call latency
 "initiator": "model",                 // model | injected (context pre-seed)
 "error": null,                        // typed error envelope, or null
 "result_ids": [{"path": "widgetlib/pricing.py", "start_line": 8, "end_line": 14}],
 "hit_count": 1,                       // len(items) in the result envelope
 "truncated": null,                    // whether the result was budget-elided
 "suggestion": null,                   // machinery suggestion text, if any
 "fired_rules": [],                    // machinery rule firings (never evidence)
 "result_preview": "## apply_discount\ndef apply_discount(price, pct): ...",
 "result_blob": "aaaa…aaaa",           // sha256 handle into blobs/ (full text)
 "result_bytes": 256,                  // full result size in bytes
 "event_id": "10000000-0000-4000-8000-000000000001:tool:000001",
 "trajectory_id": "10000000-0000-4000-8000-000000000001"}
```

`result_ids` is the distilled evidence surface — the `(path, start_line,
end_line)` rows the tool returned. Attribution reads these to decide which files
a call *surfaced*; `result_preview` is a bounded prefix and `result_blob` is the
content-addressed handle to the full text.

### `loop_event`

```json
{"_event": "loop_event",
 "kind": "result",                     // assistant | tool_use | tool_result | result
 "turn": 2,
 "message_id": "msg_1_result",         // usage dedupe key (see below)
 "usage": null,                        // token usage, attached at most once per message_id
 "tool": null,                         // set for bare (non-MCP) tool uses, e.g. Bash
 "tool_input": null,                   // the bare tool's raw input (e.g. {"command": "pytest"})
 "text": "Fixed apply_discount.",      // assistant/result text
 "is_error": false,
 "event_id": "10000000-0000-4000-8000-000000000001:loop:000002",
 "trajectory_id": "10000000-0000-4000-8000-000000000001"}
```

`kind` is one of `assistant`, `tool_use`, `tool_result`, `result`. The
`never_ran_tests` detector reads `tool_input.command` on bare `tool_use` loop
events; the token metrics read `usage`.

**Usage dedupe trap.** The stream-json can repeat the same `usage` block across
several loop events that share a `message_id`. The stream reader attaches usage
at most once per `message_id`, so summing `usage` over loop events never
over-counts tokens. Do not re-sum raw stream usage yourself.
