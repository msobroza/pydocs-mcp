#!/usr/bin/env bash
# A/B verdict run for the post-fusion dense re-ranker (dense_scorer).
#
# WHY this exists: PR #154 turned `dense_scorer` from a read-path no-op into a
# post-fusion turbovec re-ranker (re-orders the fused BM25+dense top-K by the
# exact turbovec allowlist score). Whether that re-rank actually lifts
# retrieval quality is an EMPIRICAL question the CPU dev box can't answer —
# dense indexing of the RepoQA repos is 60-215 s/needle on CPU, and n=30
# `small_test` has ±0.15 CIs. This script packages the conclusive run so it is
# one command on a GPU box.
#
# The A/B pair differs in EXACTLY one pipeline step, so any metric delta is
# attributable to the re-ranker:
#   A (baseline): configs/repoqa_hybrid_rrf_k60_norerank.yaml  (RRF only)
#   B (treatment): configs/repoqa_hybrid_rrf_k60.yaml          (RRF + dense_rerank)
#
# Reads the verdict off three numbers (see the comparing-retrieval-methods
# skill): recall@5 (headline) - MRR (tiebreaker) - repoqa-structural recall@10
# (do-no-harm gate). B wins ONLY if it lifts recall@5/MRR with NON-overlapping
# CIs on the full `test` split AND does not drop the structural gate.
#
# Usage (run from the repo root, on a machine with an NVIDIA GPU):
#   PYDOCS_VENV=.venv-li benchmarks/scripts/compare_dense_rerank.sh
#
#   # quick dev-split sanity before the full run:
#   SPLIT=small_test benchmarks/scripts/compare_dense_rerank.sh
#
# Env:
#   SPLIT       repoqa split for the headline run (default: test)
#   PYDOCS_VENV venv with torch+CUDA (default: .venv-li — passed to run_eval_gpu.sh)
set -euo pipefail

SPLIT="${SPLIT:-test}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GPU="$HERE/run_eval_gpu.sh"
OUT="benchmarks/results"
AB="benchmarks/configs/repoqa_hybrid_rrf_k60_norerank.yaml,benchmarks/configs/repoqa_hybrid_rrf_k60.yaml"
STAMP="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$OUT"

echo "== [1/3] headline A/B on repoqa --split ${SPLIT} =="
# --bench-cache on: both configs share the same ingestion (embedder), so the
# second config reuses the first's .tq — one cold index pass, two search passes.
"$GPU" \
  --systems pydocs-mcp --dataset repoqa --split "${SPLIT}" --bench-cache on \
  --configs "$AB" --trackers jsonl \
  --report "$OUT/dense_rerank_${SPLIT}_${STAMP}.md"

echo "== [2/3] do-no-harm gate on repoqa-structural (recall@10) =="
"$GPU" \
  --systems pydocs-mcp --dataset repoqa-structural --split "${SPLIT}" --bench-cache on \
  --configs "$AB" --trackers jsonl \
  --report "$OUT/dense_rerank_structural_${STAMP}.md"

echo "== [3/3] reports =="
echo "  headline:   $OUT/dense_rerank_${SPLIT}_${STAMP}.md"
echo "  structural: $OUT/dense_rerank_structural_${STAMP}.md"
echo
echo "VERDICT RULE: adopt the re-ranker (make it the shipped default) only if the"
echo "'_rrf_k60' column beats '_rrf_k60_norerank' on recall@5 AND mrr with"
echo "non-overlapping 95% CIs, AND does not lose recall@10 on repoqa-structural."
echo "A <2-needle delta or overlapping CIs = no adoption (quantized fetcher score"
echo "was already sufficient); record the numbers either way."
