# DS-1000 reference project

A tiny Python project whose `pyproject.toml` pins the exact library
versions the DS-1000 benchmark was authored against. Installing it into a
local virtual environment populates that environment's `site-packages`
with the pinned releases, which the documentation indexer then reads.

Version parity matters: DS-1000 problems target specific library APIs, so
indexing a different release would surface signatures the benchmark never
expected. This project exists purely to materialize those exact versions.

DS-1000 is the data-science code-generation benchmark introduced in
*DS-1000: A Natural and Reliable Benchmark for Data Science Code
Generation* (arXiv:2211.11501).

## Pinned dependencies

The 11 pins below cover the 7 primary DS-1000 retrieval categories
(`numpy`, `pandas`, `matplotlib`, `scikit-learn`, `scipy`,
`tensorflow-cpu`, `torch`) plus four solution dependencies (`gensim`,
`seaborn`, `statsmodels`, `xgboost`) kept for faithful environment parity.

## Operator setup

Create a dedicated virtual environment and install this project into it:

```bash
python -m venv .venv
.venv/bin/pip install -e .
```

The resulting `.venv/lib/.../site-packages` holds the pinned libraries and
is the directory the benchmark points the indexer at.

### CPU-only PyTorch

`torch==2.2.0` installs the default wheel (GPU-capable on supported
platforms). For a CPU-only install (matching the reference environment's
CPU-only PyTorch), install torch separately from the CPU wheel index
before installing this project:

```bash
.venv/bin/pip install torch==2.2.0 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -e .
```

`tensorflow-cpu==2.16.1` is already a CPU-only distribution, so no extra
step is needed for TensorFlow.
