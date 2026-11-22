# The guidance-task experiment (#391): hand-off note before the paid run

- **Tracker record:** issue #466 (Guidance-task 8), part of #391, child of the turn-efficiency umbrella #366.
- **Date:** 2026-11-22. **Written on:** `integrate/391-466-base` @ `69e3b7a2`. This branch is a **local integration** of PRs #467–#473 (tickets #459–#465) on top of the spec commit `8b625a22` (PR #458). It is not a GitHub merge of any of them.
- **Spec:** `2026-11-22-guidance-task-experiment-design.md`, beside this note. Sections used: "Outcomes", "Cost and budget", "Further Notes".
- **Readers:** the owner, who decides on the paid run and on its result, and whoever writes the winning PR.

## Where things stand

- **The default is `none`.** On this branch `ask_your_docs.guidance_task` defaults to `none` (`GuidanceTask.NONE`, restated as `guidance_task: none` in `defaults/default_config.yaml`). Under `none` chat serves today's `system_v3` prompt byte for byte.
- **Nothing is merged.** All seven ticket PRs are open. Their branches are stacked or locally integrated, never merged on GitHub.
- **Nothing has been run.** No arm has been planned against a real endpoint, no price has been looked up, and `--confirm-spend` has never been passed.
- **Three steps wait for the owner:** the paid run (and the dollar cap of each arm), the flip of the default, and the close of the experiment PR.
- **No changelog file is edited anywhere in the series.** This note describes the entries a winning PR would carry. It writes none of them.

Whether there is anything to announce depends on a paid run that has not happened. A win ships the entries below in the winning PR. A loss closes the PR unmerged and ships none.

## 1. The root changelog entry a winning PR would carry

Draft only; it goes into `CHANGELOG.md` under `## [Unreleased]` in the winning PR, and nowhere before. The names are those on this branch.

> - **Ask-your-docs chat now folds the search guidance into its prompt, and uses a slimmer system prompt.** `ask_your_docs.guidance_task` (`none` | `repo_qa` | `vuln` | `bug_loc`) now defaults to `repo_qa`. Chat therefore receives the packaged seed's `BACKBONE`, `TASK_HEAD: repo_qa` and `HARNESS_TASK_HEAD: ask_your_docs.repo_qa` sections after the catalog and the session-start pack, and `system_v4` is the active system prompt. The packaged search-guidance seed (`harness/core/skills/search_guidance_seed.md`) is rewritten in five sections: `BACKBONE`, `TASK_HEAD: repo_qa` and the three `ask_your_docs.*` heads. In `BACKBONE`, the two breadth passages are now conditional: decompose a question only when it holds several behaviours, and treat five documents as a ceiling, not a target. `BACKBONE` also gains the definition of "already shown". In `TASK_HEAD: repo_qa`, the confirming read is now conditional and the "get_symbol where exact signatures matter" clause is dropped. The six chat rules that `system_v4` no longer carries now live in the `ask_your_docs.*` heads.
>   - **Evidence:** one chat model (`qwen/qwen3.8-27b`, thinking mode, 12-Turn budget) on one slice (`repoqa-qa/small_dev`, 20 tasks[, re-run on `repoqa-qa/dev`, 100 tasks]). It is not a general result.
>   - **Unmeasured readers:** the `BACKBONE` change is unmeasured for the `vuln` and `bug_loc` tasks and for the external harness. The external harness's `repo_qa` fold also reads the rewritten `TASK_HEAD: repo_qa`, including the dropped signature clause, and that is unmeasured too. Their own heads (`TASK_HEAD: vuln`, `TASK_HEAD: bug_loc`, the three `external.*` heads) are byte-identical. To restore the old text for those readers, restore the two `BACKBONE` breadth passages (and, for the external `repo_qa` reader, the old `TASK_HEAD: repo_qa`) from the previous seed.
>   - **Way back:** `guidance_task: none` restores the old prompt. *(Holds only if the winning PR makes the served template follow the knob; see owner point 5.1.)*

**Where what landed differs from the spec's wording of this entry:**

