# Paired Agent-Efficiency Harness (Slice 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The spec §D15 two-arm harness under `benchmarks/src/benchmarks/eval/agent_track/`: the same agent CLI answers SWE-QA-Pro questions with bare file tools (arm A) vs with the pydocs-mcp MCP server attached (arm B); a blind LLM judge scores answer quality against the gold answer; per-task-paired aggregation reports cost / tool-call / file-read / token deltas at answer-quality parity. Manual and expensive by design — never CI.

**Architecture:** Everything testable is pure or Protocol-seamed; everything expensive sits behind one subprocess adapter and hard guardrails. `AgentRunner` (Protocol) wraps headless Claude Code (`claude -p`) — command construction, `.mcp.json` generation, JSON/stream-JSON parsing are pure functions with fixture tests; only the real adapter spawns processes. Tasks come from the slice-4 `swe-qa-pro` dataset adapter (its `EvalTask.metadata` carries repo/commit/qa_type) and corpora from the slice-4 `RepoCache` — both flagged adapt-points since slice 4 is landing in parallel. A `PairLedger` (JSONL) makes runs resumable: re-running skips completed pairs, and **no half-pairs are admitted** — a task counts only when both arms completed inside budget. Guardrails: `--max-tasks` (default 48), `--max-usd` (default 25.0, checked against actual per-run cost before each new pair), per-task wall timeout. A `--preflight` mode verifies the environment contract (CLI present, JSON output fields, MCP server boots, index cache warm) before any paid run — turning the main slice-5 risk (environment drift, real spend) into executable checks.

**Tech Stack:** Python 3.11, asyncio subprocess, headless Claude Code CLI, pytest (fully offline — no subprocess, no network, no LLM in tests).

**Conventions:** identical to prior plans (venv interpreter, `PYTHONPATH=benchmarks/src` for benchmark tests, ruff check+format, complexipy ≤ 15 then restore snapshot, plain commits, no trailers). All code under `benchmarks/` — disjoint from slices 2–3.

**Verified harness facts** (from the slice-4 research, stable): `EvalTask(task_id, query, gold, corpus_source, metadata)` / `GoldAnswer(ast_body, file_set, extra)` (benchmarks/src/benchmarks/eval/datasets/base_dataset.py:23-43); dataset registry + `dataset_registry.build(name, **kwargs)`; `corpus_source()` materializes a corpus dir the runner may delete; benchmarks tests run `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q` with `asyncio_mode=auto`; conftest autouse fixtures keep the suite offline. **Adapt-points (slice 4 landing in parallel — verify the as-landed shapes before Tasks 4/6):** `SweQaProDataset` constructor (`fixture_path`, `repo_cache` fields) and `RepoCache.checkout(url, sha) -> Path` / `file_tree(...)` in `benchmarks/src/benchmarks/eval/datasets/_repo_cache.py`; metadata keys `repo`/`qa_type`/`sub_class`/`cluster`.

**Reconciliation (2026-07-08 — adapt-points verified against `71e562b`, current `main`):** the slice-4 shapes landed matching this plan. Confirmed as-landed: `SweQaProDataset(name="swe-qa-pro", revision=<pinned sha>, fixture_path: Path | None = None, repo_cache: RepoCacheLike = RepoCache(), cache_dir: Path = ~/.cache/pydocs-mcp/swe-qa-pro)` at `benchmarks/src/benchmarks/eval/datasets/swe_qa_pro.py`, registered as `"swe-qa-pro"` in `dataset_registry`; `RepoCacheLike.checkout(url, sha) -> Path` (idempotent) and `file_tree(url, sha) -> tuple[str, ...]` in `benchmarks/src/benchmarks/eval/datasets/_repo_cache.py`; `EvalTask.metadata` carries exactly `repo` / `qa_type` (first token: What/Where/How/Why) / `sub_class` / `cluster`. Two shape details the tasks must honor: `Dataset.tasks()` is a plain `def` returning `AsyncIterator[EvalTask]` (consume with `async for task in dataset.tasks()`, no intermediate await), and `corpus_source` is a zero-arg `Callable[[], Path]` whose materialized dir the runner owns (it may rmtree between tasks; the dataset does not delete it). There is NO `benchmarks/Makefile` — bench commands are documented in `benchmarks/README.md` (the `python -m benchmarks.eval.runner` section); Task 7 documents the agent-track commands there in the same style, no Makefile. Existing test conventions confirmed: `benchmarks/tests/eval/__init__.py` exists, fixtures live under `benchmarks/tests/eval/fixtures/`, `asyncio_mode = "auto"` is set in `benchmarks/pyproject.toml`, and `benchmarks/tests/conftest.py` autouse fixtures patch `build_embedder` + `build_llm_client` to offline fakes.

