# ADR 0001 — Search surface: one ranked-retrieval tool, dense+graph primary, coverage-routed fusion

- **Status:** Accepted
- **Date:** 2026-07-17
- **Decision area:** D1 of the Phase 0 owner spec ("Tool Contracts for the pydocs-mcp Code Harness")
- **Siblings:** [0002-tool-naming-and-parameter-contracts.md](0002-tool-naming-and-parameter-contracts.md) (D2),
  [0003-grep-glob-backend.md](0003-grep-glob-backend.md) (D3),
  [0004-code-structure-abstraction.md](0004-code-structure-abstraction.md) (D4)

## Context

The repo's constitution (CLAUDE.md §"MCP API surface vs YAML configuration"; spec
`docs/superpowers/specs/2026-07-06-task-shaped-surface-decisions-swe-qa-design.md` §D2) fixes
the MCP surface at six task-shaped tools and declares any addition "a design-doc-level
versioning event". The Phase 0 owner spec is exactly that event: it prepares pydocs-mcp to
be the tool layer of a code-agent harness (SWE-bench-style evaluation, then text-space
optimization of tool descriptions). Under that spec the surface grows from six to nine tools
(three additions, zero renames, zero removals) and the constitution text is amended to nine
with a pointer to these ADRs. The precedent chain is the two-tool→six-tool lineage: the
2026-04-20 consolidation spec, the 2026-07-06 task-shaped spec, and CHANGELOG v0.5.0.

Within that re-frozen surface, this ADR answers the D1 question: how many ranked-retrieval
tools does the harness expose, and what backend serves them? Candidate shapes were (a) one
tool with always-on BM25+dense fusion, (b) one tool on a single backend, and (c) two tools
split by affordance (lexical vs semantic).

## Evidence

All benchmark numbers below are recorded results from `benchmarks/README.md` on the RepoQA
small_test split (n=30; metrics recall@1/recall@5/recall@10/MRR) unless noted. They were
**not re-run** for this ADR (unverified in that sense — CPU dense indexing at 60–215 s/needle
makes a fresh small_test sweep infeasible locally, benchmarks/EXPERIMENTS.md:136-141); dates
are the commit dates of the README table edits (git log -S over benchmarks/README.md:
c7a9217 2026-06-08 initial table; a87da62 2026-06-16; 4257796 2026-06-19 fusion sweep;
329a5a1 2026-06-20 structural; f74ccd0 2026-07-02 graph default flip; b5b60e7 2026-07-04),
and the underlying runs may precede those commits by days.

**Method comparison** (benchmarks/README.md:505-516):

| Method | recall@1 | recall@5 | recall@10 | MRR |
|---|---|---|---|---|
| BM25 (SQLite FTS5) | 0.167 | 0.333 | 0.400 | 0.238 |
| Dense bge-small | 0.467 | 0.733 | 0.733 | 0.567 |
| Dense gte-modernbert | 0.533 | 0.733 | 0.733 | 0.601 |
| Dense Qwen3-0.6B (n=21, partial — unverified comparability) | 0.667 | 0.810 | 0.810 | 0.738 |
| Dense F2LLM-v2-330M | 0.700 | 0.767 | 0.767 | 0.725 |
| Dense F2LLM-v2-0.6B (best) | 0.900 | 0.900 | 0.933 | 0.906 |
| Late-interaction ColBERT | 0.500 | 0.633 | 0.667 | 0.549 |
| LLM tree reasoning gpt-4o-mini (n=21, partial) | 0.333 | 0.524 | 0.524 | 0.398 |

The README's own takeaway: "Vector methods clearly beat lexical BM25 (semantic vs.
exact-term matching)" (benchmarks/README.md:534-535). The table carries an "ad-hoc runs
across one session, not a single locked sweep" caveat (benchmarks/README.md:518-522).

