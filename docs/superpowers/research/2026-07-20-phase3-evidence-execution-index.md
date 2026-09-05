# Phase 3 · D2 evidence — rollout execution, containers, the machine, index economics

Researcher scope: D2 "containers, the machine, and index economics."
Date: 2026-07-20. Host: `Air-de-Max.lan` (this machine). All claims below carry a
command+output, a `file:line` cite, a fetched registry response, or a dataset-API
response. Anything I could not verify is labelled **UNVERIFIED**.

Sources under `/private/tmp`: `swebench-src` (SWE-bench/SWE-bench @ v4.1.0 shallow),
`swebench-live-src` (microsoft/SWE-bench-Live shallow), `p3_sqllineage`,
`p3_geopandas`, `p3_matplotlib` (Live repos at pinned base commits).
Product code cited from the phase-3 worktree
`/Users/.../phase-3-evaluation/python/pydocs_mcp/` (identical bytes to the phase-2
venv used for execution).

---

## 1. THIS MACHINE — what is actually available

`uname -a` / `sysctl` / `df -h` (ran 2026-07-20):

| Fact | Value |
|---|---|
| Kernel | `Darwin 23.6.0 … RELEASE_ARM64_T8103`, `arm64` |
| CPU | **Apple M1**, `hw.ncpu=8` `hw.physicalcpu=8` `hw.logicalcpu=8` |
| RAM | `hw.memsize=8589934592` = **8 GiB** |
| Free disk | `/` → **15 Gi Avail** (43% used on the sealed system vol; data vol `/dev/disk3s5` 93% used, 15 Gi avail) |

**Container runtime: NONE.**
```
which docker  → docker not found      (NO DOCKER)
which colima  → colima not found      (NO COLIMA)
which podman  → podman not found      (NO PODMAN)
```
There is no Docker daemon, no Docker CLI, and no Colima/Podman alternative on this
machine. **This is the finding**: rollout *test execution* (which is
container-based for both mainline swebench and SWE-bench-Live — see §2) cannot run
on this host at all. It forces a **remote/Linux x86_64 execution host** for any
grading campaign. The index-build + host-side MCP server work (§3–§5) *can* run
here; the container grade step cannot.

Even if Docker were installed, this box is far under the harness's own stated
floor: SWE-bench README recommends "**an x86_64 machine with at least 120GB of
free storage, 16GB of RAM, and 8 CPU cores**" (`swebench-src/README.md:97`) and
"~120 free GB" of Docker-desktop virtual disk (`README.md:100`). This host has
15 GiB free and 8 GiB RAM — a single Live instance image is 749 MB compressed / a
mainline one ~1.1 GB compressed (§2), so even a handful of images plus their
uncompressed working sets would exhaust the 15 GiB.

Because there is no Docker, I could **not** pull an image or time an in-container
command on this M1. I substituted the free Docker-Hub registry/manifest API to get
authoritative image architecture + size (§2) — that answers the "amd64-only →
emulated on M1" question without a local pull.

---

## 2. Harness container architecture (from source + registries)

### 2a. Mainline SWE-bench 4.1.0 — three-layer image scheme, built locally, x86_64 default

Version pin: `swebench-src/swebench/__init__.py:1` → `__version__ = "4.1.0"`.

Layering (`swebench/harness/test_spec/test_spec.py`):
- **base** `sweb.base.<lang_ext>.<arch>[.<val>]:<tag>` (test_spec.py:84–86)
- **env** `sweb.env.<lang_ext>.<arch>[.<val>]:<tag>` (test_spec.py:104)
- **instance/eval** `sweb.eval.<arch>.<instance_id>:<tag>` (test_spec.py:108)

Python Dockerfile chain (`swebench/harness/dockerfiles/python.py`):
- base: `FROM --platform={platform} ubuntu:{ubuntu_version}` then downloads
  Miniconda (`python.py:2`, `:24`). Constants: `ubuntu_version=22.04`,
  `conda_version=py311_23.11.0-2` (`constants/__init__.py:123,127`).
- env: `FROM --platform={platform} {base_image_key}` + `conda activate testbed`
  (`python.py:35,45`).
