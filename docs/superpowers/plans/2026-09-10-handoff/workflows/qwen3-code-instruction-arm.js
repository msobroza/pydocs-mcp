export const meta = {
  name: 'qwen3-code-instruction-arm',
  description: 'Pre-registered code-retrieval instruction for Qwen3-Embedding-4B as a 4th RepoQA arm (owner-approved, $2 cap): pre-register → verify → run → verify → record',
  phases: [
    { title: 'Pre-register', detail: 'pick the authors\u2019 published code-retrieval instruction, commit the overlay before any run' },
    { title: 'Audit', detail: 'adversarial check of the pre-registration' },
    { title: 'Run', detail: 'small_test (fresh baseline + new arm); full test only if paired wins > losses' },
    { title: 'Verify', detail: 're-derive numbers; adversarial statistician' },
    { title: 'Record', detail: 'README / method_comparison.json / plots / overlay header; commit' },
  ],
}
const R = '/Users/msobroza/Projects/pyctx7-mcp'
const S = '/private/tmp/claude-501/-Users-msobroza-Projects-pyctx7-mcp/0b1df857-474a-4871-b172-7d0d7c0fdfbc/scratchpad'
const WT = S + '/qwen-instr'
const HANDOFF = '/Users/msobroza/pydocs-handoffs/2026-09-10/HANDOFF-query-prefix.md'
const GROUND = `Repo pydocs-mcp. Work ONLY in worktree ${WT} (branch feat/embedding-query-instruction = PR #239, head 0d4bfa44, rebased on origin/main 5461d8e; venv ${WT}/.venv has the eval deps — run benchmark code with PYTHONPATH=benchmarks/src). Never touch the main checkout or other worktrees; never push, tag or open PRs; commit with the existing identity, NO trailers; stage explicit paths; restore complexipy-snapshot.json before staging; follow ${WT}/CLAUDE.md (no PR jargon in README files, no competitor names).
CONTEXT: embedding.query_prefix shipped on this branch. The earlier paid RepoQA small_test sweep (30 needles, run stamp 20260910T182423Z, raw JSONL in ${WT}/benchmarks/results/jsonl/*20260910T182423Z*, paired script ${S}/qwen_paired.py, commands in ${HANDOFF}) compared qwen3_4b (no instruction) vs qwen3_4b_instruct (the model card's generic web-search instruction) vs an A/A re-run: MRR 0.740 → 0.751, 2 wins/2 losses/26 ties, recall@5/@10 identical, A/A 0 discordant → verdict no measurable gain. OWNER APPROVED (2026-09-10) a 4th arm: a CODE-RETRIEVAL instruction, wording fixed BEFORE any result, run on small_test, and the full test split ONLY if it wins more needles than it loses; ~$0.45 per sweep, inside the $5 cap ($0.44 spent).
SECRETS & SPEND: the key is OPENROUTER_API_KEY in ${R}/.env — load with \`export OPENROUTER_API_KEY="$(grep -E '^OPENROUTER_API_KEY=' ${R}/.env | cut -d= -f2-)"\`; NEVER print, echo or log it or write it to any file. Read spend with GET https://openrouter.ai/api/v1/key (Authorization: Bearer) → data.usage (USD); print only the number. HARD CEILING for this whole workflow: $2.00 of usage delta; abort any run that would exceed it.
CACHES: the bench index cache is hardcoded under ~/.pydocs-mcp/bench — redirect HOME to a fresh dir under ${S}/qcode-home for every run (as the earlier runs did); delete it when the workflow's runs are done. Disk is tight (~5 GB free): check \`df -h\` before each sweep and stop if < 2 GB. Do NOT implement the content-keyed bench cache (not approved).
Append a dated entry to ${HANDOFF} before returning (what you did, SHAs, numbers, spend, next step).`