**Fusion sweep at fixed embedder** (F2LLM-330M; benchmarks/README.md:616-640): pure dense
0.700/0.767/0.767/0.725 beats every fusion variant — WSI 0.3/0.7 scores
0.633/0.767/0.767/0.674; WSI 0.5/0.5 0.433/0.733/0.767/0.573; WSI 0.7/0.3
0.333/0.500/0.600/0.401; RRF k=30 0.367/0.600/0.733/0.481; RRF k=60 0.367/0.600/0.633/0.460;
RRF k=100 identical to k=60. "None beat pure dense... RRF flattens to recall@1 0.37
regardless of k" (benchmarks/README.md:611-640). Fusion in this repo is a recorded
**negative** result as a ranking strategy.

**Structural slice and graph A/B** (repoqa-structural, n=20; benchmarks/README.md:652-673,
685-709): dense alone 0.00/0.20/0.30/0.113; dense+graph_expand at decay 0.9
0.00/0.80/1.00/0.386 — structural recall@10 goes 0.30 → 1.00. Graph expansion is the
largest recorded quality lever, is pure SQL, and adds no indexing cost ("the default index
already builds the dense .tq sidecar, so the flip adds no indexing cost",
benchmarks/README.md:705-707). In the default-flip A/B (cells recall@1/recall@10/MRR):
dense+graph 0.70/0.77/0.73 standard and 0.00/1.00/0.39 structural, vs hybrid RRF k=60
0.37/0.63/0.46 standard — mixing BM25 in costs ~13 pts recall@10 on standard queries
(benchmarks/README.md:685-703).

**Latency** (recorded p50 per needle; benchmarks/README.md:580-605):

| Backend | search p50 |
|---|---|
| BM25/FTS5 | 0.03 s |
| Late-interaction | 0.13 s |
| Dense bge-small | 0.15 s |
| Dense F2LLM-330M | 0.23 s |
| Dense F2LLM-0.6B | 0.29 s |
| LLM tree methods | 8.8–13.7 s ("a 20–450× gap") |

Fusion is search-cheap ("p50 ~0.23s across the board — fusion is cheap",
benchmarks/README.md:626-627), so the rejection of always-on fusion is on quality, not cost.

**Fresh saturated-fixture run** (2026-07-17, this worktree at HEAD 261c933): a fully offline
BM25 vs dense(bge-small) vs hybrid-RRF-k60 run on the 5-needle hermetic fixture
(`HF_HUB_OFFLINE=1 PYTHONPATH=benchmarks/src .venv/bin/python -m pydocs_eval.runner
--systems pydocs-mcp --fixture benchmarks/tests/eval/fixtures/repoqa_mini.json --limit 5
--bench-cache on --configs repoqa_bm25.yaml,repoqa_dense.yaml,repoqa_hybrid_rrf_k60.yaml`)
completed in ~2 min, exit 0 — but all three systems score 100% on every metric
(scratchpad fixture report): the fixture is saturated and quality-uninformative; only
latency separated them (search p50: BM25 0.00 s, dense 0.19 s, hybrid 0.47 s). The recorded
small_test numbers above therefore remain the primary evidence.