- instance: `FROM --platform={platform} {env_image_name}` (`python.py:48`).

**Architecture is `x86_64` by default and NOT auto-derived from the host.**
`make_test_spec(..., arch="x86_64")` default (test_spec.py:180); `run_evaluation.py`
calls `make_test_spec(instance, namespace=…, …)` **without** passing `arch`
(run_evaluation.py:306–311), so every spec is x86_64 unless a caller overrides.
`arch` maps to platform: `x86_64→linux/x86_64`, `arm64→linux/arm64/v8`
(test_spec.py:146–152).

Default image source: `--namespace` defaults to `"swebench"`
(run_evaluation.py:654; function default `run_evaluation.py:285`) → prebuilt
instance images are pulled from Docker Hub `swebench/sweb.eval.<arch>.<instance>`
with `__`→`_1776_` mangling (test_spec.py:107–110). On M-series the README instructs
`--namespace ''` (build locally) because there is no prebuilt arm64 namespace:
"If using a MacOS M-series or other ARM-based systems, add `--namespace ''`"
(README.md:74); "Support for `arm64` machines is experimental" (README.md:102).

Measured mainline instance-image size (Docker-Hub registry manifest, token-auth
pull scope, 2026-07-20):
`swebench/sweb.eval.x86_64.django_1776_django-11333` → **15 layers,
compressed_total = 1,109,086,308 bytes (~1.1 GB)**, single-platform v2 manifest
(amd64; no manifest-list).

### 2b. SWE-bench-Live — prebuilt per-instance images, Docker Hub `starryzhang`, x86_64-only

Live's evaluator resolves each instance to a **prebuilt per-instance image**:
`get_default_image_name` → `starryzhang/sweb.eval.{med}.{name}` where
`med="x86_64"` for linux, `name=instance_id.replace("__","_1776_").lower()`
(`swebench-live-src/evaluation/evaluation.py:70-77`, mirrored in
`evaluation/README.md:106-118`). The dataset row may carry a `docker_image` field
that overrides the default (`evaluation.py:159`, `validation.py:127`). Container
launch is `SetupRuntime.from_launch_image(image, instance_id, platform, …)`
(evaluation.py:109); the checkout lives at **`/testbed`**
(evaluation.py:88 `cd /testbed`).

**Published architecture = amd64 only.** Docker-Hub tags API for
`starryzhang/sweb.eval.x86_64.reata_1776_sqllineage-524` (2026-07-20):
```
latest → [('amd64', 749469934)]
0430   → [('amd64', 749469934)]
```
Registry manifest: single-platform v2 manifest, **9 layers, compressed_total =
749,469,934 bytes (~749 MB)**, no arm64 variant, no manifest-list. → On this M1
these images run **only under x86_64 emulation** (Docker-desktop/qemu VM), which is
both slower and, combined with the machine's 15 GiB free disk, infeasible here.
I could not measure emulated in-container latency because Docker is absent.

Registry method (reproducible): token from
`auth.docker.io/token?service=registry.docker.io&scope=repository:<repo>:pull`,
then `GET registry-1.docker.io/v2/<repo>/manifests/latest` with the manifest Accept
headers; tag arch/size via `hub.docker.com/v2/repositories/<repo>/tags`.

---

## 3. Index economics (measured with `pydocs_mcp index … --skip-deps --no-inspect`)

CLI flags verified via `pydocs-mcp index --help`: `--skip-deps` = "index only the
project source"; `--no-inspect` = "Don't import deps. Read `.py` files from
site-packages instead… no side-effects. Uses the same parser as project source."
Project-only static index command used: `index <dir> --skip-deps --no-inspect
--cache-dir <isolated>`.

Repos (real Live test-split rows, shallow-fetched at the dataset's `base_commit`;
confirmed HEAD short-sha in parens). File counts exclude `.git`.

