# HANDOFF: embedding.query_prefix (Qwen3 query instruction)

## 2026-09-10: implementation finished; paid benchmark not run

**Worktree:** `/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad/qwen-instr`
**Branch:** `feat/embedding-query-instruction`, rebased onto `origin/main@6ca3a61`. Nothing is pushed, and there is no tag or PR.

**Venv:** `.venv` (cpython 3.11 aarch64). Besides `uv sync --frozen --all-extras` it also has the benchmarks runtime dependencies (`rapidfuzz`, `matplotlib`, `seaborn`, `unidiff`) and `gepa==0.1.4`. The benchmarks suite needs those to collect.

### Commits (oldest first)

| SHA | What |
|---|---|
| `d543a54` | Config field `embedding.query_prefix`. It has validators, is excluded from `compute_pipeline_hash`, and is folded into `compute_query_identity_hash` only when set. |
| `762f753` | `QueryPrefixEmbedder` decorator and `wrap_query_prefix` (`retrieval/query_prefix.py`). |
| `96691c7` | `build_query_embedder` (provider → prefix → cache), used at both query-side sites in `retrieval/factories.py`. The ST native path `encode_query(prompt=...)` uses the `applies_query_prefix_natively` ClassVar. Also: the ST builder forwards the prefix, and a WHY comment plus a test cover the SIMILAR linker. This was the interrupted WIP, reviewed and recommitted. |
| `4a2fd35` | The ST native path now normalizes its input (it passed raw text when the cache was off). ST 5.5.1 citations updated. Adds a contract test against the installed sentence-transformers. |
| `84fae64` | Wiring tests: cache on/off parity, identity split plus golden, both dense steps with the cache off, real openai/fastembed request input, every provider Literal, the LI hash, and the sealed serve-child env. |
| `2f744f6` | RUF012 fix in the test fake. |
| `5d653f8` | Docs: `default_config.yaml` block (with test), README, DOCUMENTATION, example README, CHANGELOG `[Unreleased]`. |
| `a480d56` | New overlays `benchmarks/configs/qwen3_4b_instruct.yaml` and `qwen3_4b_rerun.yaml`, with tests. Corrected the `qwen3_4b`/`qwen3_8b` headers and `benchmarks/README`: the 0.6B ST row **was** instructed. |

### Gates on this tree

All green:
- ruff format and ruff check;
- mypy;
- complexipy (snapshot restored);
- vulture;
- `pytest tests/` with coverage: 4313 passed, 97.42%;
- `uv lock --check`;
- `benchmarks/tests`: 2201 passed. The only failures were the 7 `gepa_harness` tests before `gepa` was installed; they pass after (30 passed).

pip-audit was not run, because this branch has no dependency changes.

### Left to do

1. **Owner decision:** the §8.0 content-keyed bench cache (`benchmarks/src/pydocs_eval/_bench_cache.make_key`: hash the corpus files instead of the `mkdtemp` path, plus tests 44–47). NOT implemented, because the design requires owner approval. Without it, every RepoQA needle re-indexes. The fallback is the design's one-off paired script.
2. **Owner go-ahead and budget (about $0.10 expected, $2 cap suggested):** the paid runs in design §8.1:
   - offline pre-flight;
   - smoke run on `repoqa_mini`;
   - `--split test` sweep over `qwen3_4b,qwen3_4b_rerun,qwen3_4b_instruct`;
   - `repoqa-structural` sweep;
   - then the §8.2 paired analysis and the §8.3 G0–G3 gates, recorded in `benchmarks/README.md`.
   `OPENROUTER_API_KEY` must be in the environment. Confirm the OpenRouter price first.
