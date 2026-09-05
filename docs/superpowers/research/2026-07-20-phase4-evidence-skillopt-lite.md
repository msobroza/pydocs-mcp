# Phase 4 evidence — SkillOpt-Lite (EvolvingLMMs-Lab) as a D1 optimizer candidate

Research scope: evaluate `github.com/EvolvingLMMs-Lab/SkillOpt-Lite` as a D1 candidate
optimizer for Phase 4 (optimizer adapters), alongside GEPA and the pinned
`skillopt` (`>=0.2,<0.3`, PyPI). Six questions: identity/lineage, license (R9),
loop shape, thin-adapter fit (R1/R2), relation to the existing pin, client stack + deps.

- Date: 2026-07-20. Worktree: `.claude/worktrees/phase-4-optimizer`, branch
  `claude/phase-4-optimizer` @ `7b7e008`.
- Method: cloned the repo fresh to `/private/tmp/SkillOpt-Lite`
  (`git clone --depth 50 https://github.com/EvolvingLMMs-Lab/SkillOpt-Lite.git`,
  20 commits, first `7381888 Initial release of SkillOpt-Lite` 2026-07-01,
  last `32708a1` 2026-07-10, author `Yifei Shen <yshenaw@connect.ust.hk>`) and
  read the actual source. Cross-read the in-repo adapter
  `benchmarks/src/pydocs_eval/optimize/optimizers/skillopt.py` and the Phase 2
  evidence `docs/superpowers/research/2026-07-18-phase2-evidence-optimizer-consumers.md`.
  Verified PyPI identity via `https://pypi.org/pypi/skillopt/json`.
- All `SL/` paths below abbreviate `/private/tmp/SkillOpt-Lite/`.
- NO paid model calls were made. Repo source was NOT modified. This file is NOT git-committed.

---

## 1. IDENTITY — SkillOpt-Lite is a companion repo to the SAME microsoft/SkillOpt we pin, not a parallel project

**Same lineage, likely overlapping authors.** The README states verbatim
(SL/README.md:204-212, Acknowledgements): "Built on top of **SkillOpt**
(github.com/microsoft/SkillOpt) — the text-space optimizer that trains reusable
natural-language skills… `SkillOpt-Lite` is the minimal coding-agent-driven variant
of that loop; `HarnessOpt` extends the same loop to also edit the agent code."
The cite block (README.md:187-193) is `shen2026skilloptlite`, authors
`Shen, Yifei and Li, Bo and Zhang, Xinjie`. The internal package docstrings still
carry the original research codename "ReflACT" (SL/skillopt/envs/base.py:1
"ReflACT environment adapter — abstract interface"; SL/skillopt/gradient/reflect.py:1
"ReflACT core Reflect engine"; SL/skillopt/model/__init__.py:1 "ReflACT model API").
Author domain `connect.ust.hk` (HKUST) + Microsoft-internal infra references
(TRAPI lanes, `gpt-5.4-nano`/`gpt-5.5` deployment names, Azure AAD) place the same
team behind both microsoft/SkillOpt and this EvolvingLMMs-Lab release.

**The repo contains TWO distinct things:**
1. A full `skillopt/` Python package + `scripts/` (SL/skillopt/, SL/scripts/train.py) —
   a **vendored variant of the microsoft/SkillOpt trainer**, declared as
   `name = "skillopt"`, `version = "0.1.0"` (SL/pyproject.toml:6-7). The README
   calls it "the runtime the run.sh scripts call into" (README.md:132).
2. The **actual novel "SkillOpt-Lite" / "HarnessOpt" contribution**: a
   coding-agent-driven loop expressed entirely as **slash-command prompt files**
   (`.github/prompts/skillopt-loop.prompt.md`, `harnessopt-loop.prompt.md`, and
   `*-improve.prompt.md`). There is NO Python "Lite loop" module — a repo-wide
   grep found no `optimize()`/loop-runner entrypoint; the only console scripts are
   `skillopt-train`/`skillopt-eval` (the vendored trainer, SL/pyproject.toml:56-58).
   The README Roadmap lists "Agent-agnostic loop runner… (Coming soon.)" and
   "Codex CLI plugin"/"Claude Code plugin" as future work (README.md:167-181) —
   i.e. Lite has no programmatic API today.

