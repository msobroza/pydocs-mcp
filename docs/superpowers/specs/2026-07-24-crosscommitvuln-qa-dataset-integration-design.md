# CrossCommitVuln-Bench → QA Dataset Integration Design

> **Redaction note.** Identifiers and gold file paths in this document are
> SYNTHETIC. Real gold (CVE ids, contributing files) lives only under the
> `crosscommitvuln` package dir, which the `_EXCLUDED_DIRS` floor makes
> un-indexable — otherwise anyone indexing this checkout could retrieve the
> answer to an eval task from the docs. See
> `tests/extraction/test_config.py::test_no_shipped_cve_id_appears_in_an_indexable_text_file`.

**Status:** Draft
**Date:** 2026-07-24
**Revision:** v3 (LLM-generated per-record queries)
**Author:** pydocs-mcp spec author
**Applies to:** `pydocs-mcp-eval` (`benchmarks/src/pydocs_eval/`) and `coding-agent-playbook` (`src/coding_agent_playbook/`)

## 0. Revision History

- **v3 (2026-07-24, LLM-generated per-record queries):** the query is no longer one fixed deterministic template shared across records — each record's query is **LLM-generated (varied, natural language)** via an injectable generator (production = the `claude` CLI), so a model cannot pattern-match the wrapper. Every candidate is gated by the same banned-token leak-check (§5.2) with **bounded regenerate-on-leak** and a **deterministic template fallback** (`build_query`) that guarantees no leaking query ever ships. The leak-check is now the PRIMARY guard over free-form LLM output (not just template-drift insurance). Build-time generation is non-reproducible, so the vendored records remain the **frozen canonical artifact**. The banned-token/needle-hiding rules, the four locked decisions, and the single-commit invariant are all unchanged.
- **v2 (2026-07-24, unbiased single-commit reframe):** all model-facing text (query template, worked-example query, playbook `prompt_args`) is scrubbed of multi-commit / "benign" / SAST-evasion framing and of all dates — the introduction window is removed from the query entirely (dates stay in metadata, analysis-only).
- New hard invariant: **one QA record ↔ exactly one repository ↔ exactly one pinned commit snapshot**, materialized history-less; the contributing commits are "merged" by pinning the assembled pre-fix snapshot, never by rewriting upstream history (§5.0), with a pinned test (§9.1).
- The date-window multi-CVE disambiguation lever is removed; replaced by a construction-time **co-resident ancestry drop** in multi-CVE repos. The vendored count becomes **≤33** (24 unambiguous + up to 9 pending the ancestry check); every "exactly 33" claim and test pin is updated to a bound.
- Motivation and summary re-scrubbed: the task is sold as single-snapshot needle-search given a vague symptom; the source dataset is cited neutrally. Grading design, leak-guard floor, `CombinedDataset`, packaging, licensing, testing, and rollout are otherwise unchanged.

## 1. Summary

This design specifies the integration of the **CrossCommitVuln-Bench** dataset — a curated corpus of real CVEs in real Python projects (paper/DOI cited in §4.2) — transformed into **question-answer (QA)** form, into the evaluation surfaces of two packages. On the pydocs-mcp side it becomes a registered `Dataset` (`crosscommitvuln`) that vends `EvalTask`s over a git-checkout haystack (one repository at one pinned pre-fix commit per record) and combines with `swe-qa-pro` via a new `CombinedDataset` so the shipped `skillopt` prompt optimizer can train on "swe-qa-pro train + crosscommitvuln train". On the coding-agent-playbook side it becomes a curated set of self-contained scenario `eval_tasks/` fixtures driven by a new needle-search prompt. The transform's defining property: **the query withholds where the vulnerability is, what it is, and how it came to exist** (it evaluates "search the needle" against a single history-less snapshot), while **the gold answer identifies the exact vulnerability** (`cve_id` + `cwe_ids` + a source→sink mechanism + the contributing files). The dataset ships self-contained (records vendored in-package, zero download); the gold answer key is protected by a stacked leak guard whose backbone is pydocs-mcp's non-shrinkable directory-exclusion floor, so the indexer structurally cannot index it.

This revision folds the grading design onto the **verified** shipped code: the `gold_substring` gate is ANY-match (`benchmarks/src/pydocs_eval/optimize/rubric/gates.py:114-132`) and the `RubricJudge` is gold-blind (`optimize/fitness/ask_rubric.py:142`); the design states those semantics honestly, adds one **additive** gate kind for deterministic exact identification, and requires per-dataset reporting so needle-search signal cannot silently regress inside a combined fitness score.

## 2. Motivation & Goals

### 2.1 Motivation

pydocs-mcp's retrieval and prompt-optimization evaluation today grounds on `swe-qa-pro` and its siblings — good general repo-QA coverage, but no *adversarial needle-search* signal. The task this dataset adds is: **given one realistic codebase snapshot and only a vague symptom ("this snapshot contains a security vulnerability"), locate the exact needle** — where untrusted input enters, what dangerous operation it reaches, and what the exploit is. Transformed into QA, it measures precisely the capability pydocs-mcp claims to serve: *given a large unfamiliar codebase and a vague symptom, can retrieval + reasoning surface the exact needle?* The source, CrossCommitVuln-Bench, is cited factually as a curated set of real CVEs in real Python projects (paper arXiv:2604.21917, DOI in §4.2); the QA task deliberately does **not** adopt the source's own detection-difficulty framing as its value proposition — how a vulnerability came to exist is source provenance, carried in metadata for offline analysis and never part of the question (§5.0, §5.2). It also gives coding-agent-playbook a battery of realistic "find the vulnerability" scenario tasks with deterministic gold identities.

### 2.2 Goals

- **G1** — A registered pydocs-mcp `Dataset` named **`crosscommitvuln`** (locked; this one identifier is simultaneously the registry name, the vendored data directory name, and the `_EXCLUDED_DIRS` floor entry — see §6.6) that vends **≤33** `EvalTask`s (24 in single-CVE repos, always retained, + up to 9 in multi-CVE repos pending the co-resident ancestry check, §5.2), each binding **exactly one repository and one pinned commit snapshot** (hard invariant, §5.0), self-contained (records vendored, corpus lazily checked out via the existing `RepoCache`).
- **G2** — A `CombinedDataset` that unions `swe-qa-pro` + `crosscommitvuln` task iterators, disjoint task_ids, satisfying the `Dataset` Protocol so the shipped `optimizer: skillopt` (`AskRubricFitness`) trains on the merged corpus with **zero changes to existing fitness/gate code**. One **additive** gate kind (`gold_substring_all`, §6.5) is registered in the rubric gate registry for exact-identification grading; the combined optimizer path itself runs on shipped gates unchanged.
- **G3** — A leak guard: the vendored gold-answer key is structurally un-indexable by the pydocs-mcp product indexer via a stacked defense whose non-shrinkable backbone is the hardcoded `_EXCLUDED_DIRS` floor (§6.6).
- **G4** — A curated subset (~8–12 CVEs) of hand-authored, self-contained coding-agent-playbook scenario fixtures + a dedicated needle-search prompt.
- **G5** — Honest, **unbiased** "search the needle": a construction-time leak-check asserts the generated query contains none of the banned tokens (cve/cwe ids, file paths, sink symbols, flaw-class words, **fix and contributing commit hashes, commit-subject-mined symbols, every commit date, and the commit-structure/detection-difficulty framing vocabulary** — §5.2), plus a mandatory manual review pass of all retained (≤33) queries.
- **G6** — CC BY 4.0 compliance: attribution NOTICE + change indication + paper/DOI citation shipped with the vendored data (wheel **and** sdist) and an `ATTRIBUTIONS.md` in the playbook fixture tree.
- **G7** — Anti-memorization grounding: the recommended grading configs include the existing `used_indexed_tools` gate (gates.py:135) and recall@k over `gold.file_set`, and per-record fix dates are carried in metadata, so an answer recalled from a model's parametric memory of public advisories cannot pass ungrounded (§5.3, §10).

### 2.3 Non-goals

- **NG1** — This is **dataset construction**, not the vulnerability-search runtime. We do not build a scanner, a taint engine, or a repo-triage agent.
- **NG2** — No **gepa plane-B bridge**. The true `gepa` optimizer consumes SWE-bench-Live *instance-id lists* (a different data plane), not QA `EvalTask`s. Combining there would require a QA→campaign bridge that does not exist and is **not built here** (see §6.4).
- **NG3** — No **third-party source bundled into any wheel**. The retrieval corpus is a git checkout (pydocs-mcp); the playbook fixtures are hand-authored paraphrases. No Weblate/PyTorch/etc. source (some GPL/AGPL) ships in-package.
- **NG4** — No **SKIP-negatives or TODO/skeleton entries** in v1. No false-positive-guard ("already safe") tasks in v1 (deferred, §7.5).
- **NG5** — No changes to the verdict composition or fitness weighting. Dataset weighting inside a combined fitness is noted as future work (it is a fitness change) and is out of v1 scope.

## 3. The Four Locked Decisions

These four forks are **decided** (user-approved recommended option); the plan step must not reopen them. Where the shipped code's verified behavior differs from the decision's original wording, the *delivery amendment* is stated here — the intent is unchanged, no fork is reopened. The v2 reframe (§0) is orthogonal to all four: it changes the **question framing** and the **record unit**, not these forks.