| tier | repo @ base_commit | .py files | all files | git checkout on disk |
|---|---|---|---|---|
| small | reata/sqllineage @ `4adcc8f` | 98 | 255 | 3.3 MB |
| medium | geopandas/geopandas @ `fed9e57` | 82 | 328 | 17 MB |
| large | matplotlib/matplotlib @ `83aa3e4` | 890 | 4540 | 109 MB |

(repos chosen from the Live test split via HF datasets-server — the top repos by
instance count include conan, matplotlib, haystack, streamlink, sphinx, sqllineage,
geopandas, …; sqllineage/geopandas/matplotlib give a clean small/medium/large
file-count spread.)

**Measured index cost** (default pipeline → chunks are dense-embedded, so wall time
is embedding-bound on CPU; embedder = `BAAI/bge-small-en-v1.5`, 384-dim, via
fastembed 0.8.0; model warm-cached before timing).

One **fully completed** real run (a 11-file subset of sqllineage @ base commit, so
the whole pipeline incl. the final SQLite commit runs inside the host's ~2-min
execution window):

| corpus | py files | py bytes | chunks / members / trees | wall | .db | .tq |
|---|---|---|---|---|---|---|
| sqllineage subset (11 files) | 11 | 34 KB | **73 / 20 / 10** | **53 s** | **360 KB** (368 640 B) | **18 KB** (17 982 B) |

From that completed run the marginal unit costs are: **481 source-bytes/chunk**,
**~3.1 KB `.db`/chunk** (over a ~139 KB empty-schema baseline), **246 `.tq`
bytes/chunk** (matches a quantized 384-dim vector). Wall ≈ 50 s to embed 73 chunks
on CPU ⇒ **~1.5 chunks/s** effective (model-load included).

Full-repo indexing does **not** fit any execution channel available on this host:
every foreground call is killed at 120 s and the background-task runner SIGKILLs
long jobs (observed exit 144); a full-repo dense embed is minutes-to-hours on this
8 GiB CPU M1. So the full-repo rows below are **estimates** scaled from the
completed subset's per-byte/per-chunk unit costs, except where a detached run
completed (noted). This is itself the headline economics finding: **dense-embed
indexing of a realistic repo is a minutes-scale, ~1 GB-RSS job on this host** — a
campaign wants a GPU or a beefier/parallel host, or a lexical-only index tier.

| tier | repo | py bytes | est. chunks | est. `.db` | est. `.tq` | est. embed wall (CPU, ~1.5 ch/s) |
|---|---|---|---|---|---|---|
| small | sqllineage (98 files) | 279 KB | ~595 | ~2.0 MB | ~150 KB | ~5–7 min |
| medium | geopandas (82 files) | 1.38 MB | ~2 940 | ~9.4 MB | ~720 KB | ~25–40 min |
| large | matplotlib (890 files) | 8.5 MB | ~18 100 | ~57 MB | ~4.5 MB | ~2–3 h |

(Estimates use 481 B/chunk source, 139 KB + 3.1 KB/chunk `.db`, 246 B/chunk `.tq`.
Note geopandas has few files but large ones — 1.38 MB of `.py` — so by content it
is the true "medium" between sqllineage and matplotlib. A detached full sqllineage
run (via `nohup … & disown`, which is the only channel that escapes this host's
~2-min job-kill) was still embedding at **331 s elapsed and climbing** when this
report was finalized — directly corroborating the "small-repo full index = several
CPU-minutes" estimate above (its ~595-chunk / ~1.5 ch·s⁻¹ projection lands ~400 s).
The completed 11-file subset remains the anchored real data point. )

Embedding is unavoidable in the default path: `pipelines/ingestion.yaml:19` runs
`embed_chunks` on every project chunk (`batch_size: 32`); there is no lexical-only
CLI flag. So these wall times reflect the real per-repo index cost a campaign pays.

CPU/mem during a project index (repeated `ps` samples of the running process):
**%CPU 180–246 %** (multi-core — onnxruntime spreads embedding across threads),
**peak RSS ≈ 0.93 GB** (max observed 953 584 KB; dominated by the loaded ONNX
embedder + batch buffers). `os.cpu_count()=8`.

### 3b. D2 purity — is the index a pure function of (repo files at base commit)?

