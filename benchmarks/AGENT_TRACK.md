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
python -m pydocs_eval.agent_track --preflight
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
python -m pydocs_eval.agent_track \
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

---

## Optimization — improving the harness's own text artifacts

The optimize layer (`benchmarks/src/pydocs_eval/optimize/`) turns the paired
agent track into a *fitness function* and searches for better versions of two
text artifacts the harness ships:

- **`tool_docs`** — the product's `TOOL_DOCS` + `SERVER_INSTRUCTIONS` surface
  (`python/pydocs_mcp/application/tool_docs.py`), served to arm B's MCP client;
- **`usage_skill`** — the seed skill document
  (`benchmarks/src/pydocs_eval/optimize/artifacts/usage_skill_seed.md`) that
  reaches the evaluated agent through `task_prompt(skill=...)`.

Two co-equal optimizers propose candidates — `critique_refine` (an LLM
critique/rewrite loop) and `skillopt` (an adapter to an external search repo).
Each candidate is scored on the same paired agent harness, run up a
**fitness ladder** (cheap screening rungs then a full finals rung), and
**accepted only on a held-out split with a real margin**. An optimizer proposes
a diff a human lands — never runtime self-modification.

> **The same guardrail applies, doubled.** A real optimization run is *many*
> paired runs. It is manual, preflight-gated, budget-capped, and never CI. The
> offline suite (`benchmarks/tests/optimize/`) covers every pure and
> Protocol-seamed part; the paid search is exercised only by an operator who ran
> the preflight *and* a `--dry-run` first, and who has an explicit go to spend.

### Preflight first — always (agent-track preflight, then `--dry-run`)

Because the optimizer's fitness *is* the paired agent track, its environment
contract is the agent track's. Before any paid optimization run, in order:

1. **Agent-track preflight** — the same five fail-fast checks a paired run
   needs (headless CLI present, JSON contract at ≤ $0.01, `pydocs_mcp`
   importable, MCP config boots, disk headroom):

   ```bash
   python -m pydocs_eval.agent_track --preflight
   ```

2. **Optimize `--dry-run`** — walks the *whole* optimize pipeline spending
   `$0.00`: it validates the seed against its §D13 firewall, echoes the wired
   ladder, checks the train/holdout split predicate is deterministic and
   both-sided, reports each optimizer adapter's availability (`skillopt` shows
   SKIPPED when its `[optimizers-skillopt]` extra is absent — a dry run must
   never require it), and runs one full orchestrator pass on a zero-cost fake
   fitness with `FakeAgentRunner` / `FakeJudge`:

   ```bash
   python -m pydocs_eval.optimize --config \
       benchmarks/src/pydocs_eval/optimize/configs/optimize_tool_docs.yaml --dry-run
   ```

If either step fails, fix the reported condition before spending. That is the
entire point of the two-step gate.

### The three-layer spend model

Spend has three layers, precedence stated (spec §D5):

1. **`OptimizationBudget.max_usd` is the outer cap** the orchestrator enforces
   across all rungs it runs *and* the holdout gate — checked before starting any
   paid unit of work. Hitting it stops the search and returns
   `OptimizationResult(accepted=False, ...)` with the trials so far.
2. **For `critique_refine`,** every rollout is a `run_agent_track` call under
   its own `--max-usd`, nested beneath the outer cap.
3. **For `skillopt`,** there is no native spend knob at all — skillopt 0.2.x
   has no budget/USD config key, so `OptimizationBudget.max_trials` maps onto
   its **rollout counts** (epochs / batch size / selection-eval size in the
   generated `configs/<name>.yaml`) and `max_usd` is recorded there as an
   explanatory comment ONLY. The outer cap **cannot** interrupt a
   `skillopt-train` run mid-flight; it applies only to the surrounding
   holdout-gate runs, which *do* go through our harness. This asymmetry is why
   the SkillOpt adapter pins the mapping in a test — the mapped rollout counts
   are the only thing bounding the external search.

`--dry-run` walks the whole pipeline (seed validation, ladder wiring, split
determinism, adapter import/stub) with a `FakeAgentRunner`, spending nothing.

### Configuring a run

Runs are configured by a benchmarks-local YAML (like the eval overlay configs —
*not* product `AppConfig`). Two ship:

- `optimize_tool_docs.yaml` — `tool_docs` via `critique_refine`;
- `optimize_usage_skill.yaml` — `usage_skill` via `skillopt`.

Registry keys in the YAML (`paired_agent`, `retrieval`, `critique_refine`,
`skillopt`, artifact names) are **byte-identical** to the registered names — a
typo is a load-time `KeyError`, not a silent no-op. The knobs: the artifact +
optimizer, the ladder rungs as `[fitness, max_tasks, survivors]`, the fitness
`weights` + `judge_parity_floor` (the parity *pre-gate*: a candidate whose blind
judge mean drops more than the floor is rejected before its efficiency counts),
the `accept_margin`, the `budget`, and (for `critique_refine`) the `llm` block.
Both configs live under
`benchmarks/src/pydocs_eval/optimize/configs/`.

### Reading an `OptimizationResult`

The run returns an `OptimizationResult` (a rejected search is *still*
information — `accepted=False` with both holdout scores is a first-class outcome,
not an error):

- **`accepted`** — `True` only when the candidate beat the seed on the holdout
  split by more than `accept_margin` (0.02). A tie, a non-finite candidate
  score, or a missing holdout score all mean *rejected*.
- **`seed_holdout` / `candidate_holdout`** — the two scores the acceptance
  decision compares; `candidate_holdout − seed_holdout > accept_margin` is the
  gate. Both are weighted fractional-reduction sums (higher = better).
- **`proposal_diff`** — the human-landable unified diff (empty when nothing beat
  the seed). This is what you apply by hand.
- **`trials`** — every candidate's journey up the ladder (fingerprint, rung
  scores, cost, and `validate()` violations — an empty violations tuple means
  the candidate passed the §D13 firewall).
- **`total_usd`** / **`provenance`** — what the run cost and the audit trail
  (seed fingerprint, dataset revision, model ids, optimizer) that makes a landed
  proposal reproducible months later.

### Landing a proposal

An accepted result hands you a diff, not a live change. Land it by hand:

1. **Apply the `proposal_diff`** to the artifact's source of truth —
   `python/pydocs_mcp/application/tool_docs.py` for `tool_docs`, or the seed
   skill `usage_skill_seed.md` for `usage_skill`. Each artifact's
   `landing_note()` states its exact target file.
2. **Rerun the §D13 lint + the full suite.** For `tool_docs`, the same
   importable constants the artifact's firewall used back the product lint
   (`tests/application/test_tool_docs_lint.py`) — rerun it plus `pytest tests/`.
   For `usage_skill`, rerun the optimize `--dry-run` to confirm the edited seed
   still validates.
3. **Commit it as an ordinary reviewed change.** No special path — it is a
   normal edit to a product/seed file with a normal PR and normal review.

### Cost expectations

A single paired run is ~$5–10 per arm per repo (see [Cost
expectations](#cost-expectations) above). An optimization run multiplies that by
the trials it scores across the ladder. As a rough anchor: a 20-trial
`critique_refine` run at ladder rung sizes 6 / 24 costs roughly **2×–3× a single
24-task paired run** — the cheap screening rung (6 tasks) prunes most candidates
before the expensive finals rung (24 tasks) touches the survivors. Budget
generously and keep `budget.max_usd` set: it is the hard outer cap that stops the
search before it overruns.

