# Harness Optimization (Slice 6) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The offline optimization layer of `docs/superpowers/specs/2026-07-07-harness-optimization-design.md` (rev 2) under `benchmarks/src/benchmarks/optimize/`: two text artifacts (`tool_docs`, `usage_skill`) improved by two co-equal optimizers (`critique_refine`, `skillopt`) behind one `HarnessOptimizer` adapter Protocol, evaluated on the slice-5 paired agent harness through a `FitnessLadder` with a judge-parity pre-gate, and accepted only on a held-out split with a real margin. Optimizers propose diffs a human lands — never runtime self-modification. Manual and expensive by design — never CI.

**Architecture:** Everything under test is deterministic and offline (§D8). One binding module (`optimize/_agent_track_binding.py`) is the single import point for agent-track shapes; one subprocess exists in the layer (the `skillopt` `train.py` invocation, never in tests); the paid fitness wraps `run_agent_track` with the candidate injected. Product-code footprint is exactly ONE audited touch: promoting the §D13 lint constants to importable (§D2b). The §D6 `tool_docs` injection ships as a benchmarks-side wrapper server (`optimize/_overlay_server.py`) that re-binds `pydocs_mcp.application.tool_docs` module attributes before delegating to `pydocs_mcp.server.run` — the spec's recorded preferred alternative, verified feasible 2026-07-08 (see Verified facts), so `AppConfig.tool_docs_overlay_path` is NOT added.

**Tech Stack:** Python 3.11, pydantic (run config), asyncio, pytest (fully offline — no subprocess, no network, no live LLM in tests), difflib (proposal diffs), hashlib/sha256 (splits + fingerprints).

**Conventions:** identical to prior plans — venv interpreter `/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python`; benchmark tests `PYTHONPATH=benchmarks/src <venv-python> -m pytest benchmarks/tests/optimize/ -q` (`asyncio_mode=auto` from `benchmarks/pyproject.toml`); product tests `PYTHONPATH=python <venv-python> -m pytest tests/ -q`; `ruff check` + `ruff format` before every commit; complexipy ≤ 15 then `git checkout complexipy-snapshot.json`; plain commits, no trailers, no Co-Authored-By.

**Verified facts (2026-07-08; re-verified 2026-07-09 against `main@92b40be`, the slice-5 merge — `server.py` imports still at lines 183/249, lint constants still test-private at `test_tool_docs_lint.py:13-21`, the `agent_track` package landed with `task_prompt(question, *, skill="")` at `_command.py:149`; Task 0 still re-confirms the full contract):**

- `python/pydocs_mcp/application/tool_docs.py` (99 lines): `TOOL_DOCS: dict[str, str]` at line 22 (six keys, insertion-ordered), `SERVER_INSTRUCTIONS` at line 89. `server.py` consumes both via **function-local imports** — `from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS` inside `run()` (server.py:183) and `... import TOOL_DOCS` inside `_register_tools()` (server.py:249), passed as `description=TOOL_DOCS[name]` (server.py:261). Because both reads happen at call time, re-binding the module attributes BEFORE calling `run()` injects overlay text with zero product change — this is what makes the §D6 wrapper the smaller footprint.
- §D13 lint constants live as privates in `tests/application/test_tool_docs_lint.py:13-22`: `_REQUIRED_MARKERS` (tuple of six section markers), `_CHARS_PER_TOKEN = 4`, `_PER_TOOL_TOKEN_BUDGET = 500`, `_TOTAL_TOKEN_BUDGET = 2400`.
- `benchmarks/src/benchmarks/eval/serialization.py:36` has `_Registry(Generic[T])` (name → class registry, `register(name)` decorator + `build(name, **kwargs)`); `dataset_registry`/others are instances of it. The optimize registries reuse it (in-repo private import with a WHY comment — DRY beats visibility ceremony here).
- `benchmarks/pyproject.toml` `[project.optional-dependencies]` currently has `mlflow` and `all`; `optimizers-skillopt` slots in beside them.
- The slice-5 agent-track surface this plan binds to (upstream contract, spec §"Required upstream contract"): `AgentTrackConfig` (with `rng_seed`), `run_agent_track(cfg, *, dataset, runner, judge, ledger_path)`, `PairResult`/`RunMetrics(cost_usd, wall_seconds, turns, tool_calls, distinct_files_read, cache_read_tokens, cache_write_tokens, answer)`/`JudgeScore` (five dims + `mean`), `AgentRunner` Protocol + `FakeAgentRunner`, `Judge` Protocol + `FakeJudge`, `task_prompt(question, *, skill: str = "")` (empty-skill byte-parity), JSONL pair ledger, enforced `--max-usd`. **Task 0 verifies every name as landed and the binding module is the only file that imports agent-track code.**
- SWE-QA-Pro dataset + split inputs: `dataset_registry.build("swe-qa-pro", fixture_path=..., repo_cache=...)`; `Dataset.tasks()` is a plain `def` returning `AsyncIterator[EvalTask]`; `EvalTask.task_id` is the split key.

**Spend gate (hard):** this slice ships machinery + `--dry-run` (walks the whole pipeline with `FakeAgentRunner`, spends nothing). A paid optimization run requires an explicit user go. No test spawns a subprocess, opens a socket, or calls a live LLM.

**Settled decisions — do not reopen (spec rev 2 + user decisions):** both optimizers co-equal in v1; text artifacts only (structured/YAML artifacts out of v1; `retrieval` fitness is scaffolding, wired into NO v1 ladder); judge parity is a pre-gate (not a weighted term); acceptance is holdout-gated with margin `0.02`; SkillOpt is a clone-and-run env-plugin contract driven by `train.py` subprocess, not a callback library.

---

### Task 0: Agent-track binding module + upstream-contract verification

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/__init__.py` (empty), `benchmarks/src/benchmarks/optimize/_agent_track_binding.py`
- Test: `benchmarks/tests/optimize/__init__.py` (empty), `benchmarks/tests/optimize/test_agent_track_binding.py`

- [ ] **Step 0: Verify the as-landed slice-5 shapes.** Read `benchmarks/src/benchmarks/eval/agent_track/` (`_types.py`, `_runner.py`, `_judge.py`, `_command.py`, `orchestrator.py`). Confirm every contract name above exists with the stated signature. If a name differs, the binding module (below) absorbs the difference (import-and-rename) and you note it in the module docstring; if a SHAPE is missing (no `rng_seed`, no `skill=` param, no `FakeJudge`), STOP and report BLOCKED — that is a slice-5 fast-follow, not a slice-6 workaround.

- [ ] **Step 1: Failing test**

```python
"""The binding is the ONLY place optimize/ imports agent-track code (spec §upstream contract)."""