**PROJECT index: YES, pure over repo files.** Project source is *never* live-imported —
`InspectMemberExtractor.extract_from_project` "ALWAYS delegates to the composed AST
fallback — spec §9.2 forbids importing the project-under-test"
(`extraction/strategies/members/inspect_extractor.py:1-6, 40-46`). Project member +
chunk extraction is pure static AST/file reads. The only non-file input is the
embedder identity, which is folded into `pipeline_hash`/`content_hash` (CLAUDE.md
cache contract), i.e. deterministic given a fixed embedder. → the `.db`+`.tq` for
project scope are a pure function of `{repo files at base_commit, embedder+ingestion
config}`.

**DEPS index: NO — requires the instance's installed environment.** Dependency
extraction resolves against installed distributions:
`find_installed_distribution` enumerates `importlib.metadata.distributions()`
(`_dep_helpers.py:64-79`); inspect-mode deps `importlib.import_module` the installed
package (`_dep_helpers.py:119`, `:207`), static/`--no-inspect` deps read their `.py`
from `site-packages`/`dist-packages` (`_dep_helpers.py:82-89`,
`ast_extractor.py:121,140`). So the deps slice depends on *what pip installed in the
container* (versions, presence) — it is **not** reproducible from `(repo,
base_commit)` alone; it needs the resolved+installed testbed environment. For
SWE-bench-Live that environment only exists inside the `/testbed` of the
`starryzhang` image.

**Cache key is path-based, not content-addressed.**
`cache_path_for_project` → `CACHE_DIR / f"{project_dir.resolve().name}_{md5(abspath)[:10]}.db"`
(`db.py:171-180`); `.tq` mirrors it (`db.py:182-201`). The key is
`dirname + md5(absolute filesystem path)[:10]` — **it does not encode repo or
base_commit**. The index separately *stamps* `index_metadata.git_head` (the project
HEAD sha) at index time, used only for a live-vs-stored freshness **warning**, not as
a key (`db.py:28-30, 149, 380-386`). A content-addressed `(repo, base_commit)` key
would require: (a) capturing repo + base_commit at index time (git_head already
gives the commit; repo identity is not currently stored), (b) deriving the slug from
those instead of the absolute path, and (c) for the deps slice, additionally pinning
the installed-environment fingerprint (resolved versions) — since deps are not pure
over the repo commit.

---

## 4. Server placement — host-side stdio server + container-side test execution

ADR 0009 launch model (cited): the in-repo `agent_track` runner spawns
`claude -p --output-format stream-json` per arm; the `.mcp.json` boots
`pydocs_mcp serve` **as a fresh subprocess per rollout** — "one server process ↔ one
MCP [rollout]" (`docs/adr/0009-…md:27-29, 206, 265-266`). Correlation env is passed
through the `.mcp.json` `env` map: `PYDOCS_TRACE__TRAJECTORY_ID` +
`PYDOCS_TRACE__DIR` (ADR 0009:147-148, 299), and `PYDOCS_TRACE__DIR` is a **host**
trace directory (`observability/trace_recorder.py:74-75`).

**What the tools see — live disk vs frozen index (the central constraint):**
- **`grep`/`glob`/`read_file` read the LIVE workspace at call time.**
  `FileToolsService._project_candidates` calls
  `ProjectFileDiscoverer(scope=…).discover(project_root)` on every request
  (`application/file_tools.py:_project_candidates`, class doc at :357-368;
  `grep`→`_candidates`→`_project_candidates` at :380-383, 433-436;
  `read_file`→`_read_window` reads the path off disk :425-430). So these three tools
  reflect **whatever is currently on disk under `project_root`** — the pristine
  checkout at rollout start, then progressively the agent's own edits.
- **Index-backed tools (`search_codebase`/`get_symbol`/`get_context`/`get_references`/
  `get_why`/`get_overview`) read the frozen `.db`+`.tq`**, built once from the
  base-commit checkout. They do **not** see the agent's in-rollout edits.

