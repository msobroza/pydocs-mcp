#!/usr/bin/env bash
# Run the benchmark eval runner on the GPU.
#
# Why this wrapper exists: torch embedders (sentence_transformers — Qwen3,
# ModernBERT, F2LLM, ...) already find CUDA via torch's own RPATH, but FastEmbed
# (onnxruntime) cannot locate libcublasLt.so.12 / libcudnn unless torch's
# bundled NVIDIA libs are on LD_LIBRARY_PATH. Without that, `--gpu` silently
# falls back to CPU for the FastEmbed/bge path (dense-indexing the RepoQA repos
# on CPU is 60-215 s/needle — a full sweep takes days). This wrapper prepends
# those libs, sets PYTHONPATH, forces `--gpu`, and passes every other arg
# straight through to `pydocs_eval.runner`.
#
# Usage (args pass through verbatim):
#   benchmarks/scripts/run_eval_gpu.sh \
#     --systems pydocs-mcp --dataset repoqa --split small_test --bench-cache on \
#     --configs benchmarks/configs/repoqa_dense_st.yaml \
#     --report benchmarks/results/out.md
#
# Override the venv with PYDOCS_VENV=/path/to/venv (default: .venv-li).
set -euo pipefail

VENV="${PYDOCS_VENV:-.venv-li}"
PY="$VENV/bin/python"
[ -x "$PY" ] || { echo "run_eval_gpu.sh: no python at '$PY' (set PYDOCS_VENV)" >&2; exit 1; }

# Discover torch's bundled NVIDIA shared-lib dirs (libcublasLt, libcudnn, ...).
NV="$("$PY" -c "import os,glob,nvidia; print(':'.join(sorted(glob.glob(os.path.dirname(nvidia.__file__)+'/*/lib'))))" 2>/dev/null || true)"
[ -n "$NV" ] || echo "run_eval_gpu.sh: warning — no nvidia/*/lib dirs found; onnxruntime CUDA may fall back to CPU" >&2

export LD_LIBRARY_PATH="${NV}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="benchmarks/src${PYTHONPATH:+:$PYTHONPATH}"

exec "$PY" -m pydocs_eval.runner --gpu "$@"
