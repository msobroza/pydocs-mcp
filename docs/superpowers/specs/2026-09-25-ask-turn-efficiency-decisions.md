# Turn-efficiency program: owner decision log (grilling 2026-09-24/25)

Every entry below was accepted by the owner. Where a later decision supersedes an earlier one, the
earlier one is marked SUPERSEDED. The spec must follow this log exactly.

## Goal and symptoms
The ask-your-docs chat agent calls too many tools. The program has three goals:
- reach the needle in fewer turns;
- check that the needle is complete before stopping;
- never end a question on LangGraph's canned "Sorry, need more steps to process this request." reply.

The four symptoms are: over-exploration, budget exhaustion with no answer, a slow first hit, and an
incomplete needle.

## Glossary (CONTEXT.md, uncommitted in the worktree)
The terms are Needle, Turn, Turns-to-answer, Budget exhaustion, Finalized answer and Turns after
needle. Use them verbatim.

## Scope and delivery
- **Q1.** Implement the proposals the analysis verified. Contested items run as experiments.
- **Q11.** A failed experiment's PR is closed unmerged and its table is recorded in the umbrella issue. Nothing is merged behind a switch that stays off.
- **Q8 / Q17.** One umbrella spec issue, plus one ticket and one PR per ladder step. Each PR carries its own CHANGELOG entry: the ladder serializes merges, so the entries do not conflict.
- **Q16.** Spend ceiling $20, stop-and-ask at $15, with spend measured at OpenRouter after each step. Every paid rung needs the owner's go before it spends.

## Settings
- **Q5.** OpenRouter throughout, pinned exactly to the 2026-09-15 before/after block:
  - chat model qwen/qwen3.8-27b in thinking mode (temperature 1.0, top_p 0.95, max_tokens 16384), from benchmarks/configs/ask_openrouter_qwen3_8_27b_llm.yaml;
  - embeddings qwen/qwen3-embedding-4b at 2560 dimensions, from benchmarks/configs/ask_openrouter_qwen3_4b.yaml;
  - OPENROUTER_API_KEY from the base repo's .env, never printed.

  Do one A/A run (same commit, same settings) before the first comparison, to measure the noise floor.
- **Q6.** Cumulative ladder: each step is measured against the previous accepted state.

## Metrics
- **Q4.** Three metrics:
  - Turns-to-answer is the headline.
  - The budget-exhaustion rate.
  - Turns after needle.
- **Q9.** Turns-to-answer is aggregated two ways:
  - The headline is a penalised mean in which an exhausted question counts as budget + 1, which is 13.
  - The answered-only mean is reported beside it.
- **Q35.** Exhausted then finalized is its own outcome. It counts as unanswered for the answered-within-budget rate and as 13 in the penalised mean. Its answer is still scored and reported.
- **Q10.** Accept a step when:
  - the penalised turns-to-answer goes down (point estimate);
  - budget exhaustion does not rise;
  - correctness stays within the A/A noise band.

  Escalate a step to a 100-task dev slice only when the correctness guard lands at the edge of the band.
- The analysis adds statistics: one-sided paired Wilcoxon on turns and calls, McNemar on gold-reached, and Holm across at most 3 variants. A step that is not significant but is non-inferior with a lower point estimate may be adopted on cost grounds, with the owner's sign-off.

## Slices
- **Q2 / Q3 / Q12. SUPERSEDED by Q29.** These had made repoqa-qa the benchmark, with small_dev per step, small_test at the end, and the chat check reported but not gating.
- **Q29.**
  - Primary for the prompt and tool-output steps is the chat slice:
    - the 10 vendored example_needle questions, with gold written from the code;
    - 20-30 NEW held-out chat questions, gold written from the code, frozen before system_v3's wording is fixed.
  - An agent drafts the questions and their gold, and the owner ratifies them.
  - repoqa-qa small_dev is a non-inferiority guard only.
  - Final confirmation runs on the held-out chat set plus a fresh repoqa test sample. small_test is consumed.
- **Q13.** Add swe-qa-questions for completeness, via the eval plumbing step. Do not stratify by repo; use gold-size buckets or /all.
- **Q24.** Carry the swe-qa reference answer text in gold.extra. The file-set gold is unchanged.
- **Q37.** Add one small-model arm (a 4-8B open model on OpenRouter) on the chat slice for the finalize step and v3. Add one larger-corpus check (a dependency-indexed workspace) before declaring symptom 1 solved.