phase('Pre-register')
const prereg = await agent(`${GROUND}

PRE-REGISTER the code-retrieval instruction — you must NOT run any embedding sweep or look at any new results in this step.
1. Read how RepoQA queries and documents are built in this repo's benchmark adapter (benchmarks/src/pydocs_eval/… repoqa*.py): what the query text is (e.g. a natural-language description of a function) and what the indexed documents are (code chunks). Quote 1-2 real query examples from the dataset files if they are local.
2. Find the Qwen3-Embedding AUTHORS' published task instructions for code retrieval — try, in order: the QwenLM/Qwen3-Embedding GitHub repo (evaluation task-prompt files, e.g. task_prompts.json, via \`curl -sL https://raw.githubusercontent.com/...\` or the GitHub API), then the Qwen3 Embedding technical report (arXiv 2506.05176) instruction tables. Record the exact source URL and the verbatim instruction(s) for code-retrieval tasks (e.g. CoIR / MTEB-Code task names).
3. Choose the ONE published instruction whose query→document form best matches RepoQA's (natural-language description → code). Tie-break by the plainest general code-search wording; never edit the wording except nothing. If no authors' source can be retrieved, fall back to a single sentence derived ONLY from RepoQA's task definition (not from results) and say so explicitly.
4. Create benchmarks/configs/qwen3_4b_instruct_code.yaml as a copy of qwen3_4b_instruct.yaml whose ONLY difference is query_prefix = "Instruct: <chosen instruction>\\nQuery:" (double-quoted, real newline — same format as the model card), with a header citing the source URL + the verbatim quote + why it was chosen + "pre-registered before any run". Extend benchmarks/tests/core/test_qwen3_instruct_overlay.py (or its sibling) so the new overlay is pinned (same embedding/pipelines blocks as qwen3_4b.yaml except query_prefix; prefix loads with a real newline). Run those tests + ruff on the changed files.
5. Commit ONLY the overlay + test: "bench(embedding): pre-register a code-retrieval instruction arm for Qwen3-4B". This commit SHA is the pre-registration proof.
Return: chosen instruction (exact string), source URL, verbatim quote, the alternatives you saw, RepoQA query examples, commit SHA.`, { label: 'preregister', phase: 'Pre-register', model: 'opus', effort: 'high' })

phase('Audit')
const audit = await agent(`${GROUND}

ADVERSARIAL AUDIT of the pre-registration below. Try to REFUTE: (a) the instruction is verbatim from the cited authors' source (re-fetch it yourself); (b) it is the best task-type match for RepoQA's NL-description→code queries among the published options (list them); (c) the overlay differs from qwen3_4b_instruct.yaml / qwen3_4b.yaml ONLY in query_prefix (diff them) and the prefix parses to the intended bytes; (d) the commit exists, contains only overlay + test, and no new benchmark result files (JSONL newer than 20260910T182423Z) exist anywhere in ${WT}/benchmarks/results — i.e. nothing was run before pre-registration. If any check fails, FIX it (amend is NOT allowed — add a follow-up commit) and report; otherwise say PASS. Return PASS/FIXED with evidence.
PRE-REGISTRATION REPORT:
${prereg}`, { label: 'audit:prereg', phase: 'Audit', model: 'opus', effort: 'high' })

