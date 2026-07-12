# Installing pydocs-mcp

## Quick install (most users)

```bash
pip install pydocs-mcp
```

Then run:

```bash
pydocs-mcp index /path/to/your/python/project
```

The default config uses `BAAI/bge-small-en-v1.5` via FastEmbed for
embeddings (no API key needed). On first run, FastEmbed downloads the
~80MB ONNX model to its cache directory.

### Air-gapped installs

pydocs-mcp and all required dependencies install from a wheelhouse with
no network access:

```bash
# On a connected machine (match the target's OS/arch/Python):
pip download pydocs-mcp -d ./wheelhouse
# On the air-gapped machine:
pip install --no-index --find-links ./wheelhouse pydocs-mcp
```

The live re-indexing dependency (`watchdog`) is part of the required set
and adds a single ~80–97 KB wheel to the wheelhouse; prebuilt wheels exist
for Linux (x86_64/aarch64/armv7l/i686/ppc64/ppc64le/s390x), macOS
(x86_64/arm64/universal2), and Windows — no compiler needed.

## System requirements

### Linux (Ubuntu / Debian)

[turbovec](https://github.com/RyanCodrai/turbovec)'s compiled wheel links
against the CBLAS C-ABI. Install OpenBLAS with the CBLAS interface:

```bash
sudo apt-get update
sudo apt-get install -y libopenblas-pthread-dev
```

Without this, `import pydocs_mcp` fails at runtime:

```
ImportError: turbovec/_turbovec.abi3.so: undefined symbol: cblas_sgemm
```

If the `libblas.so.3` alternative isn't selected after install, force it:

```bash
sudo update-alternatives --set libblas.so.3-x86_64-linux-gnu \
    /usr/lib/x86_64-linux-gnu/openblas-pthread/libblas.so.3
```

As a last-resort fallback, set `LD_PRELOAD` before running:

```bash
export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libopenblas.so.0
pydocs-mcp index .
```

### macOS

No additional packages — CBLAS is provided by the Accelerate framework.

### Windows

No additional packages — CBLAS is provided by the MSVC runtime.

## Development install

```bash
git clone https://github.com/msobroza/pydocs-mcp
cd pydocs-mcp
pip install -e .
pip install --group dev
```

If you have Rust installed and want the native acceleration module:

```bash
pip install maturin
maturin develop --release
```

The pure-Python fallback works without Rust; the native module just
speeds up the file-walking and parsing hot paths.

## GPU inference (optional)

Pass `--gpu` to `pydocs-mcp serve|index|watch` or to the benchmark runner to run
embedder inference on CUDA. It requires the GPU runtime for whichever embedder
you use (the CPU packages are the default):

- FastEmbed dense: `pip install fastembed-gpu` (replaces `fastembed`; the two
  conflict — install one).
- `sentence_transformers` dense provider: a CUDA build of torch (pulled by the
  `[sentence-transformers]` extra on a CUDA host). The extra does **not** pull
  torchvision; if a model load demands it, install a torchvision build that
  exactly matches your installed torch (torchvision exact-pins its torch
  sibling — e.g. torchvision 0.26.0 ↔ torch 2.11.0), or upgrade
  `transformers` to ≥ 5.10, which falls back to Pillow for image processors.
- PyLate late-interaction: a CUDA build of torch (already pulled by the
  `[late-interaction]` extra on a CUDA host).

`--gpu` is a runtime latency knob: it does not change retrieval results and
does not trigger a re-index (device is excluded from the index-cache key). With
the CPU runtimes installed, FastEmbed falls back to CPU; the
`sentence_transformers` and PyLate paths require real CUDA torch.
