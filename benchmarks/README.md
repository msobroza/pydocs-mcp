# pydocs-mcp Benchmark Suite

Real retrieval-quality evaluation for `pydocs-mcp` against a public benchmark
(**RepoQA-SNF**, arXiv 2406.06025) with **MLflow**-backed experiment tracking
and comparative slots for **Context7** and **Neuledge Context**.

The harness exists to A/B test YAML pipeline tunings (`AppConfig`) on a real
benchmark, then track every `(system × config × dataset)` combination as one
MLflow run with comparable params, metrics, and artifacts.

> An earlier placeholder harness (`fake_project/` + synthetic
> `dataset_gen.py`) was removed — it synthesized queries from the chunks
> it just indexed, so a chunker change shifted both the corpus and the
> queries together and the eval was blind. The current harness uses an
> external benchmark (RepoQA-SNF) with stable gold answers that the
> system under test cannot influence.

## Install

`uv`-friendly extras let you pull in only what you need:

```bash
uv pip install -e benchmarks                # core only — JSONL tracker, stdlib RepoQA loader
uv pip install -e "benchmarks[mlflow]"      # + MLflow tracker
uv pip install -e "benchmarks[all]"         # everything
```

`pip` works too — the optional extras are stock PEP 508 syntax.

## Run

The runner CLI is exposed as a module entry-point:

```bash
# Baseline run, pydocs-mcp only, against the bundled fixture (no network download).
./scripts/run_repoqa.sh \
    --systems pydocs-mcp \
    --configs <path-to-baseline.yaml> \
    --trackers jsonl \
    --fixture <path-to-fixture> \
    --limit 5

# Full sweep across YAML config variants (when configs/ lands in Task 9).
./scripts/run_repoqa.sh \
    --systems pydocs-mcp \
    --configs <baseline.yaml>,<no_stdlib.yaml>,<wide_chunks.yaml> \
    --trackers jsonl

# View results in MLflow UI (requires the [mlflow] extra).
mlflow ui --backend-store-uri file://./benchmarks/mlruns/
```

The runner can also be invoked directly (the `benchmarks/` package lives
under `benchmarks/src/` following the PyPA src-layout):

```bash
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner --help
```

For tests and offline development, pass a `--fixture` JSON to bypass the
RepoQA download entirely (see `benchmarks/tests/eval/fixtures/repoqa_mini.json`).

## Metrics

Every `(system × config × dataset)` run reports the following per-task metrics
plus aggregate values with a 95% bootstrap CI (1000 resamples, seed=0):

- **`recall@k`** — `1.0` iff the gold function appears in the top-`k` retrieved
  chunks under an AST-equivalent match (whitespace and comment tolerant);
  `0.0` otherwise. Reported at `k ∈ {1, 5, 10}`.
- **`mrr`** — Mean reciprocal rank. The score per task is `1/rank` of the first
  AST-matching item, or `0.0` if no match exists in the returned set. The
  aggregate is the arithmetic mean across tasks.
- **`pass@1-needle`** — `1.0` iff the top-1 retrieved item matches the gold
  needle, `0.0` otherwise. The strictest signal — sensitive to small ranking
  changes that `recall@k` smooths over.

The aggregator (`benchmarks/eval/metrics/aggregate.py`) emits the mean plus a
bootstrap confidence interval for each metric so regression gates can compare
runs without false positives from per-task variance.

## Benchmarks

One subsection per benchmark. Each subsection answers four questions in
the same order, so adding a future benchmark is a copy-paste of the shape:

1. **What it tests** — the retrieval task in one sentence.
2. **Example task** — a concrete query + gold answer so the shape is
   obvious without reading the paper.
3. **Dataset size + source** — how many tasks, where they come from.
4. **What this benchmark proxies** *and* what it does NOT — calibrate
   how much weight to put on the resulting numbers.

### RepoQA-SNF (Python subset of `repoqa-2024-06-23`)

**What it tests:** natural-language description → Python function retrieval
in long, real-world code repositories. Each task hands the system under
test a multi-file repo slice and a one-sentence English description of
one function ("the needle"); the system returns a ranked list of
candidate chunks; the harness counts whether an AST-equivalent match of
the needle's body appears in the top-K. This is the dominant query shape
for `search(query, kind, ...)` on the MCP surface.

**Example task** (from `benchmarks/tests/eval/fixtures/repoqa_mini.json`,
the 5-needle fixture shipped for hermetic CI):

```text
Query (description):
    Compute the factorial of a non-negative integer.

Repo content (file path → source, pinned to a specific commit):
    fixture_repo/__init__.py
    fixture_repo/math_helpers.py
    [in production tasks: 30–80 real Python files per repo]

Gold answer (AST-matched, comments + whitespace tolerant):
    def factorial(n: int) -> int:
        if n <= 1:
            return 1
        return n * factorial(n - 1)

Other needles in the same repo:
    - fibonacci  — Compute the n-th Fibonacci number.
    - is_prime   — Test whether n is prime.
    - gcd        — Greatest common divisor of two integers.
    - lcm        — Least common multiple via gcd.
```