import inspect
import subprocess

from benchmarks.optimize import _agent_track_binding as b


def test_binding_reexports_full_contract() -> None:
    for name in ("AgentTrackConfig", "run_agent_track", "PairResult", "RunMetrics",
                 "JudgeScore", "AgentRunner", "FakeAgentRunner", "Judge", "FakeJudge",
                 "task_prompt"):
        assert hasattr(b, name), f"binding misses contract name {name!r}"


def test_task_prompt_has_kwonly_skill_with_empty_default() -> None:
    p = inspect.signature(b.task_prompt).parameters["skill"]
    assert p.kind is inspect.Parameter.KEYWORD_ONLY and p.default == ""


def test_track_config_carries_seed_and_guardrails() -> None:
    cfg = b.AgentTrackConfig()
    for field in ("rng_seed", "max_usd", "max_tasks", "task_timeout_seconds", "arms", "judge_model"):
        assert hasattr(cfg, field)


def test_binding_is_the_only_agent_track_import() -> None:
    # grep the optimize package: no file other than the binding imports benchmarks.eval.agent_track
    out = subprocess.run(  # noqa: S603 — read-only grep over our own tree, not an external service
        ["grep", "-rl", "eval.agent_track", "benchmarks/src/benchmarks/optimize/"],
        capture_output=True, text=True, check=False,
    ).stdout.splitlines()
    assert out == ["benchmarks/src/benchmarks/optimize/_agent_track_binding.py"]
```

(The grep runs `grep` the binary — allowed: it is not a network/LLM/agent subprocess; if the repo's offline-test convention forbids even that, replace with `Path.rglob` + `read_text` scanning, same assertion.)

- [ ] **Step 2:** FAIL (module missing). **Step 3: Implement** `_agent_track_binding.py`: a docstring stating it is the single seam (spec §"Required upstream contract") and that a slice-5 rename is fixed HERE and nowhere else; then plain re-exports: `from benchmarks.eval.agent_track._types import AgentTrackConfig, PairResult, RunMetrics, JudgeScore` / `from benchmarks.eval.agent_track._runner import AgentRunner, FakeAgentRunner` / `from benchmarks.eval.agent_track._judge import Judge, FakeJudge` / `from benchmarks.eval.agent_track._command import task_prompt` / `from benchmarks.eval.agent_track.orchestrator import run_agent_track` (adjust module paths to as-landed; `__all__` lists the ten names). Prefer the package's public `__init__` re-exports if slice 5 shipped them. **Step 4:** green (use `Path.rglob` fallback if the grep subprocess trips the offline conftest). **Step 5: Commit** `feat(bench): agent-track binding module — slice-6 upstream contract pinned`.

---

### Task 1: §D2b — promote the tool-docs lint constants (the ONE product touch)

**Files:**
- Modify: `python/pydocs_mcp/application/tool_docs.py` (add a public contract block)
- Modify: `tests/application/test_tool_docs_lint.py:13-22` (import instead of define)
- Test: extend `tests/application/test_tool_docs_lint.py`

- [ ] **Step 1: Failing test** (append to the lint test file)

```python
def test_contract_constants_are_importable_and_pinned() -> None:
    from pydocs_mcp.application.tool_docs import (
        CHARS_PER_TOKEN, PER_TOOL_TOKEN_BUDGET, REQUIRED_MARKERS, TOTAL_TOKEN_BUDGET,
    )
    assert (CHARS_PER_TOKEN, PER_TOOL_TOKEN_BUDGET, TOTAL_TOKEN_BUDGET) == (4, 500, 2400)
    assert len(REQUIRED_MARKERS) == 6
```

- [ ] **Step 2:** FAIL (ImportError). **Step 3: Implement:** move the four constants VERBATIM from the test into `tool_docs.py` under a `# --- §D13 contract constants (importable: the offline optimizer's validate() shares them; drift here is drift in the lint) ---` block, public names (`REQUIRED_MARKERS`, `CHARS_PER_TOKEN`, `PER_TOOL_TOKEN_BUDGET`, `TOTAL_TOKEN_BUDGET`); rewrite the test's references to import them (delete the private copies). The lint's assertions themselves change not one byte. **Step 4:** `PYTHONPATH=python <venv> -m pytest tests/application/test_tool_docs_lint.py -q` green; run the FULL product suite (`PYTHONPATH=python <venv> -m pytest tests/ -q`) — untouched elsewhere. **Step 5: Commit** `refactor(app): promote tool-docs lint constants to importable contract (§D2b)`.

---

### Task 2: Optimize value objects, Protocols, registries

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/_types.py`, `optimize/protocols.py`, `optimize/registries.py`
- Test: `benchmarks/tests/optimize/test_types.py`

- [ ] **Step 1: Failing tests**

```python
import math

import pytest
from benchmarks.optimize._types import (
    FitnessReport, OptimizationBudget, OptimizationResult, Provenance, Trial,
)
from benchmarks.optimize.registries import artifact_registry, fitness_registry, optimizer_registry


def test_fitness_report_fields() -> None:
    r = FitnessReport(score=0.195, components={"tokens_fraction": 0.2}, cost_usd=1.5, n_samples=6)
    assert r.score == pytest.approx(0.195) and r.n_samples == 6


def test_budget_defaults_are_conservative() -> None:
    bud = OptimizationBudget()
    assert bud.max_trials == 20 and bud.max_usd == pytest.approx(40.0)
    assert bud.wall_timeout_seconds == 14400.0


def test_trial_and_result_shapes() -> None:
    t = Trial(fingerprint="f" * 64, rung_scores=(0.1,), cost_usd=0.5, violations=())
    res = OptimizationResult(best=None, accepted=False, trials=(t,), total_usd=0.5,
                             provenance=Provenance(seed_fingerprint="s" * 64, dataset_revision="r",
                                                   model_ids=("claude-sonnet-5",), optimizer="critique_refine"))
    assert res.accepted is False and res.trials[0].cost_usd == pytest.approx(0.5)
    assert math.isfinite(t.rung_scores[0])