**BM25's evidence-backed role is corpus coverage, not ranking:** dependency code chunks are
FTS-indexed but vectorless under the default `embedding.dependency_policy: doc_pages`, so
`scope=deps` queries route to a BM25∥dense RRF preset ("the BM25 branch reaches every
dependency chunk by keyword", python/pydocs_mcp/pipelines/chunk_search_deps.yaml:4-10), and
decision-kind queries route to a BM25∥dense decision preset
(python/pydocs_mcp/defaults/default_config.yaml:10-29). A dense-only backend would silently
miss dependency code — a correctness hole, not a tuning choice.

**No identifier query class exists in the data:** RepoQA needles are natural-language
function descriptions (benchmarks/README.md:499-501) and DS-1000 tasks are natural-language
data-science intents over library docs (benchmarks/README.md:204-209); no
dataset slice contains identifier/exact-name queries, so no evidence supports a second
retrieval tool split by query class. Identifier lookup is a different *affordance* already
served by different tools: `get_symbol` for dotted-path resolution and the `kind="api"`
corpus selector routing to LIKE-based member search
(python/pydocs_mcp/pipelines/member_search.yaml:3-16; python/pydocs_mcp/__main__.py:233-259).

**Shipped default already implements the decision:** `defaults/default_config.yaml:26`
routes default chunk search to `pipelines/chunk_search_graph.yaml` — pre_filter →
dense_fetcher → metadata_post_filter → graph_expand(top_s 10, depth 1, decay 0.9) → top_k →
limit → token_budget_formatter; "embedding-centric, no RRF, no BM25"
(python/pydocs_mcp/pipelines/chunk_search_graph.yaml:7,31-63). No backend/pipeline/fusion
parameter is exposed on `search_codebase` (python/pydocs_mcp/server.py:583-604;
python/pydocs_mcp/application/mcp_inputs.py:177-222).

## Options considered

**(a) One fused tool, BM25+dense server-side fusion always on.** Contradicted by the repo's
own ablation as a ranking strategy: with the shipped-default-class embedder (F2LLM-330M),
every fusion variant loses to pure dense — RRF k=60 recall@1 0.367 vs dense 0.700; WSI at
best approaches dense without passing it; RRF is insensitive to k because it discards score
magnitude (benchmarks/README.md:616-640). The graph-hybrid A/B repeats the finding
(benchmarks/README.md:700-703). However, the one-tool *shape* of (a) is right, and
server-side fusion is evidence-justified for the two corpus slices dense cannot reach
(vectorless dependency code; decision records). Adopted as shape, rejected as default
ranking.

**(b) One tool, single-backend.** Best supported *if* "single backend" means
dense+graph_expand, the shipped benchmarked default: it ties pure dense on standard queries
(0.70/0.77/0.73) and dominates on structural queries (recall@10 1.00 vs 0.30), at zero
extra indexing cost and ~0.2 s search p50 (benchmarks/README.md:685-709). Strictly
dense-only (no graph, no lexical anywhere) is dominated twice over: it loses the structural
slice and silently misses vectorless dependency code
(python/pydocs_mcp/pipelines/chunk_search_deps.yaml:4-10). Lexical-only is the worst
recorded system (recall@10 0.40 vs 0.77–0.93). Holds only in the refined form "one tool,
dense+graph primary, YAML-routed BM25∥dense for uncovered corpus slices" — exactly what
ships today.

**(c) Two tools split by affordance (lexical vs semantic).** No benchmark evidence supports
it: no dataset slice has an identifier query class, and on every recorded slice BM25 loses
outright (recall@1 0.17 vs 0.70–0.90). The genuine affordance split that exists in the
product is search-vs-symbol-resolution, already expressed as different tools
(`search_codebase` vs `get_symbol`/`get_references`) plus the `kind=api` corpus selector.
Splitting search into lexical/semantic tools would push the backend choice onto the calling
agent with zero measured upside, and freeze a distinction the fusion sweep shows is a
server-side tuning concern. Note: the new `grep` tool
([0003-grep-glob-backend.md](0003-grep-glob-backend.md)) is a *different affordance*
(exact regex over files, not ranked retrieval) — orthogonal to this split and not what (c)
proposes.

## Decision

Freeze a **single ranked-retrieval tool**: `search_codebase` (name per
[0002-tool-naming-and-parameter-contracts.md](0002-tool-naming-and-parameter-contracts.md)). Backend
is a server-side concern, never a tool parameter:

1. The default pipeline stays the benchmarked dense+graph_expand configuration
   (`python/pydocs_mcp/pipelines/chunk_search_graph.yaml`).
2. YAML predicate routing sends queries to BM25∥dense RRF presets **only** for corpus
   slices where dense vectors do not exist: dependency code under `scope=deps` (vectorless
   by default under `embedding.dependency_policy: doc_pages`) and decision records
   (`kind=decision`) (python/pydocs_mcp/defaults/default_config.yaml:10-29).
3. Always-on fusion (option a's default) and a two-tool lexical/semantic split (option c)
   are **rejected**.
4. Exact-string/regex needs are served by the new `grep` tool
   ([0003-grep-glob-backend.md](0003-grep-glob-backend.md)); identifier resolution
   stays with `get_symbol` and the `kind="api"` member search.
5. The LLM tree-reasoning presets (`tree_only.yaml` and the two hybrid variants) remain
   YAML-opt-in retrieval strategies behind the same tool and are **not** part of the frozen
   surface — they replace the pipeline via `--config`/`PYDOCS_CONFIG_PATH`, not via any
   caller-visible parameter (python/pydocs_mcp/pipelines/tree_only.yaml:23-24), and are
   dominated on quality-per-second (recall@10 0.524 at ~13.7 s p50 vs 0.933 at 0.29 s,
   benchmarks/README.md:587-605).

### Caveats recorded with the decision

- The winning numbers come from the officially **burned** small_test split (n=30, ad-hoc
  runs): benchmarks/README.md:461-463 says it "has absorbed many recorded tuning sweeps —
  treat every test-derived split as held out". A one-shot confirmation on the held-out
  `test` split plus the repoqa-structural do-no-harm gate is recommended before the Phase 3
  baselines are locked. This is cheap insurance, not a Phase 0 blocker.
- The 5-needle hermetic fixture can gate regressions but cannot discriminate methods
  (saturated at 100% for BM25, dense, and hybrid alike).
- Description seeds for Phase 1 (query-class wording for the text-space optimizer):
  "natural-language/conceptual queries → this tool; exact identifier → get_symbol;
  exact string/regex → grep."

## Consequences

**Easier:**
- No tool-surface change for search: zero migration for MCP clients, no new schema
  overhead per request, and the sanctioned corpus-selector parameters (`kind`, `package`,
  `scope`, `project`) plus the `limit` input-shape validator stay intact.
- Backend evolution (embedder upgrades, routing predicates, graph decay tuning, future
  fusion experiments) remains a YAML deployment concern, A/B-testable against the benchmark
  harness without touching any client.
- The shipped architecture already implements the decision, so Phase 0 carries no retrieval
  implementation work.

**Harder / load-bearing:**
- The description boundary between `search_codebase`, `grep`, and `get_symbol` becomes
  **load-bearing text**: with one ranked-retrieval tool, the only thing steering a calling
  agent between ranked retrieval, exact regex, and symbol resolution is the tool
  descriptions. The Phase 1 seeds above must survive into TOOL_DOCS and the optimizer must
  not be allowed to blur that boundary.
- CLAUDE.md's architecture summary ("BM25 + dense fused via RRF") describes the available
  *machinery*, not the shipped default (which is dense+graph with no BM25 and no RRF,
  python/pydocs_mcp/pipelines/chunk_search_graph.yaml:7) — the constitution amendment must
  clarify this to stop the summary from misleading future contributors.

**Revisit when:**
- The held-out `test` split confirmation (or the repoqa-structural gate) contradicts the
  small_test ordering.
- Dense coverage of dependency code changes (e.g., `embedding.dependency_policy` moves off
  `doc_pages`), which would remove the rationale for the `scope=deps` fusion route.
- A dataset with a genuine identifier-lookup query class lands and shows a lexical ranking
  win — the only evidence shape that could reopen option (c).

## Action items

1. None beyond documentation for the search surface itself — the shipped architecture
   already implements the decision.
2. CLAUDE.md constitution amendment (with the six→nine change from the shared preamble):
   clarify that "BM25 + dense fused via RRF" is machinery, and the shipped default is
   dense+graph with coverage-routed fusion.
3. Carry the Phase 1 description seeds (query-class wording above) into the TOOL_DOCS
   entries for `search_codebase`, `grep`, and `get_symbol`
   (see [0002-tool-naming-and-parameter-contracts.md](0002-tool-naming-and-parameter-contracts.md)).

*(Recommendation carried from the recorded caveat, not a Phase 0 action item: a one-shot
confirmation run on the held-out `test` split plus the repoqa-structural do-no-harm gate
before the Phase 3 baselines are locked — GPU box; the harness's own promotion protocol.)*