- The spec's entry names only `BACKBONE` as unmeasured for the external harness. That cannot hold for `repo_qa`. `TASK_HEAD` is harness-invariant and the spec requires that head to be rewritten, so the external `repo_qa` fold changes in two sections, `BACKBONE` and `TASK_HEAD: repo_qa`. #461 recorded this as a deviation and pinned it in `test_external_fold_over_the_candidate_differs_only_in_the_rewritten_tiers`. The draft above names it.
- The signature clause was **dropped**, not kept in conditional form. The ledger (`benchmarks/configs/guidance_task/candidate_seed_ledger.md`) records this.
- `BACKBONE` keeps its packaged length exactly: 2,173 characters, 543 tokens. To make room for the "already shown" definition, its framing preamble was shortened (#461, deviation 3).

## 2. The eval-suite changelog entry of the same PR

Draft only; it goes into `benchmarks/CHANGELOG.md` in the winning PR. The names are those on this branch.

> - **The arm overlay's guidance file may hold a `SYSTEM_PROMPT` section.** `campaign/before_after_arm_settings.py` parses the `guidance_sections` file strictly. It allows `SYSTEM_PROMPT` beside the ten skill sections and still refuses every other header (`REWRITE_PROMPT` included) and a prompt-only file. The skill sections are validated by the product loader as before. The prompt section is validated by the ask prompt's own rules: non-empty, within 1,200 tokens, every routed tool named. Those rules are now public in `optimize/artifacts/ask_prompt.py` as `system_prompt_violations()` and `SYSTEM_PROMPT_KEY`.
> - **Arm overlays accept `guidance_task`.** The product key is accepted in an arm overlay and moves the arm's settings digest (`ARM_OVERLAY_SHAPE` names it).
> - **`before-after-compare --guidance-task-clauses [--noise-edge EDGE]`** is an opt-in mode for this experiment only. After each variant's standard verdict it prints one finding per added clause, in reading order: (b) unanswered outcomes, (c) minimum gain, (a) tokens. It then prints a `### Pre-registered rows (reported, not gating)` section. The verdict, its reasons and the exit status are unchanged, and without the flag the output is byte-identical. `--noise-edge` is the slice's noise edge in penalised Turns (0.6 for the 20-task slice). Leave it out when reading the re-run on `repoqa-qa/dev`. Three combinations are refused as input errors (exit 2): the mode with `--completeness-arm`, `--noise-edge` without the mode, and an edge ≤ 0. Two supporting changes: `before_after_acceptance.ESTIMATE_TOLERANCE` is now public (it was `_TOLERANCE`; the value is unchanged), and `before_after_paired.paired_means` is the shared paired point estimate.
> - **The guidance-task experiment's three variant overlays and their guidance files**, in `benchmarks/configs/guidance_task/`:
>   - `arm_variant_a_system_v4_draft.yaml` → `variant_a_system_v4_draft.md`: the candidate seed's ten sections plus the `SYSTEM_PROMPT` draft of `system_v4`. This is the variant that decides.
>   - `arm_variant_b_candidate_seed.yaml` → `candidate_seed.md`: `system_v3` plus the candidate seed. Diagnostic.
>   - `arm_variant_c_placebo.yaml` → `placebo_guidance.md`: `system_v3` plus length-matched placebo guidance about laying out a printed conference schedule. Diagnostic.
>   - `candidate_seed_ledger.md`: the ten-section token ledger.
>
>   Each overlay holds only `guidance_sections`, and its header carries the free plan command.

**Where what landed differs from the spec:**

- The overlays live in `benchmarks/configs/guidance_task/`, beside the guidance files they name, and not at the top of `benchmarks/configs/`. `benchmarks/tests/core/test_all_overlay_configs_load.py` loads every top-level `configs/*.yaml` as a product `AppConfig`, and an arm overlay is not one (#465).
- The compare mode prints findings for **every** variant, because the verb cannot know which one is (a). The operator reads (a)'s findings (#462).
- The ledger uses a third sentence label, `neutral`, beside `call-adding` and `call-removing`, for framing, routing and answer-form sentences (#461, deviation 2).
- In a winning PR the candidate and draft move into the product (part 3). The eval entry should then say that the overlays remain as the record of what was measured.

## 3. What the winning PR must also do (not done here)

From the spec's "Outcomes". No ticket of this series does any of it.

1. **Flip the default** of `ask_your_docs.guidance_task` to `repo_qa`: the `AskYourDocsConfig` field and its restatement in `defaults/default_config.yaml`, including that YAML's "WHY off" comment.
2. **Ship the candidate as the packaged seed.** `benchmarks/configs/guidance_task/candidate_seed.md` replaces `python/pydocs_mcp/harness/core/skills/search_guidance_seed.md`, including the `BACKBONE` rewrite.
3. **Ship `system_v4` as a new template** (`harness/core/prompts/system_v4.j2`), with the gated branch rule appended as its last rule. The draft's rules run 1–10, so the branch rule becomes 11. Earlier templates are not edited. `ACTIVE_SYSTEM_PROMPT_TEMPLATE` (`harness/core/prompt_surfaces.py`, `"system_v3"` today) moves only in the way owner point 5.1 settles.
4. **Regenerate the goldens and the optimizer-side seeds** that follow the active prompt. The `system_v3` golden (`tests/fixtures/goldens/ask_your_docs_system_v3.txt`) stays as it is, because the `none` byte-identity test compares against it.
5. **Record the two fingerprint moves in the umbrella issue #366:** the ask prompt's (a second time) and the search skill's (once).
6. **Keep every routed tool named in the template a live tool.**
7. **Keep "what ships is what was measured".** The packaged seed, and the no-variable render of the packaged `system_v4` template, must equal `candidate_seed.md` and the `SYSTEM_PROMPT` section of `variant_a_system_v4_draft.md`, byte for byte, after the document grammar's one normalisation pass. The draft body is already stored in `parse_sections` normal form (#463). The appended branch rule is absent from both the draft and the baseline's render while no server advertises a branch argument. A wording change after the run is a new measurement.
8. **Carry the two changelog entries above.**

Practical constraints the winning PR inherits:

- `harness/ask_your_docs/agent.py` sits at 499 of its 500-line budget (#459). The served-template-follows-the-knob design has to land outside it, or split it first.
- The `ask_your_docs.*` heads sit at 295, 295 and 298 tokens against a 300-token cap (#461). Any wording added to a head breaches the cap, and the change would be a new measurement anyway.

## 4. The cost of the run, for the owner's go

The figures are the spec's ("Cost and budget"), quoted with their ranges. No price was looked up and no arm was planned against a real endpoint.

- **The 20-task run is five arms, not four.** The compare verb needs the baseline's A/A replicate on every slice it reads, so the arms are the baseline, its replicate, and variants (a), (b) and (c). At recorded prices that is **about $0.40–0.65** on `repoqa-qa/small_dev` ($0.08–0.13 per 20-task arm). The confirmed four-arm figure was $0.35–0.55, inside ledger line 17 ($0.60–1.20).
- **The 100-task re-run**, only if the minimum-gain clause asks for it, is three arms: the baseline, its replicate and variant (a). That is **about $1.2–1.95** on `repoqa-qa/dev`. The confirmed two-arm figure was $1.0–1.3.
- **Worst case for the experiment: about $2.60.** That is inside ledger lines 17 and 15 together. The excess over line 17's high of $1.20 is booked against line 17 as an overrun and covered by the $1.00–2.00 that line 15 (the closed zero-hit experiment #394) no longer needs. The owner approves that booking with the go for the re-run.
- **The program limits do not move:** a ceiling of $35, stop-and-ask at $25, the owner's go before each run, and a `--max-usd` for each run. On this branch, `before-after --max-usd` is the cost ceiling for both arms of one `before-after` call, so each call's cap should cover the baseline when that call also runs it. The spend is at the model provider; the Bench VM cap does not apply.
- **Drop order** under the stop-and-ask: (c) first, then (b). Never (a).
- **How the arms are run (from the overlay headers):** all arms run on one commit, the head of the experiment branch.
  - The first paid arm also runs the baseline. Every later arm adds `--reuse-baseline <baseline arm dir>`.
  - The A/A replicate is the baseline run again under the same settings with `--allow-identical`.
  - The free plan command is written in each overlay's header. The paid run adds `--confirm-spend`, and only after the owner's go.

## 5. The points that need the owner's word

None of these blocked the $0 work.

1. **`none` after a win.** "`guidance_task: none` restores the old prompt" is true only if a build under `none` still serves `system_v3` after the win. On this branch the active template is one module-level switch: `ACTIVE_SYSTEM_PROMPT_TEMPLATE = "system_v3"` in `harness/core/prompt_surfaces.py`, rendered once into `prompts.SYSTEM_PROMPT`. Moving that switch to `system_v4` would make `none` serve `system_v4` with no guidance: a prompt missing six rules, which is neither the old prompt nor a measured one. There are two options:
   - the winning PR makes the served template follow the knob (`none` serves `system_v3`; any other value serves `system_v4`);
   - or the owner rewords the sentence.

   This is the winning PR's design, and no ticket of this series builds it.
2. **The tokens clause's row (as #462 coded it).** Clause (a) gates only on `tokens in (per task)`, read as the paired mean over the tasks both arms define, the same way the verdict's estimates are read. It fires on a strict rise (more than `ESTIMATE_TOLERANCE`).
   - The uncached rows exist only as a total and per Turn. The finding quotes both, `uncached tokens in (total)` and `uncached tokens in (per turn)`, and neither gates.
   - If either arm recorded no usage, the clause prints **not read** with `n/a` and neither passes nor fires.
3. **The unanswered-outcomes clause includes `unanswered_empty`**, on the literal reading of "no unanswered outcome class may rise" (as #462 coded it). It compares whole-arm counts of `starved_reply`, `timeout` and `unanswered_empty` and fires on any strict rise. Budget exhaustion is left to the standard rule's guard and is not counted a second time.
4. **The exit status (as #462 coded it)** is the standard verdict's. The mode never changes it. The status covers every variant: 0 only when all of them PASS, 1 when any FAILs, 2 for no verdict or an input error. So a PASS whose clause says "no flip" can still exit 0, and a PASS of (a) exits 1 when a diagnostic variant fails. Read (a)'s findings, not the exit code.
5. **Other readings #462 coded, to confirm with the above:**
   - Clause (c)'s gain is the drop in penalised Turns-to-answer from the verdict's own estimate. It counts as "under the edge" only if gain < edge − `ESTIMATE_TOLERANCE`, so a gain exactly at 0.6 holds.
   - "Turns before the Needle" is computed per task, as the headline less `turns after needle`, over the tasks where both are defined. A task that never showed the Needle is left out.
   - Findings print for every variant, not only (a).
   - The mode with `--completeness-arm` is refused, and so is `--noise-edge` without the mode.
6. **The band.** 0.050 is one task of 20 and is the floor. The run's own A/A pair sets the band and may widen it, and the printed table states the band used.
7. **The re-run's sample.** `small_dev` is drawn from `dev`, so the 100 tasks include the 20 already run. It is a larger sample of the same pool, not a fresh one, and it consumes no test split.
8. **What the earlier tickets reported, as it happened:**
   - **No stop-and-report exit was triggered** in #461, #463, #464 or #465.
     - #461: all six moved rules, each with its trigger, plus the effort shape and the `Not confirmed:` sentence fit the ask heads, at 295–298 tokens of 300.
     - #463: every moved rule's instruction has a home in the `ask_your_docs.repo_qa` head.
     - #465: all three guidance files load and plan cleanly.
   - #460 confirmed the spec's flagged reading: at `8b625a22` the overlay loader refused a `SYSTEM_PROMPT` section at plan time. That was the expected red, not a stop.
   - **Owner-visible, from #463.** Two `system_v3` clauses have no word-for-word home in the variant-(a) state. #463 judged that neither meets the stop exit.
     - Rule 5's "Never invent parameters, defaults, or return shapes the tools did not show": the head keeps the trigger and the action (an Example only from a shown signature or usage), and draft rule 4 still says "do not invent an answer".
     - Rule 14's examples of a structure question.

     If the owner wants the rule-5 clause restored in the heads, it must happen **before** the paid run. The heads have only 2–5 tokens of headroom.
   - **Owner-visible, from #461:** the external `repo_qa` reader also gets the rewritten task head (part 1). The `neutral` label is a third label; a strict binary would need a direction forced on ten ledger rows.
   - **Owner-visible, from #459:** the optimize layer's `sent_settings_fingerprint` does not fold `guidance_task`; only the arm settings digest does, which is all the spec asks for. On the eval path, an arm that sets `guidance_task` without `guidance_sections` gets the packaged seed folded under the knob. The three variant overlays set neither key beyond `guidance_sections`.
   - **Owner-visible, from #464:** the placebo was rewritten after review, because its first draft mirrored the candidate's rule shapes (stop trigger, effort shape, labelled lines). Its tests now also refuse those shapes.

## If the experiment loses

The experiment PR closes unmerged, and its result table goes to the umbrella issue #366 (posted by the owner or the orchestrator, not by a ticket). No changelog entry is written. `system_v3` stays the product and the optimizer's ask-prompt seed, and the knob never lands. Two eval-side pieces stay useful beyond the experiment:

- the overlay loader's `SYSTEM_PROMPT` section, which step 8 needs on its fallback channel (decision B4: after a loss, step 8 takes the system-prompt override channel);
- the compare verb's opt-in mode.

Whether they are carried into their own eval-only change is a choice for that moment.

## The integrated gate run

The tickets landed in parallel, so this note is the series' one integrated check. The full gate set ran once on the branch with everything in: `integrate/391-466-base` @ `69e3b7a2`, plus this note, which is the only file this ticket adds.

| Gate | Result |
|---|---|
| `uv lock --check` | pass |
| `ruff check python/ tests/ benchmarks/ scripts/` | pass |
| `ruff format --check python/ tests/ benchmarks/ scripts/` | pass (2,111 files) |
| `mypy python/pydocs_mcp` | pass (347 files) |
| `complexipy python/pydocs_mcp --max-complexity-allowed 15` | pass (the tool rewrites `complexipy-snapshot.json`; that rewrite was reverted, not committed) |
| `vulture python/pydocs_mcp --min-confidence 80` | pass |
| `scripts/smoke_check_benchmark_imports.py` | pass |
| README audit (`CLAUDE.md`, "README files") | prints nothing |
| Changelog check: `git diff --name-only $(git merge-base origin/main HEAD)...HEAD` | lists neither `CHANGELOG.md` nor `benchmarks/CHANGELOG.md` |
| Product suite: `pytest tests/ --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-fail-under=90` | 8,504 passed, 23 skipped, 1 xfailed; **1 failed, 103 errors**, all environmental (below). Coverage 97.99% (≥ 90). |
| Eval suite: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q` | 4,899 passed, 1 skipped; **5 failed**, all environmental (below). |

**Neither full suite is green on this box, and no failure is owned by a ticket of this series.** All 109 failing items have one cause: `ValueError: Could not load model BAAI/bge-small-en-v1.5 from any source`. The FastEmbed cache (`/tmp/fastembed_cache`) is empty, and the box cannot reach Hugging Face. Every sibling ticket's run hit the same 104 product and 5 eval items.

- **Product:** 104 items failed: 103 errors in `tests/test_structured_envelope.py` (46), `tests/test_pointer_table_wire.py` (36), `tests/test_outline_wire.py` (18) and `tests/test_child_expand_wire.py` (3), plus 1 failure, `tests/extraction/strategies/embedders/test_model_name_matches_config.py::test_fastembed_reports_the_configured_model_name`. The log holds the error exactly 104 times.
- **Eval:** 5 failures, all raised inside a `pydocs_eval.runner` subprocess: `benchmarks/tests/core/runner_resume/test_runner_resume_cli.py::test_cli_run_id_resumes_and_the_report_covers_every_task`, plus 4 in `benchmarks/tests/core/test_runner_results_dir_and_commit.py`.
- **Owner or orchestrator action:** re-run both suites where the model is cached, or with network access. Expect green there.
- **Run conditions:** both suites ran serially (`pytest-xdist` is not in the dev group), with `uv sync --frozen --group dev --extra harness-ask-your-docs`. The eval suite also needs `uv pip install -e "benchmarks[optimizers-gepa]"` (unidiff, gepa), which the root lock does not install. That changes no lockfile.
- **Not run:** the Rust gates and `pip-audit`, because the series changes nothing on the Rust or dependency side.
- **The series' own tests pass, run on their own:**
  - 85 product tests: the knob's config and served-prompt modules, the binding modules and the prompt-seam pins.
  - 260 eval tests: the overlay loader, the `SYSTEM_PROMPT` section, the block probe, the variant command, the flip clauses (three modules), the candidate seed, the placebo, the `system_v4` draft, the variant overlays, and the unedited compare and acceptance modules.

The changelog check lists one `README.md`, `docs/review/README.md`. That file comes from the spec's base commit, PR #458 (`060dd545`, the review page), not from any ticket of this series. It prints nothing under the README audit, and this series touches no `README.md`.