A "pass" on `recall@k` means: at least one of the top-K retrieved chunks
contains a function whose AST body matches the gold needle's body
(comments + whitespace tolerant, see `benchmarks/eval/metrics/ast_match.py`).
`pass@1-needle` is the same check restricted to the top-1 result. `mrr`
rewards getting the gold high in the ranking, not just within the top-K.

**Dataset size:** 100 needles total — 10 real Python repos (HuggingFace
Transformers, vLLM, FastAPI, sympy, …) × ~10 needles each. The shipped
fixture (`repoqa_mini.json`) has 5 needles from one synthetic repo for
hermetic CI runs that don't touch the network.

**Source:** Liu, J. et al. *RepoQA: Evaluating Long Context Code
Understanding.* arXiv:2406.06025, June 2024. Apache-2.0 license, by the
EvalPlus team. Downloaded on first run to `~/.cache/pydocs-mcp/repoqa/`
and cached thereafter.

**What this benchmark proxies well:**

- **Description → function retrieval.** The 1:1 query-to-result shape
  matches the MCP `search` surface exactly.
- **Long-context indexing.** Each task ships a real repo slice, so the
  chunker and indexer are exercised on real-world Python layouts (not
  synthetic toys).
- **A/B testing YAML tunings.** Capture toggles, ranking weights,
  chunker parameters, and resolver thresholds can all be sweep-compared
  against the same dataset and metric set — the architectural payoff of
  the "behavior in YAML, surface stable" rule from `CLAUDE.md`.
- **Cross-system retrieval comparison.** `pydocs-mcp` (in-process
  pipeline) is comparable against `context7` (cloud MCP API) and
  `neuledge` (local MCP HTTP) on the same queries + the same gold answers.

**What it does NOT proxy:**

- **End-to-end LLM code generation quality.** Retrieval only — what an
  LLM does with the chunks is out of scope. The planned CodeRAG-Bench
  (DS-1000 + ODEX) integration closes this gap by scoring retrieval under
  the downstream code-generation task.
- **Multi-file / call-graph retrieval.** Each task is single-needle; the
  planned SWE-bench Verified retrieval slice covers cross-file reasoning.
- **Library-docs lookup from a natural-language intent.** RepoQA queries
  describe a *function inside a specific repo*, not "what's the right API
  in this library for what I'm trying to do." The planned DocPrompting
  CoNaLa-Docs integration covers that loop directly.
- **Multi-language coverage.** Python only.

When you read a result, treat it as evidence about the retrieval surface,
not the whole system.

### Roadmap: additional benchmarks

Each future benchmark gets its own subsection above following the same
four-question pattern. Planned additions:

| Benchmark | What it would add | Status |
|---|---|---|
| **SWE-bench Verified (retrieval-only slice)** | Given a real GitHub issue from a popular Python project, retrieve the set of files a developer needs to read to fix it. Scored against the human-verified patch set (which files actually changed). Stresses cross-file retrieval: a bug fix typically spans the changed file plus its callers, tests, and helpers — the system has to surface all of them from the issue text alone, not just one needle. Jimenez et al., arXiv:2310.06770 (2023); Verified subset (500 issues) curated by OpenAI (2024). | One-file dataset plugin; not yet implemented. |
| **DocPrompting CoNaLa-Docs** | Natural-language intent → Python library doc retrieval. Tests the exact "look up the right API doc from an English question" loop that AI assistants hit. Zhou et al., arXiv:2207.05987 (2023). | Plugin scoped, deferred. |
| **CodeRAG-Bench (DS-1000 + ODEX)** | Library-docs retrieval evaluated under the downstream code-generation task. DS-1000 covers NumPy/Pandas/SciPy/Matplotlib/scikit-learn (data-science Python); ODEX is execution-driven from StackOverflow. Wang et al., arXiv:2406.14497 (2024). | Roadmap. |

Adding one means: drop a `Dataset` Protocol implementation under
`benchmarks/src/benchmarks/eval/datasets/`, register it via
`@dataset_registry.register("<name>")`, point a config at it, and write
one README subsection mirroring RepoQA-SNF's shape. No harness changes
required.

## Current baselines

Two baseline JSON files are tracked in `benchmarks/baselines/`:

| File | What | Tasks | recall@1 | recall@5 | recall@10 | MRR |
|---|---|---:|---:|---:|---:|---:|
| `repoqa_snf.json` | Real 100-needle sweep against the Python subset of `repoqa-2024-06-23` | 100 | 14.0% [7%, 21%] | 17.0% [10%, 24%] | 18.0% [11%, 26%] | 15.2% [9%, 22%] |
| `repoqa_fixture_baseline.json` | 5-needle hermetic CI gate fixture | 5 | 60.0% | 80.0% | 80.0% | 70.0% |

