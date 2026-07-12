# ask-auto-optimization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement spec `docs/superpowers/specs/2026-07-11-ask-auto-optimization-spec.md` (20 ACs, slices A–D): three optimization axes for the ask agent, a configurable gate→rubric→verdict objective with sample-level persistence, the `config_search` optimizer, and the one product seam.

**Architecture:** The spec's §3 is adopted as written — this plan records the scout-verified deltas (82 anchors checked, 75 confirmed, 7 drifted) and the commit map. Everything lands benchmarks-side except the `AskPrompts` seam.

**Scout-verified deltas that change implementation (vs the spec's text):**

1. **The multimodal registry HAS landed** (spec §7-Q1 is resolved): `build_agent` already takes `architecture=`/`config=`/`capabilities=`; the prompt-assembly single site is now `agent.py:194-195` — `system = prompts_for(name).render("system_v1")` + catalog into `AgentBuildContext.prompt`. **The seam's fallback must be the per-architecture render, NOT the `SYSTEM_PROMPT` constant** — else a future `prompts/<name>/system_v1.j2` override would be silently shadowed:
   `resolved_system = prompts.system_prompt if prompts and prompts.system_prompt else prompts_for(name).render("system_v1")`.
2. **`REWRITE_PROMPT` is GONE** (now `prompts/shared/rewrite_v1.j2` rendered per call). The rewrite override binds as an additive kwarg on `reformulate(llm, history, question, *, rewrite_template: str | None = None)` — when set, `rewrite_template.format(history=…, question=…)`; else `prompts.rewrite_prompt(…)`. app.py never passes it (byte-identical product). The harness binding owns threading `AskPrompts.rewrite_prompt` into ITS reformulate call.
3. **AC-4 seed re-anchors to the `.j2` single sources**: system section == `prompts.render_shared("system_v1")`; rewrite section == `render_shared("rewrite_v1", history="{history}", question="{question}")` — rendering the Jinja vars into literal format-placeholders yields exactly the format-template the candidate axis edits.
4. **Bridges ship in v1, `react`/`react_no_rewrite` never exist**: `ask_architecture_registry` = four thin bridges over `agent_registry.names()` (`text_react`/`inline`/`vision_subagent`/`auto`), each delegating to product `build_agent(..., architecture=<name>, prompts=…)`. `_DEFAULT_ASK_ARCHITECTURE = "text_react"` (Q1's rename rule applied at birth). `rewrite_enabled` is a binding behavior flag (the binding calls reformulate or not), NOT a registry entry. Shipped `optimize_ask_architecture.yaml` dims: `architecture: [text_react]` only (inline/vision need vision models + an image task set — multimodal spec Q4; auto ≡ text_react on text models); the grid stays meaningful via `rewrite_enabled × retrieval_config × max_agent_turns`.
5. **`coding_agent_playbook` is external prior art only** (zero in-repo hits) — the gate→rubric→verdict layering + fail_fast short-circuit are reimplemented natively per §3.4; its file:line cites are design rationale.
6. **Q6 answered**: `benchmarks/pyproject.toml:7` still reads 0.1.0 while 0.1.1 is live on PyPI (the release never bumped the in-repo literal) — the runbook cites 0.1.1 as shipped; this PR bumps the literal to 0.2.0 (minor, additive API per §6) — flag to reviewer. The new `[ask]` extra = `["pydocs-mcp[ask-your-docs]>=0.5.2"]` placeholder floor (first release shipping the seam; product is 0.5.1) + added to the `all` union.
7. **Venv**: the worktree venv lacks rapidfuzz/langgraph again — first step reinstalls the benchmark deps + the ask stack (the recurring drill; extra-side tests run locally only).
8. **Conventions to reuse** (scout-verified): module-local `_Scripted*`/`_Fake*` dataclass doubles injected via constructor params (never monkeypatching paid clients); `benchmarks/tests/conftest.py` autouse guards already fail-loud any live-LLM path; `CritiqueClient` Protocol at `critique_refine.py:68-72`; `_assert_registry_keys` at `run_config.py:146-185`; the `[retrieval]` guard shape at `artifacts/tool_docs.py:15-31`; `ensure_available` shape at `skillopt.py:474-487`.

**Resolved open questions** (adopt-recommendations): Q1 bridges-now (delta 4). Q2 four bridges shipped, extra graph variants deferred to campaign evidence. Q3/Q5/Q7 deferred as specced. Q4 halving default. Q6 delta 6.

---

## Commit map (spec §6 slices; each independently green)

### Commit 1 — Slice A: rubric core (offline, benchmarks-side)
- `optimize/rubric/{__init__,model,gates,judge,sample_ledger}.py` per §3.4.1–3.4.5: `GateCheck`/`RubricCriterion`/`RubricConfig`/`rubric_config_hash(config, architecture=…)`; `gate_registry` (shared `_Registry`) + the six shipped gate kinds table; `ConfigurableRubricJudge` (+`FakeRubricJudge`) with strict parse-or-discard; `SampleRubricLedger` keyed `(fingerprint, split, task_id, objective_hash)` + per-sample transcript files.
- `run_config.py`: `AskRubricSettings` (runner/gates/criteria/fail_fast/weights) + top-level `rng_seed=0` + load-time weight/registry validation per §3.4.1.
- `trials_ledger.py`: optional `objective_hash` on `LedgerEntry`; `lookup` hash-matching with `None`-legacy back-compat. `_types.py`: `Provenance.rubric_hash`, `OptimizationBudget.max_judge_calls` (`_DEFAULT_MAX_JUDGE_CALLS = 200`). `orchestrator.py`: minimal threading of `objective_hash` to the ledger.
- Tests (RED first): `test_rubric_model.py`, `test_rubric_gates.py`, `test_rubric_judge.py`, `test_sample_ledger.py` + extensions to `test_run_config.py`/`test_trials_ledger.py`/`test_orchestrator.py`. ACs 7–12 (ledger halves), 19-partial.

### Commit 2 — Slice B: product seam + ask binding
- `agent.py`: `AskPrompts` frozen dataclass + `build_agent(..., prompts: AskPrompts | None = None)` substituting at the single assembly site (delta 1); `reformulate(..., *, rewrite_template=None)` (delta 2). Product tests in `tests/ask_your_docs/` (AC-1: assembled-prompt assertion via a ctx-capturing fake architecture on a scratch registry OR the recording-LLM shape; default byte-identical incl. per-arch fallback; rewrite override only changes the rewrite; app.py untouched).
- `optimize/ask_binding.py`: `AskBuildRequest`/`AskRunner` Protocol/`AskTranscript`/`ToolCallRecord`; `ask_architecture_registry` with the four product bridges (delta 4); `LangGraphAskRunner` behind `_require_ask_extra()` (RuntimeError naming `pip install "pydocs-mcp-eval[ask]"`); `FakeAskRunner`.
- `benchmarks/pyproject.toml`: `[ask]` extra + `all` union + version literal 0.1.0→0.2.0 (delta 6).
- Tests: `test_ask_binding.py` (registry names == product names; bridge delegation shape via a fake build_agent; extras guard AC-18; FakeAskRunner contract). ACs 1, 18.

### Commit 3 — Slice C: artifacts + fitness
- `artifacts/ask_prompt.py` (delimited two-section doc, budgets `_ASK_SYSTEM_TOKEN_BUDGET=1200`/`_ASK_REWRITE_TOKEN_BUDGET=300`, six-tool check via `TOOL_DOCS` keys, `[retrieval]` guard) + `ask_prompt_seed.md` (delta 3) ; `artifacts/ask_architecture.py` (canonical-YAML, validate + `enumerate_space`) ; `artifacts/retrieval_config.py` (overlay bytes; `validate()` keys ⊆ AppConfig sections via product import).
- `fitness/retrieval.py`: config-injection wiring (temp overlay → sole `config_paths` entry; split via `partition_task_ids`; scaffold line deleted).
- `fitness/ask_rubric.py`: the §3.4.4 loop (resume → run → gates → judge → verdict → persist), `FitnessReport` components per AC-13, predictive `max_judge_calls` → `BudgetExhausted`, `objective_hash()` exposure.
- Tests: the three artifact modules + `test_ask_rubric_fitness.py` + `test_retrieval_fitness.py` (renamed from scaffold). ACs 3–6, 9–10 (fitness halves), 13–15.

### Commit 4 — Slice D: config_search + shipped configs + dry-run + docs
- `optimizers/config_search.py` (`grid|random|halving`, seeded, free-tier, `accepted=False`).
- `optimize/configs/optimize_ask_prompt.yaml` + `optimize_ask_architecture.yaml` (§3.5 shapes, delta-4 dims).
- `__main__.py` dry-run: ask-binding `SKIPPED (extra not installed)` line; full $0.00 orchestrator pass with the fakes.
- Docs: `AGENT_TRACK.md` ask-optimization runbook section (spend model incl. `max_judge_calls`, preflight, sample-ledger reading, landing procedure — cites 0.1.1 as shipped baseline); `benchmarks/README.md` vendor-neutral config docs.
- Tests: `test_config_search.py` + `test_cli_dry_run.py` extension. ACs 16–17, 20.

### Task 5 — adversarial AC review (ultracode) → Task 6 — gates, push, PR
- Review workflow: refuters per slice + a never-spend/back-compat invariants refuter (no MCP change; paired track byte-untouched; existing ledgers replay; `import pydocs_eval` extra-free; dry-run $0.00).
- Gates: full CLAUDE.md set + `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`; doc-conformance harness green over README/AGENT_TRACK edits (they're outside the corpus — verify); no uv.lock change expected (benchmarks deps are a separate package; product diff adds no dep).
- Push, PR; NO merge and NO paid run without explicit user go (standing rule).