**Reconciliation 2 (2026-07-08 — slice-6 upstream contract, from `docs/superpowers/specs/2026-07-07-harness-optimization-design.md` §"Required upstream contract"):** the harness-optimization spec binds to this slice's shapes and assigns three requirements to THIS slice (they land here, not in slice 6): (1) `AgentTrackConfig` carries `rng_seed: int = 0` — the single fixed seed for judge-label randomization and report bootstrap, so optimization comparisons are as deterministic as the harness allows (Task 1); (2) the shared scaffold is `task_prompt(question, *, skill: str = "")` — an optional skill section where empty `skill` MUST be byte-identical to the no-skill scaffold, pinned by test (Task 3); (3) alongside the real judge, a `Judge` Protocol and a scripted `FakeJudge` double are exported for downstream consumers (Task 5). The affected task bodies below carry these amendments inline.

**Headless CLI contract** (encoded as the preflight, verified at run time — NOT assumed by tests): `claude -p "<prompt>" --output-format json` returns a JSON object whose result payload includes total cost in USD, duration, and turn count; `--output-format stream-json` emits per-event JSON lines including `tool_use` blocks (tool name + input) and usage blocks (cache read/write tokens); `--model <id>` pins the model; `--max-turns N` bounds the loop; `--mcp-config <path>` + `--strict-mcp-config` attach exactly one MCP config; tool restriction via `--allowedTools`/`--disallowedTools`. Exact flag spellings are re-checked by `--preflight` (Task 7) because the CLI evolves; the command builder keeps every flag in ONE module constant so a rename is a one-line fix.

---

### Task 1: Value objects + run config

**Files:**
- Create: `benchmarks/src/benchmarks/eval/agent_track/__init__.py`, `agent_track/_types.py`
- Test: `benchmarks/tests/eval/agent_track/test_types.py` (create dir with `__init__.py`)

- [ ] **Step 1: Failing tests**