**PyPI ownership is microsoft's, not this repo's.** `pypi.org/pypi/skillopt/json`:
name `skillopt`, versions `0.1.0, 0.2.0`, Homepage + Repository both
`https://github.com/microsoft/SkillOpt`, author "SkillOpt Team", MIT. So the
importable `skillopt` distribution belongs to microsoft/SkillOpt (our pin
`skillopt>=0.2,<0.3` → 0.2.0). SkillOpt-Lite's `skillopt` (0.1.0, Homepage
`github.com/EvolvingLMMs-Lab/SkillOpt-Lite`, SL/pyproject.toml:60-63) is a
DIFFERENT distribution that reuses the same import name and is NOT the PyPI
package — it is run from the source tree (README install is
`pip install -r requirements.txt`, which installs only runtime deps;
`scripts/train.py:24-27` inserts the project root on `sys.path` so `import skillopt`
resolves in-place).

**Maturity signals:** 20 commits over 9 days (2026-07-01 → 2026-07-10), single
author, `Development Status :: 3 - Alpha` (SL/pyproject.toml:17). The last ~15
commits are README/docs polish; the code landed in the initial release.
Stars/last-commit beyond the clone were not fetched (UNVERIFIED), but the git
history shows an alpha-stage, days-old research release.

## 2. LICENSE (R9) — MIT, clean, satisfied

`SL/LICENSE` is a standard MIT license, "Copyright (c) 2026 EvolvingLMMs-Lab and
SkillOpt-Lite contributors" (LICENSE:1-21). Reinforced by `license = {text = "MIT"}`
(SL/pyproject.toml:10), the `License :: OSI Approved :: MIT License` classifier
(pyproject.toml:19), and README ("Released under the MIT License", README.md:214).
The vendored trainer descends from microsoft/SkillOpt, which is also MIT (PyPI
metadata). **R9 hard requirement is met** — MIT is Apache/MIT-compatible with no
copyleft or field-of-use restriction. No disqualifier here.

## 3. LOOP SHAPE — a val-gated skill-document loop driven by a coding agent

**What it optimizes:** a single natural-language **skill document** (`skill.md`)
that is injected into the target LLM's system prompt (README.md:70 "When it stops
improving, `workspace/skill.md` is the artifact you ship"). HarnessOpt additionally
optimizes the **agent harness Python code** (`rollout.py`, `react_agent.py`,
`codegen_agent.py`, `executor.py`, `adapter.py`) with skill held fixed
(harnessopt-loop.prompt.md:74-92).

**Candidate structure:** the skill.md text (SkillOpt-Lite) or skill.md + harness
code (HarnessOpt). Single-document, not a multi-component program.