3. **Optional:** `origin/main` has moved to `5461d8e` (#242, a deps/lock change), one commit past this branch's base. Rebase and re-sync the venv before any PR.
4. **Not done:** the owner's per-task code-simplifier / clean-architecture improvement pass. Run it as a separate commit that changes no behaviour.
5. **Separate change** (CLAUDE.md defensive rule): explicit timeout and bounded retries on `AsyncOpenAI` in `extraction/strategies/embedders/openai.py`.

### Exact next step

Ask the owner:
- whether to approve the §8.0 bench-cache key change (or use the paired-script fallback);
- for the paid-run budget.

Then run design §8.1 step 0 (offline pre-flight) from the worktree root.

## 2026-09-10: correctness review (read-only) of feat/embedding-query-instruction @ a480d56

Result: no correctness findings.
- Query-only: every embed_query site (dense_fetcher, dense_scorer) takes the embedder from BuildContext, and that comes only from build_query_embedder via build_retrieval_context / build_shared_retrieval_deps (server, CLI, benchmarks pydocs.py:226). All ingestion paths (storage/factories.py:630, the benchmark pydocs/pydocs_oracle systems) use plain build_embedder. The SIMILAR linker uses embed_chunks only (tested).
- Hashes (script run): compute_pipeline_hash and compute_ingestion_pipeline_hash are the same with and without query_prefix for fastembed, openai and sentence_transformers; compute_query_identity_hash differs only when the prefix is set, and with it unset the raw input is byte-identical to origin/main. _bench_cache.make_key uses the ingestion hash, so the instruct arm reuses the qwen3_4b index.
- Validation messages include the offending value (blank, {query}, literal backslash-n, and the prompt_name mutex).
- The sentence-transformers 5.5.1 encode_query gate is confirmed: an explicit prompt= suppresses the automatic "query" prompt.
- The 11 targeted test files pass (140 passed). The new test files import the new modules, so they fail on origin/main.
Next step: unchanged (owner decision on the §8.0 bench cache and the paid-run budget).

## 2026-09-10: conventions/gates review (reviewer, read-only) of a480d56

Re-ran every CI gate on HEAD a480d56 in the qwen-instr worktree; all green:
ruff format --check (1265 files) and ruff check, including scripts/; mypy (271 files, no issues); complexipy (all within 15; snapshot restored, tree clean); vulture 80 (clean); uv lock --check; pytest tests/ with coverage: 4313 passed, 3 skipped, 1 xfailed, 97.42%, EXIT 0; the benchmarks overlay tests: 4 passed.
Conventions review found no blocking issues.
- The benchmark query side goes through build_retrieval_context, so query_prefix does reach the paid legs.
- The bench index cache is keyed on the ingestion hash, so the three arms share one index as intended.
- No new MCP param. The README jargon audit is clean.
- Patching build_embedder with monkeypatch follows existing precedent (tests/conftest.py, tests/test_query_cache_wiring.py).
The next step is unchanged: get the owner's decision on the §8.0 bench cache and a budget, then run §8.1 step 0.

## 2026-09-10: simplify + clean-architecture improvement pass (done), commit `cfde653`

Ran Skill(simplify) and Skill(python-clean-architecture:check-quality), both inline in a single pass without subagents. review-architecture and diagnose-smells were not run separately. Scope: only this branch's changed files. `cfde653` "refactor(embedding): simplify + clean-architecture pass" touches 3 files and changes no behaviour:
- `retrieval/query_prefix.py`:
  - inlined the single-use `NATIVE_QUERY_PREFIX_FLAG` constant (nothing imported it) and removed it from `__all__`;
  - `wrap_query_prefix` binds the prefix once, so `_log_query_prefix_enabled(provider, prefix, *, mode)` loses its dead `or ""`;
  - `mode` is now `Literal["native", "wrap"]`.
- `extraction/strategies/embedders/sentence_transformers.py`: the prompt_name WHY comment moved next to its code in `_query_encode_args`.
- `retrieval/config/embedder_models.py`: the query-identity hash input `raw` is renamed `identity`, matching `compute_pipeline_hash`.

Declined:
- a shared helper for the prefix digest used in both the log and the identity (different purposes);
- de-duplicating `_NativeRecordingEmbedder` across 2 test files (tests stay unchanged);
- extracting the normalize/blank-guard pattern (2 lines per embedder, each tied to a different contract);
- renaming `cfg` and `v` (repo-wide convention);
- "tell don't ask" for the native flag (pushing composition into providers would be worse).

Gates on `cfde653`, all green:
- ruff format and check on `python/ tests/ benchmarks/`;
- mypy;
- vulture 80;
- complexipy (snapshot restored);
- 248 passed in the changed-area suites (all query_prefix tests, embedders, similar_linker, serve_child_env, and the query-cache and embedding-config tests). This includes the golden query-identity hashes.

The full `pytest tests/` coverage run was not repeated. Nothing is pushed.

Left: items 1–3 and 5 of "Left to do" above. Item 4 is now done.
Exact next step: unchanged. Get the owner's decision on the §8.0 bench cache and a paid-run budget, then run §8.1 step 0 (offline pre-flight) from the worktree root. Before any PR, optionally rebase onto `origin/main` and re-run the full `pytest tests/` coverage gate.

## 2026-09-10 ~18:26Z: paid RepoQA comparison in progress (interim, benchmark subagent)

Owner approved the paid run with a $5 hard cap. OpenRouter usage before the run was $1.4781. Price is confirmed at $0.02 per 1M tokens for qwen/qwen3-embedding-4b.

Setup:
- Bench venv: `scratchpad/qwen-bench-venv`, with the worktree product and `benchmarks[retrieval]` installed editable.
- Private caches: `HOME=scratchpad/qwen-bench-cache/home` is needed because the bench cache is hardcoded to `~/.pydocs-mcp/bench`. The home dir has a symlink to the RepoQA JSON. Each arm has its own `PYDOCS_CACHE_DIR=scratchpad/qwen-bench-cache/pydocs-<arm>`.

Verified:
- All three arms have the same ingestion hash, 68dba321…
- Only `qwen3_4b_instruct` has a different query identity. `rerun` equals the baseline.
- The embedder chain for instruct is CachingEmbedder -> QueryPrefixEmbedder -> OpenAIEmbedder. Documents are sent without the prefix. Checked offline.
- Caveat: the bench-cache key hashes the mkdtemp corpus path (§8.0 was not approved). So every arm re-embeds every corpus and there is no index reuse. The hash equality holds, but it cannot save anything.

Running: 3 small_test arms in parallel (qwen3_4b, qwen3_4b_instruct, qwen3_4b_rerun).
- JSONL: `qwen-instr/benchmarks/results/jsonl/*20260910T182423Z*`
- Logs: `qwen-bench-cache/results/small_test_<arm>.log`

Paired analysis:
`python scratchpad/qwen_paired.py <jsonl_dir> 20260910T182423Z qwen3_4b qwen3_4b_instruct qwen3_4b_rerun`

Next step if interrupted: run that script. If instruct has more MRR wins than losses AND a higher recall@5 or MRR, run the full `--split test` (80 needles, about $0.40 per arm upper bound) and `--dataset repoqa-structural`. Otherwise record the small_test result only.

## 2026-09-10 ~19:00Z: paid RepoQA comparison DONE (benchmark subagent)

The small_test sweep ran 3 arms in parallel (JSONL ts 20260910T182423Z, 30/30 each, logs clean):

| arm | recall@1 | recall@5 | recall@10 | MRR | search p50 |
|---|---:|---:|---:|---:|---:|
| qwen3_4b | 0.667 | 0.833 | 0.900 | 0.740 | 1.37s |
| qwen3_4b_instruct | 0.667 | 0.833 | 0.900 | 0.751 | 1.14s |
| qwen3_4b_rerun (A/A) | 0.667 | 0.833 | 0.900 | 0.740 | 1.33s |

Paired vs baseline:
- instruct MRR: 2 wins, 2 losses, 26 ties (sign test p = 1.0). recall@5 and recall@10: all 30 needles tie.
- A/A: 0 discordant needles, so the endpoint is deterministic.
- The baseline reproduces the published row exactly.

The gate for step 2 needs wins > losses, and it got 2 = 2. So the full test split and repoqa-structural were NOT run. That was deliberate.

Verdict: do not recommend `query_prefix` for Qwen3-4B on this workload. The product default stays None.

Spend: $0.438 (OpenRouter usage went from $1.4781 to $1.9164). The cap was $5.

Recorded, in the commit "bench(embedding): RepoQA comparison — Qwen3-4B query instruction":
- `benchmarks/baselines/method_comparison.json`: new row "Dense\n(Qwen3-4B+instr, API)".
- `benchmarks/README.md`: new table row, a revised footnote, and a correction to the takeaway. The old takeaway blamed the missing instruction for recall@1; it does not.
- `benchmarks/configs/qwen3_4b_instruct.yaml`: results added to the header.
- `benchmarks/scripts/plot_method_comparison.py`: footnote and label offset.
- `benchmarks/assets/*.png`: both regenerated.

Left: nothing for this experiment. Still open and optional:
- The §8.0 content-keyed bench cache. It would make every needle from the same repo reuse one index. Today each arm re-embeds every needle's corpus.
- The AsyncOpenAI timeout/retry change.
- Rebase onto origin/main before any PR.

Nothing is pushed.

Commit: `de506f4` "bench(embedding): RepoQA comparison — Qwen3-4B query instruction" on feat/embedding-query-instruction. It touches 6 files; the tree is clean and nothing is pushed.

## 2026-09-10: final gate pass (read-only) on HEAD de506f4

Re-ran every CI gate from the qwen-instr worktree on HEAD `de506f4`. All green:
- ruff format --check (1265 files) and ruff check, on python/ tests/ benchmarks/ scripts/;
- mypy: 271 files, no issues;
- complexipy at 15: EXIT 0. Snapshot restored afterwards.
- vulture 80: clean.
- pytest tests/ with coverage: 4313 passed, 3 skipped, 1 xfailed; 97.42%; EXIT 0.
- uv lock --check: EXIT 0.
- README jargon audit: no matches.
- benchmarks/tests: 2208 passed, 1 skipped, EXIT 0. They ran with the worktree `.venv`, because `qwen-bench-venv` has no pytest installed.

Also checked:
- 10 commits, all authored by msobroza, with no trailers.
- The tree is clean.
- The branch base is 6ca3a61. origin/main is 5461d8e, which is #242 (deps/lock only). `git merge-tree` shows it merges cleanly.

Verdict: GO for a PR. The owner opens it; nothing is pushed.

Exact next step: optionally rebase onto origin/main, re-sync the venv, and re-run pytest. Then push the branch and open the PR, both on the owner's word.

Still open and optional: the §8.0 content-keyed bench cache, and the AsyncOpenAI timeout/retry change.

## 2026-09-10: review-findings fix pass (rebased branch, HEAD 0d4bfa44)

Two reviews ran on 23c68b51 after the rebase onto origin/main 5461d8e: a correctness review and a conventions/bench review. Neither found a blocker or a major. I re-checked both minor findings and both reproduced.

1. **ST citations named the wrong version.** Found by both reviews.
   - The comments in `retrieval/query_prefix.py`, `embedders/sentence_transformers.py` and `test_sentence_transformers_embedder.py` cited 5.5.1 file:line paths: `sentence_transformer/model.py:254`, `base/modules/transformer.py:969` and `base/model.py:267`. The test comment also called 5.5.1 "the uv.lock pin".
   - The locked sentence-transformers 5.3.0 has none of those files. I confirmed its gate at `SentenceTransformer.py:559`.
   - Fix: the comments now name methods and quote the gate condition instead of citing file:line. That condition is `prompt_name is None and "query" in self.prompts and prompt is None`. The comments say "verified on 5.3.0 and 5.5.1", and the lock pin is now correctly given as 5.3.0.
   - This was comment-only, so no TDD was needed. The installed-package contract test still passes.
   - Commit: `dfa4bf20` fix(embedding): address query_prefix review findings.
2. **The latency table was missing the instructed arm.** Found by the bench review.
   - The table in `benchmarks/README.md` had no row for the instructed arm, which the plot and `method_comparison.json` both carry at 1.139 s. The remote-API range still said ~1.2–5.5 s.
   - Fix: added the row `Dense (Qwen3-4B + instr, remote API) | 0.900 | 1.14s†`, with a † note. The note says the number comes from the 2026-09-10 sweep, where the plain 4B measured 1.37 s and its A/A re-run 1.33 s. I widened the range to ~1.1–5.5 s in both places and listed the instructed arm in the tier paragraph.
   - Commit: `0d4bfa44` docs(bench): list the Qwen3-4B query-instruction arm in the latency table.

Verification:
- ruff format and ruff check are clean on the touched files.
- The tests pass:
  - the ST embedder tests plus `tests/retrieval`: 721 passed;
  - the README and doc-conformance tests: 128 passed;
  - the bench tests matching readme, method_comparison, plot or qwen3: 42 passed.
- The README jargon audit found nothing.
- Both commits are authored by msobroza with no trailers, and the tree is clean.

Skipped: nothing. The bench reviewer also saw uncommitted edits appear and then disappear in the worktree. It was clean when I started and clean after both commits, so I took no action.

Nothing is pushed. The branch now has 12 commits on top of origin/main 5461d8e.

## 2026-09-10 — Full CI gate run on HEAD 0d4bfa44 (12 commits over origin/main 5461d8e)

All gates green; no fixes needed, no new commits.

- ruff format --check (python/ tests/ benchmarks/ scripts/): 1266 files already formatted
- ruff check (same paths): All checks passed!
- mypy python/pydocs_mcp: Success: no issues found in 271 source files
- complexipy --max-complexity-allowed 15: Snapshot watermark passed (complexipy-snapshot.json restored from HEAD)
- vulture --min-confidence 80: no findings (exit 0)
- pytest tests/ --ignore=tests/test_parity.py --cov: 4324 passed, 3 skipped, 1 xfailed; coverage 97.42% (>= 90)
- PYTHONPATH=benchmarks/src pytest benchmarks/tests/: 2208 passed, 1 skipped
- ~/.local/bin/uv lock --check: Resolved 214 packages (lock matches)
- README audit grep: no matches
- git status --short: clean

Verdict: GO for marking draft PR #239 ready (owner action; nothing pushed).

## 2026-09-10: pre-registered the 4th arm, a code-retrieval instruction (no run, no spend)

This step made no embedding call and looked at no new results. Spend delta: $0.00. The total is still about $0.44 of the $5 cap.

- **RepoQA form.** `datasets/repoqa.py` builds `EvalTask.query = needle["description"]`. That is a structured natural-language description of one function, written as Purpose / Input / Output / Procedure. The gold is the function body (`ast_body`) inside the indexed repo code. Example from psf/black `_merge_string_group`: "**Purpose**: To combine adjacent strings into a single string within a line of code…".
- **Source.** QwenLM/Qwen3-Embedding `evaluation/task_prompts.json` @ `490a766` (Git LFS, sha256 `c9493103…`). `run_mteb.sh` wraps each entry as `"Instruct: {}\nQuery:"`. I did not need the arXiv report.
- **Chosen entry.** `CodeSearchNetCCRetrieval`: "Given a code comment, retrieve the code snippet corresponding to that comment." Its query is a comment describing code and its target is that code, the same form as RepoQA. The rejected code entries are CosQA / StackOverflowQA / CodeFeedback* (question to code or passage), AppsRetrieval (problem statement to solution), CodeSearchNet / COIRCodeSearchNet (the reverse direction, code to comment), CodeTransOcean* / CodeEditSearch (code to code) and SyntheticText2SQL.
- **Overlay.** `benchmarks/configs/qwen3_4b_instruct_code.yaml` is `qwen3_4b.yaml` plus `query_prefix: "Instruct: Given a code comment, retrieve the code snippet corresponding to that comment.\nQuery:"`. It shares the qwen3_4b index.
- **Tests.** `test_qwen3_instruct_overlay.py` is now parametrised over both instruct arms, and a new test asserts the two arms have distinct query identities. 8 passed; ruff check and format are clean.
- **Pre-registration commit.** `61311e2` bench(embedding): pre-register a code-retrieval instruction arm for Qwen3-4B. Authored by msobroza, no trailer, not pushed.

Next step: a paired small_test sweep of qwen3_4b_instruct_code against qwen3_4b (plus the A/A arm if wanted), about $0.45, with HOME set to `scratchpad/qcode-home`. Check that `df -h` shows at least 2 GB free first. Run the full test split only if the code arm wins more needles than it loses.

## 2026-09-10: adversarial audit of the 4th-arm pre-registration (FIXED; comment-only; no run, no spend)

Verdict: **FIXED**. The prefix bytes and the arm are unchanged; only the rationale comment was wrong.

- **(a) Verbatim: PASS.** Re-fetched `task_prompts.json` from media.githubusercontent.com at both `490a766` and `main`. Both have sha256 `c9493103…` (LFS pointer oid matches) and 251 entries. `CodeSearchNetCCRetrieval` = "Given a code comment, retrieve the code snippet corresponding to that comment." `run_mteb.sh` @ `490a766` has `instruction_template: "Instruct: {}\nQuery:"`.
- **(b) Best match: PASS on wording, but the rationale was REFUTED.** The MTEB dataset behind `CodeSearchNetCCRetrieval` (CoIR-Retrieval/CodeSearchNet-ccr) is code-context retrieval: the query is a function head plus docstring, the target is the rest of the code. It is not comment-to-code, as 61311e2's header claimed. `mteb/CodeSearchNetRetrieval` is docstring→code, which is RepoQA's direction, but the authors' entry for it reads "Given a code snippet, retrieve the comment…" (reversed). No entry matches both the data and the wording. The model sees only the text, and the chosen entry is the only published wording that says description→code, so the choice stands. All code entries: AppsRetrieval, COIRCodeSearchNetRetrieval, CodeEditSearchRetrieval (truncated), CodeFeedbackMT/ST, CodeSearchNetCCRetrieval, CodeSearchNetRetrieval, CodeTransOceanContest/DL, CosQA, StackOverflowQA, SyntheticText2SQL (plus StackOverflowDupQuestions, which is not code retrieval).
- **(c) Overlay diff: PASS.** Non-comment diff vs `qwen3_4b.yaml`: only the added `query_prefix`. Vs `qwen3_4b_instruct.yaml`: only the `query_prefix` value. Parsed dicts minus the prefix are equal. The prefix loads as `b'Instruct: Given a code comment, retrieve the code snippet corresponding to that comment.\nQuery:'` (1 real newline, no backslash).
- **(d) Nothing run first: PASS.** 61311e2 contains only the overlay and the test. No file under `benchmarks/results` is newer than the commit (22:27:36 +0200). Two JSONL stamps are later than 20260910T182423Z (`baseline_…_190156Z`, `…_191753Z`), but both are pytest artifacts: config `baseline`, with cache_dir under the pytest tmp from `test_report_flag_creates_missing…`. Both predate the commit. No result mentions instruct_code. `qcode-home` does not exist yet.
- **Fix commit.** `7e129de` "bench(embedding): correct the code-instruction arm's dataset rationale" (comment-only; parsed YAML identical to 61311e2; 8 overlay tests pass). Authored by msobroza, no trailer, not pushed.
- **Spend.** OpenRouter key `data.usage` now reads **1.932302** USD. That is account-wide and higher than the "about $0.44" workstream figure, so use 1.932302 as the baseline for this workflow's $2.00 delta ceiling (abort above about 3.93). This audit spent nothing.
- **Side note.** A bench test writes `baseline_repoqa_*.jsonl` into `benchmarks/results/jsonl` (a hermeticity leak; not fixed, not in scope).

Next step (unchanged): a paired small_test sweep of qwen3_4b_instruct_code against qwen3_4b with HOME=`scratchpad/qcode-home`, after `df -h` shows at least 2 GB free (5.2 GiB now). Run the full test split only if the code arm wins more needles than it loses.