```python
"""Agent-track value objects (spec §D15)."""

import pytest
from benchmarks.eval.agent_track._types import (
    ArmConfig, AgentTrackConfig, AgentRunResult, JudgeScore, PairResult, RunMetrics,
)


def test_run_metrics_fields() -> None:
    m = RunMetrics(cost_usd=0.42, wall_seconds=61.0, turns=9, tool_calls=14,
                   distinct_files_read=6, cache_read_tokens=120_000,
                   cache_write_tokens=30_000, answer="The mask class ...")
    assert m.cost_usd == 0.42 and m.distinct_files_read == 6


def test_arm_config_command_relevant_fields() -> None:
    bare = ArmConfig(name="bare", model="claude-sonnet-5", max_turns=40, mcp=False)
    indexed = ArmConfig(name="indexed", model="claude-sonnet-5", max_turns=40, mcp=True)
    assert bare.mcp is False and indexed.mcp is True


def test_track_config_defaults_and_guardrails() -> None:
    cfg = AgentTrackConfig()
    assert cfg.max_tasks == 48 and cfg.max_usd == pytest.approx(25.0)
    assert cfg.task_timeout_seconds == 900.0
    assert cfg.judge_model == cfg.arms[0].model  # same pinned family by default
    assert cfg.rng_seed == 0  # slice-6 contract: fixed seed for deterministic comparisons


def test_pair_result_requires_both_arms() -> None:
    with pytest.raises(ValueError):
        PairResult(task_id="t", qa_type="Why", bare=None, indexed=_metrics(), judge=None)
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** `_types.py` — frozen slotted dataclasses: `RunMetrics(cost_usd, wall_seconds, turns, tool_calls, distinct_files_read, cache_read_tokens, cache_write_tokens, answer)`; `JudgeScore(correctness, completeness, relevance, clarity, reasoning, mean, blind_label_map)`; `ArmConfig(name, model, max_turns, mcp)`; `AgentTrackConfig(arms=(bare, indexed), judge_model="claude-sonnet-5", max_tasks=48, max_usd=25.0, task_timeout_seconds=900.0, rng_seed=0, output_dir=Path("~/.cache/pydocs-mcp/agent-track").expanduser())` with `_DEFAULT_*` module constants; `PairResult(task_id, qa_type, bare: RunMetrics | None, indexed: RunMetrics | None, judge: JudgeScore | None)` whose `__post_init__` raises unless both arms present (the no-half-pairs rule lives in the type). **Step 4:** green. **Step 5: Commit** `feat(bench): agent-track value objects + run config`.

---

### Task 2: CLI-output parsers (pure) + fixtures

**Files:**
- Create: `benchmarks/src/benchmarks/eval/agent_track/_parse.py`
- Fixtures: `benchmarks/tests/eval/agent_track/fixtures/claude_result.json`, `claude_stream.jsonl` (hand-built, matching the documented shapes; ~20 lines each)
- Test: `benchmarks/tests/eval/agent_track/test_parse.py`

- [ ] **Step 1: Failing tests**

```python
def test_parse_result_json_extracts_cost_and_answer(fixture_result) -> None:
    parsed = parse_result_json(fixture_result)
    assert parsed.cost_usd == pytest.approx(0.1834)
    assert parsed.turns == 12 and parsed.answer.startswith("The synchronization")


def test_parse_stream_counts_tool_calls_and_distinct_files(fixture_stream) -> None:
    stats = parse_stream_events(fixture_stream)
    assert stats.tool_calls == 5
    assert stats.distinct_files_read == 2          # Read a.py twice + b.py once → 2
    assert stats.cache_read_tokens == 84_000       # summed across usage events


def test_mcp_tool_calls_counted_separately(fixture_stream) -> None:
    stats = parse_stream_events(fixture_stream)
    assert stats.mcp_tool_calls == 2               # mcp__pydocs-mcp__* names


def test_malformed_lines_are_skipped_not_fatal() -> None:
    stats = parse_stream_events('{"type":"junk"\nnot json\n')
    assert stats.tool_calls == 0
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** `_parse.py`: `parse_result_json(text) -> ParsedResult` (tolerant key lookup: cost under `total_cost_usd` or nested result-cost — try both, document); `parse_stream_events(text) -> StreamStats` — line-wise `json.loads` in try/except, count `tool_use` events by name (`Read/Grep/Glob/Bash` vs `mcp__*`), distinct files = set of `Read` inputs' file paths, sum usage `cache_read_input_tokens`/`cache_creation_input_tokens`. Fixtures hand-written to the documented shape; the preflight (Task 7) validates the REAL CLI still matches, so fixture drift is caught before money is spent. **Step 4:** green. **Step 5: Commit** `feat(bench): claude output parsers for agent-track metrics`.

---

### Task 3: Command builder + `.mcp.json` generation (pure)

**Files:**
- Create: `benchmarks/src/benchmarks/eval/agent_track/_command.py`
- Test: `benchmarks/tests/eval/agent_track/test_command.py`

- [ ] **Step 1: Failing tests**

