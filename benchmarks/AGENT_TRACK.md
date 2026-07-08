# Agent track — paired agent-efficiency runbook

The agent track answers one question: **does attaching the pydocs-mcp server to
a coding agent make it more efficient at the same answer quality?** The same
headless agent answers repository-comprehension questions twice per task —

- **arm A (bare):** file/search tools only (`Read`, `Grep`, `Glob`, `Bash`);
- **arm B (indexed):** the same tools plus the pydocs-mcp MCP server attached —

a blind LLM judge scores both answers against the gold answer, and the report
aggregates the cost / wall-clock / turn / tool-call / file-read / cache-token
deltas **per task, paired**, so the efficiency numbers are read *at
answer-quality parity*.

> **This is manual and expensive by design. It never runs in CI.** A full run
> spawns a real headless agent per arm and spends real money.

---

## Cost expectations

A single repository's worth of paired tasks costs roughly **$5–10 per arm per
repo**, plus the blind judge arm (one extra short call per admitted pair).
Multiply by the number of repos you run. The guardrails below cap the total, but
budget generously:

- `--max-tasks N` caps how many pairs are admitted (default: the config's 48).
- `--max-usd X` is a **hard** cap checked against actual accrued spend before
  every new pair — the run stops before starting a pair that could exceed it.
- Each arm has a per-task wall timeout; an arm that overruns discards its whole
  pair (see resume semantics below).

---

## Preflight first — always

Before **any** paid run, verify the environment contract:

```bash
python -m benchmarks.eval.agent_track --preflight
```

This runs five checks in fail-fast order and exits non-zero on the first
failure:

| Check | What it proves |
| --- | --- |
| `claude-cli-present` | the headless CLI is on `PATH` |
| `claude-json-contract` | a one-token `claude -p "ok" --output-format json` returns the fields the parsers read — and costs **≤ $0.01** (a higher cost means the wrong model/flags) |
| `pydocs-mcp-importable` | `import pydocs_mcp` works under the harness interpreter |
| `mcp-config-boots` | the one-server MCP config renders and `pydocs_mcp serve` starts |
| `disk-headroom` | there is room for corpus checkouts + index sidecars |

The only paid step is `claude-json-contract`, capped at a cent. Everything else
is free. If the preflight fails, fix the reported condition before spending on a
full run — that is the entire point of the check.

---

## Running the harness

```bash
python -m benchmarks.eval.agent_track \
    --dataset swe-qa-pro \
    --max-tasks 12 \
    --max-usd 60 \
    --model <model-id> \
    --judge-model <model-id> \
    --ledger runs/agent_track_pairs.jsonl \
    --report runs/agent_track_report.md
```

- `--dataset` selects the corpus (default `swe-qa-pro`; the same dataset
  adapters the retrieval track uses).
- `--model` pins both arms to the same model so the only variable is the tool
  surface. `--judge-model` pins the blind judge arm (defaults to `--model`).
- `--ledger` is the resumable JSONL record (see below).
- `--report` writes the Markdown report; omit it to print to stdout.

---

## Resume semantics

The run is **resumable** through the ledger. Every task — admitted **or**
discarded — writes one JSONL line keyed by `task_id`. Re-running with the same
`--ledger`:

- **skips** any `task_id` already present (admitted pairs are never re-paid);
- **does not re-attempt** a task it previously gave up on (a discarded task is
  "done" too — you will not re-spend on a task whose arm kept timing out).

**No half-pairs are admitted.** A task counts only when *both* arms completed
inside budget *and* the judge scored them. If either arm times out or the judge
fails, the whole task is discarded and the discard is logged to the ledger — the
drop is always visible, never a silent cap. The report's footer states pairs
admitted / discarded / total spend for exactly this reason.

So a run interrupted (Ctrl-C, timeout, spend cap) picks up precisely where it
stopped: re-invoke the identical command and it continues from the first
un-ledgered task.

---

## Reading the report

The report is a single paired-delta table (`indexed − bare` per metric, with the
mean delta, mean percent delta, and a 95% bootstrap CI), a **judge-parity line**
(the indexed arm's mean blind 0–10 score with its CI — the "at parity" audit), a
`## By qa_type` breakout (per-category cost + judge means), and the honest
footer. Every bootstrap uses one fixed seed, so re-rendering the same ledger
reproduces a byte-identical report.

---

## Never CI

To restate the guardrail this whole track is built around: **do not wire the
agent track into CI.** It spawns real agents and spends real money. It is an
operator-run, preflight-gated, ledgered, manual evaluation. The offline test
suite covers every pure and Protocol-seamed part of it (`benchmarks/tests/eval/
agent_track/`); the paid path is exercised only by an operator who ran the
preflight first.