1. **Haystack / self-containment = "Checkout + original repros."** pydocs-mcp vendors QA records + gold metadata only; the searchable corpus is a git checkout of `repo @ pre-fix state` (the parent of `fix_commit`) via the existing `RepoCache`, identical to how `swe-qa-pro` grounds its corpus — no third-party source in the wheel. The checkout is materialized history-less (§5.0). coding-agent-playbook uses hand-authored minimal fixtures paraphrasing the source→sink pattern (not copied from the real repo; authorship rule in §7.4).
2. **Scope = FILLED-chain positives only.** Include a CVE iff `annotation.json` has `annotation_status == "complete+sast"` **AND** a non-empty `vulnerability_chain.description` that does **not** start with `"TODO"`. This first gate yields **33 candidate records** (verified: 39 filled chains − 6 `SKIP —…` documented negatives). Log the exclusion count (no-silent-caps). **v2 amendment:** a second construction gate — the co-resident ancestry drop (§5.2) — applies only to the 9 candidates in multi-CVE repos, so the vendored count is **≤33** (24 unambiguous + up to 9, drop count logged). The scope intent (filled positives only) is unchanged.
3. **Gold answer = STRUCTURED identification.** `cve_id` + `cwe_ids` + a source→sink mechanism sentence + the contributing files (union of `contributing_commits[].files_changed`). Grading intent: a deterministic all-tokens-verbatim gate plus LLM-judged mechanism articulation. **Delivery amendment (verified against code):** the shipped `gold_substring` gate is ANY-match over `gold.file_set` ∪ string values of `gold.extra`, ignores its params entirely, and passes vacuously on empty gold (gates.py:114-132); the shipped `RubricJudge` never receives the gold — it is called with only `question`, `answer`, `criteria` (ask_rubric.py:142). The decision is therefore delivered as: (i) a new **additive** registered gate kind `gold_substring_all` provides the all-tokens-verbatim exactness check (§6.5); (ii) mechanism/exploit-chain articulation is LLM-graded on the **playbook** surface, where per-task rubric `judge=` assertions encode the mechanism (§7.3); (iii) on the pydocs-mcp optimizer path v1, the generic gold-blind rubric is retained and mechanism is **not** claimed to be judge-graded — the mechanism sentence is carried gold-side (`GoldAnswer.ast_body`, read by no gate, never model-visible) as the seam for a future gold-aware judge, which would be a scoped `ask_rubric` change and is out of v1 scope.
4. **Optimizer combination = a `CombinedDataset`.** A new concrete `Dataset` unioning `.tasks()` of `swe-qa-pro` + `crosscommitvuln`, prefixing task_ids to keep them disjoint. It plugs into `ask_rubric` fitness unchanged — the zero-change claim applies to **dataset plumbing and the existing fitness/gate code**, which are untouched. "swe-qa-pro train + crosscommitvuln train" is the combined dataset's hash-derived train partition (`optimize/_split.py`). Out of scope: the gepa instance-id plane (NG2).

## 4. Source Dataset Overview, License, Inclusion Filter

### 4.1 Source shape

The source is a clone of `dataset/<CVE-ID>/{annotation.json, reproduction.md}`, 71 CVE directories. Each `annotation.json` carries (fields used by the transform in **bold**):

- **`cve_id`**, `ghsa_id`, **`repo`** (GitHub URL), **`ecosystem`**, **`cwe_ids`** (list), **`severity_combined`** (`critical`/`high`), **`summary`** (one-line vuln description), **`fix_commit`** (+ `fix_commit_url`).
- **`contributing_commits`** — list of `{hash, short_hash, date, author, subject, role, description, files_changed, isolated_severity, …}`. `role` reads like `"SINK — …"` / `"SOURCE EXPANSION — …"`.
- **`vulnerability_chain`** — `{description, attack_vector, exploitability, why_sast_misses_per_commit}`.
- `commit_span_days`, **`annotation_status`** (`complete+sast` | `skeleton — …` | `SKIP —…`), `cumulative_scan`, etc.