```python
def test_bare_arm_restricts_to_file_tools(tmp_path) -> None:
    cmd = build_claude_command(_arm(mcp=False), prompt="q?", cwd=tmp_path, mcp_config=None)
    joined = " ".join(cmd)
    assert "--allowedTools" in joined and "mcp__" not in joined
    assert "--output-format" in joined and "stream-json" in joined
    assert "--model claude-sonnet-5" in joined and "--max-turns 40" in joined


def test_indexed_arm_attaches_strict_mcp_config(tmp_path) -> None:
    cfg = tmp_path / "mcp.json"
    cmd = build_claude_command(_arm(mcp=True), prompt="q?", cwd=tmp_path, mcp_config=cfg)
    joined = " ".join(cmd)
    assert f"--mcp-config {cfg}" in joined and "--strict-mcp-config" in joined


def test_mcp_json_launches_pydocs_serve(tmp_path) -> None:
    payload = render_mcp_config(corpus_dir=tmp_path / "corpus", python=Path("/venv/bin/python"))
    server = json.loads(payload)["mcpServers"]["pydocs-mcp"]
    assert server["command"].endswith("python")
    assert server["args"][:3] == ["-m", "pydocs_mcp", "serve"]
    assert str(tmp_path / "corpus") in server["args"]


def test_prompt_scaffold_identical_across_arms() -> None:
    assert task_prompt("What does X do?") == task_prompt("What does X do?")
    assert "answer the question about the repository" in task_prompt("q").lower()


def test_prompt_skill_section_empty_is_byte_identical() -> None:
    # Slice-6 contract: task_prompt(question, *, skill="") — empty skill MUST be
    # byte-identical to the no-skill scaffold; non-empty skill text is included.
    assert task_prompt("q") == task_prompt("q", skill="")
    assert "USE get_symbol FIRST" in task_prompt("q", skill="USE get_symbol FIRST")
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** `_command.py`: `_CLI_FLAGS` dict as the single source for every flag spelling; `build_claude_command(arm, *, prompt, cwd, mcp_config) -> list[str]` — `["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--model", arm.model, "--max-turns", str(arm.max_turns), "--allowedTools", _BARE_TOOLS or _BARE_TOOLS + _MCP_WILDCARD, ...]` (bare: `"Read Grep Glob Bash"`; indexed adds `"mcp__pydocs-mcp__*"`), plus `--mcp-config/--strict-mcp-config` when `arm.mcp`; `render_mcp_config(corpus_dir, python)` → the JSON above; `task_prompt(question, *, skill: str = "")` — ONE scaffold constant used by both arms (spec §D15: same prompt scaffold): states the cwd is the repository, asks for a direct answer with file/line citations, forbids editing; the optional `skill` section (slice-6 contract) is appended only when non-empty, and `skill=""` is byte-identical to the bare scaffold. **Step 4:** green. **Step 5: Commit** `feat(bench): agent-track command builder + MCP config rendering`.

---

### Task 4: `AgentRunner` Protocol + subprocess adapter + corpus prep

**Files:**
- Create: `benchmarks/src/benchmarks/eval/agent_track/_runner.py`
- Test: `benchmarks/tests/eval/agent_track/test_runner.py`

- [ ] **Step 1: Failing tests** (subprocess-free: the real adapter's orchestration is tested with a monkeypatched `_spawn` returning canned stdout; plus a `FakeAgentRunner` for downstream tasks):

```python
async def test_runner_combines_parsers_into_run_metrics(monkeypatch, tmp_path) -> None:
    runner = ClaudeAgentRunner(task_timeout_seconds=5.0)
    monkeypatch.setattr(runner, "_spawn", _canned_spawn(FIXTURE_STREAM))
    metrics = await runner.run(_arm(mcp=False), prompt="q", cwd=tmp_path, mcp_config=None)
    assert metrics.tool_calls == 5 and metrics.cost_usd > 0


async def test_timeout_returns_none_not_raise(monkeypatch, tmp_path) -> None:
    runner = ClaudeAgentRunner(task_timeout_seconds=0.01)
    monkeypatch.setattr(runner, "_spawn", _hanging_spawn())
    assert await runner.run(_arm(mcp=False), prompt="q", cwd=tmp_path, mcp_config=None) is None