**Mutation / reflection mechanism (the novel part):** a **coding agent** (VS Code
Copilot Chat / Claude Code / Codex / etc. — "anything that reads
`.github/prompts/*.prompt.md`", README.md:24-27) executes the loop by hand,
following the markdown prompt. Per round it: reads
`workspace/.skillopt/samples/{failed,passed}/*.md` trace files, diagnoses failure
clusters, and applies the smallest patch to `skill.md` via `editFiles`
(skillopt-loop.prompt.md:81-87, delegating to `skillopt-improve.prompt.md`). This
is the reflect step done by the agent, NOT by a library call.

**Evaluation interface (what an env must provide):** each env ships a `run.sh`
that calls `scripts/eval_only.py --config … --skill … --split {train|val|test}
--eval_limit N` (SL/copilot_example/livemath/run.sh:211-227). It produces
`results.jsonl` (per-item rows `{id, input, expected, predicted, success}`) plus
`eval_summary.json` carrying aggregate `hard`/`soft` (SL/scripts/eval_only.py:465-469;
run.sh:249 parses `hard=… soft=…`). `make_samples.py` fans `results.jsonl` out into
per-item `.md` traces the agent reads. The underlying env contract is the same
`skillopt.envs.base.EnvAdapter` ABC (build_train_env / build_eval_env / rollout →
`[{id, hard, soft}]` / get_task_types / **reflect**), SL/skillopt/envs/base.py:187-256.

**Selection / acceptance machinery:** a **validation-gated keep-or-revert** loop
mirroring `skillopt/evaluation/gate.py` `evaluate_gate` (SL/skillopt/evaluation/gate.py:76-148,
actions `accept_new_best | accept | reject`). The prompt reimplements this in
markdown (skillopt-loop.prompt.md:96-134): gate the patched skill on the FULL val
split, apply a dead band (±0.01 skill / ±0.05 harness), then
accept_new_best/accept/reject with rollback from a `__before.md` snapshot (skill) or
`git reset --hard <tag>` (harness). Best is snapshotted to
`workspace/.skillopt/history/`; the **test split runs exactly once at the very end**
(never peeked mid-loop — skillopt-loop.prompt.md:252, "Never peek at test mid-loop").

**Where an external gate plugs in:** the val gate IS the acceptance gate here, but
it is enforced by the prompt/agent, not a library seam. HarnessOpt adds a **hard-stop
user-approval gate** in round 0 — the agent must print a brief and end its turn,
waiting for the human to reply `approve`/`abort` before any patch
(harnessopt-loop.prompt.md:396-454). That human-in-the-loop checkpoint is
antithetical to an unattended campaign runner.

## 4. THIN-ADAPTER FIT (R1/R2) — poor for the Lite loop; redundant for the trainer

**The "Lite" loop cannot be wrapped by a thin `evaluate()`-delegating adapter.**
There is no programmatic optimizer to call: the loop's orchestration
(rollout → improve → val-gate → rollback → best-tracking) lives in the
`*.prompt.md` files and is executed interactively by a coding agent, with
explicit turn-ending / no-poll discipline and (for HarnessOpt) a user-approval
hard stop (harnessopt-loop.prompt.md:396-454; skillopt-loop.prompt.md:224-256).
Our Phase 4 R1/R2 thin-adapter pattern (as in the existing `skillopt.py`) needs an
`async def optimize(seed, ladder, budget) -> OptimizationResult` that runs to
completion unattended and hands a candidate to the D4 gate. SkillOpt-Lite offers no
such entrypoint — you would be re-authoring the loop, not adapting a library. Fit: **very poor**.

**The bundled `skillopt/` trainer IS adaptable — but that is exactly what our
existing adapter already does for microsoft/SkillOpt 0.2.0**, and this vendored
copy is not even drop-in compatible:
- Same custom-benchmark contract our adapter targets: `EnvAdapter` subclass +
  `scripts.train:main` + `_ENV_REGISTRY` injection + `configs/<name>.yaml`
  (SL/scripts/train.py:36 `_ENV_REGISTRY: dict[str, type] = {}`; run.py-style
  registry injection is the same pattern). `RolloutResult` `{id, hard, soft, …}`
  is present (SL/skillopt/types.py:104-124). `evaluate_gate`/`select_gate_score`
  with hard/soft/mixed metric is present (SL/skillopt/evaluation/gate.py:46-148).
- **BUT `reflect()` is `@abstractmethod` here** (SL/skillopt/envs/base.py:234-256,
  grep-confirmed) — a subclass MUST implement it. Phase 2 evidence recorded that
  microsoft/skillopt 0.2.0's `base.py` gives `reflect()` a DEFAULT that delegates
  to `run_minibatch_reflect`. Our generated `PydocsEnvAdapter` implements only
  build_train_env/build_eval_env/rollout/get_task_types and **relies on that base
  default** — under this 0.1.0 variant it would be an abstract class and fail to
  instantiate (`TypeError: Can't instantiate abstract class`). So the surface our
  `_CONSUMED_SKILLOPT_SURFACE` canary pins does NOT hold for this repo.

**Integration cost estimate (same scale as gepa/skillopt researchers):**
- Adapting the **Lite loop**: effectively unbounded / not a thin adapter — there is
  no library loop to delegate to. Would require re-implementing rollout→improve→
  gate→rollback as our own optimizer (hundreds of LOC) AND embedding a coding-agent
  driver. Not R1/R2-shaped. **Recommend: do not adapt.**
- Adapting the **vendored trainer** as a distinct optimizer: ~equal to the existing
  `skillopt.py` (~600 LOC) PLUS a fork of the generated `EnvAdapter` to implement
  `reflect()`, PLUS a way to install a `skillopt`-named package that is NOT on PyPI
  (git/source install), which violates the air-gap/PyPI-only constraint our adapter
  documents (skillopt.py:76-85). Net: strictly more cost than the incumbent, for a
  strictly worse-pinned, non-PyPI, older surface. **Assumptions:** Azure/TRAPI infra
  swapped for OpenAI-compatible; abstract `reflect` implemented; name-collision with
  the pinned `skillopt` resolved.

## 5. RELATION TO THE EXISTING PIN — same name, older/diverged surface, install collision

The `skillopt` PyPI project + import name belong to **microsoft/SkillOpt** (0.2.0,
our pin). SkillOpt-Lite re-declares `name="skillopt"` at **version 0.1.0** with a
different Homepage and is **not published to PyPI** under that name — so installing
it would collide on the `skillopt` import with the pinned 0.2.0 (two distributions,
one module name). What "Lite" changes vs the 0.2.x surface our adapter consumes:
- **Drops the automated trainer orchestration** as the user-facing product and
  replaces it with a coding-agent prompt loop (`/skillopt-loop`, `/harnessopt-loop`)
  — the reflect/aggregate/select/update engine still exists in `skillopt/` but is
  invoked by `scripts/train.py`, not by the Lite loop (which drives `eval_only.py` +
  hand-edits).
- **`reflect()` is abstract** (base.py:234) vs the 0.2.0 default — a breaking change
  for our generated adapter (§4).
- `RolloutResult` gains env-specific fields (`target_system_prompt`,
  `target_user_prompt`, `spreadsheet_preview`, SL/skillopt/types.py:121-123) not in
  the Phase 2-documented 0.2.0 shape — additive, but confirms a diverged snapshot.
- Adds **HarnessOpt** (edit agent code, not just the skill) — out of scope for
  optimizing a single `skill.md`/usage-skill artifact, and gated by human approval.

**Justification to choose it over the two incumbents: essentially none.** For the
skill-document target, the pinned microsoft/SkillOpt 0.2.0 is the same lineage, is
on PyPI (air-gap-installable), has the surface our canary already pins, and its
`reflect()` default is what our generated adapter depends on. SkillOpt-Lite adds no
programmatic capability GEPA + skillopt don't already cover; its genuine novelty
(the coding-agent loop) is not a library and not adaptable under R1/R2.

## 6. CLIENT STACK + DEPS

- **Python floor:** `requires-python = ">=3.10"` (SL/pyproject.toml:11) — compatible
  with the repo's 3.11+.
- **Core deps** (SL/pyproject.toml:26-34, requirements.txt:2-8): `openai>=1.30.0`,
  `pyyaml>=6.0`, `numpy>=1.24.0`, `openpyxl>=3.1.0`, **`azure-identity>=1.15.0`**,
  **`azure-core>=1.30.0`**, `httpx>=0.27.0`. The two Azure SDK deps are HARD core
  deps (not extras) and are new to this repo's environment.
- **Optional extras:** `alfworld`+`gymnasium`, `claude` (`claude-agent-sdk>=0.1.0`),
  `qwen` (`vllm>=0.4.0`), `docs`, `webui` (`gradio`), `dev` (pyproject.toml:36-54).
- **LLM client assumptions:** OpenAI-compatible via the `openai` SDK, but the
  **shipped default is Azure OpenAI + AAD** (`SKILLOPT_AUTH_MODE=azure_cli`,
  `az login`, SL/.env.example:10-16), routed through **Microsoft-internal TRAPI
  lanes** (`SKILLOPT_TRAPI_LANE=msra/shared`, dated deployment names like
  `gpt-5.5_2026-04-24`, run.sh:97-104). A plain OpenAI / OpenAI-compatible endpoint
  is mode 3 (`.env.example:23-26`, `OPENAI_BASE_URL` override for e.g. vLLM).
  Backends are pluggable (SL/skillopt/model/__init__.py:25-53): `openai_chat`
  (Azure, default), `claude_chat` (Anthropic via `claude-agent-sdk`),
  `codex_exec`/`claude_code_exec` (CLI exec backends), `qwen_chat` (vLLM). Nothing
  is hardcoded to one vendor, but the **defaults assume Microsoft-internal Azure infra**.
- **Collisions with our repo env:** (a) `skillopt` import-name collision with the
  pinned PyPI package (§5); (b) `azure-identity`/`azure-core` hard deps add an Azure
  SDK surface the repo doesn't currently carry; (c) TRAPI/`az login` defaults clash
  with the air-gapped/OpenAI-compatible expectation documented for the existing
  adapter; (d) not on PyPI → source/git install, which the air-gap constraint forbids.

---

## Open questions

1. Stars / fork count / issue activity beyond the shallow clone were not fetched
   (UNVERIFIED) — maturity read is from the 20-commit, 9-day git history only.
2. Whether microsoft/SkillOpt `main` (git) has itself moved to an abstract
   `reflect()` since PyPI 0.2.0 was not re-checked this session — the Phase 2
   evidence (2026-07-18) is the basis for the "0.2.0 reflect has a default" claim.
3. Whether SkillOpt-Lite intends to publish its `skillopt` variant to PyPI (which
   would create a real name conflict) is unstated; the Roadmap's "agent-agnostic
   loop runner (coming soon)" suggests the Lite loop may later gain a programmatic
   entry — worth a re-check if that ships, but it does not exist today.