def test_registries_are_distinct_and_register() -> None:
    assert artifact_registry is not fitness_registry is not optimizer_registry

    @artifact_registry.register("_probe")
    class _Probe:  # noqa: N801 — throwaway registry probe
        pass

    assert artifact_registry.build("_probe").__class__ is _Probe
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** `_types.py` — frozen slotted dataclasses with `_DEFAULT_*` module constants (`_DEFAULT_MAX_TRIALS = 20`, `_DEFAULT_MAX_USD = 40.0`, `_DEFAULT_WALL_TIMEOUT = 14400.0`): `FitnessReport(score: float, components: Mapping[str, float], cost_usd: float, n_samples: int)`; `OptimizationBudget(max_trials, max_usd, wall_timeout_seconds)`; `Trial(fingerprint: str, rung_scores: tuple[float, ...], cost_usd: float, violations: tuple[str, ...])`; `Provenance(seed_fingerprint, dataset_revision, model_ids: tuple[str, ...], optimizer: str)`; `OptimizationResult(best: object | None, accepted: bool, trials: tuple[Trial, ...], total_usd: float, provenance: Provenance, seed_holdout: float | None = None, candidate_holdout: float | None = None, proposal_diff: str = "")`. `protocols.py` — the spec's three `@runtime_checkable` Protocols verbatim: `OptimizableArtifact` (name, render, with_content, validate, landing_note, fingerprint), `FitnessFunction` (name, cost_tier, `async def evaluate(artifact, *, split)`), `HarnessOptimizer` (name, `async def optimize(seed, ladder, budget) -> OptimizationResult`). `registries.py` — `from benchmarks.eval.serialization import _Registry  # WHY: same in-repo registry mechanic as datasets/systems; a second copy would drift` and three instances. **Step 4:** green. **Step 5: Commit** `feat(bench): optimize-layer value objects, protocols, registries`.

---

### Task 3: Delimited format + `tool_docs` artifact (§D2a)

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/artifacts/__init__.py`, `optimize/artifacts/_delimited.py`, `optimize/artifacts/tool_docs.py`
- Test: `benchmarks/tests/optimize/test_tool_docs_artifact.py`

- [ ] **Step 1: Failing tests**

```python
import pytest
from benchmarks.optimize.artifacts._delimited import parse_delimited, render_delimited
from benchmarks.optimize.artifacts.tool_docs import ToolDocsArtifact
from benchmarks.optimize.registries import artifact_registry


def test_render_parse_round_trip_preserves_order_and_bytes() -> None:
    art = ToolDocsArtifact()
    assert art.with_content(art.render()).render() == art.render()


def test_headers_follow_spec_format() -> None:
    text = art_render = ToolDocsArtifact().render()
    assert text.startswith("=== SERVER_INSTRUCTIONS ===\n")
    assert "\n=== TOOL: get_overview ===\n" in art_render


def test_header_like_line_inside_content_is_a_violation() -> None:
    art = ToolDocsArtifact()
    poisoned = art.render().replace(
        "=== TOOL: get_why ===", "=== TOOL: get_why ===\n=== TOOL: fake_tool ===", 1)
    assert any("header" in v.lower() for v in art.with_content(poisoned).validate())


def test_budget_violation_detected_before_any_fitness() -> None:
    art = ToolDocsArtifact()
    sections = parse_delimited(art.render())
    sections["TOOL: get_symbol"] = "x" * (500 * 4 + 40)   # blows the 500-token/tool cap
    fat = art.with_content(render_delimited(sections))
    assert any("get_symbol" in v for v in fat.validate())


def test_missing_tool_section_is_a_violation() -> None:
    art = ToolDocsArtifact()
    sections = parse_delimited(art.render())
    del sections["TOOL: get_why"]
    assert any("get_why" in v for v in art.with_content(render_delimited(sections)).validate())


def test_seed_validates_clean_and_fingerprint_is_stable() -> None:
    a, b = ToolDocsArtifact(), ToolDocsArtifact()
    assert a.validate() == () and a.fingerprint == b.fingerprint and len(a.fingerprint) == 64


def test_registered_as_tool_docs() -> None:
    assert isinstance(artifact_registry.build("tool_docs"), ToolDocsArtifact)
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** `_delimited.py` — `_HEADER_RE = re.compile(r"^=== (SERVER_INSTRUCTIONS|TOOL: [a-z_]+) ===$")` as the single format source; `render_delimited(sections: Mapping[str, str]) -> str` (insertion order, one trailing newline per section trimmed on parse); `parse_delimited(text: str) -> dict[str, str]`; `find_header_collisions(sections) -> tuple[str, ...]` (a content line matching `_HEADER_RE` is a violation — WHY: this rule is what makes the format escaping-free). `tool_docs.py` — `@artifact_registry.register("tool_docs")` frozen dataclass `ToolDocsArtifact(content: str | None = None)`: `render()` returns `content` if set else renders live `TOOL_DOCS` + `SERVER_INSTRUCTIONS` (sections: `SERVER_INSTRUCTIONS` first, then `TOOL: <name>` in `TOOL_DOCS` insertion order); `with_content(content)` returns `dataclasses.replace`; `validate()` — parse (malformed → violation), all six tools present + no extras + order preserved, §D13 rules via `from pydocs_mcp.application.tool_docs import REQUIRED_MARKERS, CHARS_PER_TOKEN, PER_TOOL_TOKEN_BUDGET, TOTAL_TOKEN_BUDGET` (Task 1 — the same constants the lint uses, zero drift), header-collision rule; `fingerprint` = `hashlib.sha256(self.render().encode()).hexdigest()`; `landing_note()` names `python/pydocs_mcp/application/tool_docs.py` and says a human applies the diff + reruns the lint. **Step 4:** green. **Step 5: Commit** `feat(bench): delimited tool_docs artifact with shared §D13 validation`.

---

### Task 4: `usage_skill` artifact + committed seed

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/artifacts/usage_skill.py`, `optimize/artifacts/usage_skill_seed.md`
- Test: `benchmarks/tests/optimize/test_usage_skill_artifact.py`

- [ ] **Step 1: Failing tests**

```python
from benchmarks.optimize.artifacts.usage_skill import _SKILL_TOKEN_BUDGET, UsageSkillArtifact
from benchmarks.optimize.registries import artifact_registry


def test_seed_loads_validates_clean_and_names_all_six_tools() -> None:
    art = UsageSkillArtifact()
    assert art.validate() == ()
    for tool in ("get_overview", "search_codebase", "get_symbol",
                 "get_context", "get_references", "get_why"):
        assert tool in art.render()


def test_oversized_skill_is_a_violation() -> None:
    art = UsageSkillArtifact()
    fat = art.with_content(art.render() + "x" * (_SKILL_TOKEN_BUDGET * 4 + 40))
    assert any("token" in v.lower() for v in fat.validate())


def test_dropping_a_tool_name_is_a_violation() -> None:
    art = UsageSkillArtifact()
    broken = art.with_content(art.render().replace("get_why", "get_qhy"))
    assert any("get_why" in v for v in broken.validate())