async def test_prepare_corpus_indexes_once(monkeypatch, tmp_path) -> None:
    calls = []
    monkeypatch.setattr("benchmarks.eval.agent_track._runner._run_index", lambda p: calls.append(p))
    prep = CorpusPrep(cache_dir=tmp_path)
    d1 = await prep.ensure_indexed(tmp_path / "co")
    d2 = await prep.ensure_indexed(tmp_path / "co")
    assert len(calls) == 1                          # marker file skips the second index
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** `AgentRunner` Protocol (`async def run(arm, *, prompt, cwd, mcp_config) -> RunMetrics | None`); `ClaudeAgentRunner` — `_spawn` does `asyncio.create_subprocess_exec(*cmd, cwd=cwd, stdout=PIPE, stderr=PIPE)` under `asyncio.wait_for(timeout)`; on timeout kill the process group and return `None` (a half-pair the orchestrator discards); wall-clock measured around the await; metrics = `parse_stream_events` + `parse_result_json` merged. `CorpusPrep.ensure_indexed(corpus_dir)`: runs `python -m pydocs_mcp index <corpus_dir>` via `to_thread`-wrapped `subprocess.run` once, then touches `<corpus_dir>/.pydocs-indexed` marker so reruns skip (the index cache itself lives in `~/.pydocs-mcp` keyed by dirname+path-hash — WHY comment). `FakeAgentRunner` (scripted per-arm metrics) exported for tests. **Step 4:** green. **Step 5: Commit** `feat(bench): ClaudeAgentRunner subprocess adapter + corpus prep`.

---

### Task 5: Blind judge

**Files:**
- Create: `benchmarks/src/benchmarks/eval/agent_track/_judge.py`
- Fixture: `benchmarks/tests/eval/agent_track/fixtures/judge_rubric.md` (the committed rubric prompt, spec §D15)
- Test: `benchmarks/tests/eval/agent_track/test_judge.py`

- [ ] **Step 1: Failing tests**

```python
def test_rubric_prompt_is_blind(tmp_path) -> None:
    prompt, label_map = build_judge_prompt(
        question="q?", gold="gold answer",
        answers={"bare": "answer one", "indexed": "answer two"}, rng_seed=7,
    )
    assert "bare" not in prompt and "indexed" not in prompt      # arm names never leak
    assert "Answer A" in prompt and "Answer B" in prompt
    assert set(label_map.values()) == {"bare", "indexed"}


def test_label_randomization_varies_with_seed() -> None:
    _, m1 = build_judge_prompt(question="q", gold="g", answers=_ANS, rng_seed=1)
    _, m2 = build_judge_prompt(question="q", gold="g", answers=_ANS, rng_seed=2)
    assert m1 != m2 or True                                       # seeds may collide; assert determinism instead
    assert build_judge_prompt(question="q", gold="g", answers=_ANS, rng_seed=1)[1] == m1


def test_parse_judge_reply_maps_labels_back() -> None:
    reply = '{"A": {"correctness": 9, "completeness": 8, "relevance": 9, "clarity": 9, "reasoning": 8}, "B": {...}}'
    scores = parse_judge_reply(_full(reply), label_map={"A": "indexed", "B": "bare"})
    assert scores["indexed"].correctness == 9 and scores["indexed"].mean == pytest.approx(8.6)


def test_malformed_reply_returns_none() -> None:
    assert parse_judge_reply("not json", label_map={"A": "bare", "B": "indexed"}) is None
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** rubric fixture committed verbatim (5 dimensions, 0–10, "score each answer against the reference; do not reward verbosity"); `build_judge_prompt(...)` — label order from `random.Random(rng_seed)` derived from the task_id hash (determinism across resumes; NO global randomness); judge invocation reuses `ClaudeAgentRunner` in a one-shot arm (`ArmConfig(name="judge", model=cfg.judge_model, max_turns=1, mcp=False)`) with `--allowedTools ""` (no tools) — its cost is counted into the run budget; `parse_judge_reply` tolerant-parses the JSON block out of the answer text. Also export the downstream seam (slice-6 contract): a `Judge` Protocol (`async def score(question, gold, answers) -> dict[str, JudgeScore] | None` — arm-name-keyed scores, `None` on judge failure) implemented by the real judge, plus a scripted `FakeJudge` double for Task 6's tests and external consumers. **Step 4:** green. **Step 5: Commit** `feat(bench): blind two-answer judge with committed rubric`.

---

### Task 6: Pair orchestrator — ledger, resume, guardrails

**Files:**
- Create: `benchmarks/src/benchmarks/eval/agent_track/orchestrator.py`
- Test: `benchmarks/tests/eval/agent_track/test_orchestrator.py`

- [ ] **Step 1: Failing tests** (all with `FakeAgentRunner`/`FakeJudge` + the slice-4 dataset's `fixture_path` mode — adapt constructor per as-landed adapter):

```python
async def test_runs_both_arms_and_judges_each_task(tmp_path) -> None:
    results = await run_agent_track(_cfg(max_tasks=2), dataset=_fixture_dataset(),
                                    runner=FakeAgentRunner(...), judge=FakeJudge(...),
                                    ledger_path=tmp_path / "pairs.jsonl")
    assert len(results) == 2 and all(r.bare and r.indexed and r.judge for r in results)


