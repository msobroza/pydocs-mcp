# Fix: Qwen3 + `sentence_transformers` provider demands torchvision at import

| Field    | Value                                                        |
|----------|--------------------------------------------------------------|
| Version  | 0.1 (draft)                                                  |
| Status   | Proposed                                                     |
| Date     | 2026-07-11                                                   |
| Audience | Implementers + reviewers                                     |
| Component| `python/pydocs_mcp/extraction/strategies/embedders/` + `pyproject.toml` (the `[sentence-transformers]` extra) |

## 1. Context & problem statement

### 1.1 The bug report

> "when I try to run the code with qwen3 embedder and sentence_transformers it
> gives me an error and asks me to install torchvision too."

The user configured the dense embedder as

```yaml
embedding:
  provider: sentence_transformers
  model_name: Qwen/Qwen3-Embedding-0.6B
  dim: 1024
```

(the exact recipe documented at `README.md:301-320` and shipped in
`benchmarks/configs/repoqa_dense_st.yaml:8-11`), installed the
`[sentence-transformers]` extra, and hit an error instructing them to install
**torchvision** — a package that appears **nowhere** in this repository
(`grep -n torchvision uv.lock` → 0 matches; 0 matches across all
`*.py`/`*.toml`/`*.md`/`*.yaml` in the repo outside this spec). torchvision
is not, and has
never been, part of the extra's declared payload:

```toml
# pyproject.toml:71
sentence-transformers = ["sentence-transformers>=5.0,<6.0", "transformers>=4.48"]
```

### 1.2 Root-cause analysis — the exact chain that produces "install torchvision"

The demand for torchvision is **never an install-time resolution** — it is a
runtime `requires_backends` raise inside `transformers`, surfaced (and made
user-visible as an install instruction) by `sentence-transformers`. Four links,
each verified against the versions pinned in `uv.lock` or resolved by a fresh
install:

**Link 1 — the extra leaves `transformers` unbounded above, so user installs
float across majors.** `pyproject.toml:71` pins
`sentence-transformers>=5.0,<6.0` but only `transformers>=4.48` (the lower
bound exists for ModernBERT support — comment at `pyproject.toml:69-70`; the
`[openvino]` extra at `pyproject.toml:75` and `[late-interaction]` at
`pyproject.toml:64` are equally unbounded above). `uv.lock` (dev/CI only —
users install from wheel metadata, not the lock) pins `sentence-transformers
5.5.1` (`uv.lock:4169-4170`), `transformers 5.0.0` (`uv.lock:4779-4780`),
`torch 2.11.0` (`uv.lock:4720-4721`). A fresh `pip install
'pydocs-mcp[sentence-transformers]'` today resolves ST 5.6.0 / transformers
5.13.0 / torch 2.13.0. Extras interplay moves the floor too: installing
`optimum[onnxruntime]` (optimum 2.1.0) into a lock-pinned venv silently
downgraded transformers 5.0.0 → 4.57.6. **User environments and CI diverge
arbitrarily inside `>=4.48`.**

**Link 2 — transformers 5.0.x–5.9.x hard-raise on any torchvision-backed
class.** transformers' base dependency list contains **no torch and no
torchvision** (`uv.lock:4782-4793`: filelock, huggingface-hub, numpy,
packaging, pyyaml, regex, safetensors, tokenizers, tqdm, typer-slim). Instead,
`transformers/utils/import_utils.py` wires a presence check into
`requires_backends`:

```
# transformers 5.0.0, import_utils.py:1610 + :1880
TORCHVISION_IMPORT_ERROR = "{0} requires the Torchvision library but it was
not found in your environment. Check out the instructions on the installation
page: https://pytorch.org/get-started/locally/ ..."
```

Any instantiation or lazy-module attribute resolution of a torchvision-backed
class (fast image processors, video/image utilities) raises this with **no
fallback** on transformers 5.0.0 (the lock pin). transformers 5.10.2 added a
graceful degradation: names ending in `ImageProcessor` whose missing backend
is torchvision transparently fall back to `<Name>Pil` with a warning
("Install torchvision to use the default backend") —
`import_utils.py:2213-2229` in 5.10.2; the block is absent in 5.0.0. So the
**hard-fail window for image-processor names is transformers 5.0.x through
<5.10** (exact introduction version of the fallback unverified; 5.11/5.12
untested).

**Link 3 — sentence-transformers ≥5.5 converts that raise into an explicit
"install" instruction.** ST 5.5.x introduced a multimodal load path:
`base/modules/transformer.py:661-665` loads the tokenizer via
`AutoProcessor.from_pretrained` wrapped in `suggest_extra_on_exception()`
(`util/environment.py:16-37`), which catches any ImportError/AttributeError
whose message contains `'torchvision'` and re-raises with:

```
To install the required dependencies, run:
pip install -U "sentence-transformers[image]"
```

This machinery is **absent in ST 5.3.0** (no `base/` package, 0 torchvision
grep hits). It is the only place in the stack that converts a
torchvision-mentioning failure into an install instruction — the message shape
matching "asks me to install torchvision too".