(These are descriptions of the *source's* annotation fields. Which of them may surface in the model-facing question is governed exclusively by §5.2 — in v2, none of the temporal or commit-structure fields ever do.)

### 4.2 License & attribution obligations (hard compliance item)

The annotations are **CC BY 4.0** (Copyright 2026 Arunabh Majumdar) — free to adapt + redistribute commercially with attribution and indication of changes. Compliance obligations, both repos:

**pydocs-mcp-eval** — the vendored data directory ships a **`NOTICE`** file (name locked) that:

- Credits **Arunabh Majumdar**, links the **CC BY 4.0** license URL, and states the data was **"transformed into QA form"** (the required change indication).
- Cites the paper **arXiv:2604.21917** and the Zenodo DOI **10.5281/zenodo.19338596**.

The NOTICE is part of the packaged `package-data` (§6.2) and MUST reach **both the wheel and the sdist** — the packaging piece verifies sdist inclusion (setuptools includes package-data in sdists only when the build config says so; add `include-package-data`/MANIFEST coverage if the current config does not) and this is a release gate.

**coding-agent-playbook** — ten scattered `description` strings are a weak vehicle for CC BY §3(a), so the fixture tree ships one attribution file: **`resources/eval_tasks/ATTRIBUTIONS.md`** carrying the author credit, the CC BY 4.0 URL, the change statement ("source→sink patterns paraphrased into original fixture code; annotations transformed into QA scenarios"), and the paper/DOI. Each derived fixture's `task.toml` `description` still cites its CVE + the CC BY 4.0 source as the per-task pointer.

### 4.3 The exact inclusion filter (first gate — 33 candidate records)

```python
def _is_included(a: dict) -> bool:
    desc = (a.get("vulnerability_chain") or {}).get("description", "") or ""
    return (
        a.get("annotation_status") == "complete+sast"
        and bool(desc)
        and not desc.startswith("TODO")
    )
```

Verified counts against the clone: **33 included candidates**, **6 `SKIP —…` excluded** (`CVE-2025-43859`, `CVE-2026-1669`, `CVE-2026-27953`, `CVE-2026-28416`, `CVE-2026-32274`, `CVE-2026-33057`), **32 TODO/empty-chain skeletons excluded**. The loader logs the dropped counts by reason (mirrors `swe_qa_pro.py`'s excluded-row COUNT log). This filter is the **first** construction gate; the **second** gate is the co-resident ancestry drop applied only to the 9 candidates in multi-CVE repos (§5.2), which makes the final vendored count **≤33**. The construction tool that generates the vendored JSONL applies both gates; the runtime loader simply reads the vendored records (already filtered), but still logs the vendored count for observability.

The candidates span a diverse CWE surface: command injection (CWE-78), code injection (CWE-94), deserialization (CWE-502), path traversal (CWE-22/73), SSRF (CWE-918), SQLi (CWE-89), NoSQL/query injection (CWE-943), XSS (CWE-79), signature bypass (CWE-347), mass assignment (CWE-915), missing auth (CWE-306/321), resource exhaustion (CWE-770), and more — good spread for both the optimizer's train signal and the playbook subset (§7.2).

## 5. The QA Transform

### 5.0 Single-snapshot record unit & "commit-merge" semantics (v2)

**Hard invariant: one QA record ↔ exactly one repository ↔ exactly one pinned commit snapshot.** The snapshot is the **parent of `fix_commit`** — the pre-fix state where the full vulnerability is already assembled. The vendored record shape enforces this by construction (each record carries exactly one `repo_url` and exactly one 40-hex `prefix_sha`), and §9.1 pins it as an explicit test: every record has one `repo_url` and one 40-hex `prefix_sha`, and the loader never emits a task spanning repositories or commits.

The corpus for a record is the repository checked out at that single pinned SHA and materialized **history-less**: `read_checkout_files → materialize_corpus` produces a plain file tree with no `.git`, so **no commit-history, commit-count, commit-date, or multi-commit signal is observable by the model**. This is how the source's contributing commits are "merged" into one unit: the model sees one assembled snapshot, never a history. We do **not** rewrite git history or squash commits upstream — the "merge" is realized entirely by pinning the single assembled pre-fix snapshot and stripping history at materialization. For coding-agent-playbook, each fixture's `setup/` tree is likewise a single self-contained snapshot (one merged state, no history).

**Naming note:** the internal identifier `crosscommitvuln` (registry name, vendored dir-component, floor entry) is **never shown to the model** — it appears only in code, config, and filesystem paths, all outside the materialized corpus and the query — so the dataset's own name cannot leak the source's multi-commit provenance into the question. The name is kept (no rename).

### 5.1 Vendored record schema

The construction step emits **`records.jsonl`** (name locked) of vendored QA records, plus a sibling **`banned_tokens.jsonl`** (one line per record: the mined banned-token list, so the leak-check is reproducible in tests — §5.2, §9.1). Both live in the floor-protected vendored directory (§6.2, §6.6). Each record line is self-describing and network-independent at read time (the pre-fix parent SHA is resolved and pinned **at construction time**, §6.3):

```jsonc
{
  "task_id": "cve-2026-27602",              // stable, lowercase, prefix-free at this layer
  "repo_url": "https://github.com/modoboa/modoboa",   // exactly ONE repo per record (§5.0)
  "prefix_sha": "82d64bb9...",              // exactly ONE pinned parent-of-fix_commit snapshot (§5.0)
  "fix_commit": "27a7aa133d3608fe8c25ae39125d1012c333cbfa",
  // query: unbiased single-snapshot question, §5.2 leak-checked — no temporal or provenance signal
  "query": "…",
  "gold": {
    "cve_id": "CVE-2099-00018",
    "cwe_ids": ["CWE-78"],
    "mechanism": "…source→sink sentence…",
    "files": ["examplepkg/admin/jobs.py", "examplepkg/admin/models/inbox.py",
              "examplepkg/lib/sysutils.py", "examplepkg/webmail/models.py"]
  },
  "metadata": {
    // Temporal/provenance fields below are METADATA ONLY — never interpolated into the query.
    // They exist for offline analyses (pre/post-cutoff splits, span statistics).
    "ecosystem": "PyPI", "severity": "high", "commit_span_days": "313",
    "intro_window": "2024-01-15..2024-11-23",   // from contributing_commits[].date (illustrative; analysis-only)
    "fix_commit_date": "2025-01-08",            // resolved at construction; pre/post-cutoff analyses
    "co_resident_cves": "",                     // other included CVEs fully assembled at prefix_sha (§5.2)
    "source": "CrossCommitVuln-Bench (CC BY 4.0, Arunabh Majumdar); transformed to QA"
  }
}
```

`prefix_sha` is the **parent of `fix_commit`** — the vulnerable "pre-fix state" that contains the needle. Where a chain lists multiple contributing commits, the corpus is checked out at the pre-fix parent (the state that contains the *fully assembled* chain), matching `cumulative_scan.label == "cumulative (pre-fix state)"`. The `cumulative_scan.sha` in the annotation is an abbreviated hint; construction resolves the full 40-hex parent SHA via `RepoCache` at build time and pins it.

### 5.2 Query construction (the unbiased needle-hiding rule)

The query is **LLM-generated per record** (varied, natural language) from an injectable generator (production = the `claude` CLI; tests inject a fake, so the real CLI is never called in a test) so each record carries a *distinct* security-audit request rather than one pattern-matchable wrapper. Every candidate is **sanitized by the construction-time banned-token leak-check** (below) so it reveals neither the location, the nature, nor the provenance/history of the flaw: an empty or leaking candidate is rejected and regenerated with **bounded regenerate-on-leak** (up to `_GEN_ATTEMPTS = 3`); if every attempt is empty or leaks, the build falls back to the **deterministic template** (`build_query` / `_QUERY_TEMPLATE`) which is guaranteed clean — so a leaking query can **never** ship. The generator is *asked* to hide the needle (the prompt hard-forbids naming any CWE/CVE/file/sink/flaw-class/commit/date); the leak-check is what **enforces** it. Fallback / canonical template (plan may refine wording; the constraints below are binding for both generated queries and the fallback):

> "This snapshot of the {ecosystem} project **{repo_slug}** contains a {severity}-severity security vulnerability. Locate the exploitable condition: identify where **untrusted input enters**, the **dangerous operation it reaches**, and classify the vulnerability by its exploit."

**MUST NOT contain** (banned tokens and framings — a construction-time assertion fails the build if any appears in the generated query, case-insensitive, word-boundary aware):

- the `cve_id` and each `ghsa_id`;
- each `cwe_ids` entry (both `CWE-78` and bare `78` forms);
- **`fix_commit`** (full 40-hex and short forms) and every **`contributing_commits[].hash` / `short_hash`**;
- any **symbol/sink token mined from `contributing_commits[].subject`** (upstream commit subjects can name the sink, e.g. a subject mentioning `exec_cmd`);
- any file path or basename from `contributing_commits[].files_changed` (e.g. `jobs.py`, `sysutils.py`);
- any **sink/API symbol** mined from `role` and `vulnerability_chain.description` (e.g. `exec_cmd`, `doveadm_cmd`, `subprocess`, `shell=True`, `pickle.loads`, `eval`, `os.system`);
- any **flaw-class keyword** from `summary` and the CWE names (e.g. `command injection`, `path traversal`, `SQL injection`, `deserialization`, `SSRF`, `XSS`);
- **framing bans (new in v2)** — the words/phrases `commit`, `commits`, `multiple`, `multi-commit`, `gradually`, `over time`, `across`, `benign`, `static analysis`, `SAST`, `per-commit`, `scanner`, `individually`. WHY: any of these hints at the vulnerability's provenance and detection difficulty and biases the needle-search measurement;
- **every commit DATE** (fix and contributing, any format). The introduction window is **removed from the query entirely** — no temporal signal of any kind reaches the model. Dates and spans remain in **metadata only**, for offline analysis (§5.1).

**MAY contain:** the repository identity/slug and ecosystem (a real reviewer knows which repo they audit) and a generic severity hint. Nothing temporal, nothing about commit structure, nothing about how the flaw was introduced or why it is hard to detect.

**Precedence rule (slug collisions):** if a repo slug itself contains a mined banned token (identity vs. leak conflict), **repo identity wins** — the slug stays, the overlap is logged by the construction tool, and the manual review pass (below) inspects that record explicitly.

**What the leak-check actually protects (honesty note, v3):** now that each query is **free-form LLM output**, the banned-token assertion is the **PRIMARY guard** over every generated query — it is what makes free-form generation safe, no longer just insurance against template drift. The deterministic template (`build_query` / `_QUERY_TEMPLATE`) is retained as the **guaranteed-clean fallback** for any record whose generations all leak or come back empty (and still guards template evolution: any drift that names a sink, a flaw class, a date, or the commit-structure framing fails the build). Because automated token mining has residual false negatives (synonyms), a **manual review pass of all retained (≤33) generated queries is a hard v1 construction step**, not optional. **Non-reproducibility (v3):** build-time generation calls a live model, so — exactly like `swe-qa-pro`'s pinned revision — the vendored `records.jsonl` is the **frozen canonical artifact**: re-running the build re-generates the queries, and the *committed* records are what ship and what the §9.1 pins (including the query-distinctness pin) enforce.

**Multi-CVE-per-repo reconciliation (v2, verified numbers):** of the 33 candidate records there are **28 distinct repos**: **24 records live in single-CVE repos (unambiguous)**, and **9 records live in 4 repos that contribute multiple included CVEs** — picklescan (CVE-2025-10155/10156/10157), changedetection.io (CVE-2026-27696/29065), mcp-atlassian (CVE-2099-00020/27826), authlib (CVE-2099-00022/28490). Each CVE has a **distinct** `fix_commit` → **distinct** `prefix_sha` snapshot, so they are distinct questions; but at one CVE's pre-fix snapshot another included CVE of the same repo may also be fully present (co-resident), and with the date window removed from the query there is no longer any lever to tell the model which is "the" target. **Design decision (recommended default; plan confirms):** construction runs a git-ancestry check per multi-CVE repo and **DROPS any record whose pinned `prefix_sha` co-resides another included CVE's fully assembled chain** — clean single-answer gold — logging the dropped `cve_id`s and count. Consequence: the vendored record count is **≤33** (24 always-clean + up to 9 pending the ancestry check), not a hard 33. `metadata["co_resident_cves"]` is still recorded for any retained record (empty by construction after the drop; kept for observability and future revisions). coding-agent-playbook fixtures are per-pattern isolated hand-authored single snapshots, so co-residence does **not** arise there.

The banned-token lists are stored per-record in the vendored `banned_tokens.jsonl` (floor-protected alongside the gold, §6.6) so the leak-check is re-runnable in tests. This on-the-query leak-check is **distinct** from the index leak-guard (§6.6): one keeps the *question* honest, the other keeps the *answer* un-indexable.

Rationale (WHY): the query models the Visa vulnerability-agentic-harness framing — *trace untrusted input → dangerous sink, gate a finding on reachable + unmitigated + concrete-exploit + cited, classify by exploit not bug-class* — without naming the specific file/CWE/flaw. If the query named the sink, the benchmark would measure reading comprehension, not needle-search. Equally, if the query hinted that the flaw was assembled gradually or that it evades certain scanners, it would leak the flaw's nature and detection difficulty; the model must get nothing beyond "there is a needle in this snapshot".

### 5.3 Gold schema and field→scoring mapping

The gold answer is the thing the query withholds. It maps onto `GoldAnswer` (`datasets/base_dataset.py`) as:

| Gold content | `GoldAnswer` field | Read by |
|---|---|---|
| Contributing files (union of `files_changed`) | `file_set: tuple[str, ...]` | `gold_substring` (ANY) and `gold_substring_all` candidates; retrieval `recall@k` (`metrics/recall_at_k.py`) |
| `cve_id` (string) | `extra["cve_id"]` | `gold_substring` (ANY) and `gold_substring_all` candidates |
| each `cwe_id` (strings) | `extra["cwe_id_0"], extra["cwe_id_1"], …` | `gold_substring` (ANY) and `gold_substring_all` candidates |
| source→sink `mechanism` sentence | `ast_body` | **no gate, no shipped judge** — gold-side carrier (never model-visible) reserved for a future gold-aware judge; also drives playbook rubric authoring (§7.3) |

**Actual shipped gate semantics (verified, gates.py:114-132):** `gold_substring` is an **existential** (ANY) match — it passes iff *any one* candidate from `gold.file_set` ∪ the string values of `gold.extra` appears as a substring of the answer; it **ignores its params** (`_ = params`), so no whitelist/exclusion configuration exists; an **empty candidate set passes vacuously** (gates.py:130-131). Consequence stated plainly: under this gate, an answer citing a single contributing file passes with no CVE id and no CWE. It is a **weak groundedness screen**, not an exactness check.

**Design responses:**

- **Mechanism is kept OUT of `gold.extra`.** Since both the ANY gate and the new ALL gate tokenize every string value of `extra`, prose must not live there. The mechanism sentence rides in `GoldAnswer.ast_body` — `str | None`, unused by QA datasets, read by **no** gate, gold-side (never surfaced to the model), available to a future gold-aware judge without a record-format change. No gate whitelist machinery is needed or specified — none exists in the code. The mechanism sentence is written as a **plain source→sink description** (see §5.4) — it does not narrate the flaw's commit provenance, so a future gold-aware judge cannot be biased toward a provenance story either.
- **Exact identification = a new additive gate kind, `gold_substring_all`** (§6.5): passes iff **every** candidate (all of `file_set` + all string values of `extra`) appears verbatim in the answer; optional param `keys: list[str]` restricts which parts are tokenized (default: all); empty candidates pass vacuously, mirroring its sibling — irrelevant for crosscommitvuln, whose gold is **always non-empty** (≥1 file, `cve_id`, ≥1 CWE), so neither gate ever passes vacuously on this dataset.
- **The judge is gold-blind (verified, ask_rubric.py:142):** `RubricJudge` receives only `question`, `answer`, and the config's generic criteria (correctness/grounding/completeness/conciseness). v1 does **not** claim mechanism grading on the optimizer path; a gold-aware judge (passing reference text into the judge call) is named future work and would be a scoped `ask_rubric` change.
- **Anti-memorization grounding (G7):** all included CVEs are public and may be recallable from the repo slug alone by a model's parametric memory. The recommended grading configs therefore include the existing `used_indexed_tools` gate (gates.py:135) — a zero-tool-call answer fails — and report `recall@k` over `gold.file_set` (retrieval evidence memorization cannot fake). `metadata["fix_commit_date"]` enables pre/post-cutoff splits in future analyses. Residual risk is stated in §10.

Final verdict (composition unchanged, `optimize/rubric/model.py`, default weights 0.3/0.7): `verdict = gate_weight * gate_pass_fraction + rubric_weight * rubric_score`.

### 5.4 Worked example — CVE-2099-00018 (Modoboa OS command injection)

Source annotation (verbatim highlights): `repo = modoboa/modoboa`, `cwe_ids = [CWE-78]`, `severity = high`, `summary = "Modoboa OS Command Injection via exec_cmd() with user-controlled input"`, two contributing commits — `43ace1de` (role `SINK — exec_cmd(f-string) with user-controlled mailbox path`, files `examplepkg/admin/jobs.py`, `examplepkg/admin/models/inbox.py`) and `a81ba437` (role `SOURCE EXPANSION — wires additional user-controlled data into exec_cmd paths`, files `examplepkg/lib/sysutils.py`, `examplepkg/webmail/models.py`), `commit_span_days = 313`.

**Generated query** (needle-hidden and framing-free — no `exec_cmd`, no `CWE-78`, no `command injection`, no file names, no commit hashes or dates, no temporal or commit-structure language):

> "This snapshot of the PyPI project **modoboa/modoboa** contains a high-severity security vulnerability. Locate the exploitable condition: identify where untrusted input enters, the dangerous operation it reaches, and classify the vulnerability by its exploit."

**Gold answer** (what the model must reconstruct):

- `cve_id`: `CVE-2099-00018`
- `cwe_ids`: `CWE-78`
- `mechanism` (plain source→sink description; gold-side, never model-visible): "A user-controlled mailbox path / email address (`operation.argument`, `full_address`) flows into a custom shell wrapper that executes string arguments via subprocess with `shell=True`; additional user-controlled inputs are wired into that same wrapper, widening the tainted surface. The exploitable condition is the reachable tainted-source → shell-executing-sink chain present in this snapshot."

**`GoldAnswer` mapping:**

```python
GoldAnswer(
    ast_body="…the source→sink mechanism sentence above…",   # no gate reads this; gold-side only
    file_set=(
        "examplepkg/admin/jobs.py",
        "examplepkg/admin/models/inbox.py",
        "examplepkg/lib/sysutils.py",
        "examplepkg/webmail/models.py",
    ),
    extra={
        "cve_id": "CVE-2099-00018",
        "cwe_id_0": "CWE-78",
        # NOTE: no prose keys — every string value here is gate-tokenized (ANY and ALL gates)
    },
)
```

**Metadata** (all temporal/provenance fields analysis-only — never interpolated into the query): `{"ecosystem": "PyPI", "severity": "high", "commit_span_days": "313", "intro_window": "…", "fix_commit_date": "…", "co_resident_cves": "", "source": "CrossCommitVuln-Bench (CC BY 4.0, Arunabh Majumdar); transformed to QA"}`.

**Corpus:** `corpus_source` lazily checks out `modoboa/modoboa` at the pinned parent of `27a7aa13…` — the **single pinned pre-fix snapshot** in which the vulnerability is fully assembled — reads the working tree via `read_checkout_files`, and materializes a **history-less** temp corpus (no `.git`; §5.0) — the exact `swe_qa_pro.py` pattern.

## 6. pydocs-mcp Integration

### 6.1 The `crosscommitvuln` loader (mirror `swe_qa_pro.py`)

A new `datasets/crosscommitvuln.py`, registered `@dataset_registry.register("crosscommitvuln")` (name locked; verified unique against existing `swe-qa-pro`, `swe-qa`, `repoqa`, `ds1000`, `repoqa-structural`). **Registration seam:** `registries.py::_populate_datasets()` only imports `pydocs_eval.datasets`, so the decorator fires only if **`datasets/__init__.py` imports the new module** — one import line each for `crosscommitvuln.py` and `combined.py` is a required part of this piece; without it `dataset_registry.build("crosscommitvuln")` raises `KeyError`. Shape mirrors `datasets/swe_qa_pro.py` but reads **vendored** records instead of downloading:

```python
@dataset_registry.register("crosscommitvuln")
@dataclass
class CrossCommitVulnDataset:
    name: str = "crosscommitvuln"
    revision: str = "1.0"
    fixture_path: Path | None = None                    # test override (local JSONL)
    repo_cache: RepoCacheLike = field(default_factory=RepoCache)
    cache_dir: Path = field(default_factory=lambda: Path("~/.cache/pydocs-eval").expanduser())

    async def tasks(self) -> AsyncIterator[EvalTask]:   # matches swe_qa_pro's async-def shape
        records = self._read_records()                  # vendored via importlib.resources, or fixture_path
        vendored, dropped = 0, 0
        for rec in records:
            ...  # build EvalTask; count malformed/dead rows into `dropped`
            yield EvalTask(
                task_id=rec["task_id"],
                query=rec["query"],
                gold=_gold_from_record(rec),
                corpus_source=(lambda u=rec["repo_url"], c=rec["prefix_sha"]:
                    materialize_corpus(read_checkout_files(self.repo_cache.checkout(u, c)))),
                metadata=rec["metadata"],
            )
        _log_counts(vendored=vendored, dropped=dropped)  # no-silent-caps
```

**Key differences from `swe_qa_pro.py`:** (a) records are **vendored** and read via `importlib.resources`, not `urllib` + on-disk cache; (b) the checkout SHA is the **pinned pre-fix parent** stored in the vendored record (analogous to swe-qa-pro's pinned `commit_id`) — exactly one `repo_url` + one `prefix_sha` per record (the §5.0 invariant, enforced by shape and pinned in tests, §9.1). The sketch matches swe_qa_pro's shapes deliberately — `async def tasks(...)` directly (the Protocol accepts both `async def` and a plain `def` returning an async generator) and a `Path` `cache_dir` with an expanduser default_factory. Everything else — `corpus_source` lambda closing over `(url, sha)`, `RepoCache.checkout → read_checkout_files → materialize_corpus`, laziness (a task never scored costs no clone), the excluded-count log — is copied verbatim.

### 6.2 Vendored-data packaging (self-containment)

The records live under a new **importable package dir** with an `__init__.py`:

```
benchmarks/src/pydocs_eval/datasets/data/crosscommitvuln/
├── __init__.py            # makes it importlib.resources-addressable
├── records.jsonl          # the vendored QA records (≤33; gold answers — §5.2 second gate sets the count)
├── banned_tokens.jsonl    # per-record mined banned-token lists (leak-check reproducibility)
└── NOTICE                 # CC BY 4.0 attribution + change indication + paper/DOI (§4.2)
```

`benchmarks/pyproject.toml` adds `[tool.setuptools.package-data]` globs for `pydocs_eval/datasets/data/crosscommitvuln/*.jsonl` (and the `NOTICE`). Today `package-data` ships only `optimize/artifacts/*.md`, `optimize/configs/*.yaml`, `trajectory/configs/*.yaml`, `campaign/overlays/*.yaml` — no dataset data ships yet, so this is a purely additive entry. **Sdist coverage is a release gate:** confirm the vendored dir ships in the sdist as well as the wheel (add `include-package-data`/MANIFEST coverage if the backend does not include package-data in sdists by default — §4.2). The loader reads via:

```python
import importlib.resources as ir
data = ir.files("pydocs_eval.datasets.data.crosscommitvuln").joinpath("records.jsonl")
```

WHY `importlib.resources` and not a repo-relative path: it resolves in a **built/installed** wheel, not just a source checkout. `swe_qa_pro.py`'s urllib+cache approach is explicitly **not** the template for packaged resources. PyPI name/version target: **`pydocs-mcp-eval` 0.2.0**.

### 6.3 Pre-fix parent-SHA resolution & pinning

Resolved **at construction time**, not at runtime. The construction tool, for each included CVE: (1) `RepoCache.checkout(repo_url, fix_commit)`; (2) `git rev-parse fix_commit^` (the parent = pre-fix vulnerable state); (3) verify the parent contains all `contributing_commits` hashes (else log + drop with count); (4) for multi-CVE repos, run the co-resident ancestry check (§5.2): if another included CVE's fully assembled chain is present at the pinned `prefix_sha`, **DROP the record** (log the dropped `cve_id`s + count); retained records carry `metadata["co_resident_cves"]` (empty by construction after the drop, kept for observability); (5) write the full 40-hex `prefix_sha` into the vendored record. Runtime therefore never resolves refs — it checks out a pinned immutable SHA. If a repo/commit later disappears upstream, only the affected task fails its clone (logged), the rest are unaffected (laziness isolates failures). This resolution is idempotent and re-runnable when refreshing the dataset revision.

### 6.4 `CombinedDataset` (Protocol conformance, prefixing, threading, config)

A new concrete `datasets/combined.py`, registered under its own name (recommended `swe-qa-pro+crosscommitvuln`; plan finalizes only whether the `+` is YAML-safe or the fallback `swe-qa-pro-plus-crosscommitvuln` is used). It is imported from `datasets/__init__.py` (registration seam, §6.1). It satisfies the `Dataset` Protocol by attribute (`name`, `revision`) and method (`tasks()`), and unions the member iterators with **task_id prefixing** to keep them disjoint:

```python
@dataset_registry.register("swe-qa-pro+crosscommitvuln")
@dataclass
class CombinedDataset:
    name: str = "swe-qa-pro+crosscommitvuln"
    revision: str = "1.0"
    fixture_path: Path | None = None            # accepted for registry-build signature parity only
    members: tuple[tuple[str, Dataset], ...] = ()  # (prefix, dataset); injected in tests

    def tasks(self) -> AsyncIterator[EvalTask]:  # Protocol method — REQUIRED
        return self._iter_tasks()

    async def _iter_tasks(self) -> AsyncIterator[EvalTask]:
        for prefix, ds in self._members():
            async for task in ds.tasks():
                yield replace(task, task_id=f"{prefix}/{task.task_id}")

    def _members(self) -> tuple[tuple[str, Dataset], ...]:
        if self.members:                        # test injection of fakes
            return self.members
        if self.fixture_path is not None:       # combined fixture dry-run is unsupported by design
            raise ValueError(
                "CombinedDataset does not support a top-level fixture_path "
                f"(got {self.fixture_path!r}): its members read incompatible JSONL shapes. "
                "Inject fake members= for tests, or fixture-test each member loader separately."
            )
        return (                                # production: each member resolves its own data
            ("sweqapro", dataset_registry.build("swe-qa-pro")),
            ("ccv",      dataset_registry.build("crosscommitvuln")),
        )
```

**`fixture_path` contract (decided, not deferred).** The optimizer builds datasets as `dataset_registry.build(cfg.dataset.name, fixture_path=cfg.dataset.fixture_path)` (`optimize/__main__.py:187`), so `CombinedDataset.__init__` **must accept** `fixture_path` for signature parity. But the two members read **incompatible JSONL shapes**, so a single top-level fixture cannot feed both — and `optimize/__main__.py:185-188` builds the dataset for the dry-run split probe **only when `cfg.dataset.fixture_path` is not None**, which is exactly the mis-threading trigger. Therefore the combined dataset **raises loudly on a non-None `fixture_path`** (message names the offending value and points at `members=`), and **never forwards `fixture_path` to its members**. Testing seams: inject fake member datasets via `members=` and unit-test only the *chaining/prefixing* logic; test the `crosscommitvuln` loader separately against its vendored/fixture JSONL. Per-member fixtures are a possible future extension (an optional fixtures mapping) but are **not** in v1.

**task_id disjointness → non-empty split.** `optimize/_split.py::task_split(task_id)` assigns `train` iff `int(sha256(task_id),16) % 2 == 0`. Prefixing guarantees disjoint ids; 24–33 crosscommitvuln + ~260 swe-qa-pro ids is ample for both `partition_task_ids` sides to be non-empty (it raises loudly if either is empty). "swe-qa-pro train + crosscommitvuln train" is therefore just the combined dataset's hash-derived train partition — **no on-disk split file needed**.

**Per-dataset reporting (required, small-N safety).** Because prefixes tag every task_id (`sweqapro/…`, `ccv/…`), fitness and holdout metrics MUST be reported **broken down per prefix** so a crosscommitvuln (ccv) regression is visible even when the combined score improves — at ≤33 ccv vs ~260 swe-qa-pro, ccv is roughly 8–11% of the blended score and can silently regress otherwise. The construction/run step also logs the realized ccv train/holdout counts and per-side CWE-class distribution. Because ccv holdout is roughly 12–16 tasks (depending on the post-ancestry count), **ccv-slice holdout deltas are directional only, not accept/reject evidence** — stated in §10. Dataset weighting is future work (a fitness change), out of v1 (NG5).

**Config selection.** An `optimize_*.yaml` selects it via `dataset: { name: swe-qa-pro+crosscommitvuln }`; `DatasetSettings` (`optimize/run_config.py:211`) already has `name: str` + `fixture_path: Path | None`. The shipped `optimizer: skillopt` (`AskRubricFitness`, `optimize/__main__.py:311`) consumes it unchanged. Recommended new config file: `optimize/configs/optimize_ask_prompt_combined.yaml` (plan finalizes filename), whose gate list adds `gold_substring_all` and `used_indexed_tools` (§5.3, §6.5).

### 6.5 Gold → scoring mapping (concrete)

- `file_set` = union of `contributing_commits[].files_changed` (fallback: SOURCE/SINK-role files if `files_changed` empty). Feeds the `gold_substring`/`gold_substring_all` candidate set **and** `recall@k` retrieval scoring — so crosscommitvuln is **also usable on the free retrieval rung** (needle = "did retrieval surface the vulnerable file(s)?").
- `extra["cve_id"]`, `extra["cwe_id_N"]` = gate candidate strings. **`extra` carries no prose** — every string value there is tokenized by both the ANY and ALL gates.
- `ast_body` = the mechanism sentence — **no gate reads it, no shipped judge reads it**; it is a gold-side carrier for future gold-aware judging and the source text for authoring the playbook rubric assertions (§7.3).
- **New additive gate kind `gold_substring_all`** (registered in `optimize/rubric/gates.py`'s gate registry, selected via YAML): all-candidates-must-appear-verbatim, with an optional `keys: list[str]` param to restrict tokenization (e.g. `["file_set", "cve_id"]`). This is the **only** code addition on the grading side; the existing `gold_substring` (ANY) and the verdict composition are untouched (G2). The shipped `gold_substring` remains available as the weak groundedness screen.
- Gold is **always non-empty** (≥1 file, `cve_id`, ≥1 CWE), so neither gate ever passes vacuously on this dataset.
- **Recommended crosscommitvuln gate stack (YAML):** `gold_substring_all` (exact identification: file_set + cve_id + each cwe_id must all appear) + `used_indexed_tools` (anti-memorization grounding) + `recall@k` reporting on `gold.file_set`.

### 6.6 Leak-guard floor edit

The vendored gold answer key (`records.jsonl` + `banned_tokens.jsonl`) must be structurally un-indexable by the pydocs-mcp product indexer. The protection is a **stack**, stated honestly:

1. **Extension ceiling (already true today):** `.jsonl` is **not** in `ALLOWED_EXTENSIONS` (`python/pydocs_mcp/extraction/config.py:34-40`; the text/config set is `.toml .yaml .yml .cfg .ini .rst .txt .json`). So `records.jsonl`/`banned_tokens.jsonl` are already structurally un-indexable by extension **as the code stands today**.
2. **Directory floor (the durable backbone):** `_EXCLUDED_DIRS` is the **hardcoded, non-shrinkable** directory-exclusion floor; user `exclude_dirs` can only ADD to it, matched by **bare path-component name** via `path_under_excluded(filepath, excluded=_EXCLUDED_DIRS)`. We add the dataset's own dir-component name **`crosscommitvuln`** to it.

**Why the floor edit despite the ceiling already covering `.jsonl` today:** the ceiling is a *moving* line — ADR 0021 has been **widening** `ALLOWED_EXTENSIONS`, and gold answers could also land in an already-indexable format (a `.json`/`.md`/`.yaml` debug dump, a fixture copy, a future re-serialization). The floor makes the guarantee **directory-structural and future-proof**: anything under a `crosscommitvuln` path component is excluded regardless of extension or how the ceiling moves. That is the defensible WHY (the earlier "only mechanism" framing was wrong — the ceiling already helps today; the floor is durability against ceiling widening and format drift).

```python
# eval gold-answer key — never index; data-leak guard for the CrossCommitVuln QA dataset.
# The vendored gold answers (cve/cwe/mechanism/files) must never enter any pydocs-mcp
# index or they would leak needle answers into retrieval results. ADR 0021 keeps widening
# ALLOWED_EXTENSIONS, so the extension ceiling is not a durable guarantee — this floor is.
_EXCLUDED_DIRS = frozenset({..., "crosscommitvuln"})
```

**Fixture placement (closes a real gap).** `path_under_excluded` matches bare path **components**, so a fixture named `crosscommitvuln_mini.jsonl` would **not** be covered by the floor (its component is the filename, not `crosscommitvuln`) — only the extension ceiling would protect it. Therefore test fixtures containing gold answers are placed under a **floor-covered directory**: `tests/fixtures/crosscommitvuln/mini.jsonl`, not `tests/fixtures/crosscommitvuln_mini.jsonl`. A repo-invariant test (§9.1) asserts every file that contains gold answers sits under a `crosscommitvuln` path component.

**Single-identifier coupling (locked).** `crosscommitvuln` is deliberately **one identifier** serving three roles — registry name, vendored dir-component, and floor entry — so the leak-guard coupling argument holds by construction (the dir the indexer must skip *is* the dataset's name). This identifier is code/config/path-side only and never model-facing (§5.0), so it cannot bias the question. Adding an eval-specific name to the core product floor is coupling weighed and accepted: the **alternative** (`[tool.pydocs-mcp] exclude_dirs = ["crosscommitvuln"]` in `benchmarks/pyproject.toml`) is a *config* entry that can be forgotten, overridden per-deployment, or dropped in a partial checkout, whereas the floor **cannot be shrunk** by any config. One documented, distinctive frozenset member is far below the leak risk it removes. A config-level `exclude_dirs` MAY be added as cheap defense-in-depth **alongside** the floor (the floor remains the guarantee).

**Encoding alternative (considered, rejected for v1).** Encoding the vendored gold (e.g. base64 fields) would make it leak-proof under *any* indexer with zero product coupling. Rejected because plaintext JSONL keeps the gold **greppable and debuggable** (construction, review of all retained queries, and test assertions all read it directly), and the floor already gives a structural guarantee. Encoding is noted as a possible future defense-in-depth if the coupling ever becomes contentious.

**Belt-and-suspenders note.** The `ask_rubric` corpus is a `materialize_corpus` temp dir built from a git checkout — the vendored gold is **never inside it** — so the floor addition is *not* what protects the scoring path; it protects the *separate* "operator indexes the benchmarks source tree" scenario.

## 7. coding-agent-playbook Integration

### 7.1 The new prompt

Author a **new, dedicated, non-ML-gated** prompt (recommended id `find-injected-vulnerability`; plan finalizes) at `resources/prompts/find-injected-vulnerability.md.j2`, rather than overloading the ML-scoped `security-review` prompt. WHY not overload: `security-review` is `applies_if: any_ml_stack:[…]`-gated and framed around unsafe-deserialization-first ML review; the needle-search framing is generic (SQLi, SSRF, path traversal, command injection…), and a clean dedicated prompt keeps the bi-directional `eval_tasks:` back-reference unambiguous.

Its frontmatter `eval_tasks:` lists every crosscommitvuln fixture task id (validator-enforced bi-directional back-ref, `evals/task.py`). Its body borrows the Visa-harness intuition — *trace untrusted input → dangerous sink; gate a finding on reachable + unmitigated + concrete-exploit + cited; classify the CWE by exploit not bug-class; start at the attack surface, not line 1* — **without** naming the specific file/CWE/flaw and **without any commit-structure, "benign-changes", or scanner-evasion framing** (same unbiased needle-hiding discipline as §5.2; the fixture is presented as a single project snapshot, §5.0).

### 7.2 Curated fixture subset (proposed; plan finalizes exact list)

Only a curated subset gets a hand-authored, self-contained fixture (paraphrased source→sink, **not** copied from the real repo — zero third-party licensing exposure; authorship rule §7.4). Proposed ~10, chosen for **diverse CWE coverage** and for chains that paraphrase cleanly into a small fixture:

| CVE | CWE class | Why included (diversity) |
|---|---|---|
| CVE-2099-00018 (modoboa) | CWE-78 OS command injection | The worked example; f-string → `shell=True` wrapper, clean source→sink pair |
| CVE-2099-00016 (ormar) | CWE-89 SQL injection | Classic taint → query string |
| CVE-2026-27696 (changedetection.io) | CWE-918 SSRF | URL fetch reachability |
| CVE-2099-00004 (langroid) | CWE-94 code injection | `eval`/exec-style sink |
| CVE-2099-00003 (pytorch) | CWE-502 deserialization | Untrusted load path |
| CVE-2099-00023 (changedetection.io) | CWE-22 path traversal | Filesystem escape |
| CVE-2099-00022 (authlib) | CWE-347 signature bypass | Non-injection logic flaw |
| CVE-2026-32247 (graphiti) | CWE-943 query injection (non-SQL) | Different injection surface |
| CVE-2099-00008 (pydash) | CWE-915 mass assignment | Object-property tampering |
| CVE-2099-00014 (python-aiplatform) | CWE-79 XSS | Output-encoding sink |

This spans injection (78/94/89/943), traversal (22), SSRF (918), deserialization (502), signature (347), mass-assignment (915), XSS (79) — a broad enough battery that the prompt is exercised across exploit classes. Note `changedetection.io` appears twice (SSRF + path traversal) — playbook fixtures are hand-authored and self-contained, so the multi-CVE-per-repo co-residence issue (§5.2) does **not** arise here (there is no shared checkout); each fixture isolates exactly one source→sink pattern in one self-contained snapshot. The plan finalizes the exact 8–12 (dropping any whose real chain resists faithful small-fixture paraphrase).

### 7.3 `task.toml` shape

Each fixture is a directory `resources/eval_tasks/<id>/` with `task.toml` + a self-contained `setup/` project (hand-authored source files paraphrasing the source→sink + `setup/pyproject.toml`). The shape below is the **verified real schema** (`src/coding_agent_playbook/evals/task.py`; cross-checked against `security-review-pickle-load/task.toml`): `[gate]`/`[rubric]` each carry an `expr` string plus a `bind` **table keyed by handle name**; a rubric handle binds `{fail=<0-1 float>, rubric=[{judge=…, weight=…}, …]}`; `version` is an **integer**.

```toml
id = "find-injected-vuln-modoboa-cmdi"          # unique
prompt_id = "find-injected-vulnerability"        # bi-directional back-ref
description = "OS command injection paraphrase; derived from CVE-2099-00018 (CrossCommitVuln-Bench, CC BY 4.0, A. Majumdar)."
version = 1

[setup]
files = ["app/jobs.py", "app/sysutils.py", "pyproject.toml"]
project_kind = "library"

[invocation]
prompt_args = "This project contains a security vulnerability. Find where untrusted input reaches a dangerous operation and classify the exploit."  # the QUERY that hides the vuln — unbiased, single-snapshot framing

[gate]
# Review-only: the agent must NOT modify the fixture. Deterministic, cheap.
expr = "jobs_untouched and sysutils_untouched"
[gate.bind]
jobs_untouched     = { check = "file_unchanged", path = "app/jobs.py" }
sysutils_untouched = { check = "file_unchanged", path = "app/sysutils.py" }

[rubric]
expr = "identifies_exact_vuln"
[rubric.bind]
identifies_exact_vuln = { fail = 0.5, rubric = [
  { judge = "The answer names OS command injection (CWE-78) as the vulnerability class.", weight = 0.34 },
  { judge = "The answer traces the untrusted source (user-controlled path/email) to the shell-executing sink.", weight = 0.33 },
  { judge = "The answer cites the specific fixture files where source and sink live.", weight = 0.33 },
] }

[verdict]
trial = "gate and rubric"     # default
```

- **`[invocation].prompt_args` = the query** — the free text the "user types", needle-hiding **and framing-free** (no CWE, no sink symbol, no filename, no commit/temporal language, no "benign"/scanner talk — the §5.2 bans apply verbatim).
- **`[gate]` = review-only** — `expr` composes the bound handles (`jobs_untouched and sysutils_untouched`); each `[gate.bind]` entry is a table keyed by handle name whose value carries `check = "file_unchanged", path = …`. Deterministic and token-free; asserts the agent found the flaw by *reading*, not editing.
- **`[rubric]` = exact-identification** — `expr` names the bound rubric handle; the `[rubric.bind]` entry carries `fail` = a **0–1 float cutoff on the weighted rubric-group score** (not a string, not an assertion name) and a `rubric` list of `{judge=, weight=}` groups summing to 1.0. The LLM judge (`evals/judge.py`; `StubJudge` for hermetic tests) scores each assertion against the agent transcript. Because the mechanism is authored into these assertions (sourced from `GoldAnswer.ast_body`), the **playbook surface is where mechanism/exploit-chain articulation is actually LLM-graded** — the gold-aware grading the optimizer path defers (§5.3).

### 7.4 Self-containment & licensing

Fixtures are hand-authored paraphrases — **no real-repo source** — so there is zero GPL/AGPL/third-party contamination and each fixture is fully self-contained under `setup/`. **Binding authorship rule (makes the no-derivative claim auditable):** fixtures are authored **exclusively** from the CC BY 4.0 `annotation.json` + `reproduction.md`; authors **must not consult the upstream repository source** while writing fixture code, so a close paraphrase of (possibly GPL/AGPL) upstream code cannot occur. Attribution: the shared `resources/eval_tasks/ATTRIBUTIONS.md` (§4.2) carries the full CC BY credit/URL/change-statement/DOI, and each `task.toml` `description` cites its originating CVE + the CC BY 4.0 source as the per-task pointer.

### 7.5 False-positive guard (deferred)

The closest existing analog is the `security-review-pickle-load` (positive) + `security-review-already-safe` (false-positive guard) pair. Since **v1 scope is positives-only** (§3.2), we author **only positive** fixtures now. A future "clean lookalike" guard (a fixture with a near-identical shape but a sanitized sink, where the correct answer is "no exploitable condition") is **noted and deferred** — it belongs to a later revision and would pair naturally with the same prompt.

### 7.6 Shipped-task count test

`tests/integration/test_shipped_eval_tasks_c7.py::test_ships_ten_tasks` asserts the exact count **10**. Adding N crosscommitvuln fixtures changes the expected count to **10 + N** (with the proposed ~10 subset, 20). The other assertions there — every shipped task compiles to `{gate, rubric}` with verdict `"gate and rubric"` and validates clean — apply unchanged to the new fixtures and must pass.

## 8. Data-Flow Diagrams

### 8.1 pydocs-mcp (ASCII)

```
CONSTRUCTION (offline, one-time / on revision bump)
  source clone: dataset/<CVE>/annotation.json
        │  gate 1 — filter: complete+sast AND chain.description not TODO
        │           (33 candidates kept, 6 SKIP + 32 TODO dropped, counts logged)
        ▼
  per CVE:  resolve fix_commit^ via RepoCache  →  pin prefix_sha   (ONE repo + ONE commit per record, §5.0)
            verify contributing hashes present
            gate 2 — co-resident ancestry drop (multi-CVE repos): another included CVE's
                     assembled chain present at prefix_sha → DROP record (cve_ids + count logged)
                     → ≤33 retained (24 always-clean + up to 9)
            build unbiased single-snapshot query (no commit/temporal framing, no dates)
                     →  banned-token + framing leak-check (FAIL build on leak)
            manual review pass of all retained queries (hard step)
            build gold {cve, cwe[], files} + mechanism→ast_body
        ▼
  vendored:  datasets/data/crosscommitvuln/{records.jsonl, banned_tokens.jsonl, NOTICE}  (CC BY 4.0)

RUNTIME (eval / optimize)
  CrossCommitVulnDataset.tasks()
        │  read records.jsonl via importlib.resources   (or fixture_path in tests)
        ▼
  EvalTask{ query, gold(GoldAnswer: file_set+extra; mechanism in ast_body), corpus_source=lazy(RepoCache.checkout(url, prefix_sha)) }
        │                                      └─ read_checkout_files → materialize_corpus
        │                                         (single pinned pre-fix snapshot, HISTORY-LESS temp dir —
        │                                          no .git, no commit signal; gold NOT inside)
        ▼
  CombinedDataset.tasks()  ── unions ──►  swe-qa-pro.tasks()  +  crosscommitvuln.tasks()
        │  prefix task_ids (sweqapro/… , ccv/…)  → disjoint → non-empty train & holdout
        │  non-None fixture_path → RAISE (incompatible member shapes)
        ▼
  dataset_registry.build("swe-qa-pro+crosscommitvuln")
        ▼
  AskRubricFitness (optimizer: skillopt)   — existing fitness/gates UNCHANGED
        ├─ gold_substring (ANY): weak groundedness screen  (shipped, unchanged)
        ├─ gold_substring_all (NEW additive gate): file_set + cve_id + cwe_ids ALL verbatim  (non-vacuous here)
        ├─ used_indexed_tools gate: zero-tool-call answer fails  (anti-memorization)
        ├─ recall@k over gold.file_set  (free retrieval rung; grounding memorization can't fake)
        └─ RubricJudge: gold-blind generic criteria  (mechanism NOT judge-graded on this path in v1)
        ▼
  metrics reported PER PREFIX (sweqapro/ vs ccv/)  → ccv regression stays visible

LEAK GUARD (product indexer, separate path)
  1) .jsonl ∉ ALLOWED_EXTENSIONS (true today)   +   2) _EXCLUDED_DIRS ∋ "crosscommitvuln" (durable backbone)
        → path_under_excluded() → gold answer key never indexed, even if the extension ceiling widens
```

### 8.2 coding-agent-playbook (Mermaid)

```mermaid
flowchart TD
    A["annotation.json + reproduction.md (curated CVE subset ~10, CC BY 4.0)"] -->|hand-author paraphrase source→sink; upstream source NOT consulted| B["resources/eval_tasks/&lt;id&gt;/setup/ (self-contained single-snapshot fixture, no real-repo source, no history)"]
    A -->|unbiased query — no CWE/sink/file, no commit/temporal framing| C["task.toml [invocation].prompt_args"]
    A -->|mechanism → rubric judge assertions| D["task.toml [rubric] expr + [rubric.bind] {fail=float, rubric=[judge,weight]}"]
    A -->|review-only| E["task.toml [gate] expr + [gate.bind] {check=file_unchanged, path}"]
    P["prompt find-injected-vulnerability.md.j2 (non-ML-gated, Visa-harness framing, no commit-structure language)"] -->|eval_tasks: lists ids| C
    C --> F["playbook eval &lt;id&gt; --run --runner fake"]
    E --> F
    D --> F
    F --> G["StubJudge scores rubric vs transcript (hermetic)"]
    B --> F
    F --> H["verdict = gate AND rubric"]
    P -. bi-directional back-ref validated .-> C
    A -.->|full credit/URL/DOI| N["resources/eval_tasks/ATTRIBUTIONS.md (CC BY 4.0)"]
```

## 9. Testing Strategy

### 9.1 pydocs-mcp (`benchmarks/tests/`)

Mirror `tests/datasets/test_swe_qa_loaders.py` (hermetic via `fixture_path` + a fake `RepoCache`; `asyncio_mode="auto"` so `async def test_…` needs no decorator). Gold-bearing fixtures live under a **floor-covered** dir, `tests/fixtures/crosscommitvuln/mini.jsonl` (§6.6), never `crosscommitvuln_mini.jsonl`.

- **`tests/datasets/test_crosscommitvuln_loader.py`** pins:
  - `isinstance(CrossCommitVulnDataset(), Dataset)` (Protocol conformance) and `dataset_registry.build("crosscommitvuln", fixture_path=…)`.
  - Unique registry name (no duplicate-registration `ValueError`); registration actually fires (i.e. `datasets/__init__.py` import seam works).
  - **Single-repo/single-commit invariant (§5.0):** every record carries exactly one `repo_url` and exactly one 40-hex `prefix_sha`; the loader emits exactly one task per record and no task references more than one repository or commit.
  - Hermetic `fixture_path` path — **no network**, fake `RepoCache` returns a temp checkout; assert `file_set`, `extra["cve_id"]`/`extra["cwe_id_*"]`, `ast_body` holds the mechanism, metadata carries `intro_window`/`fix_commit_date`/`co_resident_cves` — and that **none of those temporal fields appear in the query text**.
  - **Needle-leak assertion on the query** — for the worked-example fixture, assert the query contains none of its banned tokens **from the stored `banned_tokens.jsonl` list** (cve/cwe ids, file basenames, `exec_cmd`, `command injection`, fix/contributing commit hashes, subject-mined symbols, every commit date, and the v2 framing vocabulary of §5.2).
  - Exclusion/vendored **count is logged** (no-silent-caps).
  - Gates are **non-vacuous**: gold non-empty for every record; `ast_body` (prose) is **not** in `extra` (no gate reads it).
  - Corpus materialization: `corpus_source()` returns a temp dir populated by `read_checkout_files` (plain file tree, no `.git` — §5.0).
- **Vendored-corpus pins (on the real packaged resource, not just the mini fixture):**
  - `24 <= len(records) <= 33` read via `importlib.resources` from `pydocs_eval.datasets.data.crosscommitvuln` — a **bound, not a hard pin**, because the co-resident ancestry drop (§5.2) may remove up to 9 of the multi-CVE-repo records at construction; 24 single-CVE-repo records are always retained. The plan pins the exact post-ancestry number once the check has run (and the pin may then be tightened to equality); the loader logs the vendored count either way.
  - **All-records banned-token sweep** — for every vendored record, assert its `query` contains none of its stored banned tokens (using `banned_tokens.jsonl`), **including** the v2 framing bans (`commit`/`commits`/`multiple`/`multi-commit`/`gradually`/`over time`/`across`/`benign`/`static analysis`/`SAST`/`per-commit`/`scanner`/`individually`) and every commit date.
- **Construction-tool unit tests:**
  - The query leak-check **raises on a deliberately leaking query** (e.g. one containing `exec_cmd`, the CVE id, a commit date, or the word `gradually`) — the failure mode is tested, not just the clean pass.
  - **Co-resident ancestry drop:** given a fake multi-CVE repo where CVE A's pinned `prefix_sha` contains CVE B's fully assembled chain, the construction tool **drops** A's record and logs the dropped `cve_id`s + count (the drop path is tested, not just the clean retain path).
  - `gold_substring_all` gate: ALL candidates present → pass; one missing → fail; `keys=` restricts tokenization; empty candidates → vacuous pass (documented, not reachable for this dataset).
- **`tests/datasets/test_combined_dataset.py`** pins:
  - Protocol conformance (incl. the `tasks()` method) + `dataset_registry.build(<combined-name>)`.
  - **task_id disjointness** — inject two fake member datasets with colliding raw ids; assert prefixed ids are unique and both members' tasks appear.
  - **`fixture_path` contract** — a non-None `fixture_path` **raises** `ValueError` naming the value and pointing at `members=` (§6.4).
  - Combined ids yield **non-empty train AND holdout** under `task_split` / `partition_task_ids`; the test also asserts per-prefix counts are reported.
- **`tests/optimize/…`** (light): a config-load test that `optimize_ask_prompt_combined.yaml` resolves `dataset.name` to the combined dataset, builds it, and that its gate list includes `gold_substring_all` + `used_indexed_tools`.
- **Leak-guard tests** (`python/…` side, `tests/`):
  - `"crosscommitvuln" in _EXCLUDED_DIRS` and `path_under_excluded("…/data/crosscommitvuln/records.jsonl", excluded=_EXCLUDED_DIRS)` is `True`.
  - **Repo-invariant test:** every file in the repo that contains gold answers sits under a `crosscommitvuln` path component (guards the fixture-naming gap of §6.6).

### 9.2 coding-agent-playbook (`tests/`)

- **`tests/integration/test_shipped_eval_tasks_c7.py`** — update `test_ships_ten_tasks` count to `10 + N`; the existing per-task assertions (compiles to `{gate, rubric}`, verdict `"gate and rubric"`, validates clean) cover the new fixtures.
- **New prompt back-ref validity** — a test that `find-injected-vulnerability`'s `eval_tasks:` lists exactly the crosscommitvuln fixture ids and each fixture's `prompt_id` points back (bi-directional validator).
- **Hermetic run per fixture** — `playbook eval <id> --run --runner fake` + `StubJudge`, monkeypatching `uv sync`; assert the gate is review-only (`file_unchanged` handles hold under the boolean `expr`) and the rubric binding is well-formed (weights sum to 1.0, `fail` is a 0–1 float cutoff).
- **Needle-leak assertion on `prompt_args`** — assert each fixture's query contains none of its CVE's banned tokens, **including** the v2 framing bans and commit dates (§5.2).
- **Attribution present** — a test that `resources/eval_tasks/ATTRIBUTIONS.md` exists and carries the CC BY 4.0 URL + author credit + DOI.

## 10. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| **Pretraining contamination** (all included CVEs are public, pre-cutoff; a model may recall `cve_id`/`cwe_ids`/files from the repo slug alone, measuring CVE-database recall not search) | Recommended gate stack includes `used_indexed_tools` (gates.py:135) so a zero-tool-call answer fails, and reports `recall@k` over `gold.file_set` (grounding memorization cannot fake); `metadata["fix_commit_date"]` supports pre/post-cutoff analyses. **Residual risk explicitly accepted:** a model that both recalls the CVE and makes a token tool call can still score; the grounding signals bound but do not eliminate this. |
| **Weak deterministic gate** (shipped `gold_substring` is ANY-match, ignores params, passes on one file) | Add the additive `gold_substring_all` gate (§6.5) for exact identification; keep `gold_substring` only as a groundedness screen; state the ANY semantics plainly wherever the gate is referenced. |
| **Gold-blind judge** (shipped `RubricJudge` never sees gold; mechanism can't be judged on the optimizer path) | v1 does not claim optimizer-path mechanism grading; mechanism is LLM-graded on the **playbook** surface via authored rubric assertions (§7.3). `ast_body` carries the mechanism gold-side as the seam for a future gold-aware `ask_rubric` change (out of v1). |
| **Multi-CVE-per-repo co-residence** (a pre-fix checkout for CVE A can contain CVE B's assembled chain; with the date window removed from the query, no in-question lever can disambiguate) | Construction-time **co-resident ancestry drop** (§5.2, §6.3): any record whose pinned `prefix_sha` co-resides another included CVE's fully assembled chain is **dropped**, giving clean single-answer gold; dropped `cve_id`s + count are logged; vendored count becomes ≤33 (24 always-clean + up to 9). Retained records still carry `co_resident_cves` metadata for observability. Playbook fixtures are per-pattern and self-contained, so the ambiguity does not arise there. |
| **Small-N signal dilution & split skew** (≤33 ccv ≈ 8–11% of combined fitness; sha256%2 on ≤33 ids can skew the split and cluster CWE classes; ccv holdout ≈ 12–16) | **Per-prefix reporting is required** (§6.4) so a ccv regression is visible even as the blended score rises; construction logs realized ccv train/holdout counts + per-side CWE distribution; ccv-slice holdout deltas are **directional only** at N ≈ 12–16, not accept/reject evidence. Dataset weighting is future work (fitness change, NG5). |
| **Parent-SHA resolution fails / dead commit** (repo deleted, force-push) | Resolve + verify at construction; a runtime clone failure is isolated to that lazy task and logged; drop-with-count at construction if the parent lacks the contributing commits. |
| **Banned-token leak-check false negatives** (a sink name or framing synonym the miner missed slips into the query) | Derive banned tokens from `cve_id`, `cwe_ids`, `summary` keywords, `files_changed` basenames, `role`/chain-mined symbols, **fix/contributing commit hashes, subject-mined tokens, every commit date, and the v2 framing vocabulary (§5.2)**; make the leak-check a **build-failing assertion**; a **manual review pass of all retained queries is a hard v1 step**; slug/banned-token collisions resolve to repo identity (logged). Residual synonym risk accepted. |
| **Floor-coupling** (eval name in the product `_EXCLUDED_DIRS`) | Weighed in §6.6: one documented, distinctive frozenset member equal to the dataset's own name; the config-file alternative is forgettable/overridable, the floor is not shrinkable — structural guarantee wins; extension ceiling is the day-one cover, the floor is durability against ceiling widening. |
| **Judge/clone cost** (rubric judge tokens + up to 28 distinct repo clones — the ≤33 records span 28 repos, and ancestry drops can only lower it — some very large e.g. pytorch) | The gates + `recall@k` are free/deterministic and gate before the judge; rubric cost is bounded by the existing swe-qa-pro optimize budget; **`RepoCache` reuse bounds clones to one per repo across rounds** (laziness means unscored tasks never clone); pytorch-scale clones are the expected worst case — shallow-clone support in `RepoCache` is noted as future work, not v1. |
| **CC BY 4.0 non-compliance** | `NOTICE` shipped in `package-data` (**wheel and sdist**, verified as a release gate); change indication ("transformed to QA") + paper (arXiv:2604.21917) + DOI (10.5281/zenodo.19338596) + author credit; playbook ships `resources/eval_tasks/ATTRIBUTIONS.md` + per-task `description` pointers. Hard gate on packaging. |
| **Fixture paraphrase drifts from real exploit** (playbook) | Paraphrase preserves the source→sink *shape* only, authored solely from CC BY annotation/reproduction (upstream source not consulted, §7.4); the rubric grades class + source→sink + file citation, all satisfiable by the fixture; the CVE is cited for provenance, not asserted as a faithful reproduction. |

## 11. Rollout / Sequencing

Three independently landable pieces (the plan orders them):

- **(A) pydocs-mcp `crosscommitvuln` loader + packaging + leak-floor + additive gate.** The construction tool (both gates: inclusion filter + co-resident ancestry drop, counts logged), the vendored records (≤33) + `banned_tokens.jsonl` + `NOTICE`, `datasets/crosscommitvuln.py`, the `datasets/__init__.py` import line, the `package-data` glob (+ sdist coverage), the `_EXCLUDED_DIRS` edit + repo-invariant test, the additive `gold_substring_all` gate kind, and loader/leak-guard tests (including the single-repo/single-commit invariant test). Self-contained; ships on its own (usable directly by `dataset_registry.build`).
- **(B) `CombinedDataset` + optimize config + per-prefix reporting.** Depends on (A). `datasets/combined.py`, its `datasets/__init__.py` import line, `optimize_ask_prompt_combined.yaml` (gate list adds `gold_substring_all` + `used_indexed_tools`), per-prefix metric reporting, combined tests. Unlocks the "swe-qa-pro train + crosscommitvuln train" optimizer path.
- **(C) coding-agent-playbook fixtures + prompt.** Fully independent of (A)/(B) (different repo, no shared code). The new prompt, ~10 hand-authored fixtures, `ATTRIBUTIONS.md`, the count-test bump, and hermetic run tests.

Recommended order: A → B (dependency), C in parallel. Each is a discrete PR with its own green gate set (`pydocs-mcp-eval`: `pytest` + `ruff`; playbook: `ruff`, `mypy --strict`, `pytest --cov-fail-under=80`).

## 12. Open Questions for the Plan Step

The four big forks are decided (§3), and the grading semantics, the leak-guard stack, the single `crosscommitvuln` identifier, the unbiased single-snapshot query framing (§5.0, §5.2), and the vendored filenames (`records.jsonl`, `banned_tokens.jsonl`, `NOTICE`) are all locked above. Only three finalizations remain:

1. **Combined-dataset registry name** — recommended `swe-qa-pro+crosscommitvuln`; confirm the `+` is acceptable to the YAML config loader, else use the fallback `swe-qa-pro-plus-crosscommitvuln`. (Both member names and the `crosscommitvuln` name itself are locked; only the combined string's punctuation is open.)
2. **Exact curated CVE subset for the playbook** — proposed ~10 in §7.2; the plan confirms 8–12, dropping any whose real chain resists faithful small-fixture paraphrase.
3. **Exact vendored record count after the co-resident ancestry check** — the design bounds it (24 ≤ N ≤ 33, §5.2); the plan runs the check across the 9 multi-CVE-repo candidates, pins the resulting N (optionally tightening the §9.1 bound test to equality), and records the dropped `cve_id`s.