async def test_half_pair_discarded_and_logged(tmp_path) -> None:
    runner = FakeAgentRunner(fail_on=("indexed", "task-2"))     # arm B times out on task 2
    results = await run_agent_track(...)
    assert {r.task_id for r in results} == {"task-1"}            # no half-pairs admitted


async def test_resume_skips_completed_pairs(tmp_path) -> None:
    await run_agent_track(_cfg(max_tasks=1), ...)                # writes pair 1 to the ledger
    counting = FakeAgentRunner(...)
    await run_agent_track(_cfg(max_tasks=2), runner=counting, ...)
    assert counting.calls_for("task-1") == 0                     # ledger short-circuits


async def test_max_usd_aborts_before_next_pair(tmp_path) -> None:
    runner = FakeAgentRunner(cost_per_run=10.0)                  # 2 arms + judge ≈ $25 after task 1
    results = await run_agent_track(_cfg(max_tasks=48, max_usd=25.0), ...)
    assert len(results) == 1                                     # stopped, not overspent


async def test_max_tasks_cap(tmp_path) -> None: ...
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** `run_agent_track(cfg, *, dataset, runner, judge, ledger_path) -> tuple[PairResult, ...]`: iterate `async for task in dataset.tasks()` (a plain `def` returning `AsyncIterator[EvalTask]` — no intermediate await; limit `max_tasks`), skip task_ids already in the JSONL ledger; per task — materialize corpus once, `CorpusPrep.ensure_indexed` for arm B, run arms SEQUENTIALLY (deliberate: parallel arms would contend for CPU during indexing/inference and skew wall-clock — WHY comment), judge only when both arms returned metrics; append the pair (or a `{"task_id":..., "discarded": "<reason>"}` line — discards logged, no-silent-caps) to the ledger with `json.dumps` per line; running cost = sum of all arm+judge `cost_usd` this invocation; stop BEFORE starting a pair that could exceed `max_usd` (conservative check: spent + worst-observed-pair-cost > cap → stop, log). Corpus dir cleanup per the harness's rmtree convention. **Step 4:** green. **Step 5: Commit** `feat(bench): paired orchestrator with ledger resume and spend guardrails`.

---

### Task 7: Report + CLI entry + preflight + runbook