CIs are 95% Wilson intervals from bootstrap resampling (1000 iter, seed=0).
Both baselines were captured against the `chunk_search_ranked.yaml` preset
that returns top-K ranked separate chunks. The MCP server's default
`chunk_search.yaml` collapses to one composite chunk via
`token_budget_formatter` — correct for LLM consumers, but it structurally
caps `recall@k > 1` at 0 because there is only ever one item to retrieve.
The ranked preset drops the formatter so `recall@k` can actually measure
top-K hits; the docstring at the top of `chunk_search_ranked.yaml`
expands on the split.

The real-100-needle numbers are the headline figure: PR-B3.1 (dense
embeddings + RRF) should beat `recall@10 = 18%` to be worth landing.

## Visualizing baselines

`benchmarks.eval.plotting` produces grouped vertical bar plots from one or
more baseline JSON files. Each baseline becomes a colored bar group; each
metric becomes an X-axis category; 95% CI error bars come straight from
each metric's `ci_low` / `ci_high`. Default palette is seaborn's
`colorblind` (colorblind-safe + Nature figure-guideline compliant).

**Apples-to-apples constraint:** every baseline passed to `plot_baselines`
must come from the same `dataset` field (e.g., all from
`repoqa-2024-06-23-python`). Mixing the 5-needle CI fixture next to the
real 100-needle sweep would silently misrepresent the numbers — the
fixture is a hermetic regression test, not a competing system. The
function raises `ValueError` listing the differing datasets if you try.
To compare across datasets, call `plot_baselines` once per dataset and
arrange the figures yourself.

**Title convention:** keep the title benchmark-focused (dataset + tasks)
and let the legend carry the system / config / method names. That way
the same chart still makes sense when PR-B3.1 adds a second bar group
for dense embeddings — no title rewrite needed. If you omit `--title`,
the default uses the first record's `dataset` field and `tasks_ran`.

```bash
# Today's plot — single baseline on real-100-needles. Method is in the
# legend (pydocs-mcp / baseline), not the title.
PYTHONPATH=benchmarks/src python -m benchmarks.eval.plotting \
    benchmarks/baselines/repoqa_snf.json \
    --output benchmarks/results/plots/repoqa_real.png \
    --metrics recall@1,recall@5,recall@10,mrr,pass@1-needle \
    --title "RepoQA-2024-06-23 (Python, n=100)"

# Future: side-by-side compare two configs on the SAME dataset
# (e.g., PR-B3.1's dense embeddings vs current BM25). The plot picks up
# the second bar group automatically — no code change to plotting.py,
# and the title still works because it describes the benchmark, not the
# methods being compared.
PYTHONPATH=benchmarks/src python -m benchmarks.eval.plotting \
    benchmarks/baselines/repoqa_snf.json \
    benchmarks/baselines/repoqa_snf_dense.json \
    --output benchmarks/results/plots/repoqa_real_with_dense.png \
    --title "RepoQA-2024-06-23 (Python, n=100)"
```

The legend identifies each system as `<system> / <config> (<label>) [<git_sha>, n=<tasks>]`
so a plot stays self-describing even when copy-pasted into a PR
description. Sample output (committed to `benchmarks/docs/repoqa_baselines.png`):

![RepoQA-2024-06-23 (Python) baseline plot](docs/repoqa_baselines.png)

Programmatic API — same behavior, more flexible for notebook use:

```python
from pathlib import Path
from benchmarks.eval.plotting import plot_baselines

fig = plot_baselines(
    baselines=[
        Path("benchmarks/baselines/repoqa_snf.json"),
        # Path("benchmarks/baselines/repoqa_snf_dense.json"),  # PR-B3.1
    ],
    metrics=("recall@1", "recall@5", "recall@10", "mrr"),
    output=Path("benchmarks/results/plots/repoqa_real.png"),
    palette="colorblind",                       # also: "deep", "muted", "Set2"
    title="RepoQA-2024-06-23 (Python, n=100)",  # keep it benchmark-focused;
                                                # legend carries the methods.
                                                # default: <dataset> (<tasks_ran> tasks)
)
```

The returned `matplotlib.figure.Figure` is yours to further customize,
`.show()` in a notebook, or `.savefig()` again with different DPI.

## License and attribution

Per-benchmark licensing + citation lives in the relevant subsection under
`## Benchmarks` above (each benchmark cites its own source). Cross-cutting
attribution:

- **MLflow** — Apache-2.0, Databricks. Used as the experiment-tracking
  backend; tracking URI defaults to a local `file://` store so no network
  or remote server is required to run the harness.
- **seaborn / matplotlib** — BSD-3-Clause, used for the baseline plotting
  module described under `## Visualizing baselines`.
- Third-party attribution lands in `LICENSE-third-party` once it is added.

## Running tests

```bash
uv pip install -e "benchmarks[all]"
pytest benchmarks/ -q
```

The bundled fixture (`benchmarks/tests/eval/fixtures/repoqa_mini.json`) lets
the full test suite run without network access.