def test_registered_and_fingerprint_stable() -> None:
    assert isinstance(artifact_registry.build("usage_skill"), UsageSkillArtifact)
    assert UsageSkillArtifact().fingerprint == UsageSkillArtifact().fingerprint
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** `usage_skill_seed.md` — a real hand-written skill document (~600–900 tokens; hard cap `_SKILL_TOKEN_BUDGET = 1500` by the shared `CHARS_PER_TOKEN` rule): teaches an agent operating pydocs-mcp which tool answers which question shape (`get_overview` to orient; `search_codebase` for "where/which code does X"; `get_symbol` for one known symbol; `get_context` for multi-symbol relationships; `get_references` for callers/callees/impact; `get_why` for rationale/decision questions), how to decompose a repository question into 1–3 retrieval queries before reading files, and when to STOP searching and read the cited file instead. Written vendor-neutrally (no competitor product names — the repo doc rule applies). `usage_skill.py` — `@artifact_registry.register("usage_skill")` frozen dataclass mirroring Task 3's shape: `render()` = `content` if set else the packaged seed via `importlib.resources.files("benchmarks.optimize.artifacts").joinpath("usage_skill_seed.md").read_text()`; `validate()` = token cap via `CHARS_PER_TOKEN` + all six names present (iterate `TOOL_DOCS` keys — single source for the tool list); `landing_note()` says the skill text feeds `task_prompt(skill=...)` and where the seed file lives. Add the `.md` to package data if `benchmarks/pyproject.toml` needs an explicit include (check `[tool.setuptools]`; tests run from source via PYTHONPATH so `files()` resolves regardless). **Step 4:** green. **Step 5: Commit** `feat(bench): usage_skill artifact + committed seed document`.

---

### Task 5: Deterministic split + `FitnessLadder`

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/_split.py`, `optimize/ladder.py`
- Test: `benchmarks/tests/optimize/test_split_and_ladder.py`

- [ ] **Step 1: Failing tests**

```python
import hashlib

import pytest
from benchmarks.optimize._split import partition_task_ids, task_split
from benchmarks.optimize.ladder import FitnessLadder, Rung


def test_split_predicate_is_the_pinned_sha256_mod2() -> None:
    for tid in ("swe-qa-pro:0001", "swe-qa-pro:0002", "anything"):
        expected = "train" if int(hashlib.sha256(tid.encode()).hexdigest(), 16) % 2 == 0 else "holdout"
        assert task_split(tid) == expected


def test_partition_errors_clearly_when_one_side_empty() -> None:
    one_sided = [t for t in (f"t{i}" for i in range(50)) if task_split(t) == "train"][:4]
    with pytest.raises(ValueError, match="holdout"):
        partition_task_ids(one_sided)


def test_ladder_rungs_and_survivor_selection() -> None:
    ladder = FitnessLadder(rungs=(Rung("paired_agent", max_tasks=6, survivors=4),
                                  Rung("paired_agent", max_tasks=24, survivors=1)))
    scored = {"a": 0.3, "b": 0.1, "c": float("-inf"), "d": 0.2, "e": 0.25}
    assert ladder.rungs[0].select_survivors(scored) == ("a", "e", "d", "b")  # -inf never survives
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** `_split.py` — `task_split(task_id: str) -> Literal["train", "holdout"]` with the spec-pinned predicate `int(sha256(task_id.encode()).hexdigest(), 16) % 2` (`0` → train); `partition_task_ids(ids) -> tuple[tuple[str, ...], tuple[str, ...]]` raising `ValueError` naming the empty side and the task count (a tiny pool is a config error, not silent skew — spec §D3). `ladder.py` — frozen `Rung(fitness_name: str, max_tasks: int, survivors: int)` with `select_survivors(scores: Mapping[str, float]) -> tuple[str, ...]` (descending score, non-finite excluded, capped at `survivors`); frozen `FitnessLadder(rungs: tuple[Rung, ...])` + `from_lists(raw: Sequence[Sequence])` for the YAML `[fitness, max_tasks, survivors]` rung schema. Walking the ladder lives in the orchestrator (Task 8) — the ladder itself stays a pure value object. **Step 4:** green. **Step 5: Commit** `feat(bench): deterministic train/holdout split + fitness ladder value objects`.

---

### Task 6: `paired_agent` fitness (+ `retrieval` scaffolding)

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/fitness/__init__.py`, `optimize/fitness/paired_agent.py`, `optimize/fitness/retrieval.py`
- Test: `benchmarks/tests/optimize/test_paired_agent_fitness.py`, `test_retrieval_fitness_scaffold.py`

- [ ] **Step 1: Failing tests** (all with `FakeAgentRunner`/`FakeJudge` from the Task-0 binding; the injection seam is a value object so no MCP server is involved)

```python
# Worked example (spec §D3 requires one): baseline vs candidate over the same split —
#   tokens (cache_read+write): 100_000 → 80_000  → fraction (100k-80k)/100k = 0.20
#   tool_calls:                20      → 15      → fraction 5/20            = 0.25
#   files_read:                10      → 9       → fraction 1/10            = 0.10
#   score = 0.5*0.20 + 0.3*0.25 + 0.2*0.10 = 0.100 + 0.075 + 0.020 = 0.195
async def test_score_matches_worked_example(tmp_path) -> None:
    fit = _fitness(baseline=_metrics(tokens=100_000, tools=20, files=10),
                   candidate=_metrics(tokens=80_000, tools=15, files=9),
                   judge_delta=0.0, ledger=tmp_path / "trials.jsonl")
    report = await fit.evaluate(_candidate_artifact(), split="train")
    assert report.score == pytest.approx(0.195)
    assert report.components["tokens_fraction"] == pytest.approx(0.20)


async def test_judge_parity_pre_gate_returns_neg_inf(tmp_path) -> None:
    fit = _fitness(judge_delta=-0.30, ledger=tmp_path / "l.jsonl")   # below the -0.25 floor
    report = await fit.evaluate(_candidate_artifact(), split="train")
    assert report.score == float("-inf")


async def test_baseline_computed_once_and_cached(tmp_path) -> None:
    runner = _counting_fake_runner()
    fit = _fitness(runner=runner, ledger=tmp_path / "l.jsonl")
    await fit.evaluate(_candidate_artifact(), split="train")
    calls_after_first = runner.total_calls
    await fit.evaluate(_other_candidate(), split="train")
    assert runner.total_calls - calls_after_first == _CANDIDATE_ONLY_CALLS  # no re-baseline