**Link 4 — pydocs-mcp's provider lets that error propagate raw, with no
actionable pydocs-side message.** `sentence_transformers.py:90-94` catches
only the top-level `from sentence_transformers import SentenceTransformer`
ImportError (re-raising `_INSTALL_HINT`, which names sentence-transformers +
torch + transformers but **not torchvision** —
`sentence_transformers.py:38-44`). The construction guard at lines 104-116
handles only **non-torch** backends (`if self.backend == "torch": raise` at
:107-108). A torchvision-mentioning ImportError raised *inside*
`SentenceTransformer(...)` construction on the default torch backend
propagates raw — the user sees upstream's message with no pydocs-anchored
remediation, and following ST's own printed hint **doesn't even work on the
lock-pinned transformers line** (4.x/5.0.x, §1.4).

### 1.3 CRITICAL caveat — reproduction was NOT achieved for Qwen3

Extensive local testing (macOS arm64, model pre-cached, torchvision confirmed
absent in every venv) could **not** reproduce the failure with
`Qwen/Qwen3-Embedding-0.6B`. All of the following PASS — import, construction,
and `encode_query`:

| Combo | ST | transformers | torch | Backend | Result |
|---|---|---|---|---|---|
| repo `.venv` | 5.3.0 | 5.3.0 | 2.9.0 | torch | PASS |
| demo `.venv` (external demo project) | 5.5.1 | 5.10.2 | 2.11.0 | torch | PASS |
| scratch venv at **exact uv.lock pins** | 5.5.1 | 5.0.0 | 2.11.0 | torch **and** openvino (optimum-intel 2.0.0, full auto-export) | PASS |
| transformers sweep | 5.5.1 | 5.2 / 5.4 / 5.6 / 5.8 | 2.11.0 | torch | PASS |
| transformers v4 line | 5.5.1 | 4.57.6 | 2.11.0 | torch | PASS |
| latest stack | 5.6.0 | 5.13.0 | 2.13.0 | torch | PASS |

The reason Qwen3 never trips the chain: `AutoProcessor.from_pretrained`
resolves `Qwen/Qwen3-Embedding-0.6B` to a **plain tokenizer**. Its
`tokenizer_config.json` declares `tokenizer_class: "Qwen2Tokenizer"` with no
`processor_class`, and the live Hub file list (verified via the HF API)
contains no `preprocessor_config.json` / processor files. The same holds for
`Qwen/Qwen3-Embedding-4B`, `codefuse-ai/F2LLM-v2-0.6B` / `-330M`, and
`Alibaba-NLP/gte-modernbert-base` — every model this repo documents for the
provider is a text-only repo, so no image-processing class is touched.

**Consequence for this spec:** the import chain in §1.2 is the only
torchvision-demand mechanism in the stack and is fully verified *as a
mechanism*, but the specific frame that raised in the user's environment is
unproven. The failing environment was not found on this machine; the Linux
CUDA benchmark box (`benchmarks/configs/repoqa_dense_st.yaml:9-11`, RTX 2080
Ti, torch CUDA wheels from a pytorch index URL where torch/torchvision skew is
plausible) or a fresh container is the likely locus. The spec therefore
recommends a fix that is **correct regardless of which frame raised**
(actionable catch-and-rethrow + docs + bounded pins) rather than one that
bakes in an unreproduced hypothesis (shipping torchvision). Web search
(GitHub issues, HF discussions) surfaced no published issue matching "Qwen3 +
sentence-transformers demands torchvision", so upstream offers no shortcut.

### 1.4 Secondary defect — the upstream remedy hint is broken

ST's suggested remedy does **not install torchvision** on the exact
transformers line this repo's lockfile pins: ST 5.5.1's `[image]` extra is
exactly `transformers[vision]`, and transformers' `vision` extra is
`Pillow<=15.0,>=10.0.1` **only** — torchvision sits in the separate
`torch-vision` extra — at both 4.57.6 and 5.0.0 (PyPI metadata; 5.0.0 is the
uv.lock pin). From transformers **5.1.0** the `vision` extra does include
torchvision (verified: 5.1.0 / 5.2.0 via PyPI metadata; 5.3.0 / 5.10.2 via
installed dist-info METADATA). So a user who obediently runs
`pip install -U "sentence-transformers[image]"` on a 4.x or 5.0.x stack gets
Pillow and the same error again — and even where the hint works (≥ 5.1) it
installs a torchvision that exact-pins torch (§1.5) with no warning about the
coupling. This is precisely why pydocs-mcp must own an actionable message at
its boundary instead of letting upstream's propagate.

### 1.5 Fix-space constraint — torchvision exact-pins torch

torchvision hard-pins an **exact** torch version: torchvision 0.28.0 requires
`torch==2.13.0`; 0.26.0 requires `torch==2.11.0` (PyPI metadata). Adding an
unbounded `torchvision` to the extra therefore couples/forces the resolved
torch version, can conflict with CUDA-specific torch index installs (the
INSTALL.md GPU path), and inflates any offline mirror by a wheel whose version
must exactly match the torch wheel — a skewed wheelhouse becomes unresolvable
offline. This is the central argument against Alternative A (§4.1).

### 1.6 Minimal reproduction snippet

Because the plain-Qwen3 path does not reproduce (§1.3), two snippets are
given: (a) the user-shaped invocation to run in the *failing* environment to
capture the raising frame, and (b) a mechanism repro that deterministically
triggers Link 2 + Link 3 on the lock-pinned stack, proving the chain exists.