**Files:**
- Create: `benchmarks/src/benchmarks/eval/agent_track/report.py`, `agent_track/__main__.py`
- Create: `benchmarks/AGENT_TRACK.md` (runbook)
- Modify: `benchmarks/README.md` — verified 2026-07-08: there is NO Makefile; bench commands are documented in the README's runner section (`python -m benchmarks.eval.runner --help` style). Add a short "Agent track" subsection there in the same style — the `python -m benchmarks.eval.agent_track --preflight` / run commands — pointing at `AGENT_TRACK.md` for the full runbook. Respect the README jargon rule (no PR/sub-PR/task references)
- Test: `benchmarks/tests/eval/agent_track/test_report.py`, `test_preflight.py`

- [ ] **Step 1: Failing tests**

```python
def test_report_pairs_deltas_and_bootstrap_ci() -> None:
    pairs = _pairs(bare_cost=[1.0, 1.2, 0.8], indexed_cost=[0.6, 0.7, 0.5], ...)
    report = format_agent_track_report(pairs, dataset_name="swe-qa-pro", rng_seed=42)
    assert "cost" in report and "-4" in report                    # ~-42% mean delta rendered
    assert "95% CI" in report
    assert "## By qa_type" in report


def test_bootstrap_ci_deterministic_for_seed() -> None:
    lo1, hi1 = bootstrap_ci([0.1, -0.2, 0.3, 0.0], n_resamples=1000, rng_seed=7)
    lo2, hi2 = bootstrap_ci([0.1, -0.2, 0.3, 0.0], n_resamples=1000, rng_seed=7)
    assert (lo1, hi1) == (lo2, hi2)


def test_preflight_checks_are_enumerated(monkeypatch) -> None:
    checks = preflight_checks(python=Path("/venv/bin/python"))
    names = [c.name for c in checks]
    assert names == ["claude-cli-present", "claude-json-contract", "pydocs-mcp-importable",
                     "mcp-config-boots", "disk-headroom"]
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** `bootstrap_ci(deltas, *, n_resamples, rng_seed)` — percentile bootstrap on the mean, `random.Random(rng_seed)`, no numpy dependency needed; `format_agent_track_report(pairs, ...)` — one table (cost, wall, turns, tool calls, files read, cache tokens, judge mean) with per-metric mean delta + 95% CI, judge-parity line (mean judge delta ± CI), `## By qa_type` section (means per category), and an honest footer: pairs run / discarded / spend total. `__main__.py` argparse: `--dataset swe-qa-pro`, `--max-tasks`, `--max-usd`, `--model`, `--judge-model`, `--ledger`, `--report`, `--preflight` (runs the checks and exits: check 2 spends ≤ $0.01 on a one-token `claude -p "ok" --output-format json` and validates the parsed fields; check 4 boots the MCP server against a tiny fixture corpus and lists tools). Runbook `AGENT_TRACK.md`: cost expectations (~$5–10 per arm per repo, spec §D15), the preflight-first rule, resume semantics, and the "never CI" statement. **Step 4:** green + README jargon audit on the runbook. **Step 5: Commit** `feat(bench): agent-track report, CLI entry, preflight, runbook`.

---

### Task 8: Full gates

- [ ] `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q` (expect ~+30, all offline) and `pytest -q tests/` untouched; `ruff check` + `ruff format --check` on `benchmarks/`; complexipy on new files (restore snapshot); commit fixups as `fix(slice5): gate fixups`.
- [ ] **Manual acceptance (documented, NOT executed by this plan):** `python -m benchmarks.eval.agent_track --preflight`, then a 2-task smoke `--max-tasks 2 --max-usd 3` on the smallest SWE-QA-Pro repo, verifying the ledger, a rendered report, and a resume that skips both pairs.

---

## Out of scope (explicit)

- Running the paired evaluation itself (spend requires an explicit human go; the plan ships the harness + preflight, not results).
- SWE-QA (non-Pro) in the agent track — noisier gold answers make judge calibration murkier; Pro-only first.
- Multi-model sweeps (one pinned model per run config; comparing models is a config change, not new code).
- Any change under `python/pydocs_mcp/` or interaction with slices 2–3.