## Answer scoring and judge
- **Q7.** Store the answer text. Score it deterministically, plus an LLM judge.
- **Q18.** The judge is TypeSafe Jev, reached through OpenRouter (POST https://openrouter.ai/api/v1/systemone), billed to the same key.
- **Q21.** Use plain HTTP behind an owned `JevJudgeClient` with a `FakeJevJudgeClient`; no typesafe-sdk dependency. Timeouts and bounded retries: the judge profile has a 10 s timeout and 2 retries.
- **Owner rule.** Every Jev use follows the TypeSafe skill (installed at ~/.claude/skills/typesafe-ai/) and the live docs (https://docs.typesafe.ai/llms.txt):
  - one narrow judgment per question, over named-JSON state;
  - criteria levels written as concrete standalone situations;
  - independent questions sent in one request;
  - thresholds validated on our own data, with policy kept in code.
- **Pinned model.** Pin jev-1.13, never jev-latest. The client asserts that the response's model field equals the pin. An upgrade is a deliberate YAML change followed by recalibration on the stored labelled set and an A/A re-score. Cache calls by input hash.
- **Q22 / Q22′. SUPERSEDED by Q22″.**
- **Q22″ (repoqa-qa).**
  1. Code first: extract every citation in the answer (datasets/_citations.extract_path_citations plus a dotted-name extractor), normalize it with module and qualname aliases, and exact-match it against the gold. The result is **needle cited**, the primary correctness guard.
  2. Jev Noul `needle_identified`, with the gold in state. The criteria list the forms that count and the near-misses that do not, and hedged candidate lists count as false.
  3. Jev Noul `addresses_grader`, to flag answers that address the grader (injection).
  4. No completeness score for repoqa.
  5. A gold-blind Choice is used as a calibration audit only.

  `gold_substring_all` and `gold_recall` remain reference columns only.
- **Q19 / Q19′. SUPERSEDED by Q19″.**
- **Q19″ (swe-qa-questions).** State is `{task:{question}, reference_answer, agent_answer}`.
  - One Noul per gold file: "points to this file as a place that answers". A file that is ruled out counts as false. Code computes recall, and precision from the extracted citations.
  - A `completeness` Score with 4 levels, L0-L3 as in jev_research/brief.md.
  - A `contradicts_reference` Noul, named "agreement", not "grounding".
- **Q23.** Jev judges the ladder only. The optimizer keeps its existing claude-sonnet-5 rubric judge.
- **Q26 (refines Q20).** Calibration:
  1. The stronger LLM judge (via OpenRouter) labels 60 answers, stratified, with borderline cases oversampled and 3-5 injection answers planted. The owner spot-checks 10.
  2. Each question gets a review band (start at 0.3/0.7, then sweep). Answers inside the band escalate to the LLM judge, never back to Jev.
  3. Scores are gated on confidence and cut at the midpoints between levels; never average them.
  4. Thresholds are per question and per model version.

## Product changes (ladder)
- **Q14. SUPERSEDED by Q27.** It had made a budget-aware architecture auto's default.
- **Q27.** Finalize on the sentinel, independent of architecture.
  1. In agent.ask and binding._build_and_execute, when the last AIMessage is the prebuilt sentinel (or a GraphRecursionError escapes a hand-built graph), drop the sentinel.
  2. Make one call without tools over the accumulated messages. The call carries a trailing finalize note (a new frozen template, sent as a trailing message, never a system-prefix edit) and ends with a `Not confirmed:` line.
  3. Bind with tool_choice="none", with a fallback for endpoints that do not support it, and strip leftover tool_calls.
  4. Stamp the reply as finalized and meter its usage. In the UI, label it "answered at the step limit" and store it in history.
  5. Test it with forced-cap arms (max_agent_turns 4-6).

  There is no per-turn budget note and no new default architecture.
- **Q15. SUPERSEDED by Q28.**
- **Q28.** Slim system_v3.
  - It removes only call-inducing rules:
    - the Example-chain rule;
    - rule 7 becomes permission, not obligation;
    - rule 12 is replaced;
    - orientation: call get_overview only after a search misses, never alongside the first search;
    - windowed reads;
    - a definition of "showed".
  - It adds no stop rule.
  - The completeness definition and stop test ship later as a separate arm.
  - The guidance_task knob defaults to NONE.
- **Q33.** system_v3 absorbs #323's gated branch rule (`{% if branch_selector_advertised … %}`), and #323 closes against that PR.
- **Q34.** Edit descriptions.md so get_overview is no longer "first call on an unfamiliar project". This regenerates the goldens and moves the description artifact hash.
- **Q32.** Ratify ADR 0023 with four amendments, in the tool-fix PR:
  - grep `path` means "a directory, or one file" (§3.7);
  - a partial get_context rendering note (§3.4);
  - the heading source-pointer rule (§3.2);
  - any "Members:" line (§3.2).
- **Q36.** Accept the CHUNK_TREE_RULE_VERSION bump in the gap-free views step: one re-extract, zero re-embed.
- **Q31.** Before restarting, the owner copies the failing questions and answers out of the two running UIs (ports 8512/8513). Both UIs are then restarted on main. Add an opt-in chat trace-persistence knob (default off, byte-identical).
- **Q38.** Jev inside the harness (stop gate, citation check, rerank) is a late experiment only, after step 9: opt-in, default off, never gating, and judged by the deterministic check plus the LLM judge, never by Jev.

## Q30: the ladder, in order
- **0.** Capture the owner's failing questions; restart the UIs; add the opt-in chat trace persistence.
- **1.** Vendor the chat slice (10 questions, run_repro.py, config; curated code-authored gold, owner-ratified) and write the 20-30 held-out questions.
- **2.** Measurability, thin slice:
  - is_budget_exhausted_reply, plus eval-side detection with a legacy back-fill;
  - persisted answer text;
  - an outcome taxonomy;
  - calls_after_first_gold and calls_after_first_gold_read;
  - paired turns and calls rows;
  - cost and latency rows;
  - TurnBudgetExceededError carries its trace.
- **3.** Finalize on the sentinel (Q27).
- **4.** Four tool-hygiene fixes:
  - grep on a single file;
  - partial get_context;
  - get_context body slots (no slot for builtins, focus body first);
  - the heading source pointer.

  Plus ADR 0023 ratification with amendments.
- **5.** Slim system_v3, plus the descriptions.md get_overview edit, absorbing #323.
- **6.** Gap-free class and module source views (span_filler, CHUNK_TREE_RULE_VERSION bump; no decorator span shift).
- **7.** Decision-protocol plumbing:
  - same-commit variant arms (--baseline-settings/--candidate-settings), --reuse-baseline, and resumed arms that keep finished tasks;
  - a scaffold-free task rendering (a new TASK_SCAFFOLD_VERSION);
  - corpus metadata so multi-location slices work, and the swe-qa-questions unlock;
  - a holm_adjust helper;
  - an expected-turns cost model.
- **8.** The completeness rule and effort shape as a separate measured arm on the multi-location slices (definition per question shape, stop test, `Not confirmed:` labelling).

**Experiments** (contested items in narrowed form):
- a "Members:" line on class/module hits (plus an optional child_expand with a displacement guard);
- the guidance_task knob, default NONE;
- the eval binding seeds the bare question (fixes a latent bug; do NOT flip seed_search_with_question on);
- pointer density: keep callers, leave pointer_hits at its default, and retarget zero_hit.

**Refuted, do not build:**
- M-15 cost-aware optimizer objective;
- M-11 embedding headers;
- M-12 LLM rerank;
- M-13 identifier route;
- M-4 call cache and context elision;
- M-2 grounding re-entry gate;
- M-1 per-turn budget note and 8-turn cap;
- M-6 Part B chat task name;
- M-3 seed flip;
- M-7 b/c;
- M-8 prose tag, score suffix and budget raise;
- M-9 decorator span shift;
- M-10 dropping callers;
- hybrid RRF/WSI, centrality_prior and parent_rollup as defaults;
- lowering max_agent_turns;
- declaring symptom 1 solved on the current evidence.

## ADR
One new ADR, 0025, records how the eval scores the end of the budget and uses the Jev judge:
- finalized is its own outcome;
- the penalised mean;
- pinning jev-1.13;
- the deterministic check gates correctness and Jev is second;
- no Jev question both steers and grades.

It is written with the spec and reviewed by the owner before merging.

## Evidence files (same folder)
- final_report.md: the analysis's verified ranking, with file:line evidence.
- final_meta.json and workflow_results.json: reader maps and verdicts.
- jev_research/brief.md: the Jev request designs and calibration plan.
- repro/: the 10-question live reproduction.
- README.md: the program state.

## Round 5 (2026-09-25): spec follow-ups Q39-Q50 and the vision bridge B1-B6 (all accepted)
- **Q39.** Keep the $20 ceiling with the spec's drop order: one pointer-density variant first, then the experiments, the cap-5 arm, the seed variant, and the small-model arm. Stop-and-ask at $15.
- **Q40.** system_v3 also includes:
  - (j) "context and uniqueness checks go in one parallel turn";
  - a second get_overview trigger, "or for a question about the project's structure" (alongside "after a search misses", never with the first search);
  - a reworded descriptions.md:4 Workflow line.
- **Q41.** Proceed: v3 carries #323's gated branch rule, which decouples #323 from its listed blocker #321. The rule stays inert until the capability is advertised, and tests cover both renderings.
- **Q42.** Step 4d: suppress the heading source pointer, and show a read window on every prose hit whose text is short. Not tree-hydrated.
- **Q43.** STARVED_REPLY, TIMEOUT and UNANSWERED_EMPTY count as budget + 1 (13) in the penalised Turns-to-answer mean. They are excluded from the answered-only mean and kept in the outcome tally.
- **Q44.** Step 8 may be adopted when gold-site coverage improves and the penalised turns rise by at most +0.5, with the owner's sign-off. Otherwise the Q10 rule applies unchanged.
- **Q45.** If the guidance_task experiment wins, its PR flips the default. B1 defines what that PR contains.
- **Q46.** Split step 2 (2a eval, 2b product) and step 7 into ordered sub-PRs, each with its own CHANGELOG bullet.
- **Q47.** Write 30 held-out chat questions and reserve 10 of them for final confirmation only. The other 20 are the adoption set; every consumption is recorded.
- **Q48.** Models and routing:
  - The small model for the Q37 arm is qwen/qwen3-8b.
  - The label judge for calibration is claude-sonnet-5 via OpenRouter.
  - Timeouts and retries live in the pinned Q5 --llm-block file (single source), not as separate constants.
  - Pin the OpenRouter upstream provider route for qwen/qwen3.8-27b.
  - Model names are confirmed at plan time.
- **Q49.** The larger-corpus check uses pydocs-mcp itself, indexed with its runtime dependencies (embed cost printed before spending), plus 10 questions the owner writes.
- **Q50.** Publishing, after the updated spec is shown:
  1. Commit the spec, ADR 0025, this log and the CONTEXT.md terms on the branch, with no Co-Authored-By trailer.
  2. Push and open a documentation PR (not merged).
  3. Create the umbrella issue with a condensed body under 65,536 characters that links to the full spec.
  4. Create one ticket per unit with blocked-by edges.
- **B1.** The ladder stays unchanged; v3 is itself the trainable ask_prompt surface. The guidance_task experiment carries a $0 deliverable: a candidate seed file restating v3's rules in the correct skill layers. It compares v3 + knob=REPO_QA + candidate seed against v3 + NONE, and borrows the repo_qa task (the `chat` task name stays refuted). A winning PR flips the default, ships the packaged seed and a slimmed system_v4.j2 (routing sentences plus gated text). A losing PR closes unmerged, and v3 stays the product and the optimizer's ask_prompt seed. Any seed text chat may receive is committed only after the held-out chat set is frozen.
- **B2.** Chat-derived policy goes into the HARNESS_TASK_HEAD ask_your_docs.* sections, not BACKBONE; promotion to BACKBONE is left to the multitask optimizer. The three ask heads are rewritten: drop the "catalog is already in your prompt" claim and the dead "skip the example-call snippet".
- **B3.** In the candidate seed:
  - rewrite BACKBONE :36-41 so it keeps the stop rule and adds the "already shown" definition;
  - make the repo_qa TASK_HEAD confirming read conditional;
  - leave the external heads untouched.

  Every seed edit is a replacement, with a per-section token ledger in the PR (BACKBONE ≤1000, each head ≤300).
- **B4.** Step 8's channel is decided after the guidance_task result: a guidance_sections arm if the knob won, the SYSTEM_PROMPT override (system_v4) otherwise.
- **B5.** A separate $0 PR hand-edits SERVER_INSTRUCTIONS (:9/:11/:14) to agree with v3, regenerating the goldens (the hash moves once). It is outside the ladder.
- **B6.** Tool descriptions stay ONE shared document (ADR 0005: the 11-section set is independent of task and arm), optimized across tasks; there are never per-task variants. There is no descriptions overlay key: the surface varies only with the commit, and traces already carry current_artifact_hash.
- **Also from the bridge:**
  - Drop "track Q10 metrics in the optimizer's metric set"; it becomes a future optimizer ticket.
  - The per-turn cost of the knob (+800-900 tokens) is stated against the ceiling.
  - Acceptable fingerprint moves: AskPromptArtifact twice (v3, v4); SearchSkillArtifact once (seed adoption); current_artifact_hash plus the tool_docs fingerprint (description edits).
  - Must not move: the nine-tool surface, DELIVERED_SECTION_CHANNELS, HARNESS_NAMES/TASK_NAMES, and the freeze-pool digests.

## Round 5 amendments (2026-09-25, owner): these override Q37(a) and Q48
- **Q37(a) and Q48(a) REMOVED.** There is no small-model arm anywhere in the program: no qwen/qwen3-8b, no informational small-model runs, and no ledger lines or drop-order entries for it. Q37(b), the larger-corpus check, stays as in Q49.
- **Q48(b) REPLACED.** The calibration labeller (the stronger LLM judge that labels the 60 calibration answers and decides the in-band cases) is OpenAI GPT-6 Luna via OpenRouter:
  - The model id is pinned to `openai/gpt-6-luna` (never the `~openai/gpt-luna-latest` alias). It is verified on OpenRouter's model list on 2026-09-25: $0.10/M input, $0.50/M output, and it supports `reasoning_effort`, `structured_outputs` and `seed`.
  - It runs at the highest reasoning effort the endpoint accepts. The owner's words were "extreme high effort", so the level is "xhigh" if accepted, else "high". The first live call verifies the level, and the client asserts the returned model id.
  - Labels are requested as structured output (JSON schema).
  - Because reasoning tokens at maximum effort dominate the cost, the estimate is about $0.10-1.00 per 60-label pass. Its ledger lines are updated accordingly.
  - It is NOT the optimizer's judge (the optimizer keeps its claude-sonnet-5 rubric judge, Q23). The label judge and the chat model are different vendors, which weakens same-source bias; the owner spot-check of 10 stays.
- Q48(c) (timeouts and retries in the pinned --llm-block) and Q48(d) (OpenRouter provider pin for qwen/qwen3.8-27b) are unchanged.

## Round 5b (2026-09-25, owner): calibration tiers and budget. These override the calibration parts of Q26/Q48(b)
- **Q53. Tiered judging.**
  - **Jev** (`jev-1.13`) scores every answer. Outside its review band, Jev's verdict stands.
  - **GPT-6 Luna** (`openai/gpt-6-luna`, highest accepted reasoning effort, structured-output labels, pinned id) decides in-band cases during ladder scoring. Luna is trusted only if it agrees with the Astra reference labels on at least 90% of the calibration set; otherwise in-band cases are reported "undecided". Luna labels the calibration set once to measure that agreement (about $0.2-0.8).
  - **GPT-6 Astra** (`openai/gpt-6-astra`, via OpenRouter's `:batch` variant, `high` reasoning effort, pinned id, structured-output labels) produces the calibration reference labels only. It never runs during ladder scoring. It moves to `xhigh` only if a pilot shows `high` disagreeing with the owner's spot-checks.
  - The owner spot-checks 10 Astra labels per calibration pass.
- **Q54. Budget.**
  - The program ceiling rises from $20 to $35, with the stop-and-ask moving to $25 (proportional).
  - A dedicated $15 sub-budget covers calibration labels.
  - A 10-label Astra pilot (batch, `high`, about $1-3) measures real tokens per label before calibration is sized.
- **Q51. Sizes.** Pass 1 (chat + repoqa) takes 100 labels and pass 2 (swe-qa pilot) takes 60, if the $15 sub-budget allows after the pilot. Otherwise the size is set to what the sub-budget buys, and the owner is told.
- **Q52.** In-band escalations to Luna during the ladder are cached (an answer is never labelled twice) and capped at $2 for the whole program. Beyond the cap, in-band answers are reported as "undecided". Add a ledger line for it.
- **Fact (owner asked 2026-09-25).**
  - For repoqa-qa the gold exists: the needle's path and symbol, and the underlying repoqa carries the function body.
  - No prose reference answer is needed there.
  - Agent answers to calibrate are NOT on disk. The 2026-09-15 traces kept only `answer_chars`, so the A/A run regenerates them with the agent under test (qwen/qwen3.8-27b at the pinned Q5 block): about 120 answers (2 arms × (10 chat dev + 20 chat adoption + 30 repoqa small_dev)). The forced-cap arms add finalized answers later.
  - The labellers grade answers; they never generate them.

## Round 5c (2026-09-25, owner): reference answers generated from ground truth
- **Q55.** Generate a reference answer for each repoqa-qa task in small_dev (30) and in the fresh final test draw (30), from ground-truth context only.
  - **Input:** the question, the gold path and symbol, and the needle's function body (repoqa `ast_body`) plus a few surrounding lines of its file.
  - **Output:** a short reference shaped like a good agent answer: path, symbol and line span, plus 2-4 sentences on what the function does, taken only from its body.
  - **Storage:** `gold.extra["reference_answer"]` (the same field as swe-qa, Q24), stamped with the generator's model id and prompt hash, then frozen and versioned.
  - **Code check:** the reference must contain the gold path and symbol and no other file path, otherwise it is regenerated. The owner spot-checks 5.
  - **What it enables:** Jev `contradicts_reference` (agreement) on repoqa-qa too, and a reference for the labellers. There is still no completeness score for repoqa (single needle).
  - **Not for:** generating the calibration answers. Calibration always grades real agent answers from the A/A and forced-cap runs.
- **Q56.** The generator is OpenAI GPT-6 Sol: pinned id `openai/gpt-6-sol`, via OpenRouter's `:batch` variant (listed 2026-09-25 at $1/M in and $5/M out; standard is $2/$10), `high` reasoning effort, structured output. It is deliberately a different model from the Astra labeller, which avoids self-preference. The estimate is $0.7-1.7 for the 60 repoqa references.
- **Q57.** The same generator also writes reference answers for the 40 chat-slice questions (10 dev plus 30 held-out, the 10 reserved included) from their code-authored gold sites. The owner ratifies them together with the chat gold. This enables a real Jev completeness Score and a contradicts_reference check on the chat slice, in addition to the per-site Nouls. The estimate is about $0.5-1.2. The chat references are written after the held-out set is frozen and before system_v3's wording is fixed.
- The combined Q56+Q57 cost of about $1.2-3 gets its own ledger line under the $35 ceiling.

## Round 5d (2026-09-25, owner): Q56 REPLACED by Q56′
- **Q56′.** The reference-answer writer, for both the 60 repoqa-qa references and the 40 chat references, is `anthropic/claude-opus-5.5:batch`, via OpenRouter (listed 2026-09-25 at $2/M in and $10/M out on batch; standard is $4/$20), with `high` reasoning effort, structured output and a pinned model id. `anthropic/claude-sonnet-5:batch` is the fallback only if Opus batch jobs fail or time out. The estimate is $2.50-5.50 for all 100 references, with its own ledger line.
- **Why a different family.** The program keeps three families apart:
  - Qwen (qwen/qwen3.8-27b) is the agent under test.
  - Anthropic (Opus 5.5) writes the references from ground truth.
  - OpenAI is the judges: Astra reference labels, Luna in-band escalation, plus TypeSafe Jev.

  No judge grades text written by its own family.
- **Ledger.** It is now roughly $24 at the low estimate and $45 at the high one, against the $35 ceiling. The drop order plus the stop-and-ask at $25 bound actual spend, and the Astra pilot measures the largest unknown. The ceiling stays $35.

## Round 5e (2026-09-25, owner wording): judge roles, clarifying Q53
- **Luna is the Jev escalation judge in the LLM-as-judge setting.** When Jev's probability falls inside the review band, the answer is judged by `openai/gpt-6-luna`. It is used only while scoring ladder arms, and remains gated on its measured agreement with the alignment set (≥90%, otherwise "undecided"). Escalations are cached and capped at $2.
- **Astra produces the alignment.** `openai/gpt-6-astra:batch` labels the **alignment set**: the reference verdicts that Jev's thresholds and review band are fitted to, and that Luna's agreement is measured against. The owner spot-checks 10 per pass. Astra never judges ladder answers directly.
- **Config blocks are named by role**, replacing `judge.label_judge.*`:
  - `judge.alignment.{model, endpoint, api_key_env, reasoning_effort, timeout_seconds, retries, n_labels}` for Astra;
  - `judge.escalation.{model, endpoint, api_key_env, reasoning_effort, timeout_seconds, retries, agreement_floor, spend_cap_usd}` for Luna;
  - `judge.jev.{model, ...}` for the Jev pin.

  Defaults are single-sourced; `model` stays empty so the code refuses until it is set, then the owner's pinned ids go in the deployment YAML.

## Round 5f (2026-09-25, owner): Q58, dual cross-family alignment labelling. This overrides the single-labeller parts of Q53, Q51 and Q26.1
- **Q58. Selection.** Items are chosen actively from the pool of agent answers (the A/A arms, then the forced-cap arms):
  - Jev-borderline answers (probability in [0.2, 0.8] on a first pass);
  - code-vs-Jev disagreements ("needle cited" vs `needle_identified`);
  - the planted injection answers;
  - a random share, which keeps the set representative.

  The 10 reserved held-out questions are excluded.
- **Labellers.** Two independent, blind labellers from different families label every item:
  - `openai/gpt-6-astra:batch` (`high` effort);
  - `anthropic/claude-opus-5.5:batch` (`high` effort).

  Neither sees the other's label or Jev's score. Both get the ground-truth context (gold path and symbol, the function body and the reference answer) and return a structured verdict plus a one-line evidence quote.
- **Agreement and adjudication.** When the two agree, that is the alignment label. When they disagree, the item goes to the owner with both rationales. This owner adjudication REPLACES the random 10-label spot-check.
- **Ceiling.** Inter-labeller agreement (Cohen's κ, per question) is reported and is the ceiling for the Jev and Luna agreement targets. A question with low κ is ill-posed, and its criteria are rewritten before any judge is trusted on it.
- **Self-preference check.** Opus wrote the reference answers. On reference-based questions (`contradicts_reference`, `completeness`), the direction of Astra-vs-Opus disagreements is reported, and a label counts only when Astra independently agrees.
- **Sizing and cost.**
  - A 10-item pilot with both labellers (about $1-2) measures real cost and the first κ.
  - The set is then sized to fit the $15 calibration sub-budget, aiming at about 100 items for pass 1 (chat + repoqa) and 60 for pass 2 (swe-qa).
  - The estimate is $0.09-0.20 per dual-labelled item, so $15 buys about 75-165 items; the owner is told if fewer fit.
- **Roles (Round 5e) unchanged.**
  - Luna (`judge.escalation.*`) is the Jev escalation judge while scoring ladder answers. It is gated on agreement with the alignment set (≥90%, or with the per-question κ ceiling if that is lower), capped at $2 and cached.
  - Astra and Opus (`judge.alignment.*`, now a two-labeller block) produce the alignment set only.
  - Jev is pinned under `judge.jev.*`.

## Round 5g (2026-09-25, owner): human escalation of AGREED alignment labels, and the annotation tool
- **Q59.** In addition to Q58's disagreement adjudication, an alignment item where Astra and Opus AGREE still goes to the owner when any of these holds:
  1. Both labellers contradict the deterministic code check ("needle cited" vs their verdict).
  2. Either labeller reports low confidence in its structured output.
  3. The item is a planted injection that both labellers passed. That is wrong by construction, so the owner sees how it happened.
  4. It falls in a random 10% audit of agreed items, which measures the agreed-but-wrong (shared blind spot) rate. If the audit finds more than 5% wrong, the audit share grows and that question's criteria are reviewed.

  The expected owner load is about 30-55 items over about 160: roughly 15-30 disagreements and 15-25 agreement escalations. Each shows both verdicts, both evidence lines and the code check.
- **Q60.** A small Streamlit annotation page in the eval suite (benchmarks-side, never the product) reads and writes the alignment label file. For each item it shows the question, the gold, the reference answer, the agent answer, both labellers' verdicts and evidence, and the code check, and it captures the owner's decision plus a free-text note. It is one small ticket inside the judge work, blocked by the alignment-labelling code, with a FakeJudge-backed AppTest.