**(a) User-shaped repro — run in the failing env, attach full traceback:**

```bash
python - <<'EOF'
import importlib.metadata as md, platform, sys
for p in ("sentence-transformers", "transformers", "torch", "torchvision"):
    try: print(p, md.version(p))
    except md.PackageNotFoundError: print(p, "ABSENT")
print(platform.platform(), sys.version)

from pydocs_mcp.extraction.strategies.embedders.sentence_transformers import (
    SentenceTransformersEmbedder,
)
emb = SentenceTransformersEmbedder(
    model_name="Qwen/Qwen3-Embedding-0.6B", dim=1024
)  # __post_init__ loads the model — the failure fires here
EOF
```

**(b) Mechanism repro — transformers 5.0.0 (the uv.lock pin), torchvision
absent, touch any torchvision-backed class:**

```bash
python -m venv /tmp/tv-repro && /tmp/tv-repro/bin/pip install \
    'sentence-transformers==5.5.1' 'transformers==5.0.0' 'torch==2.11.0'
/tmp/tv-repro/bin/python - <<'EOF'
from transformers.utils import requires_backends
class FakeImageProcessor: ...
requires_backends(FakeImageProcessor, ["torchvision"])
# ImportError: `FakeImageProcessor` requires the Torchvision library but it
# was not found in your environment. Check out the instructions on the
# installation page: https://pytorch.org/get-started/locally/ ...
EOF
```

Wrapping (b)'s raise in ST's `suggest_extra_on_exception` (what
`base/modules/transformer.py:981` does around `_call_processor`) appends the
`pip install -U "sentence-transformers[image]"` instruction — the full
user-visible message shape.

## 2. Goals / Non-goals

### Goals

1. Any torchvision-mentioning failure during `SentenceTransformersEmbedder`
   construction on the **torch** backend is re-raised with a pydocs-anchored,
   actionable message (what to install, with the torch-exact-pin caveat; what
   to upgrade; what to check) — closing Link 4.
2. Bound the `transformers` float in the `[sentence-transformers]` and
   `[openvino]` extras to the range actually validated by the local sweep,
   without breaking the uv.lock pins or the fresh-install resolution —
   narrowing Link 1.
3. Regression tests that run in the default venv (no torch / ST / torchvision
   installed), following the existing fake-module pattern in
   `tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py`.
4. Documentation updates in every place that names the extra's payload
   (README, INSTALL, pyproject comment, `_INSTALL_HINT`), including the
   air-gapped/offline implication (§6.3).
5. A defined escalation path once the user's traceback arrives (§7).

### Non-goals