async def test_usage_skill_candidate_reaches_task_prompt(tmp_path) -> None:
    capturing = _prompt_capturing_runner()
    fit = _fitness(runner=capturing, artifact_kind="usage_skill", ledger=tmp_path / "l.jsonl")
    await fit.evaluate(_skill_artifact("ALWAYS start with get_overview"), split="train")
    assert any("ALWAYS start with get_overview" in p for p in capturing.prompts)
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** `paired_agent.py`: `@fitness_registry.register("paired_agent")` — `PairedAgentFitness(runner, judge, dataset, ledger_path, agent_cfg, seed_artifact, inject, weights=_DEFAULT_WEIGHTS, judge_parity_floor=_DEFAULT_PARITY_FLOOR)`; module constants `_DEFAULT_WEIGHTS = {"tokens": 0.5, "tool_calls": 0.3, "files_read": 0.2}`, `_DEFAULT_PARITY_FLOOR = -0.25`, `_EPS = 1e-9`. `inject: Callable[[OptimizableArtifact], ArtifactInjection]` where frozen `ArtifactInjection(skill: str = "", overlay_path: Path | None = None)` — `skill` threads into arm prompts via `task_prompt(question, skill=...)`; `overlay_path` threads into the arm-B server command (Task 12 wires the real mechanism; the fitness only carries the value). `evaluate(artifact, *, split)`: collect the dataset's task ids, run `partition_task_ids` (Task 5 — the non-empty-split assertion fires HERE on the real path, not only in dry-run), keep the requested split (respect the rung's `max_tasks` via the `agent_cfg` handed in); run `run_agent_track` once for the SEED (baseline — keyed `(seed.fingerprint, split)` in the trials ledger, so it is computed once per run and reused; WHY comment) and once for the candidate; `tokens_k = cache_read + cache_write` summed per task from both arms' `RunMetrics`; pre-gate: `mean(judge_candidate.mean − judge_seed.mean) < floor → FitnessReport(score=-inf, ...)`; else `score = Σ weight_k · mean_over_tasks((baseline_k − candidate_k) / max(baseline_k, _EPS))`; `components` carries every raw mean and fraction; `cost_usd` = both runs' spend this call; `n_samples` = paired-task count. `retrieval.py`: `@fitness_registry.register("retrieval")`, `cost_tier="free"`, wraps `benchmarks.eval.sweep.run_sweep` behind the same `evaluate` shape; docstring states plainly it is **scaffolding for a future structured-artifact slice, wired into NO v1 ladder** (spec §D3); one unit test drives it with a synthetic in-memory artifact + monkeypatched `run_sweep` to prove the seam. **Step 4:** green. **Step 5: Commit** `feat(bench): paired_agent fitness with parity pre-gate + retrieval scaffold`.

---

### Task 7: Trials ledger + resume

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/trials_ledger.py`
- Test: `benchmarks/tests/optimize/test_trials_ledger.py`

- [ ] **Step 1: Failing tests**

```python
from benchmarks.optimize.trials_ledger import TrialsLedger


def test_record_and_lookup_by_fingerprint_and_split(tmp_path) -> None:
    led = TrialsLedger(tmp_path / "trials.jsonl")
    led.record(fingerprint="f" * 64, split="train", score=0.19, components={"t": 0.2}, cost_usd=2.0)
    hit = led.lookup(fingerprint="f" * 64, split="train")
    assert hit is not None and hit.score == 0.19
    assert led.lookup(fingerprint="f" * 64, split="holdout") is None   # split keys never collide


def test_resume_reads_existing_file(tmp_path) -> None:
    path = tmp_path / "trials.jsonl"
    TrialsLedger(path).record(fingerprint="a" * 64, split="train", score=0.1, components={}, cost_usd=1.0)
    assert TrialsLedger(path).lookup(fingerprint="a" * 64, split="train") is not None


def test_total_spend_sums_all_entries(tmp_path) -> None:
    led = TrialsLedger(tmp_path / "t.jsonl")
    led.record(fingerprint="a" * 64, split="train", score=0.1, components={}, cost_usd=1.0)
    led.record(fingerprint="b" * 64, split="train", score=0.2, components={}, cost_usd=2.5)
    assert led.total_spend() == 3.5


def test_corrupt_line_skipped_not_fatal(tmp_path) -> None:
    path = tmp_path / "t.jsonl"
    path.write_text('{"fingerprint": "a", "split": "train", "score": 1, "components": {}, "cost_usd": 0}\nnot json\n')
    assert TrialsLedger(path).lookup(fingerprint="a", split="train") is not None
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** `TrialsLedger` — load-on-init (line-wise `json.loads` in try/except, corrupt lines skipped with a `log.warning`), `record(...)` appends one JSON line and updates the in-memory index `dict[(fingerprint, split), LedgerEntry]`, `lookup(...)`, `total_spend()`. Frozen `LedgerEntry(fingerprint, split, score, components, cost_usd)`. The fitness (Task 6) and orchestrator (Task 8) consult `lookup` before spending — already-scored candidates return recorded scores (spec §D5 resume). **Step 4:** green. **Step 5: Commit** `feat(bench): trials ledger with (fingerprint, split) resume`.

---

### Task 8: Acceptance gate + orchestrator

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/orchestrator.py`
- Test: `benchmarks/tests/optimize/test_orchestrator.py`

- [ ] **Step 1: Failing tests** (fake fitness + fake optimizer; no agent-track code)

```python
async def test_accepts_only_above_margin(tmp_path) -> None:
    # margin is 0.02: +0.020 exactly → rejected; +0.021 → accepted
    res_eq = await _run(seed_holdout=0.10, cand_holdout=0.12, tmp_path=tmp_path)
    res_gt = await _run(seed_holdout=0.10, cand_holdout=0.121, tmp_path=tmp_path)
    assert res_eq.accepted is False and res_gt.accepted is True


async def test_nonfinite_seed_holdout_aborts_never_autoaccepts(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="seed"):
        await _run(seed_holdout=float("-inf"), cand_holdout=1.0, tmp_path=tmp_path)


async def test_neg_inf_candidate_is_rejected_but_reported(tmp_path) -> None:
    res = await _run(seed_holdout=0.1, cand_holdout=float("-inf"), tmp_path=tmp_path)
    assert res.accepted is False and res.candidate_holdout == float("-inf")


async def test_optimizer_fitness_is_train_bound(tmp_path) -> None:
    recording = _split_recording_fitness()
    await _run(fitness=recording, tmp_path=tmp_path)
    assert set(recording.requested_splits_seen_by_optimizer) == {"train"}   # holdout physically unreachable


async def test_budget_exhaustion_stops_and_reports(tmp_path) -> None:
    res = await _run(cost_per_eval=30.0, max_usd=40.0, tmp_path=tmp_path)   # 2nd eval would exceed
    assert res.accepted is False and res.total_usd <= 40.0 and len(res.trials) >= 1