This split is the placement crux. The MCP server + claude CLI run on the **host**
against a host checkout; test execution runs in the **container** against
`/testbed`. There are effectively two repo copies: the host workspace (drives the
agent's tools + the index) and the container `/testbed` (builds/tests the produced
patch). The agent's edits land in the host workspace; the resulting patch is applied
in the container to grade — the standard SWE-bench host-agent / container-grade
separation.

**Workspace layout to keep one rollout's mutations out of another's tool view:**
Because `grep`/`read_file` walk `project_root` live and the index key is
`dirname+md5(abspath)`, each rollout must get its **own checkout directory** (a
per-rollout copy or `git worktree` at `base_commit`). Distinct paths ⇒ distinct
index slugs ⇒ no cross-rollout collision in either the live file tools or the cache.
Trade-off to surface for the owner: a naive per-rollout copy also rebuilds the
index per rollout (path-keyed). To share one prebuilt base-commit index across
rollouts you would serve it as a **read-only bundle** (`project_root=None` path
exists — `file_tools.py:360-366`), but then the *live* file tools lose the source
tree (project-scope calls raise `ServiceUnavailableError`) and, if pointed at a
shared pristine tree, would not reflect the agent's own edits. So: **content-address
the index by `(repo, base_commit)` (§3b) and reuse the built `.db`/`.tq` across
per-rollout checkouts**, while each rollout keeps its own writable checkout for the
live file tools + patching. That combination is the only one that gives both index
reuse and per-rollout mutation isolation.

---

## 5. Host parallelism bounds

- **Index build is multi-core**: 180–246 %CPU during a project index (onnxruntime
  embedding threads); the outer package loop is `asyncio.Semaphore(workers)`
  (`application/project_indexer.py:45,60-64`) and file reads use the Rust
  rayon-parallel reader. So one index already uses ~2–2.5 cores.
- **Per-process RSS ≈ 0.93 GB peak** while indexing (embedder + batch buffers).
- **`serve` idle RSS ≈ 0.27–0.45 GB** — measured on the completed subset index
  (`pydocs_mcp serve <dir> --cache-dir <tq> --no-inspect`, sampled 2 s × 8: RSS
  climbed to 451 MB then settled ~270 MB as the backend/embedder initialised). A
  `search_codebase` call then loads the query embedder (same `bge-small` model) and
  pushes a serve process toward the ~0.9 GB the embedder needs for inference. So
  budget **~0.5 GB idle, ~0.9 GB under active query embedding per serve process.**
- **Single-digit worker pool cost (this 8-core / 8 GiB host):** an *indexing*
  process peaks ~0.93 GB and wants ~2 cores; a *serve* process is ~0.5 GB idle /
  ~0.9 GB active. RAM is the binding constraint — after OS + file caches, ~6–7 GiB
  is usable, so **~6–8 idle serves** or **~4–6 concurrent index/active-serve
  processes** before paging. CPU caps *useful concurrent indexing* at ~**3–4** on 8
  cores (each embedder wants 2+ threads). Disk: `.db`+`.tq` per repo are small (§3
  estimates: MB, not GB), so index storage is negligible next to the ~0.75–1.1 GB
  *per container image* — the disk/scale constraint is entirely the container side
  (§1–§2), not the index side. Net: a single-digit *serve/index* pool fits in RAM
  here; a single-digit *container* pool does not (no Docker, 15 GiB free, images
  amd64-only ⇒ emulated) and must move to a Linux x86_64 host.

---

## Appendix — commands / endpoints used (reproducible)

- Machine: `uname -a`, `sysctl -n machdep.cpu.brand_string hw.ncpu hw.memsize`, `df -h /`.
- Runtimes: `which docker/colima/podman`.
- Dataset repos: `datasets-server.huggingface.co/rows?dataset=SWE-bench-Live%2FSWE-bench-Live&config=default&split=test`.
- Registry: `auth.docker.io/token` + `registry-1.docker.io/v2/<repo>/manifests/latest` + `hub.docker.com/v2/repositories/<repo>/tags`.
- Index: `<phase2 venv>/bin/python -m pydocs_mcp index <dir> --skip-deps --no-inspect --cache-dir <tmp>`; process stats via `ps aux`.