- **Not** adding torchvision to the default extra payload (rejected —
  §4.1; the "default install stays ~90 MB / heavy deps are opt-in extras"
  policy in CLAUDE.md §Key Technical Details stands, and even the *extra's*
  weight budget shouldn't grow for an unreproduced failure).
- **No MCP surface or YAML config changes.** This is a dependency-metadata +
  error-surface fix; it introduces no tunable behavior. Per CLAUDE.md §"MCP
  API surface vs YAML configuration" the six task-shaped tools are untouched,
  and no new `AppConfig` key is warranted — there is nothing here to A/B test
  against a benchmark, so nothing belongs in YAML either.
- **Not** vendoring, shimming, or monkey-patching transformers'
  `requires_backends` (fragile across the very version float that caused the
  bug).
- **Not** changing the `[late-interaction]` extra's `transformers>=4.57.3`
  pin in this spec (PyLate has its own constraints; flagged as an open
  question, §7).

## 3. Detailed design

### 3.1 Module layout — changed files (no new modules)

```
pyproject.toml                              # extras pins + rationale comment
python/pydocs_mcp/extraction/strategies/embedders/sentence_transformers.py
                                            # _TORCHVISION_HINT + widened guard
tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py
                                            # new tests AC1-AC4b, AC6, AC7 (§5);
                                            # AC4a/AC5 preserved unmodified
README.md                                   # :301-304, :345, :351-361
INSTALL.md                                  # :84-94 (GPU section note)
uv.lock                                     # relock (bounds only; pins unchanged)
```

No new Protocols, services, or registry entries — the change lives entirely
inside one existing strategy class (CLAUDE.md §SOLID: Single Responsibility;
the embedder already owns its own import-failure surface at lines 90-116).
`build_embedder` (`embedders/__init__.py:14`; its `sentence_transformers`
branch at :55-74) and `EmbeddingConfig`
(`retrieval/config/embedder_models.py:46`) are untouched.

### 3.2 Code change — `sentence_transformers.py`

**New module constant** (single source of truth per CLAUDE.md §"Default
values" — the hint text appears once, referenced by code and asserted by
tests):

```python
_TORCHVISION_HINT = (
    "Constructing the SentenceTransformer model raised an error that "
    "mentions 'torchvision'. torchvision is NOT part of the "
    "'pydocs-mcp[sentence-transformers]' extra: recent transformers "
    "releases (5.0 <= v < 5.10) hard-require it for image-processing "
    "classes that some model repos reference. Remedies, in order:\n"
    "  1. Upgrade transformers to >= 5.10 (adds a Pillow fallback that "
    "removes the torchvision requirement for image processors):\n"
    "       pip install -U 'transformers>=5.10,<6'\n"
    "  2. Or install torchvision — NOTE it exact-pins its torch sibling "
    "(e.g. torchvision 0.26.0 <-> torch 2.11.0), so match your installed "
    "torch:\n"
    "       pip install torchvision\n"
    "  3. If embedding.model_name points at a multimodal repo by mistake, "
    "switch to a text-only embedding model (e.g. "
    "Qwen/Qwen3-Embedding-0.6B).\n"
    "Do NOT follow upstream's 'pip install -U sentence-transformers[image]' "
    "hint on transformers 4.x or 5.0.x — there that extra installs only "
    "Pillow and does not resolve the error (transformers' vision extra "
    "gained torchvision in 5.1)."
)


def _mentions_torchvision(exc: BaseException) -> bool:
    """True when exc or anything in its __cause__/__context__ chain names
    torchvision — ST's suggest_extra_on_exception re-raises with the
    original as __cause__, so the marker may sit one level down."""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if "torchvision" in str(cur).lower():
            return True
        cur = cur.__cause__ or cur.__context__
    return False
```

**Widened construction guard** — the `try` around
`SentenceTransformer(self.model_name, **ctor_kwargs)`
(currently `sentence_transformers.py:102-116`) becomes:

```python
try:
    self.model = SentenceTransformer(self.model_name, **ctor_kwargs)
except (ImportError, AttributeError) as e:
    # ST >= 5.5 routes model loading through AutoProcessor wrapped in
    # suggest_extra_on_exception(); a missing torchvision surfaces as an
    # ImportError (or AttributeError from lazy-module resolution) whose
    # chain mentions 'torchvision'. Upstream's own remedy hint is broken
    # on transformers 4.x/5.0.x (there its [image] extra == Pillow only),
    # so we own an actionable message here. See spec
    # docs/superpowers/specs/2026-07-11-sentence-transformers-torchvision-bug-spec.md
    if _mentions_torchvision(e):
        raise ImportError(_TORCHVISION_HINT) from e
    if not isinstance(e, ImportError) or self.backend == "torch":
        raise
    # A non-torch backend fails construction when optimum / openvino
    # aren't installed — surface the extras hint instead of the deep
    # import error.  (unchanged behavior, lines 109-116 today)
    raise ImportError(
        f"embedding.backend: {self.backend} requires the matching "
        "sentence-transformers extra. Install with: pip install "
        f"'sentence-transformers[{self.backend}]'"
    ) from e
```

Control-flow invariants (each is a test in §5):

- torchvision-mentioning failure → `_TORCHVISION_HINT`, **any backend**
  (checked first — a torchvision error on the openvino backend must not be
  misdiagnosed as a missing-optimum problem).
- Non-torchvision ImportError on `backend == "torch"` → propagates raw
  (today's behavior, preserved; pinned by a NEW guardrail test in AC4(b) —
  the existing `test_torch_backend_construction_failure_propagates_raw`
  injects a `RuntimeError`, which never enters the guard at all, so it
  alone cannot prove this path).
- Non-torchvision ImportError on a non-torch backend → the existing
  `sentence-transformers[<backend>]` hint (today's behavior, preserved).
- AttributeError that does **not** mention torchvision → propagates raw
  (we widen the catch only to inspect, never to swallow).

`AttributeError` joins the catch because transformers' lazy-module attribute
resolution can surface the missing backend as an AttributeError; ST's own
`suggest_extra_on_exception` catches exactly this pair
(`util/environment.py:16-37`), and we mirror its contract.

**`_INSTALL_HINT` update** (`sentence_transformers.py:38-44`) — append one
sentence so the top-level hint acknowledges the caveat:

```python
_INSTALL_HINT = (
    "The 'sentence_transformers' embedding provider requires the "
    "'sentence-transformers' extra. Install with: "
    "pip install 'pydocs-mcp[sentence-transformers]' (pulls "
    "sentence-transformers + torch + transformers; expect ~1-5 GB "
    "depending on CUDA wheel selection). torchvision is NOT included; "
    "if a model load demands it, see the error raised at construction "
    "time for remedies."
)
```

The class stays a plain `@dataclass` (it mutates `self.model` in
`__post_init__`/`close`, so `frozen=True` does not apply here — pre-existing,
deliberate). No async change: construction already happens before any event
loop involvement, and the CLAUDE.md async rules (`asyncio.to_thread` for
blocking work) already govern `embed_query`/`embed_chunks`, untouched.

### 3.3 Data models / YAML config surface

**None.** No new dataclass fields, no `EmbeddingConfig` change, no
`AppConfig` key, no pipeline YAML change. The YAML litmus test from CLAUDE.md
("if a new behavior could be A/B tested against a benchmark, it belongs in
YAML") returns *no*: an error message and a dependency bound are not
retrieval behavior. The MCP surface (six tools) is untouched.

### 3.4 `pyproject.toml` change — exact diff

```diff
 # Torch-backed on-device dense embedder (Qwen3-Embedding via
 # sentence-transformers). torch frees CUDA memory across sequential
 # index-builds, so it stays GPU-reliable over a benchmark sweep.
 # Heavy (~1-5 GB with torch). Opt-in; default install stays ~90 MB.
 # transformers>=4.48 is required for ModernBERT (Alibaba-NLP/gte-modernbert-base);
 # the bare sentence-transformers>=5.0 pin alone can resolve an older transformers.
-sentence-transformers = ["sentence-transformers>=5.0,<6.0", "transformers>=4.48"]
+# transformers<6 caps the float to the majors validated against this provider
+# (4.57 and 5.x); transformers 5.0-5.9 hard-raise "requires the Torchvision
+# library" on torchvision-backed classes with no fallback (>=5.10 falls back
+# to Pillow), and torchvision is deliberately NOT a dependency here — it
+# exact-pins its torch sibling (e.g. 0.26.0 <-> torch==2.11.0), which would
+# couple our torch resolution and break CUDA-index and air-gapped installs.
+# The provider re-raises torchvision-mentioning construction failures with
+# an actionable hint (extraction/strategies/embedders/sentence_transformers.py).
+sentence-transformers = ["sentence-transformers>=5.0,<6.0", "transformers>=4.48,<6.0"]
 # OpenVINO CPU inference for the sentence_transformers provider
 # (embedding.backend: openvino, optionally + a qint8 model_file_name).
 # Pulls optimum-intel + openvino on top of the torch stack.
-openvino = ["sentence-transformers[openvino]>=5.0,<6.0", "transformers>=4.48"]
+openvino = ["sentence-transformers[openvino]>=5.0,<6.0", "transformers>=4.48,<6.0"]
```

Why `<6.0` and not a `5.0–5.9` window exclusion:

- The window exclusion (`!=5.0.*` … `!=5.9.*`) encodes the hypothesis that
  the user hit the hard-fail window — **unproven** (§1.3: every window
  version tested passes for Qwen3), and it would forbid the exact
  `transformers==5.0.0` that `uv.lock` pins and CI runs, forcing a relock and
  invalidating the currently-green gate for a speculative benefit.
- `<6.0` is resolver-safe: it matches ST 5.5.1's own core range
  (`transformers<6.0.0,>=4.41.0` — verified via `importlib.metadata`), keeps
  the lock pin (5.0.0), the demo env (5.10.2), the optimum downgrade path
  (4.57.6), and today's fresh resolve (5.13.0) all inside the range, and
  blocks only the *untested future major* — the same class of unbounded float
  that produced this bug.
- Single source of truth (CLAUDE.md §"Default values"): the bound lives once
  per extra in `pyproject.toml`; docs reference the *range*, not re-pinned
  literals.

Post-diff: `uv lock` (the `uv lock --check` CI gate requires the lockfile to
match `pyproject.toml`; per the project memory, macOS relocks are CI-valid).
Expect a metadata-only lock change — all locked versions already satisfy the
new bound.

### 3.5 Control flow (end to end, after the fix)

```
AppConfig YAML (embedding.provider: sentence_transformers)
  └─ build_embedder (embedders/__init__.py:14; ST branch :55-74) — lazy import, unchanged
       └─ SentenceTransformersEmbedder.__post_init__
            ├─ local_model_dir / enable_hf_offline (air-gap, unchanged)
            ├─ from sentence_transformers import SentenceTransformer
            │     └─ ImportError → _INSTALL_HINT   (unchanged, text extended)
            └─ SentenceTransformer(model_name, **ctor_kwargs)
                  ├─ (ImportError|AttributeError) chain mentions "torchvision"
                  │     → raise ImportError(_TORCHVISION_HINT) from e   [NEW]
                  ├─ ImportError, backend == "torch" → raise  (unchanged)
                  ├─ ImportError, backend != "torch"
                  │     → sentence-transformers[<backend>] hint (unchanged)
                  └─ AttributeError, no torchvision → raise    [NEW, pass-through]
```

## 4. Alternatives considered

### 4.1 Alternative A — add `torchvision` to the `[sentence-transformers]` extra

```toml
sentence-transformers = ["sentence-transformers>=5.0,<6.0", "transformers>=4.48", "torchvision"]
```

**Pros**

- Makes the reported error impossible by construction, whatever frame raised
  it — zero code change.
- Simple to explain; one-line diff; upstream itself added torchvision to
  transformers' `vision` extra at 5.1, signaling ecosystem direction.

**Cons**

- **Torch coupling:** torchvision exact-pins torch (0.28.0 ↔ `torch==2.13.0`,
  0.26.0 ↔ `torch==2.11.0`). The extra would silently dictate the torch
  version, and a user installing CUDA torch from the pytorch index
  (INSTALL.md:84-94 GPU path) can end up with an unsatisfiable or silently
  CPU-downgraded pair.
- **Air-gap wheelhouse impact:** every offline mirror must now carry a
  torchvision wheel whose version exactly matches the torch wheel; any skew
  makes the extra unresolvable offline (§6.3).
- **Weight:** adds a wheel (tens to ~hundreds of MB with CUDA deps) to an
  extra whose budget is already flagged "~1-5 GB" — for a dependency that
  **no documented model needs** (§1.3: all five documented models are
  text-only) and a failure we could not reproduce.
- Violates the spirit of the opt-in-extras policy: paying a permanent
  dependency-coupling cost to suppress an unconfirmed, version-window-scoped
  error.

**Verdict: rejected** as the primary fix. Revisit only if the user's
traceback (§7) proves torchvision is genuinely required on a supported path.

### 4.2 Alternative B — upper-bound / window-exclude `transformers`

Variants: (B1) `transformers>=4.48,<5.0` (duck under the whole v5 line);
(B2) `transformers>=4.48,!=5.0.*,…,!=5.9.*` (excise the hard-fail window);
(B3) `transformers>=4.48,<6.0` (cap the major).

**Pros**

- Pure metadata; no runtime cost; addresses Link 1 (uncontrolled float)
  directly.
- B2 would guarantee users never land in the no-fallback window.

**Cons**

- B1 breaks the lock (`transformers==5.0.0`) and the demo env (5.10.2), and
  contradicts the local evidence that v5 works for every documented model.
- B2 also forbids the lock pin (5.0.0 is inside the excluded window), forces
  a relock + CI churn, is ugly to maintain (ten `!=` clauses), and — decisive
  — encodes the unproven hypothesis that the window caused the report; every
  window version tested **passes** for Qwen3 (§1.3). The fallback's exact
  introduction version is also unverified (present 5.10.2, absent 5.0.0,
  5.11/5.12 untested), so the exclusion boundary itself is guesswork.
- B3 alone doesn't fix the reported error (the window is inside `<6.0`).

**Verdict: B3 adopted as hygiene** (part of the recommended fix, §4.5), B1/B2
rejected.

### 4.3 Alternative C — lazy-import guard / env-var opt-out in transformers

**Investigated and unavailable.** transformers offers **no env-var opt-out**:
the mechanism is `requires_backends(...)` driven by
`is_torchvision_available()` — a pure package-presence check
(`import_utils.py:735-741` in 5.10.2). The only mitigations transformers
ships are (a) the ≥5.10 Pillow fallback for `*ImageProcessor` lazy-module
attribute access and (b) importing the `*ImageProcessorPil` class directly —
neither is reachable from pydocs-mcp's side of the boundary (we call ST, not
transformers, and ST chooses the class). Our imports are already maximally
lazy: ST is imported inside `__post_init__` (`sentence_transformers.py:90-94`)
and torch only inside `close()` under `contextlib.suppress`
(:163-167), so a default install never touches the stack.

**Pros:** would have been zero-cost. **Cons:** does not exist.
**Verdict: not applicable** — collapses into documenting "upgrade
transformers ≥ 5.10" inside `_TORCHVISION_HINT`.

### 4.4 Alternative D — catch-and-rethrow with an actionable message

The §3.2 design: widen the existing construction guard (the lines 104-116
pattern already established for non-torch backends) to detect
torchvision-mentioning failures on any backend and re-raise with
`_TORCHVISION_HINT`.

**Pros**

- Fixes the *actual* user experience defect that is proven: pydocs-mcp
  currently propagates a raw upstream error whose own remedy hint is broken
  on the lock-pinned transformers line (4.x/5.0.x, §1.4).
- Correct regardless of which frame raised — mechanism-level, not
  hypothesis-level.
- Zero dependency, zero weight, zero air-gap impact; consistent with the
  provider's existing error-surface ownership (`_INSTALL_HINT`, backend
  hint); testable today in the default venv with the existing fake-ST
  infrastructure.

**Cons**

- Does not *prevent* the underlying condition — the user still has to act on
  the message (install torchvision / upgrade transformers).
- Message-content matching (`"torchvision" in str(exc)`) is heuristic;
  mitigated by walking the full `__cause__`/`__context__` chain and by the
  fact that ST's own `suggest_extra_on_exception` uses the identical
  substring contract, so the marker is stable upstream API-adjacent behavior.

**Verdict: adopted** as the primary fix.

### 4.5 Recommendation

**D (catch-and-rethrow, §3.2) + B3 (`transformers<6.0` cap on both ST-family
extras, §3.4) + docs (§6), shipped together; A held in reserve** behind the
open question of the user's traceback. This is the only combination that (a)
improves the failure experience for every possible raising frame, (b) narrows
the version float that created the CI/user divergence, and (c) adds no
weight, no torch coupling, and no air-gap burden for an unreproduced failure.

## 5. Testing & acceptance criteria

All new tests live in
`tests/extraction/strategies/embedders/test_sentence_transformers_embedder.py`
and run in the default venv — **no torch / sentence-transformers /
torchvision installed** (the module's existing contract, docstring lines 1-6).
They reuse `_install_fake_st_module(monkeypatch, records, fail=...)` (line
151), which fabricates a fake `sentence_transformers` module in `sys.modules`
and can make the fake `SentenceTransformer.__init__` raise a chosen exception.
The autouse `build_embedder → MockEmbedder` patch in `tests/conftest.py` is
irrelevant here (these tests construct the embedder class directly), and none
of them need the `real_embedder` marker (`pyproject.toml:165-171`).

Numbered acceptance criteria — each independently checkable:

- **AC1 — torchvision failure gets the hint (torch backend).**
  `_install_fake_st_module(..., fail=ImportError("`SomeImageProcessorFast`
  requires the Torchvision library but it was not found in your
  environment"))`; constructing
  `SentenceTransformersEmbedder(model_name="Qwen/Qwen3-Embedding-0.6B",
  dim=1024)` raises `ImportError` whose message is `_TORCHVISION_HINT`
  (assert key fragments: `"torchvision"`, `"transformers>=5.10"`,
  `"exact-pins"`, and the warning against `sentence-transformers[image]`),
  with `__cause__` set to the original error.
- **AC2 — chained torchvision failure is detected.** Same as AC1 but `fail`
  is `ImportError("To install the required dependencies, run: pip install -U
  \"sentence-transformers[image]\"")` whose `__cause__` is an
  `ImportError("... requires the Torchvision library ...")` — i.e. the exact
  shape ST's `suggest_extra_on_exception` produces. The chain walk
  (`_mentions_torchvision`) must find the marker one level down and raise
  `_TORCHVISION_HINT`. (If the outer ST message itself is deemed to contain
  "torchvision" via the extra name, keep the test but make the outer message
  torchvision-free to force a genuine chain walk.)
- **AC3 — torchvision failure on a non-torch backend still gets the
  torchvision hint,** not the `sentence-transformers[openvino]` hint:
  construct with `backend="openvino"` and a torchvision-mentioning `fail`;
  assert `_TORCHVISION_HINT`, not the backend-extra message.
- **AC4 — raw propagation on the torch backend preserved.** Two parts:
  (a) `test_torch_backend_construction_failure_propagates_raw` (line 218)
  still passes unmodified — it injects `RuntimeError("boom")`, which must
  never enter the widened `(ImportError, AttributeError)` guard at all;
  (b) a NEW guardrail test injects `fail = ImportError("No module named
  'foo'")` (no torchvision mention) on the default torch backend and asserts
  the same ImportError escapes un-rewrapped. (b) is required because the
  guard rewrite reroutes exactly this case through
  `if not isinstance(e, ImportError) or self.backend == "torch": raise` —
  the existing RuntimeError test cannot pin it.
- **AC5 — existing backend hint preserved.**
  `test_nontorch_backend_construction_failure_gets_install_hint` (line 206)
  still passes unmodified.
- **AC6 — non-torchvision AttributeError propagates raw.** `fail =
  AttributeError("module 'transformers' has no attribute 'Whatever'")` on the
  torch backend → the same `AttributeError` escapes (identity or message
  match), proving the widened catch never swallows unrelated errors.
- **AC7 — import-time cleanliness (regression for the chosen fix).**
  `import pydocs_mcp.extraction.strategies.embedders.sentence_transformers`
  succeeds in the default venv and afterwards `"torchvision" not in
  sys.modules and "torch" not in sys.modules and "sentence_transformers" not
  in sys.modules` — the provider module itself must never demand the heavy
  stack at import time. (Had Alternative A been chosen instead, this AC would
  flip to a metadata test: `importlib.metadata.requires("pydocs-mcp")`
  contains `torchvision; extra == "sentence-transformers"`. Recorded here per
  the bug-spec requirement that the regression test tracks the chosen fix.)
- **AC8 — pyproject bounds.** A test (or the `uv lock --check` gate plus a
  small metadata assertion in `tests/`) verifies both ST-family extras pin
  `transformers>=4.48,<6.0`: parse `pyproject.toml`
  (`tomllib`) and assert the requirement strings for
  `project.optional-dependencies.sentence-transformers` and `.openvino`.
  Guards against a future edit silently re-unbounding the float.
- **AC9 — full CI gate set green** (per CLAUDE.md §Tests & Lint and the
  CI-gates memory): `pytest tests/ --ignore=tests/test_parity.py
  --cov=pydocs_mcp --cov-fail-under=90`, `ruff check` + `ruff format --check`,
  `mypy python/pydocs_mcp`, `complexipy` ≤ 15 (the widened guard must not push
  `__post_init__` over — extract `_mentions_torchvision` as a module helper,
  as designed, to keep it flat), `vulture`, `uv lock --check`, `pip-audit`.
- **AC10 — docs updated** exactly at the audited sites: README.md:301-304
  (extra payload sentence gains the "torchvision NOT included" caveat),
  README.md:345 (transformers range now "≥ 4.48, < 6"), README.md:351-361
  (air-gap section gains the §6.3 note), INSTALL.md:84-94 (GPU section:
  torchvision caveat + exact-pin warning next to the CUDA-torch note),
  pyproject.toml comment (in the §3.4 diff), `_INSTALL_HINT` (in §3.2). No
  README acquires internal PR/task jargon (CLAUDE.md §README rule; the spec
  file is the sanctioned home for the history). Vendor neutrality: no
  third-party AI coding-assistant product names appear in any touched doc
  ("AI coding assistants" generically).

TDD ordering (CLAUDE.md §Design Patterns, TDD): AC1–AC3 land as failing
tests first (red), then §3.2 turns them green. AC4(b), AC6, and AC7 are
characterization guardrails — the paths they pin already behave this way
today, so they are green before AND after the change; they land in the same
commit as §3.2 to hold the widened guard honest. AC8 lands as a failing test
first, then §3.4. AC4(a)/AC5 are the do-not-break guardrails run
continuously.

## 6. Rollout / migration / back-compat

### 6.1 Compatibility

- **No behavior change on any success path.** The widened guard only alters
  what is raised when construction already fails. Constructor kwargs are
  byte-identical (the "Pass backend/model_kwargs ONLY when non-default"
  invariant at `sentence_transformers.py:96-101` is untouched).
- **No index invalidation.** Nothing here touches embedder identity,
  `pipeline_hash`, or vectors — existing `.db`/`.tq` sidecars are unaffected.
- **Resolver back-compat of `transformers<6.0`:** satisfied by the uv.lock
  pin (5.0.0), the demo env (5.10.2), the optimum-downgraded env (4.57.6),
  and today's fresh resolve (5.13.0). ST 5.5.1 itself already imposes `<6.0.0`,
  so no currently-valid environment is excluded. Users who somehow hold
  transformers 6.x (none exists today) would see a resolver conflict at
  upgrade time instead of undefined runtime behavior — the intended trade.
- **Exception-type stability:** callers that caught `ImportError` from
  construction still catch the new hint (it *is* an ImportError, `from e`
  chained). The only observable change is message text plus the new
  AttributeError pass-through, which previously escaped as-is anyway.

### 6.2 Rollout steps

1. Land tests + code + pyproject + relock + docs as **one PR** (single
   reviewable unit; the pyproject comment cross-references the code file).
2. Reply to the user with the §1.6(a) snippet, requesting the full traceback
   and `pip freeze` from the failing environment (platform, CUDA vs CPU,
   model_name, backend, entry point — `pydocs-mcp index/serve`,
   `ask-your-docs`, or the benchmark runner).
3. Run the benchmark-box sanity check: on the Linux RTX 2080 Ti host
   (`benchmarks/configs/repoqa_dense_st.yaml`), fresh venv, install the extra,
   run the §1.6(a) snippet — the most plausible skew locus (§1.3).
4. If the traceback proves a torchvision-requiring frame on a supported
   text-only model path, escalate per §7 (Alternative A or a targeted
   window exclusion becomes evidence-based).

### 6.3 Air-gapped / offline implication (documentation requirement)

This repo's air-gap support today is **model-side only**: side-loaded weight
directories via `local_model_dir` + `enable_hf_offline`
(`extraction/strategies/embedders/local_source.py:13-43`, README.md:351-361);
there is **no wheelhouse/offline-pip document in the repo** (`grep -i
wheelhouse INSTALL.md documentation/` → 0 matches). Two notes must land:

- **Under the recommended fix (no new dep):** README.md:351-361 gains one
  sentence — the `[sentence-transformers]` extra's Python-package set is
  unchanged (no torchvision), so existing offline package mirrors stay valid;
  if an operator's environment does hit the torchvision demand, their mirror
  must add a torchvision wheel whose version **exactly matches** the mirrored
  torch wheel (0.26.0↔2.11.0, 0.28.0↔2.13.0), since a skewed pair is
  unresolvable offline.
- **If Alternative A is ever adopted:** this becomes a breaking change for
  every existing offline mirror (a new transitive wheel + an exact-pin
  constraint), and MUST ship with an INSTALL.md offline-install section — a
  design-doc-level consideration recorded here so it cannot land as a silent
  one-line pyproject edit.

## 7. Open questions

1. **The user's exact traceback + environment** (pip freeze:
   transformers/ST/torch versions, OS, CUDA vs CPU, `model_name`, `backend`,
   entry point). Without it the raising frame is unproven — no tested
   combination reproduces the failure with `Qwen/Qwen3-Embedding-0.6B`
   (§1.3). This gates any escalation to Alternative A.
2. **Was the failing host the Linux GPU benchmark box** (RTX 2080 Ti; CUDA
   torch wheels from a pytorch index URL, where torch/torchvision/transformers
   skew is plausible) or a fresh container — versus this macOS machine where
   every combination passes? (§6.2 step 3 answers this cheaply.)
3. **Was the model actually `Qwen/Qwen3-Embedding-4B` via
   `examples/ask_your_docs_agent/configs/serve_cpu_openvino.yaml`** (openvino
   backend, auto-export) rather than the 0.6B? 4B was not exercised locally
   (8 GB download); its Hub repo is equally text-only, so the same non-repro
   is expected — but unverified.
4. **Which ST version introduced the `AutoProcessor` +
   `suggest_extra_on_exception` path** (present 5.5.1, absent 5.3.0, 5.4.x
   untested) — matters only if a future escalation pins ST below the
   multimodal refactor instead of bounding transformers.
5. **Exact transformers version introducing the Pillow fallback** (present
   5.10.2, absent 5.0.0; 5.11/5.12 untested) — bounds the hard-fail window a
   window-exclusion fix (B2) would need, should evidence ever justify it. The
   `_TORCHVISION_HINT` text says "≥ 5.10"; if the boundary turns out to be
   5.11, the constant is the single place to correct.
6. **Should `[late-interaction]`'s `transformers>=4.57.3` gain the same
   `<6.0` cap?** Same unbounded-float risk, different consumer (PyLate).
   Out of scope here (§2 Non-goals); needs a PyLate-compatibility check
   before mirroring the bound.