async def test_result_carries_unified_diff_of_proposal(tmp_path) -> None:
    res = await _run(seed_holdout=0.1, cand_holdout=0.2, tmp_path=tmp_path)
    assert res.proposal_diff.startswith("---") and "+++" in res.proposal_diff
```

- [ ] **Step 2:** FAIL. **Step 3: Implement** `run_optimization(seed, optimizer, ladder, budget, *, fitness_by_name, ledger, provenance) -> OptimizationResult`: (1) `seed.validate()` must be `()` else raise with the violations; (2) build the train-bound fitness wrapper — a closure that FORCES `split="train"` regardless of what the optimizer passes (spec: optimizers physically cannot request holdout) and charges every evaluation against `budget.max_usd` via the ledger's running spend, raising a `BudgetExhausted` control exception the orchestrator catches to stop the search gracefully; (3) `await optimizer.optimize(seed_bound_view, ladder, budget)`; (4) the D4 gate: score seed and best candidate on `split="holdout"` of the FINAL rung's fitness (`_ACCEPT_MARGIN = 0.02` module constant); seed non-finite → `RuntimeError` (never auto-accept); `accepted = math.isfinite(cand) and (cand - seed) > _ACCEPT_MARGIN`; (5) `proposal_diff` = `difflib.unified_diff(seed.render().splitlines(keepends=True), best.render().splitlines(keepends=True), fromfile=seed.name + "@seed", tofile=seed.name + "@candidate")` joined (D1: a run's output is a proposal — text + diff + report); (6) always return the full `OptimizationResult` (rejected results are information). **Step 4:** green. **Step 5: Commit** `feat(bench): optimization orchestrator with holdout acceptance gate`.

---

### Task 9: `critique_refine` optimizer

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/optimizers/__init__.py`, `optimize/optimizers/critique_refine.py`
- Test: `benchmarks/tests/optimize/test_critique_refine.py`

- [ ] **Step 1: Failing tests**

```python
async def test_loop_keeps_best_of_scripted_replies(tmp_path) -> None:
    client = FakeCritiqueClient(replies=[_reply(better), _reply(worse), _reply(best)])
    opt = CritiqueRefineOptimizer(client=client, fitness=_scripted_fitness({better: 0.2, worse: 0.05, best: 0.4}))
    result = await opt.optimize(_seed(score=0.1), _ladder(), OptimizationBudget(max_trials=3))
    assert result.best.render() == best


async def test_invalid_candidate_discarded_without_fitness_spend(tmp_path) -> None:
    counting = _counting_fitness()
    client = FakeCritiqueClient(replies=[_reply(_HEADER_POISONED_CONTENT)])
    opt = CritiqueRefineOptimizer(client=client, fitness=counting)
    result = await opt.optimize(_tool_docs_seed(), _ladder(), OptimizationBudget(max_trials=1))
    assert counting.candidate_evaluations == 0                     # validate() firewalled the spend
    assert result.trials[0].violations != ()


async def test_max_trials_respected() -> None:
    client = FakeCritiqueClient(replies=[_reply(x) for x in _many_variants(10)])
    opt = CritiqueRefineOptimizer(client=client, fitness=_scripted_fitness_default(0.05))
    result = await opt.optimize(_seed(score=0.1), _ladder(), OptimizationBudget(max_trials=4))
    assert len(result.trials) == 4


def test_registered_as_critique_refine() -> None:
    built = optimizer_registry.build(
        "critique_refine", client=FakeCritiqueClient(replies=[]), fitness=_counting_fitness())
    assert isinstance(built, CritiqueRefineOptimizer)
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** `CritiqueClient` Protocol — `async def complete(self, prompt: str) -> CritiqueReply` with frozen `CritiqueReply(text: str, cost_usd: float)`; `FakeCritiqueClient(replies)` scripted double (exported for Task 11's dry-run); `ClaudeCliCritiqueClient(model)` — one-shot completion reusing the binding's `ClaudeAgentRunner`-style judge invocation pattern via `_agent_track_binding` (no tools, max_turns=1; WHY: same spend path and parser as the judge, no second LLM client stack). `@optimizer_registry.register("critique_refine")` `CritiqueRefineOptimizer(client, fitness)`: loop up to `budget.max_trials` — build a critique prompt from the current best's `render()` + its `FitnessReport.components` summary, ask the client for a bounded rewrite (the prompt instructs: full replacement document in one fenced block, respect the §D13 budgets), extract the block, `with_content` → `validate()`; violations → record a `Trial(violations=...)` and continue WITHOUT calling fitness (the constraint firewall, spec §D2); valid → train-fitness score (rung 1 `max_tasks` sizing), keep-best; assemble `OptimizationResult` (acceptance left `False` — the ORCHESTRATOR owns the holdout gate; WHY comment). **Step 4:** green. **Step 5: Commit** `feat(bench): critique_refine optimizer with constraint firewall`.

---

### Task 10: `skillopt` adapter + pinned extra

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/optimizers/skillopt.py`
- Modify: `benchmarks/pyproject.toml` (`[project.optional-dependencies]`: add `optimizers-skillopt`; extend `all`)
- Test: `benchmarks/tests/optimize/test_skillopt_adapter.py`

- [ ] **Step 1: Failing tests** (subprocess-free: `_invoke_train` is monkeypatched; the module import is stubbed)