phase('Run')
const run = await agent(`${GROUND}

RUN the owner-approved comparison. Steps:
1. Record usage (USD) before. df -h check.
2. small_test sweep (same split, same command family as the earlier sweep in ${HANDOFF}) with TWO arms in ONE sweep: qwen3_4b (fresh baseline) and qwen3_4b_instruct_code. HOME redirected to ${S}/qcode-home. Record usage after; abort further runs if the delta already exceeds $1.00.
3. Paired analysis per needle — NOTE: /private/tmp was wiped when the session restarted, so ${S}/qwen_paired.py and the earlier git-ignored raw JSONL (run 20260910T182423Z) NO LONGER EXIST; write your own short paired script under ${S}/qcode/ and copy the new raw JSONL + that script to ${HANDOFF.replace('HANDOFF-query-prefix.md', 'qcode-evidence/')} (mkdir -p) so the evidence survives another wipe. Compute recall@1/5/10, MRR, search p50 per arm; paired MRR W/L/T + sign-test p for code-arm vs the fresh baseline; paired recall@5/@10 W/L/T. Endpoint drift check: compare the fresh baseline's aggregates with the published baseline row (recall@1 0.667, @5 0.833, @10 0.900, MRR 0.740 on these 30 needles) — per-needle drift vs the earlier run is unavailable, say so. Report the code arm vs the published generic-instruction row (MRR 0.751) for context only, not the gate. The venv was rebuilt after the wipe: if an eval dependency is missing, install it into ${WT}/.venv with \`~/.local/bin/uv pip install --python ${WT}/.venv/bin/python <pkg>\`.
4. GATE (pre-registered, apply mechanically): if code-arm MRR paired wins > losses vs the fresh baseline → run the full RepoQA test split for the same two arms (check projected spend first from the small_test delta scaled by corpus size; skip with a note if it would push the workflow total past $2.00), then the same paired analysis on test. Otherwise STOP after small_test. Do not run repoqa-structural.
5. Delete ${S}/qcode-home when done. Do NOT commit anything in this step.
Return: exact commands, run stamps + JSONL paths, all tables, drift count, gate decision, spend before/after/total, and a one-line verdict (no gain / gain on small_test only / gain confirmed on test / regression).
PRE-REGISTRATION: ${prereg}
AUDIT: ${audit}`, { label: 'run:sweeps', phase: 'Run', model: 'opus', effort: 'high' })

phase('Verify')
const [nums, stats] = await parallel([
  () => agent(`${GROUND}

INDEPENDENT RE-DERIVATION: from the raw JSONL named in the run report (do not trust its tables), recompute per arm recall@1/5/10, MRR and search p50, the paired W/L/T vs the fresh baseline, and the fresh baseline vs the published baseline row (recall@1 0.667, @5 0.833, @10 0.900, MRR 0.740). Write your own short script (do not reuse the run agent's script). Report every number that does NOT match the run report (or "all match"). No paid calls.
RUN REPORT:
${run}`, { label: 'verify:numbers', phase: 'Verify', model: 'opus', effort: 'high' }),
  () => agent(`${GROUND}

ADVERSARIAL STATISTICIAN: challenge the run's verdict. Was the gate applied exactly as pre-registered? Is the verdict wording justified given n, ties, the sign test, the minimum detectable effect at this n (the earlier design estimated only ~13-15 recall points are detectable at n≈80, less at n=30), the drift check, and multiple-comparison concerns (two instruction arms now tried)? Propose the most accurate one-paragraph verdict and the exact table rows to publish. No paid calls.
RUN REPORT:
${run}`, { label: 'verify:stats', phase: 'Verify', model: 'opus', effort: 'high' }),
])

phase('Record')
const record = await agent(`${GROUND}

RECORD the result on the branch (no paid calls). Use the verified numbers and the statistician's verdict wording (resolve any mismatch in favour of the re-derivation; if a number conflicts, recompute it). Update: benchmarks/baselines/method_comparison.json (new row for the code-instruction arm, same schema as the generic-instruction row), benchmarks/README.md (recall table row + latency row + footnote; state that this is a second, pre-registered instruction and name the source; update the takeaway accordingly; no PR/sub-PR jargon), benchmarks/configs/qwen3_4b_instruct_code.yaml header (results block like the generic overlay), regenerate the plots with benchmarks/scripts/plot_method_comparison.py (minimal label-position tweaks only if the new point collides). If benchmarks/CHANGELOG.md or root CHANGELOG [Unreleased] is required by ${WT}/CLAUDE.md for this kind of change, add the entry. Run: the benchmark tests touching configs/README/method_comparison/plot, ruff check + format --check on changed files, the README audit grep. Commit: "bench(embedding): RepoQA result for a pre-registered code-retrieval instruction (Qwen3-4B)". Return the commit SHA, the files changed, the final verdict paragraph and the published rows.
RUN REPORT:\n${run}\n\nNUMBER CHECK:\n${nums}\n\nSTATISTICIAN:\n${stats}`, { label: 'record:results', phase: 'Record', model: 'opus', effort: 'high' })

return { prereg, audit, run, nums, stats, record }