```python
def test_env_plugin_layout_generated(tmp_path) -> None:
    plugin = generate_env_plugin(tmp_path, tasks=_train_tasks(4), config=_skillopt_cfg())
    for rel in ("dataloader.py", "rollout.py", "evaluator.py", "configs/pydocs_usage_skill.yaml"):
        assert (plugin / rel).is_file()


def test_budget_mapping_asserted(tmp_path) -> None:
    # OptimizationBudget(max_trials=20, max_usd=40.0) must land in SkillOpt's own config fields —
    # our --max-usd does NOT bound SkillOpt's internal rollouts (spec §D4 spend asymmetry).
    cfg_text = (generate_env_plugin(tmp_path, tasks=_train_tasks(2),
                                    config=_skillopt_cfg(max_trials=20, max_usd=40.0))
                / "configs/pydocs_usage_skill.yaml").read_text()
    cfg = yaml.safe_load(cfg_text)
    assert cfg["budget"]["max_usd"] == 40.0 and cfg["rollouts"]["total"] == 20


def test_consumed_surface_is_enumerated_and_stable() -> None:
    # The version-pin canary: everything we assume about SkillOpt in ONE tuple.
    assert _CONSUMED_SKILLOPT_SURFACE == (
        "python -m skillopt.train --config <yaml>",
        "env-plugin: dataloader.py / rollout.py / evaluator.py / configs/<name>.yaml",
        "output: <run_dir>/best_skill.md",
    )


async def test_best_skill_parsed_and_validated(monkeypatch, tmp_path) -> None:
    async def fake_invoke(cmd, run_dir):  # noqa: ANN001 — mirrors the real hook it replaces
        (run_dir / "best_skill.md").write_text(_VALID_SKILL_TEXT)
        return 0
    monkeypatch.setattr("benchmarks.optimize.optimizers.skillopt._invoke_train", fake_invoke)
    opt = SkillOptOptimizer(python=Path("/venv/bin/python"))
    result = await opt.optimize(_usage_skill_seed(), _ladder(), OptimizationBudget(max_trials=2))
    assert result.best is not None and result.best.validate() == ()


async def test_missing_skillopt_module_raises_actionable(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "skillopt", None)
    with pytest.raises(RuntimeError, match=r"optimizers-skillopt"):
        SkillOptOptimizer(python=Path("/venv/bin/python")).ensure_available()
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** module constant `_CONSUMED_SKILLOPT_SURFACE` exactly as the test pins (the ONE place our SkillOpt assumptions live; a SkillOpt version bump that breaks any line is caught here first). `generate_env_plugin(root, *, tasks, config) -> Path` — writes the env-plugin package: `dataloader.py` (yields our train-split `(task_id, question, gold)` rows serialized into the plugin as JSON — SkillOpt re-imports it standalone), `rollout.py` (selects SkillOpt's built-in Claude-Code execution backend, cwd pointed at our indexed corpora), `evaluator.py` (maps our rung fitness onto the reward hook: reads our components JSON emitted per rollout), `configs/<name>.yaml` carrying the MAPPED budget (`OptimizationBudget.max_trials` → rollout count, `max_usd` → SkillOpt's budget field — the asymmetry documented in the module docstring: our `--max-usd` cannot interrupt `train.py` mid-run; only the D4 gate runs are under our cap). `@optimizer_registry.register("skillopt")` `SkillOptOptimizer(python)`: `ensure_available()` — `importlib.util.find_spec("skillopt")` else `RuntimeError` naming the `[optimizers-skillopt]` extra; `optimize(...)` — generate plugin into a run dir, `await _invoke_train(cmd, run_dir)` where `_invoke_train` wraps `asyncio.create_subprocess_exec(python, "-m", "skillopt.train", "--config", ...)` (the ONLY subprocess in the layer, module-level so tests monkeypatch it), parse `<run_dir>/best_skill.md`, `with_content` → `validate()` (a violating best is recorded, `best=None`), assemble `OptimizationResult`. **pyproject:** `optimizers-skillopt = ["skillopt @ git+https://github.com/microsoft/SkillOpt@<COMMIT>"]` — resolve `<COMMIT>` NOW by running `git ls-remote https://github.com/microsoft/SkillOpt HEAD` (dev machine has network; tests never import the real package) and pin that full SHA; extend `all` with the same entry. If `ls-remote` fails (repo moved/renamed), STOP and report BLOCKED with the error — do not guess a pin. **Step 4:** green. **Step 5: Commit** `feat(bench): skillopt adapter — env-plugin generation, budget mapping, pinned extra`.

---

### Task 11: Run config + CLI + `--dry-run`

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/run_config.py`, `optimize/__main__.py`, `optimize/configs/optimize_tool_docs.yaml`, `optimize/configs/optimize_usage_skill.yaml`
- Test: `benchmarks/tests/optimize/test_run_config.py`, `test_cli_dry_run.py`

- [ ] **Step 1: Failing tests**

```python
def test_both_shipped_configs_load_typed() -> None:
    for name in ("optimize_tool_docs.yaml", "optimize_usage_skill.yaml"):
        cfg = load_run_config(_shipped(name))
        assert cfg.artifact in ("tool_docs", "usage_skill")
        assert cfg.ladder.rungs[0].fitness_name == "paired_agent"
        assert cfg.accept_margin == pytest.approx(0.02)
        assert cfg.fitness.judge_parity_floor == pytest.approx(-0.25)


def test_unknown_registry_key_is_a_clear_error(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(_shipped("optimize_tool_docs.yaml").read_text().replace("critique_refine", "gradient_descent"))
    with pytest.raises(KeyError, match="gradient_descent"):
        load_run_config(bad)


async def test_dry_run_walks_pipeline_spending_nothing(tmp_path, capsys) -> None:
    code = await cli_main(["--config", str(_shipped("optimize_usage_skill.yaml")),
                          "--dry-run", "--ledger", str(tmp_path / "trials.jsonl")])
    out = capsys.readouterr().out
    assert code == 0 and "DRY RUN" in out and "$0.00" in out
    # seed validated, ladder wired, split determinism checked, adapters importable — all printed
    for check in ("seed", "ladder", "split", "optimizer"):
        assert check in out.lower()
```

- [ ] **Step 2:** FAIL. **Step 3: Implement:** `run_config.py` — pydantic `OptimizeRunConfig` mirroring the spec's canonical YAML: `artifact: str`, `optimizer: str`, `ladder: FitnessLadder` (via `from_lists` validator on `[[name, max_tasks, survivors], ...]`), `fitness: FitnessSettings(judge_parity_floor=-0.25, weights={"tokens": 0.5, "tool_calls": 0.3, "files_read": 0.2})`, `accept_margin: float = 0.02` (import the orchestrator's `_ACCEPT_MARGIN` as the default — single source), `budget: OptimizationBudget`, `llm: CritiqueLlmConfig | None` (provider/model_name/temperature — `critique_refine` only), `dataset: DatasetSettings(name="swe-qa-pro", fixture_path=None)`; `load_run_config(path)` validates registry keys against the three registries at load time (byte-identical names, spec §D7). `__main__.py` — argparse `--config` (required), `--dry-run`, `--resume LEDGER`, `--ledger`; `asyncio.run` into a `_main_async` that builds artifact/optimizer/fitness from registries and either dry-runs or (real run) prints the spend expectations + runbook pointer and proceeds. `--dry-run`: seed `validate()` report, ladder wiring echo, split determinism check over the dataset's fixture task ids (`partition_task_ids`), optimizer availability (`critique_refine` constructs with `FakeCritiqueClient`; `skillopt` checks `ensure_available()` and reports SKIPPED if the extra is absent — a dry-run must not require it), one full orchestrator pass with `FakeAgentRunner`/`FakeJudge` + a zero-cost fake fitness, printing `total spend: $0.00` and `DRY RUN — no money was spent`. Shipped YAMLs exactly as the spec's canonical example (tool_docs variant uses `optimizer: critique_refine`; usage_skill variant `optimizer: skillopt`), both with the degenerate two-rung ladder `[[paired_agent, 6, 4], [paired_agent, 24, 1]]`. **Step 4:** green. **Step 5: Commit** `feat(bench): optimize run config, CLI entry, zero-spend dry-run`.

---

### Task 12: Arm-B overlay server wrapper (§D6, zero product hook)

**Files:**
- Create: `benchmarks/src/benchmarks/optimize/_overlay_server.py`
- Test: `benchmarks/tests/optimize/test_overlay_server.py`

- [ ] **Step 0: Verify the serve path.** Read `python/pydocs_mcp/__main__.py`'s serve command implementation and record which helper turns a project path into `server.run(...)` kwargs (db path resolution). The wrapper delegates through the SAME helper so path-hash cache resolution never forks. Note the helper name in the module docstring.

- [ ] **Step 1: Failing tests**

```python
def test_valid_overlay_rebinds_module_attrs_then_delegates(monkeypatch, tmp_path) -> None:
    calls = {}
    monkeypatch.setattr("pydocs_mcp.server.run", lambda **kw: calls.setdefault("run", kw))
    overlay = tmp_path / "overlay.txt"
    overlay.write_text(_valid_overlay_with(get_why="Explains rationale. USE WHEN ..."))
    serve_with_overlay(project=tmp_path, overlay=overlay)
    import pydocs_mcp.application.tool_docs as td
    assert "Explains rationale" in td.TOOL_DOCS["get_why"] and "run" in calls


def test_invalid_overlay_refuses_to_serve(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("pydocs_mcp.server.run", lambda **kw: pytest.fail("must not serve"))
    overlay = tmp_path / "overlay.txt"
    overlay.write_text(_overlay_blowing_the_token_budget())
    with pytest.raises(OverlayValidationError, match="get_symbol"):
        serve_with_overlay(project=tmp_path, overlay=overlay)     # fail-closed (spec §D6)


def test_no_overlay_is_byte_identical_noop(monkeypatch, tmp_path) -> None:
    import pydocs_mcp.application.tool_docs as td
    before = (dict(td.TOOL_DOCS), td.SERVER_INSTRUCTIONS)
    monkeypatch.setattr("pydocs_mcp.server.run", lambda **kw: None)
    serve_with_overlay(project=tmp_path, overlay=None)
    assert (dict(td.TOOL_DOCS), td.SERVER_INSTRUCTIONS) == before
```

(Module-attr mutation needs cleanup — an autouse fixture in this test file snapshots and restores `TOOL_DOCS`/`SERVER_INSTRUCTIONS` around each test.)

- [ ] **Step 2:** FAIL. **Step 3: Implement** `_overlay_server.py`: docstring explaining the decision — the spec's §D6 recorded alternative was chosen over `AppConfig.tool_docs_overlay_path` because `server.py` reads `TOOL_DOCS` (server.py:249→261) and `SERVER_INSTRUCTIONS` (server.py:183) via function-local imports at call time, so re-binding `pydocs_mcp.application.tool_docs` attributes before `run()` injects cleanly with ZERO product hook (product footprint stays at the §D2b refactor only). `OverlayValidationError(RuntimeError)`; `serve_with_overlay(project, overlay)`: if overlay — read, `ToolDocsArtifact().with_content(text).validate()`; violations → raise (fail-closed, never serve garbage); parse via `parse_delimited` and re-bind `td.SERVER_INSTRUCTIONS` + `td.TOOL_DOCS[name]` per section; then delegate to the serve helper recorded in Step 0. `__main__`-style `main(argv)` so `.mcp.json` can launch `python -m benchmarks.optimize._overlay_server <project> --overlay <file>`; the Task-6 `ArtifactInjection.overlay_path` plugs in here — the fitness's arm-B `.mcp.json` swaps the server command for this wrapper when `overlay_path` is set (wire that in `paired_agent.py` where the injection value is consumed; add a test in `test_paired_agent_fitness.py` asserting the rendered arm-B command names `_overlay_server` when an overlay is injected). **Step 4:** green. **Step 5: Commit** `feat(bench): arm-B overlay server wrapper — fail-closed tool_docs injection`.

---

### Task 13: Runbook chapter + README pointer + full gates

**Files:**
- Modify: `benchmarks/AGENT_TRACK.md` (append an "Optimization" chapter), `benchmarks/README.md` (short pointer section)

- [ ] **Step 1:** Write the "Optimization" chapter: preflight-first rule (agent-track preflight + `--dry-run` before any paid run); the three-layer spend model verbatim from spec §D5 including the SkillOpt asymmetry (its internal rollouts are bounded by the MAPPED budget, not our `--max-usd`); how to read an `OptimizationResult` (holdout scores, margin, diff) and how to land a proposal (apply the unified diff to `python/pydocs_mcp/application/tool_docs.py` or the seed skill, rerun the §D13 lint + full suite, ordinary reviewed commit); cost expectations (a 20-trial critique_refine run at rung sizes 6/24 ≈ 2×–3× a single 24-task paired run). README: 4–6 lines under the bench-commands section pointing at `python -m benchmarks.optimize --config ... --dry-run` and the runbook. Run the README jargon audit grep from CLAUDE.md — zero matches.
- [ ] **Step 2: Full gates** (from the repo root, venv python): `PYTHONPATH=benchmarks/src <venv> -m pytest benchmarks/tests/ -q` (expect ~+55 over the slice-5 count, all offline); `PYTHONPATH=python <venv> -m pytest tests/ -q` (product suite green — Task 1 touched it); `ruff check python/ tests/ benchmarks/`; `ruff format --check python/ tests/ benchmarks/`; `<venv> -m mypy python/` (no NEW errors; pre-existing `fast_plaid` noise acceptable); complexipy on new files ≤ 15 then `git checkout complexipy-snapshot.json`.
- [ ] **Step 3: Commit** `docs(bench): optimization runbook chapter + README pointer` (+ `fix(slice6): gate fixups` if gates surfaced fixes).

---

## Out of scope (explicit)

- Running a paid optimization (machinery + `--dry-run` only; spend needs an explicit user go).
- Non-prompt / structured artifacts (pipeline YAML) end-to-end — `retrieval` fitness ships as scaffolding wired into NO v1 ladder (user decision, spec rev 2).
- `AppConfig.tool_docs_overlay_path` — superseded by the Task-12 wrapper (the spec's recorded preferred alternative); adding the AppConfig field would be a SECOND product touch the wrapper makes unnecessary.
- Optimizing the judge rubric (circular without an independent quality signal).
- Multi-objective Pareto reporting (v1 scalarizes: parity pre-gate + weights).
- Any change to the six-tool MCP surface, the schema, or retrieval product code.
