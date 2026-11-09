# Changelog

All notable changes to `pydocs-mcp-eval`, the eval suite published from
`benchmarks/`, are documented in this file. Changes to the product itself
(`pydocs-mcp`) are in the root [`CHANGELOG.md`](../CHANGELOG.md).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Releases are tagged `eval-v<version>`, never colliding with the product's
`v<version>` tags. Entries for 0.1.0 and 0.1.1 were rebuilt from their tags,
because until 0.2.0 eval-suite changes were recorded in the root changelog.

## [Unreleased]

### Added

- **Same-commit variant arms, a reusable baseline, and resume that keeps
  finished tasks** (`campaign before-after`, issue #385). Two arms on one commit
  can now differ only by their settings, a finished baseline can stand in for a
  new run's, and a killed arm resumed from its `queue.jsonl` keeps every task it
  had already answered.
  - **`--baseline-settings` / `--candidate-settings <overlay.yaml>`** give one
    arm an `ask_your_docs` overlay: `architecture`, `max_agent_turns`,
    `seed_search_with_question` and `llm`, validated at plan time by the
    product's own `AskYourDocsConfig` (`extra="forbid"`, so `guidance_task` is
    refused until the product has that knob), plus the eval-only
    `guidance_sections` (a skill document, validated with the product loader
    and handed to every rollout) and `task_scaffold` (`citing` only for now). A
    `model` key, top-level or under `llm`, is refused: `--model` owns it. The
    `--llm-block` file merges under the overlay's `llm`.
    (`campaign/before_after_arm_settings.py`, `before_after_arm_variant.py`.)
  - **The plan prints each arm's settings digest and the sorted diff** between
    the arms. The digest is a SHA-256 of the effective settings plus the
    scaffold version; the guidance file counts by its text, not its path.
    One commit under one setting is refused unless `--allow-identical` marks it
    as the A/A arm. A plan without an overlay prints exactly what it printed
    before.
  - **Each arm runs and is probed under its own variant.** The runner and the
    plan-time block probe are built with the arm's architecture (never the
    pinned `text_react`; an `auto` arm lets the product route a multimodal
    model) and its harness keys, sent as written so the arm's own commit
    decides their defaults. Each sample carries its task's own framing, else
    `repo_qa`, which the binding uses to find the guidance's task head. The
    probe also refuses an architecture the arm's product does not register.
  - **`arm_settings.json`, `arm.json` and the report's provenance carry the
    architecture, scaffold, guidance path and digest**, and the compare verb's
    arm lines carry the digest, which its A/A twin check also compares (two
    arms that recorded none still pair). Files written before these fields still load:
    an old settings file reads as the pinned arm it ran, an old summary as
    "not recorded".
  - **`--reuse-baseline <arm dir>`** compares the candidate against a finished
    baseline arm instead of running one. It is refused by name, at plan time
    and before either arm is probed, unless its `commit`, `model`,
    `max_agent_turns` and settings digest all match the plan's baseline. The
    arm must also have finished (`completed`), with no task outside the plan's
    split and enough tasks answered or excluded to cover it. On a match it runs
    zero baseline rollouts, never probes the baseline, links `<out>/baseline`
    to the reused arm, and prices only the candidate. A later run that finds
    that link refuses to write through it. The
    vendored chat runner's output now records its own variant, digest
    included: the served architecture and the bare question the chat page
    sends. A plan whose baseline runs the same settings can therefore reuse it,
    and a citing-scaffold plan refuses it by its digest. Every arm, the chat
    runner's included, also writes a `serving.json` fingerprint (the serving
    YAML's content, the endpoint, the served index's embedder and scope, and
    the llm block key by key), and the reuse is refused naming each key that
    differs, or as "not recorded" for an arm without one, unless
    `--allow-serving-mismatch` forces it (a forced reuse is logged as one JSON
    line and named in the plan, in a `serving_override.json` the parent writes
    beside the candidate's `arm.json` before it runs, and in every report),
    and an `--out` whose arms were served differently is not resumed.
  - **A resumed arm keeps its finished tasks.** The arm rewrites `arm.json`
    atomically after every answered task, before the ledger marks the task DONE.
    The checkpoint's halt reason is `in_progress`. A restart seeds its rows
    from that file and drops any row whose trace directory no longer holds
    `server_events.jsonl`, logging each drop as a
    `before_after_resume_dropped_untraced_rows` event; every DONE task left
    without a row is queued again one attempt later (one
    `before_after_resume_requeued_untraced_rows` event), and exclusions are
    counted off the ledger, so an earlier process's survive. Rows answered under
    another role, commit, model or settings digest, or for tasks outside the
    plan's (another `--split`, a shorter `--limit`, another draw), are refused
    by name before the parent writes into the arm dir, never restamped. `--report-only` and `before-after-compare` refuse an arm that still reads `in_progress`
    (`HaltReason.IN_PROGRESS`). Before this, a restart wrote an `arm.json`
    holding only its own process's tasks.
  - **`TaskScaffold`** (`task_rendering.py`) names `citing` and `bare` and
    gives each a version. `citing` keeps `task_scaffold_v1` byte-identical.

- **The bare task scaffold: the chat slice runs through `before-after`**
  (`task_rendering.py`, issue #386). An arm overlay's `task_scaffold: bare` now
  sends every rollout the question alone, as the chat page does, instead of the
  citing scaffold; `citing` stays the default.
  - **`render_task_prompt(question, scaffold=)`** returns the question unchanged
    for `bare`; `sample_row_for_task(scaffold=)` renders the row's prompt under
    it, and a before/after arm mints its samples under its own scaffold. The
    module keeps its stdlib-only imports, now pinned by a test, and renders each
    scaffold through its own template, so a scaffold without one fails loudly.
  - **`ask_binding_identity(scaffold=)` folds that scaffold's version.** The
    citing identity and `TASK_SCAFFOLD_VERSION` (`task_scaffold_v1`) are
    byte-identical, so no recorded ledger moves; the bare scaffold folds
    `task_scaffold_bare_v1`. Every ask-rubric objective still renders citing; a
    before/after arm is identified by its settings digest, which folds the same
    per-scaffold version.
  - **A bare `auto` arm writes the rows the vendored chat runner writes** for
    the same questions, and its settings digest is the runner's, so
    `--reuse-baseline <runner output>` accepts a runner output for a plan whose
    baseline runs the same settings. Two shipped files make that the
    instrument: `configs/ask_example_needle_chat_bare_auto_arm.yaml` (the arm
    overlay: `architecture: auto`, `task_scaffold: bare`, the served
    `llm.vision: true`) and `configs/ask_openrouter_example_needle_chat_serve.yaml`
    (the serving file `--config` accepts: the chat config's embedder and
    discovery, without the model settings a plan refuses).
  - **The chat runner records the llm block its agent is sent**: the
    `--llm-block` file plus the served vision rule. Before, the recorded block
    left `vision` out, so no plan that runs the same multimodal agent could
    reproduce the runner's digest.

- **Corpus metadata on the multi-location datasets, `/all` for the unpartitioned
  ones, the swe-qa-questions slices and the reference-answer carry**
  (`campaign before-after`, issue #387).
  - **Every task of swe-qa-questions, lca-bug-loc and crosscommitvuln carries
    `repo` + `commit`**, so a before/after plan gives each corpus its own
    workspace instead of the shared `--workspace`, which holds none of them.
    swe-qa (and so swe-qa-questions) now spells `repo` as the pinned clone's
    `owner/name` (`matplotlib/matplotlib`, not the split key `matplotlib`, which
    stays in the task id) with its short commit pin; lca-bug-loc and
    swe-bench-verified-loc add their base commit; crosscommitvuln derives `repo`
    from its `repo_url` and pins `commit` to the record's `prefix_sha` (a record
    whose URL names no `<owner>/<name>` is dropped and logged, like a bad sha).
    The plan's `corpora:` block now names every corpus as
    `owner/name@<sha12> — N task(s), built|to build`.
    (`datasets/repo_coordinates.py`.)
  - **Sweeps that run the Context7 or Neuledge baselines over these datasets
    name a different library.** `sweep_support` hands those systems
    `metadata["repo"]` (plus `@<sha7>` of `metadata["commit"]` for Neuledge), so
    swe-qa now names `matplotlib/matplotlib@a5e1f60` instead of `matplotlib`,
    lca-bug-loc and swe-bench-verified-loc gain the `@<sha7>` pin, and
    crosscommitvuln names a library where it named none. Such baseline runs made
    before this change are not comparable with later ones either.
  - **crosscommitvuln runs made before this change are no longer comparable
    with runs made after it: the corpus key changed.** Its tasks used to carry
    no corpus coordinates and all searched the one shared workspace; each now
    searches the index of its own pinned snapshot.
  - **`lca-bug-loc/all` and `crosscommitvuln/all` load.** The two datasets have
    no split field, so `before_after_split.UNPARTITIONED_DATASETS` builds them
    whole for `/all` and refuses any other slice by name, naming the dataset
    and `all`.
  - **swe-qa-questions is sliceable** — `dev`, `test`, `small_dev`,
    `small_test` and `all` — by gold-size bucket (`1`, `2-3`, `4+` cited files;
    `swe_qa_question_slices.SWE_QA_QUESTION_SLICES`), never by repo, through
    the shared `stratified_split` with seed 0 and a 0.2 dev fraction
    (`_SWE_QA_QUESTION_SPLIT_SEED`, `_SWE_QA_QUESTION_DEV_FRACTION`). `swe-qa`
    itself still refuses every dev/test name, and `all` is the whole corpus as
    before.
  - **Reference answers ride the gold, for the judge only.** A swe-qa task
    carries its human-written answer as `gold.extra["reference_answer"]`, a
    `ReferenceAnswer` with model id `swe-qa` and an empty prompt hash; its
    file-set gold is unchanged. A repoqa-qa task carries its row from
    `repoqa_reference_answers_v1.jsonl`, looked up by task id; a task without a
    row carries none. The text never reaches the agent (pinned over the real
    binding: neither the rendered prompt nor the candidate skill holds it) and
    is never a gate or check candidate: a `params: {keys: [...]}` naming
    `reference_answer` is refused when the gate or check is built, and by the
    candidate reader itself.

- **A fresh repoqa test draw: `--split-seed`, `--exclude-task-ids` and the
  committed consumed-ids file** (`campaign before-after`, issue #388).
  - **`--split repoqa-qa/small_test --split-seed <int>` redraws `small_test`**
    — 30 tasks, apportioned by repo with `small_test`'s own largest-remainder
    rule — from the held-out `test` partition at the dataset's OWN split seed.
    The seed moves only the redraw (`_split.reseeded_stratified_subsample`),
    never the dev/test cut: `stratified_split` drives the cut and the
    subsample from one RNG, so forwarding the seed to `RepoQADataset.split_seed`
    would have let seed-0 `dev` and `small_dev` tasks into a "fresh" sample.
    Any other dataset or slice is refused by name.
  - **`--exclude-task-ids <file>` filters, then draws.** The file's ids leave
    the pool first, so the draw keeps its full size; an id outside the `test`
    pool is refused (it would exclude nothing), and a draw that still shares
    an id with the file is refused by name. It needs `--split-seed`. The file
    is JSON, `{"provenance": …, "task_ids": […]}`; the plan, each arm's
    `arm_settings.json` (`task_draw`, ids frozen at plan time, so the arm child
    loads the same tasks) and the report's provenance record the seed, the
    file's sha256, its id count and its provenance. `--report-only` and
    `before-after-compare` score against the draw the arms recorded (flags
    optional; flags naming another sample, arms that answered different ones,
    or an exclusion file edited since the run are refused), and compare prints
    it. `write-references` takes the same two flags. Without either flag every
    slice, plan text and arm identity is unchanged.
    (`campaign/before_after_fresh_draw.py`, `before_after_recorded_draw.py`.)
  - **`baselines/repoqa_qa_small_test_consumed_2026-09-15.json`** holds the 30
    `repoqa-qa/small_test` ids the 2026-09-15 run consumed, re-derived offline
    from release `2024-06-23` at split seed 0 (no output of that run
    survives), with its provenance; a test pins its sha256. The seed-1 draw
    against it is recorded in the README's consumption ledger.

- **Each before/after arm re-indexes its task bundles under its own product
  before serving** (`campaign/before_after_index_step.py`). Both arms share one
  bundle per corpus, the bundle key carries no chunk-tree rule, and the serve
  child never re-indexes. A candidate whose chunker emits different trees (the
  product's `chunk-trees/2`) would otherwise serve trees the baseline extracted.
  Before its first rollout, each arm now runs the shipped incremental
  `pydocs-mcp index` over every bundle, with the build's own flags and never
  `--force`. A changed chunk-tree rule then re-extracts the bundle, and the chunk
  cache re-embeds nothing.
  - **The arm summary records each step.** `arm.json` gains `index_steps`, one
    row per bundle with its wall time, its embed diff (the chunk hashes the step
    added, so `0` means nothing was embedded) and its chunk count. A
    `before_after_index_step` JSON log line is written per bundle. An `arm.json`
    or `arm_settings.json` written before these fields still loads, with no step.
  - **What it costs.** Arms run one after the other, so sharing a bundle is safe.
    The price is extraction time only: each arm re-extracts the shared bundle
    under its own rules, and re-running the older arm re-extracts it again. It
    is never an embed. A bundle that does not hold its source's index is refused
    by name before any step runs, because indexing it would embed the whole
    corpus at a cost no plan printed. Each step is bounded by a one-hour timeout.

- **The retrieval runner resumes an interrupted sweep, writes where it is told, and
  names its commit without git** (`python -m pydocs_eval.runner --run-id <id>
  --results-dir <dir>`, #418, #420). Without either flag, and without the commit
  variable below, the runner's output is byte-for-byte what it was.
  - **`--results-dir <dir>`** points the JSONL tracker's output directory at `<dir>`.
    Only the location moves; file names and events are unchanged. Omitted, the
    tracker's own default applies.
  - **The run header names the commit even without a git checkout.** When
    `git rev-parse` fails, the run tags fall back to `PYDOCS_EVAL_COMMIT_SHA`, which
    the bench VM images set. Git's answer still wins; with neither, the tag stays
    empty.
  - **`--run-id <id>`** (and `run_id=` on `run_sweep_detailed` / `run_sweep`) makes
    each leg one stable file, `<results dir>/<run-id>/<system>_<config>_<dataset>.jsonl`.
    Re-running with the same id skips every task already recorded, rebuilds those
    observations from their records and assembles the leg in dataset order, so the
    seeded bootstrap sees the same sequence and the aggregates are bit-identical to
    an uninterrupted leg's. `--limit` counts recorded tasks. A leg whose tasks are
    all recorded runs nothing and re-emits its aggregates, and the markdown report
    covers every task.
  - **New events, in run-id mode only.** The `run_start` header carries the run id
    and the leg's identity: system, config name, flattened config parameters,
    dataset name and revision, dataset arguments, metric specs and commit, with
    their canonical-JSON sha256. Each finished task appends a `task` event (a full
    `TaskObservation`) after its metric lines, and that event is the task's commit
    point. A reopened leg appends `run_resume` with the recorded-task count instead
    of a second header, and every invocation ends with a fresh aggregate block and
    `run_end`, so the last block in a file covers the whole leg.
  - **Refused before any task runs:** a reopened leg whose identity fingerprint
    differs (the error names the run id, both fingerprints and the first differing
    component; a changed dataset revision is refused, not opened as a second file),
    a run id outside `A-Z a-z 0-9 . _ -` (or `.` / `..`), two configs whose leg files would collide,
    and a run-id sweep without exactly one resumable tracker among `--trackers`.
  - **Damaged run files.** A truncated last line is skipped with a
    `jsonl_run_corrupt_line_skipped` warning and its task runs again; appended
    records start on their own line. A leg file holding only a header cut short
    (a kill or a state sync mid-header) holds nothing to resume: it is logged
    (`jsonl_named_run_without_header_treated_as_empty`) and the leg starts afresh.
    Task records with no header are still refused, and the file is left untouched.
  - **Resume is an opt-in `ResumableTracker` capability** of the JSONL tracker
    (`pydocs_eval.trackers`). `ExperimentTracker`, the MLflow tracker and every test
    fake are unchanged; other trackers receive only the newly run tasks' metrics plus
    the full-leg aggregates.
  - **An offline kill-and-resume smoke** (`benchmarks/scripts/runner_resume_smoke.py`)
    drives the real runner CLI with `--run-id` on a 10-task copy of the RepoQA mini
    fixture (BM25 only, `HF_HUB_OFFLINE`, every proxy dead, `PYDOCS_*` overlays
    dropped). It runs an uninterrupted control, SIGKILLs a second run once its leg
    file holds some but not all task records, re-runs the same command, and fails
    unless the kill landed mid-leg, `run_resume` counts the records the kill left,
    only the unrecorded tasks ran (in dataset order), and the final aggregate block
    matches the control's bit for bit (latency percentiles by name only). Its
    pass/fail helpers are covered by
    `benchmarks/tests/tools/test_runner_resume_smoke.py`.
- **The bench VM: a benchmark run on an ephemeral GCP VM, described by a committed run
  spec, resumable after a spot preemption** (`pydocs_eval.bench_vm`, `infra/bench-vm/`,
  ADR 0026, #419, #421–#432). `make bench-vm RUN=runs/<name>.yaml` launches one Compute
  Engine VM in `europe-west4` that runs exactly what the spec names, inside an image
  built from the spec's commit, syncs everything the run writes to the owner's runs
  bucket, records how the run ended and deletes itself. The runbook is
  `infra/bench-vm/README.md`. The product is untouched, and the code adds no Python
  dependency: the cloud adapter shells out to `gcloud`.
  - **ADR 0026 and two glossary terms.** ADR 0026 (Proposed) records the decisions
    and amends ADR 0014 Decision 1: one ephemeral VM per run at the same x86_64
    floor, and artifacts only in the owner's private buckets, in place of one
    long-lived host and its local disk. Decisions 3 and 4 carry over. `CONTEXT.md`
    gains "Running benchmarks in the cloud", with *Run spec* and *Bench VM*.
  - **The make targets.** `make bench-vm RUN=…` launches a run or relaunches it as its
    next attempt, and returns without waiting, printing the run id, the results URI
    and the status command. `make bench-vm-status RUN=…` prints the run's status
    history, commits, image, machine, live VM and the tail of the current attempt's
    synced log; with no `RUN`, it lists every live bench VM in the region.
    `make bench-vm-stop RUN=…` deletes the run's live VM and says who records what
    next (a running supervisor records `stopped`); a run with no live VM is refused,
    naming its status. `make bench-vm-pull RUN=…` downloads
    `gs://<runs-bucket>/<run-id>/` into the git-ignored
    `benchmarks/results/bench-vm/<run-id>/`, never deleting a local file.
    `make bench-vm-check` is described below. `RUN` is relative to `infra/bench-vm/`.
  - **`DRY_RUN=1` (`launch --dry-run`) is a plan-only launch.** It makes every check
    the laptop can make alone, with the same code a real launch runs: the spec, the
    run id, the commits and their remotes, and the recipe, configs, `corpus_dir` and
    host scripts at the commit. Then it prints the plan. What only the cloud knows
    (the deployment's names, the image digest, the machine type's facts, whether the
    tag exists, an existing record, a live VM) is shown as a marked placeholder or
    listed as not checked. It never runs `gcloud` or `terraform` and writes nothing.
  - **Run specs and run ids.** A run spec is one YAML file under `infra/bench-vm/runs/`,
    loaded into a strict model: unknown keys, duplicate keys, bad enum values and
    wrong scalar types are refused. `check SPEC...` (`python -m pydocs_eval.bench_vm`)
    prints each run id, or every problem as field, value and expected shape. Its
    fields are `git_ref`, `kind`, the inputs (`dataset`, `split`, `configs`,
    `corpus_dir`, `baseline_ref`, `candidate_ref`), the passthrough `args`, the
    machine (`machine_type`, `gpu`, `spot` true, `zone`, `disk_gb` 250), the guards
    (`max_runtime` 6h, `self_delete` true, `confirm_spend`, `max_usd`) and
    `sync_interval` (5m, which must be shorter than `max_runtime`). CPU runs default
    to `n2-standard-8` and GPU runs to `g2-standard-8` with one NVIDIA L4.
    - **One kind table** (`run_kinds.RUN_KIND_TABLE`) holds one row each for
      `retrieval`, `agent_track` and `before_after`: the CLI, the flags the
      supervisor owns, the provider keys, the pre-flight, GPU use, whether the kind
      is paid, and the inputs it accepts and requires. Refused: `args` that name an
      owned flag (argparse abbreviations included), a GPU on a CPU-only kind, a paid
      kind without `confirm_spend: true` and `max_usd`, and a `before_after` spec
      without exactly one config and both refs.
    - **Run id** = `<spec file stem>-<first 8 hex of sha256 of the canonical JSON of
      the fields the file sets>`. Comments, key order, whitespace, equal spellings
      (`90m` / `1h30m`) and changed code defaults keep it; any value the file states
      changes it, so any edit is a new run. The stem is lowercase letters, digits and
      hyphens, bounded so that `bench-<run-id>-<attempt>` fits Compute Engine's
      63 characters.
    - **Two committed specs:** `cpu-smoke.yaml` (the baseline config over the RepoQA
      mini fixture, 1h) and `gpu-smoke.yaml` (`dense_st.yaml` on one spot L4, 2h).
  - **The launcher** (`launch`). In order, it loads the spec, reads the deployment's
    names from `terraform output` and reads the run record. On a first launch it
    resolves and freezes the commits, each reachable from a remote-tracking branch;
    a `before_after` baseline and candidate must be on a branch of a remote whose
    fetch URL is the product repository itself, because the VM clones it, so a
    fork's branch is refused and the error lists every remote checked. It checks the
    configs, `corpus_dir`, the image recipe and the host scripts at the commit,
    never in the working tree, and checks the machine type through the Compute API
    against ADR 0014's floor (x86_64, 8 vCPU, 32 GB). Then it ensures the image,
    writes the run record and the verbatim spec copy under generation
    preconditions, and creates exactly one VM, moving to the next zone on a stockout
    only. Every check that can fail on the laptop fails before anything is written;
    once the record exists, any failure records `failed`. Every `gcloud` call has a
    timeout and a bounded retry, and runs as the owner's `gcloud auth login`
    session: credential-file overrides are dropped.
    - **The VM** is `bench-<run-id>-<attempt>`, labelled with the run id and
      attempt, spot or standard, with the maximum run duration set to
      `max_runtime` and action DELETE, no external IP, the bench service account,
      and the run commit's startup and shutdown scripts in its metadata.
    - **Images on demand.** A missing `bench-<flavour>:<sha>` tag submits exactly
      one Cloud Build build of the flavour the run needs, from a `git archive` of
      the commit with that commit's own `cloudbuild.yaml`, waits with a deadline,
      and reads the new digest. A failed build whose tag exists afterwards lost the
      immutable-tag race, and the launch uses the winning image. Any other failed
      build fails the launch before a record is written, naming the build and its
      log.
  - **The run record** (`gs://<runs-bucket>/<run-id>/run.json`) carries a schema
    version that the launcher and the supervisor both check, the spec digest and
    kind, the ref and its frozen commits, the image and digest, the status and
    attempt, every transition (time, attempt, instance, zone, machine, detail), the
    last sync, the exit code and the cache bytes restored and pushed. Writes use
    generation preconditions. A write whose answer was lost, then found to have
    landed, is adopted and extended, never rebuilt from an older record. The
    statuses are `launching`, `running`, `succeeded`, `failed`, `timed_out`,
    `preempted`, `stopped` and `lost`.
  - **Relaunch** is the owner re-running `make bench-vm` on the same spec; nothing
    relaunches on its own. `preempted`, `failed`, `timed_out`, `stopped` and `lost`
    relaunch as attempt n+1 on the frozen commits, even after the branch moved; a
    `before_after` relaunch checks its recorded commits against the product remote
    too. The relaunched record drops the ended attempt's exit code. A `succeeded`
    run is refused with its results location, and a run with a VM still labelled
    with its id in the region is refused with the instance and its zone. A
    `launching` or `running` record whose VM is gone is recorded `lost` first.
  - **The supervisor** (`supervise`, the container's entrypoint) reads the host facts
    from the metadata server, then the run record (refusing an unknown schema
    version) and the spec copy, and records `running` with the machine type, zone,
    GPU, spot and image digest. It restores the run's state and caches, runs the
    pre-flight, then runs the kind's CLI from the in-image repository with its owned
    flags pointed into the state directory: the runner gets `--run-id`,
    `--results-dir`, `--report` and `--bench-cache on`; the agent track gets
    `--ledger` and `--report`; before/after gets `--out`, `--workspace`, `--repo`,
    `--max-usd` and `--confirm-spend`; a GPU run adds `--gpu`. The child's output
    goes to `state/logs/child-attempt-<n>.log`, a file created new for the attempt,
    verbatim except for the provider key values of its kind (see "Provider keys
    reach the child only" below).
    - **Endings.** Exit 0 is `succeeded`; non-zero is `failed` with the exit code;
      the soft deadline stops the child's process group and records `timed_out`.
      Each comes after a final sync, and then the VM deletes itself unless
      `self_delete: false`. The soft deadline counts from the attempt's `launching`
      record, so boot and the image pull come out of it, and ends 25 minutes plus
      the stop grace before `max_runtime` (half of `max_runtime` when it is too short
      for that margin). Each sync attempt is capped at 5 minutes. Once `running` is
      recorded, any unexpected failure still ends the run `failed`, with the final
      sync and the self-delete. The command exits 0 when the run succeeded, 1 when
      it ended any other way and 2 when it refused to start.
    - **A refused supervisor deletes its own VM.** One that refuses its run, or
      cannot read it, writes nothing. It still deletes its VM once the host facts say
      which VM it is, unless the spec copy it can read says `self_delete: false`.
      This covers a create the launcher recorded as `failed` that came up anyway, and
      the first VM of two concurrent launches. Without the host facts, nothing is
      deleted, and `max_runtime` remains the backstop.
  - **Preemption and the state sync.** Everything the run writes mirrors to
    `gs://<runs-bucket>/<run-id>/state/` every `sync_interval` and at every ending.
    The sync is incremental, never deletes, and skips temporary files and
    before/after's product worktrees. The host shutdown script stops the container
    with an 80 s grace. On that signal, the supervisor reads the metadata server's
    preempted flag, stops the child within a short grace, and pushes the JSON and
    JSONL resume files before the rest. It records `preempted` when the flag is set
    and `stopped` otherwise, and never self-deletes after either. From attempt 2 on,
    the state is restored into the same fixed path before the child starts, so the
    runner, the agent track and before/after each resume from their own ledgers. The
    restore leaves out `state/logs/`: earlier attempts' logs stay in the bucket and
    are never brought back, read, redacted twice or uploaded again. A
    failed restore ends the run `failed` and pushes nothing. Work done after the last
    completed sync runs again; in a paid kind its first cost is in no restored
    ledger line, which is why long paid campaigns set `spot: false`.
  - **Pre-flights before any spend.** On a GPU run, the image's interpreter must
    show torch seeing CUDA and onnxruntime listing the CUDA execution provider. Then
    the kind's own pre-flight runs: the agent track's `--preflight`, or
    before/after's plan-only invocation without `--confirm-spend`. Its output is
    redacted like the child's, straight into `state/logs/preflight-attempt-<n>.log`
    with no raw copy anywhere, and the supervisor's log repeats at most its last
    16,000 bytes.
    Before/after also gets a blobless clone of the public product repository under
    the cache directory (its `--repo`), checked to hold both frozen commits. A
    failing step ends the run `failed`, naming what is missing, before the paid child
    starts. The pre-flight's time comes off the soft deadline, and a pre-flight that
    uses it up ends `timed_out` without starting the child.
  - **Provider keys reach the child only.** Each kind's row declares its keys
    (`ANTHROPIC_API_KEY` for `agent_track`, `OPENROUTER_API_KEY` for `before_after`,
    none for `retrieval`). The supervisor reads only those from Secret Manager and
    puts them only in the child's environment, never in its own environment, a log,
    the run record or the synced state. Any other provider key the container carried
    is dropped. A key the child or its pre-flight prints anyway is replaced by its
    name in angle brackets (`<ANTHROPIC_API_KEY>`) before any byte reaches disk or
    GCS: the supervisor reads their stdout and stderr through one pipe on a thread of
    its own, in bounded memory, and holds back only bytes that could still begin a
    key, so a key cut across reads, overlapping another or split by a pause longer
    than a sync is still masked. Every other byte is kept as written, except at the
    very end: output that ends in the first bytes of a key (a process that exits,
    is stopped or is cut off by the drain bound mid-key) ends in
    `<partial key withheld>` instead, fail-closed, even when those bytes were
    benign text that merely could begin a key. A key value
    shorter than 20 bytes or padded with whitespace fails the run at the pre-flight,
    naming the key only. A log file that already exists fails the run before its
    process starts. Not covered: a key printed transformed (wrapped, encoded,
    masked, truncated) or interleaved with other writes, files the benchmark writes
    itself, and runs at commits before this change.
  - **Persisted caches** (the cache bucket, partitioned by dataset plus one shared
    partition). The child runs with `HOME`, `HF_HOME` and `FASTEMBED_CACHE_PATH`
    under the fixed cache mount. Before the child starts, the supervisor restores
    the shared partition and the run's dataset partition: the embedder weights, the
    dataset and repository downloads, the answer-judge cache and the index entries.
    It starts no restore transfer once half of the time to the soft deadline is
    spent, so every run, the hour-long smoke included, starts warm the second time.
    What finished is pushed at every sync, on a worker thread beside the child, one
    push at a time, and once more after the ending; no transfer starts once a
    shutdown is requested. A run never overwrites or deletes anything in the bucket.
    - **Index entries are whole or absent.** Each is one archive object, uploaded
      under a does-not-exist precondition and only after its producer marked it
      finished: a promoted bench-cache key directory, or a before/after bundle that
      carries its new completion marker. SQLite files are packed through the online
      backup API. Bench index entries are kept only for a `retrieval` spec with a
      `corpus_dir`, under a partition named by the image's `uv.lock` digest and
      flavour. Entries from per-task corpora are never pushed, because their keys can
      never be hit again.
    - **Download caches mirror as plain directories,** skipping in-flight files. A
      Hugging Face hub cache uploads each weight once, through its snapshot file. A
      file that moves or vanishes mid-walk is logged, never fatal, and a cache
      failure never ends the run. The record's `cache_bytes_pushed` counts only the
      files a push added.
  - **The CPU and CUDA images** (`infra/bench-vm/docker/`, `cloudbuild.yaml`).
    `bench-cpu:<sha>` and `bench-cuda:<sha>` are built by Cloud Build from a
    `git archive` of the commit, with that commit's recipe, in multi-stage builds.
    The product's Rust extension is built with maturin, and the final stage holds no
    toolchain. Every base image is pinned by digest. Nothing is resolved fresh: the
    product set installs from `uv export --frozen` with `--require-hashes`, and the
    eval suite from a hash-pinned `eval-requirements.txt` compiled against `uv.lock`
    (`image_deps.py lock-eval [--check]`). Both images preload the pthread OpenBLAS
    as the CI benchmark job does, carry the Cloud SDK, git and a pinned,
    sha256-checked `claude` CLI, sit at `/opt/pydocs-mcp`, and set
    `PYDOCS_EVAL_COMMIT_SHA`.
    - The CPU image carries no CUDA library: it installs the product with `[graph]`
      only, so `sentence_transformers` and late-interaction configs need
      `bench-cuda`.
    - The CUDA image uses one CUDA major (13) across its base, torch and
      `onnxruntime-gpu`, swaps `fastembed-gpu` in for `fastembed`, and bakes the
      `run_eval_gpu.sh` library-path fix into `ldconfig`.
    - The eval lock records the cutoff it was resolved at (`# exclude-newer:`), so
      `lock-eval --check` reproduces it and fails only when `uv.lock` or
      `benchmarks/pyproject.toml` changed, never on a later PyPI release. The
      `bench-vm-check` workflow runs that check and a `pip-audit` of the lock with
      nothing ignored, and now also runs when `uv.lock` alone changes: CI's security
      job audits only `uv.lock`'s export, so a dependency bump used to leave the
      images' pins behind unnoticed.
    - Every build ends with a smoke step (`smoke.py`), so a broken image never gets
      a tag. It imports the product with turbovec loaded and runs `pydocs-mcp
      --help`, the runner's and the supervisor's `--help`. On CUDA, it also checks
      the device-free part of the CUDA path. `build-local.sh` mirrors the build for
      a local Docker, without a push.
  - **The Terraform foundation** (`infra/bench-vm/terraform/`, state in a GCS backend
    bucket the runbook bootstraps). It enables the APIs, and declares the VPC and
    `europe-west4` subnet with Private Google Access, Cloud Router and Cloud NAT, and
    an SSH firewall rule for IAP's range only. It declares the runs bucket (kept),
    the cache bucket (objects deleted after `cache_retention_days`, 60) and a
    build-sources bucket, all with uniform access and public access prevention. It
    declares an Artifact Registry repository with immutable tags and an age-based
    cleanup that keeps the most recent versions. The bench and Cloud Build service
    accounts get least-privilege IAM. Instance delete is limited to `bench-…` names
    by an IAM condition, and no service-account key exists anywhere. The Anthropic
    and OpenRouter secrets are declared, with their values added out of band, so
    they never enter state. Boot images are `cos-stable` for CPU and the Deep
    Learning VM family `common-cu129-ubuntu-2404-nvidia-580` for GPU. Outputs cover
    every name the launcher reads, and a `terraform test` runs against a mocked
    provider.
  - **The spend guards and the budget.** The VM deletes itself when its run ends;
    the platform deletes it at `max_runtime` (default 6 hours) whatever the guest is
    doing, `self_delete: false` included; spot is the default; there are no external
    IPs; boot disks go with their VM. A `google_billing_budget` of 50 EUR a month
    emails `notification_emails` at 50%, 90% and 100% of actual spend and at 100% of
    forecast. It only notifies, and billing data lags by hours. The paid kinds need
    `confirm_spend: true` and a `max_usd` in the committed spec, and `max_usd` reaches
    the kind's own spend guard.
  - **`make bench-vm-check` and its CI job.** The target needs no credentials and no
    Terraform backend. It runs `check` on every committed spec, `shellcheck` on
    every `*.sh` under `infra/bench-vm/`, `hadolint` on both Dockerfiles and
    `terraform fmt -check`. Then it runs `terraform init -backend=false
    -lockfile=readonly` into a throwaway data directory, followed by
    `terraform validate` and the mocked `terraform test`. Every step runs even after
    one fails, and the last line names the failed steps. It never leaves
    `.terraform/` in the tree or rewrites the lock file.
    `.github/workflows/bench-vm-check.yml` runs the target on changes to
    `infra/**`, the eval suite's code or dependencies, the Makefile or the workflow
    itself.
  - **Tested offline.** The launcher and the supervisor are driven end to end
    through their entry points against `FakeBenchCloud`, which is the `BenchCloud`
    port's fake beside the `gcloud`-backed `GcloudBenchCloud`. The tests use a real
    fixture git repository with a bare origin, real child processes that signal
    their supervisor and resume from a ledger, and short injected deadlines. No test
    reaches a cloud.
- **Reference answers written from ground truth, and the `write-references` verb that
  writes them** (`pydocs_eval.judge.reference_*`,
  `python -m pydocs_eval.campaign write-references --split repoqa-qa/<slice>`). Each
  repoqa-qa task gets a short reference answer, shaped like a good agent answer: the
  gold path, the function and its line span, then 2–4 sentences on what the code does.
  The pinned writer (`reference_writer.model`) writes it from the question and the
  needle's code alone (its body plus `reference_writer.context_lines` lines on each
  side), never from an agent answer. The judges use it to score agreement. Without
  `--confirm-spend` the verb prints its plan and spends nothing.
  - **Two named code checks guard every row.** `check_repoqa_reference` requires the
    gold path and symbol and no other file path; `check_chat_reference` requires every
    gold site's path and symbol and no path outside them. A failing row is regenerated,
    told what the rejected draft got wrong, at most `reference_writer.retries` times.
    A row still failing is listed for the owner, never kept.
  - **The fallback writer is used only for a job that failed.** That means a row the
    Batch API answered with an error, or a batch that ended `failed`, `expired` or
    `cancelled`, and the row keeps the reason. A batch that is only still running when
    the writer stops waiting is listed by id. The next run collects it by that id
    instead of buying it again on the fallback.
  - **Every batch id is journaled the moment it is submitted,** before the first poll,
    so a run that stops waiting or dies loses nothing. Re-running the verb collects
    what is still open and deletes each batch once its rows are stored (OpenRouter
    otherwise keeps a batch's inputs and results for 30 days).
    - A batch that also answered tasks outside the run stays open for the run that
      covers them, so a paid answer is never deleted unread.
    - A batch OpenRouter no longer knows (a 404 once its retention ran out) is
      recorded as lost, and its tasks are asked again.
    - When a submit fails, the report warns that the batch may have been accepted
      anyway.
  - **The references ship as package data** in
    `pydocs_eval/datasets/data/repoqa_qa/repoqa_reference_answers_v1.jsonl`, keyed by
    task id and read through `reference_answers_by_task`. Each row is one canonical
    JSON line carrying the text, the writer's model id, the prompt hash, why the
    fallback wrote it (empty when the writer did) and the format revision `1.0`. A
    stored reference is never rewritten. The `example-needle-chat` records are
    unchanged: chat left acceptance, so its references wait until a step runs on chat.
  - **Batch 1 is written:** the 20 `repoqa-qa/small_dev` tasks. All 20 references
    came from the writer (`anthropic/claude-opus-5.5`), none from the fallback, and
    every one passes the repoqa code check. They cost $0.1056 at OpenRouter: a
    2-task pilot at $0.0080, then 18 tasks at $0.0976. A test pins the file's
    sha256 and re-runs the code check and the provenance check on every row.
  - **RepoQA tasks carry the needle function's own span** as
    `needle_first_line` / `needle_last_line` (1-based, inclusive). The release's
    `start_line` is 0-based: on all 100 Python needles the `def` sits at
    `start_line + 1`. The gold `ast_body` deliberately keeps the line above the
    `def` (a blank line, or the lowest decorator). Its canonical AST is the same
    for all 100 needles, so no relevance score moves, and it stays the agent-track
    judge's reference text. The loader now documents this, and the mini test
    fixture follows the release's line convention.
  - **The shared chat client now says what kind of failure each row met**
    (`ChatFailure.kind`: `job_failed`, `unusable_answer`, `still_running`,
    `not_submitted`) and which batch answered each row (`ChatCompletion.batch_id`). It
    hands a batch's id to `on_submitted` before the first poll, and can `collect` a
    batch by id and `delete_batch` one that ended. A batch row's id goes on the wire
    as itself only when it matches `[A-Za-z0-9_-]{1,64}`, the strictest backend's
    rule (Anthropic's batch backend refused a longer task id); any other id is sent
    as `row-` plus 32 hex digits of its sha256 and read back under the caller's id.
    OpenRouter prices a batch as a whole and puts no cost on its rows. So each row
    of an ended batch carries that batch's cost (`batch_cost_usd`), and the
    writer's report counts each batch once.
- **The Jev judge client, one OpenRouter chat client for the three LLM roles, the
  Jev request designs and the role-named judge configuration** (`pydocs_eval.judge`).
  Nothing here calls a model until a deployment pins it.
  - **`JevJudgeClient`** asks TypeSafe Jev on OpenRouter's System One route over
    plain httpx, with no vendor SDK. It reads the bearer from `$OPENROUTER_API_KEY`
    when the call is made, and no error or log line carries it. A timeout, a 5xx
    or a rate limit (429, after the `Retry-After` it asks, capped at 30 s) is
    retried at most twice, with backoff; any other 4xx never is. Answers are
    cached on disk by the hash of the whole request body, model pin included, so
    a warm cache makes no call. `FakeJevJudgeClient` is its scripted double.
  - **Every answer's model is checked against the pin.** `JudgeModelMismatchError`
    names both ids. OpenRouter reports the served model in its own spelling
    (`jev-1.13` answers as `typesafe/jev-1.13-20260917`), so the check accepts
    exactly that: the `typesafe/` namespace on a bare System One id, a dated
    snapshot, and a variant of the pin's own (`:batch`) dropped. Another vendor,
    version or variant is a mismatch.
  - **`OpenRouterChatClient`** is shared by the escalation judge, the two alignment
    labellers and the reference writer. It asks for structured output by JSON
    schema at the role's `reasoning_effort`. A `:batch` model runs its requests as
    one batch on OpenRouter's Batch API, submitted once and polled until it ends
    or the role's timeout passes. A batch given up on while it may still run
    upstream is logged by id, so it can be collected or deleted. A synchronous role
    refused `xhigh` falls back to `high` for the rest of the run. Every row comes
    back answered or failed with its reason. `FakeOpenRouterChatClient` is its
    scripted double.
  - **The request designs** put every question about one answer into one request
    over named-JSON state:
    - repoqa-qa asks `needle_identified`, `addresses_grader` and, once references
      exist, `contradicts_reference`;
    - example-needle-chat asks one `site_<i>` per gold site, and swe-qa-questions
      one `gold_file_<i>` per gold file, each then `addresses_grader`,
      `completeness` (L0–L3) and `contradicts_reference`.

    An answer over `judge.jev.max_answer_chars` is flagged and never sent, and
    more than 12 gold locations are split into recorded parts. The gold-blind
    `committed_function` Choice is built apart, for alignment items only. Four
    planted injections are vendored for the `addresses_grader` check.
  - **`score_answer`** looks up `judge.thresholds.<jev-model>.<dataset>.<question>`
    first and refuses a missing block by name before any call. It reports
    `contradicts_reference` as `agreement` and `completeness` as a level or
    `undefined`. A request with no usable answer (an outage, or a body in no
    documented shape) leaves its rows `undefined`, never 0. An answer from any
    Jev other than `judge.jev.model`, whose thresholds score it, is refused.
    `gold_location_recall`, the Jev twin of `gold_site_coverage`, is computed in
    code from the per-site (or per-file) verdicts once every one is decided.
  - **The configuration is named by role:** `judge.jev`, `judge.escalation`,
    `judge.alignment.labellers`, `judge.thresholds` and `reference_writer`. Every
    `model` is empty by default, so a role refuses, naming its key, until the
    deployment YAML pins it. `benchmarks/configs/judge_openrouter.yaml` (no secret)
    pins `jev-1.13`, `openai/gpt-6-luna`, `openai/gpt-6-astra:batch`,
    `anthropic/claude-opus-5.5:batch` and `anthropic/claude-sonnet-5:batch`, and a
    test holds it to the model-family rule. (#377)
- **`before-after-compare`, the acceptance record, with `holm_adjust` and an expected-turns cost model.**
  - **The verb** compares up to three variant arms with one baseline. Campaign
    arms and chat runner outputs read alike, on either side, and more than
    three variants are refused. For each variant it prints:
    - the one-sided Wilcoxon p on penalised turns and tool calls, and
      McNemar's exact p on Needle reached and `needle cited`, each
      Holm-adjusted across the variants;
    - the correctness band: the larger of the A/A pair's difference and one
      task. The A/A replicate (`--aa-replicate`, a second baseline arm on the
      same commit and settings) is required. A replicate from another commit,
      model or turn cap is refused by name, and one with no stored answers
      leaves no band;
    - a PASS/FAIL verdict naming the rule that decided it.
  - **The rules.** The standard rule (Q10) needs penalised turns-to-answer
    down, budget exhaustion not up, and `needle cited` within the band below
    the baseline, plus gold-site coverage within its own band on multi-site
    tasks. `--completeness-arm` tries the bounded step-8 rule (Q44) first:
    coverage up and penalised turns up by at most +0.5, printed with an owner
    sign-off line. If Q44 does not hold, the standard rule decides.
  - **No verdict, and exit status.** Arms without stored answers get no
    verdict. The exit status is 0 when every variant passes, 1 when any
    fails, and 2 on an input error or when a variant gets no verdict.
  - **`holm_adjust`** (`metrics/aggregate.py`) is Holm's step-down
    adjustment: monotone and capped at 1.0.
  - **`--expected-turns-per-rollout`** (`CostModel.expected_turns_per_rollout`)
    prices each rollout at the smaller of the expected turns and the budget
    (at least 1 turn, the answering one). Unset, the plan text is unchanged.
    (#372)
- **`needle cited`, scored by code, and the answer rows of the before/after report.**
  - **The scorer** (`pydocs_eval.judge.needle_citation`) matches what an answer
    names against every gold site of its needle, before any judge is asked.
    - It reads file paths, now past `.py` to `.md .toml .yaml .yml .cfg .ini
      .rst .txt .json`, and names written as code: in backticks or a fenced
      block, a dotted chain, a called name, or a word only code spells
      (`get_params`, `MaxSimScorer`). A plain prose word is not a name, and
      `matplotlib.pyplot` still cites no `matplotlib.py`.
    - A site answers to its path, the path without `src/`, its trailing parts,
      its module, and its symbol bare, qualified or under the module. A short
      spelling (a bare file name or symbol) shared by sites in two files of one
      needle cites neither.
    - On a needle spanning two or more files, `needle cited` needs every site,
      with `any_site_cited` and `gold_site_coverage` beside it. A needle inside
      one file is one site, cited by any of its spellings, and there the three
      agree. Nothing after a `Not confirmed:` line counts.
    - The package imports nothing from the product: it reads the label from
      the eval mirror `ASK_NOT_CONFIRMED_LABEL`. The swe-qa pseudo-qrel
      extractor is unchanged, and a parity test holds the two together.
  - **The answer rows.** The report gains `needle cited` (McNemar-paired); on a
    needle spanning several files, `gold-site coverage at stop` and `cited-path
    precision` (a file cited by two spellings counts once); and `answers over
    the judge cap`. The gold-reach block gains `calls to full coverage`, for
    needles of 2 to 12 gold files. An arm stored before answers
    reads `n/a` on every answer row, never 0, and `--report-only` re-scores
    stored answers.
  - **`JudgeConfig`**, loaded from the packaged `judge/configs/judge.yaml`,
    holds `judge.jev.max_answer_chars` (12000) and `judge.jev.citation_extensions`.
  - The ten `example-needle-chat` repro answers are test fixtures with
    hand-labelled citations; q01 and q08 are the multi-site cases. (#373)
- **The held-out `example-needle-chat` set, the `reserved` draw and the reference-answer carrier.**
  - **Thirty held-out questions** join the ten `dev` ones: 20 `test` (the adoption
    set the ladder may run on) and 10 `reserved` (run once, by final
    confirmation). Every shape appears at least twice and in `reserved`, and 29
    of the thirty need gold from two or more files (180 gold sites in all).
    - Agents drafted forty candidates from the vendored `authoring_prompt.md`,
      which holds only the question shapes and the pinned checkout. Ten were
      dropped: five restated a `dev` question, two had their whole answer
      inside a `dev` gold site, two leaned on a `dev` question's files, and
      one would have been a fourth demo-tooling question. Kept questions may
      still share a site with a `dev` question's gold, since central code
      answers many questions (ten share at least one); none restates one.
    - No owner-captured chat question existed when the set was written, so
      all thirty are agent-written (`query_source` `agent`).
    - Each gold site comes from a fresh grep of the answer's identifiers. Every
      non-test hit outside the gold sites is listed with the reason it was left
      out, in `held_out_gold_accounting.jsonl`. The gold awaits the owner's
      ratification.
    - The `reserved` ten were drawn once, with seed 0 and stratified by shape
      (`datasets/example_needle_chat_reserved.py`), and are stored as each
      record's literal split. The loader never draws; a test re-runs the draw
      and fails on any disagreement.
    - The records are byte-pinned before any `system_v3` prompt exists, so a
      later edit is a visible diff.
  - **`before-after` learns the chat slice names.** `DATASET_SLICE_NAMES` maps a
    dataset to its own slice names and is consulted before the global
    vocabulary, so `example-needle-chat/reserved` and `/held_out` load while
    `repoqa-qa/reserved` is still refused by name. No other dataset's names
    change.
  - **`ReferenceAnswer`** (text, model id, prompt hash) is the one carrier of a
    reference answer. It rides `gold.extra` under `REFERENCE_ANSWER_KEY` and is
    read only through `reference_answer_of`. It is a value object, not a string,
    so the rubric gates and checks never take its text as evidence an answer
    must contain. Anything else under the key is refused, and printing a gold
    never shows the text. A chat record may carry one as
    `gold.reference_answer`; the agent track's blind judge leaves it out of the
    gold it is shown. (#369)
- **The `example-needle-chat` dataset and a chat repro runner that writes an arm.**
  - **The dataset.** The `dev` slice is the ten open-ended chat questions about
    `msobroza/example_needle`, verbatim.
    - Each record pins commit `9c170b02fe93` and the embedder its gold was
      written against (`qwen/qwen3-embedding-4b` at 2560 dimensions).
    - Its gold is a list of code-authored sites (`path:start-end` plus one
      symbol), awaiting the owner's ratification.
    - The corpus is the pinned checkout, widened past `.py` because gold names
      `README.md` and config files.
    - The slice names (`dev`, `test`, `reserved`, `held_out`, `all`) and the
      shape, gold-source and query-source vocabularies are `StrEnum`s. A bad
      value names itself and the accepted set.
  - **In `before-after`.**
    - `--split example-needle-chat/dev` plans one workspace for the slice and
      prints the pinned embedder.
    - A task that pins an embedder the run does not serve is refused by task
      id, naming both values, before any corpus is materialized.
    - A slice the dataset refuses is now a named plan error, not a traceback.
  - **The runner** (`tools/run_example_needle_chat_repro.py`):
    - asks each question through the chat page's own agent and serve child;
    - requires `--llm-block` and honours `--max-agent-turns`;
    - writes `arm.json` and `arm_settings.json`, plus one trajectory directory
      per question. `read_arm_summary`, `measure_arm` and the report read the
      result as they read a campaign arm.
    - Each question's `question.json` gains the per-question record: turns,
      calls by tool, tokens, the outcome, whether the canned apology was seen,
      and the seven pre-registered behaviour counts.
  - **The serving config** `configs/ask_openrouter_example_needle_chat.yaml`
    carries the pinned before/after llm block field for field, and a test holds
    the two together. (#368)
- **How each task ended, and Turns-to-answer that counts it.** Every before/after
  task now gets exactly one outcome — `timeout`, `budget_exhausted`,
  `exhausted_finalized`, `starved_reply`, `unanswered_empty` or `answered`
  (`unrecorded` for an old row) — decided by one truth table
  (`trajectory.ask_outcome.outcome_of`). LangGraph's canned "need more steps"
  reply at the turn cap was booked as a 12-turn answer; it now reads
  `budget_exhausted`, recognised by an eval mirror of the product literal
  (`ASK_BUDGET_EXHAUSTED_REPLY`, beside `ASK_NOT_CONFIRMED_LABEL`; each parity
  test skips until the product constant lands). A timed-out run reads `timeout`,
  never an empty answer, and an empty reply the endpoint cut at `length` while
  the arm let the model think reads `starved_reply`. Each `arm.json` row now
  persists the answer text (never the canned reply, which is not an answer), its
  outcome, its tool-call count and `near_cap`, and the summary its
  `max_agent_turns`; an `arm.json` written before loads as
  `unrecorded` and is back-filled at measurement (47 characters at exactly the
  cap reads `budget_exhausted`). The report leads with the outcome rows (the
  budget-exhausted and answered-within-budget rates, McNemar-paired; one count
  per outcome; `near cap`; `finalize format failures`, reserved) and the turn
  rows: Turns-to-answer penalised — every unanswered task counts the budget + 1,
  13 at the default — beside the answered-only mean, `turns after needle`,
  paired turns and tool calls per task, calls after the first gold and after the
  first gold read (total, mean and a new p90 tail row), tool calls to the first
  gold read, wall seconds per task and uncached tokens in (total and per turn).
  The provenance prints each arm's outcome tally and names an arm that recorded
  no usage sidecar. `--report-only` replays all of it over stored arms against
  the plan's budget: on the 2026-09-15 `repoqa-qa/small_test` run, turns per
  task move −0.90 [−1.73, −0.23] (one-sided p = 0.017), calls after the first
  gold 114 → 92, budget exhausted 1 → 0. The plan now promises the report's own
  row labels, and an arm books a run as complete only when its trace file is on
  disk (anything else stays infra: retried once, then excluded).
- **`branch_reindex_cost`: what a second branch costs against its diff.**
  `python -m pydocs_eval.micro.branch_reindex_cost --files 200
  --changed-percent 1 5 20` builds a synthetic git repository in a temp
  directory (`main` with `--files` modules; `feature/x` edits one function in a
  share of them, or in `--changed N` of them), then runs the real
  `pydocs-mcp index` twice and `pydocs-mcp index --branch feature/x` once, in
  process, with a counting embedder, so no model is downloaded. It prints one
  JSON report per diff size: each run's wall time and the embedder's text
  counts; `seconds_branch_pass`, the `--branch` run less the plain re-run (that
  run re-parses all of `main` before its branch pass, so its own time grows
  with the project); and the `--branch` run's own `branch_reindex` line — files
  total, reused and extracted; chunks embedded (re-embedded for the branch) and
  shared (already held by `main`); vectors removed. New package
  `pydocs_eval.micro`; needs `git` on `PATH`, the `[retrieval]` extra, and a
  pydocs-mcp with `index --branch` (0.8.1 and earlier lack it).
- **Visible-hit metrics: what the response text actually showed the model.**
  `ToolEvent` gained `rendered_rows: int | None` — how many of a call's
  `items[]` rows its text rendered, read straight from the product capture.
  A search returns more rows than its token budget renders, so a returned row
  was never proof the model read it. Three pure functions beside the existing
  gold-reach ones read the same path-matching rule over the rendered prefix
  only: `visible_hit` (one call), `visible_hit_rate` and `gold_visible` and
  `tool_calls_to_first_visible_gold` (one trajectory). The before/after report
  prints `visible gold rate`, `tool calls to first visible gold` and
  `visible-hit rate per search call` beside their returned-row siblings.
  Undefined, never zero: a capture that cannot say prints `n/a`.
- **Trajectory schema 2.** The bump carries `rendered_rows`. Older captures
  stay readable — `assert_schema_version` now accepts any version up to the
  reader's own, and every field a version-1 stream lacks reads as undefined.
  A capture NEWER than the reader is still refused, because the reader would
  otherwise read a missing field as a measured value.

- **Call-efficiency metrics over one trajectory's recorded tool events.**
  `needless_call_rate` is the share of calls charged by any of four
  components, each also exposed on its own: **resurfacing** (every identifier
  the call returned had already been returned earlier), **zero-yield** (no
  identifier and no error, so a failed call is never charged twice),
  **fan-out-where-batch** (three or more single-target calls of one tool in
  one turn where that tool has a batch counterpart; the group is charged in
  full) and **tool-mismatch** (a search whose query is shaped like a dotted
  path). A call two components charge counts once. Beside it:
  `pointer_followed_rate` (the share of the distinct follow-up calls earlier
  responses offered that the agent actually issued), `parallel_calls_per_turn`
  and `batch_vs_fanout_ratio`. New modules
  `pydocs_eval/trajectory/call_efficiency.py` and `pointer_lines.py` — a
  tolerant reader of the rendered follow-up lines that ignores whatever
  precedes the arrow, so the group labels a product change adds cannot break
  it — registered on the `TrajectoryMetrics` bundle under `call_efficiency`,
  where `tool_calls_to_first_gold` and `per_tool_yield` already ride, and
  re-exported from `pydocs_eval.trajectory`. Empty denominators are decided
  once, not per call site: a rate over calls reads `0.0`, a rate over
  opportunities the server created reads `None`, so "nothing was offered"
  never averages into a before/after comparison as "every pointer was
  ignored". Consumers drop the `None`s.
- **A before/after command for the measurement run.**
  `python -m pydocs_eval.campaign before-after` runs one dataset split through
  the same harness twice, changing only the commit of the product the serve
  child imports. Without `--confirm-spend` it prints the plan and stops: task
  count, both commits with their description-token counts, model and endpoint,
  turn budget, estimated call and token counts, a dollar figure with every
  assumption behind it, and the exact metric list the report will carry. Each
  rollout books the plan's estimated per-rollout cost against the campaign
  budget guard — recorded as an estimate, never as a measurement, because the
  in-process harness reports `cost_usd = 0.0` by contract, which would
  otherwise leave `--max-usd` unable to fire. An arm is a git worktree plus a
  child process whose `PYTHONPATH` puts that worktree's `python/` first, and
  an arm whose imported `pydocs_mcp` is not under its worktree refuses to run:
  measuring the same code twice would report a difference of zero as a
  finding. `--split` takes `<dataset>/<slice>`, so `repo_qa/dev` is refused
  with a message naming the sliceable spelling (`repoqa-qa/dev`). Documented
  in `benchmarks/README.md`, cost assumptions included.
- **`--report-only` re-renders a finished before/after run's report.**
  `python -m pydocs_eval.campaign before-after ... --report-only` rebuilds
  `before_after.md` (and `plan.txt`) from the arm summaries already under
  `--out`, checking out no commit, spawning no arm, building no workspace and
  needing no `--confirm-spend` — it spends nothing. The report stage is the
  last thing a run does, after both arms have answered every task at the
  endpoint, so a crash there must never cost a re-run of the paid part. A
  missing `arm.json` is refused by name, with the directory that has none.

### Changed

- **`ci_compare` reads a resumed run file** (#420). The last `<metric>_mean` in the
  file still wins, and in a file written with `--run-id` it is the full-leg aggregate.
  A corrupt line before the file's last `run_resume` (the cut tail of an invocation
  that was then resumed) is now skipped. Any other corrupt line, a blank one included,
  is still an input error.
- **`TaskObservation` moved to `pydocs_eval.task_observation`** (#420), with
  `to_record` / `from_record` for the runner's `task` events. It is still re-exported
  from `pydocs_eval.sweep`, so existing imports keep working.
- **Before/after bundles carry a completion marker** (#429).
  `build_missing_workspaces` writes `.bundle-assembled.json` into each bundle
  directory, atomically, after the bundle is assembled. It names the corpus source
  directory and the unserved canonical index files the bundle was copied from, which
  the bench VM reads to persist only finished bundles. It is a dot-file, so
  `serve --workspace`'s `*.db` glob never sees it.
- **Before/after reads the Finalized answer (#375).**
  - **The `finalize format failures` row is now measured.** It is defined on
    `exhausted_finalized` tasks alone and counts each Finalized answer without
    a `Not confirmed:` line. The answer scorer and this row share one reading of
    that line, `ask_outcome.NOT_CONFIRMED_LINE`, which tolerates the markdown a
    model wraps the label in.
  - **A starved reply after exhaustion is `starved_reply`.** A run that
    exhausted its budget and came back empty while its last reply was cut at
    `length` used to be `budget_exhausted`. That reply is now a finalize call
    the endpoint starved, never a plain exhaustion.
  - **The chat repro runner asks through the chat page's own `ask()`**, on its
    live path with the built agent's finalizer. A question that runs out of
    steps is booked `exhausted_finalized`, and its `question.json` says
    `finalized: true`.

- **`needle cited` on a one-function needle requires the function's name.** A
  needle of one site with a symbol, which is the repoqa-qa shape (one file, one
  function), is cited only when the answer names the function: bare, qualified
  (`Class.method`) or under its module (`pkg.mod.fn`). The file's path, or the
  bare module alone, no longer cites it, so "the right file, the wrong function"
  reads as the miss it is. Multi-site needles and symbol-less gold keep the
  path-or-symbol rule. This is the owner's decision on #366, amending spec 9a
  for one-function needles.
  - A name written against its file counts as the name: `path.py::fn`,
    `path.py:Cls.fn` and a link `[fn](path.py#L3)`. On a one-function needle
    the nested form pytest writes counts too: `path.py::Cls::fn` names
    `Cls.fn` (#410). Every other needle, and the Jev audit's candidates, read
    it as before.
  - On a one-function needle a bare file name is a path, never the function's
    name (#410). `visit.py` alone no longer cites a function `visit`,
    whichever file it names, and neither does a link whose text is the file
    name. `Response.json`, spelled like a file name, names no method `json`;
    no repoqa needle is named so.
  - Written limits: a function named like a plain word (`visit`, 12 of the 100
    repoqa needles) is cited only when written as code; the function's name
    beside another file still cites it, a namesake elsewhere being the Jev
    tier's call (#410).
  - Scoring a runaway answer is linear. A 120 KB answer that repeated one long
    token took minutes, where it now takes milliseconds.
- **`pydocs-eval-bench-cache evict` and `--bench-cache-cleanup` remove index
  entries only.** An index entry is a key-named or database-holding directory,
  or an unfinished build. The answer judge's cache (`judge.jev.cache_dir`,
  `cache_root()/jev` by default) lives under the same root, so an index cleanup
  no longer re-rolls answers Jev already judged. `info` lists index entries
  only. (#377)
- **The shipped before/after LLM block no longer carries
  `parallel_tool_calls: null`.** An unset knob is never sent, so the line
  changed nothing about the request — but the key itself has to exist in BOTH
  arms' products, and it does not exist in a baseline that predates it. The
  block now says so in a comment where the key used to be
  (`benchmarks/configs/ask_openrouter_qwen3_8_27b_llm.yaml`).
- **The shipped qwen3.8-27b block now fixes the chat model's timeout, retries
  and upstream.** It sets `timeout_seconds: 300` and `max_retries: 2`, and
  routes every request to OpenRouter's `deepinfra/bf16` endpoint with fallbacks
  off, so one run is served by one full-precision upstream instead of whichever
  of the model's ~16 upstreams OpenRouter picks per request. Both arms need a
  product that knows the three keys: the plan's per-arm block probe refuses an
  older baseline before anything is spent. The `--llm-block` help and its error
  messages list the keys, and the two eval mirror parity tests
  (`ASK_BUDGET_EXHAUSTED_REPLY`, `ASK_NOT_CONFIRMED_LABEL`) now compare against
  the product constants instead of skipping.
- **A task the per-task timeout kills is measured as `timeout`, not retried as
  infra.** `TimeoutBoundedAskRunner` makes a fresh product trace handle active
  for each run. When the timeout fires and the product recorded a trace, the
  failed trajectory keeps it: its calls are read from the trace and its turns
  are the model replies made before the kill (budget + 1 only when no trace is
  readable). An arm then books the task once under its `timeout` outcome, with
  its calls and per-turn rows, where it used to be retried and then excluded. A
  trace that does not parse (a kill can cut its last line) counts as no trace,
  on this path and on the turn-budget path, so the failure handler never raises.
  A product that predates the handle, and the external CLI agent, keep the
  traceless sentinel.
- **The metrics command reads response text from the run's blob store**, not
  from the byte-capped preview carried on each event. A response renders its
  follow-up calls at its very end, past that cap, so the preview
  systematically under-reported them and every pointer metric read low. The
  blob directory name is now one constant shared by the writer and both
  readers.
- The in-process index seam of the campaign index cache
  (`index_project_in_process`) passes the product's new bundle members
  (`read_prior_state`, `grammar_fingerprint`) to `run_index_pass`, so the
  bundles it builds carry the coverage-aware grammar stamp the product's
  `[Unreleased]` entry describes. No recorded number changes: the seam still
  indexes project-only, static.
- The bug-localization corpus (`swe-bench-verified-loc` and `lca-bug-loc`)
  now follows the product's project-scope default: `CORPUS_GLOBS` adds the
  code extensions `.js .ts .tsx .c .h .rs .java` to the text/config set. C
  sources and headers a fix patch touches become retrievable gold only with a
  product release that indexes project code by default (ADR 0022, the
  product's `[Unreleased]` changes); pydocs-mcp 0.6.x indexes them only when
  a YAML overlay opts in. On the pinned revisions every gold path was already in the
  corpus, so today the change adds code files as candidates, not reachable
  gold. No bug_loc baselines had been recorded yet, so no recorded number
  changes meaning.

### Fixed

- **A seed-on arm seeds the task's question, not the scaffolded prompt.**
  `sample_row_for_task` now carries the task's bare query in an optional
  `question` field beside `rendered_prompt` (the required keys are unchanged),
  and the product's ask binding sends that field to the search it runs before
  the model's first turn when an arm sets `seed_search_with_question: true`.
  Such an arm's hash moves once — the product folds `seed_source: question`
  into its sent-settings fingerprint — so rows measured under the old,
  scaffolded seed are never resumed; every seed-off arm keeps its hash. (#384)
- **A before/after arm whose product recorded no model turns is measured, not
  refused.** The metric layer read every ask trajectory through a reader that
  requires the `model_turns.json` sidecar, so an arm whose commit predates that
  sidecar raised `MissingModelTurnsError` and the whole run ended with no
  report — after both arms had answered every task at the endpoint. Such an
  arm is now read by a tolerant loader that stamps each call with its own
  `seq`, one call per turn, and says `turns_recorded=False`; the measurement
  then sets `parallel_calls_per_turn` and `fan_out_where_batch` to `None` (the
  two numbers a turn defines) instead of fabricating `1.0` and `0`, and leaves
  every turn-independent number measured — the needless-call rate's other three
  components, the batch-versus-fan-out ratio, retrieval, usage and spend. One
  call per turn can never reach the fan-out threshold, so that arm's
  needless-call rate is a LOWER BOUND of its true rate; the report says so in a
  header bullet naming the arm and its task count, and reads `n/a` in both
  per-turn rows for it. Against an understated BASELINE a reported decrease in
  the needless-call rate is conservative — the real decrease can only be
  larger. The strict reader is unchanged and still refuses a sidecar that is
  present but does not cover a recorded call: that is a defect, not an old
  product.
- **The before/after plan validates the `--llm-block` against EACH arm's own
  product.** The block reaches both arms byte-identically, but it was checked
  once — against the product of the checkout the command runs from, i.e. the
  candidate's. A key the candidate added therefore passed the plan and was an
  unknown extra in the baseline, whose runner settings forbid extras: every
  baseline rollout raised at runner-build time, booked its attempt at the
  plan's assumed cost, and the arm halted under the budget guard having
  answered nothing — while the candidate arm was already spending at the
  endpoint. The plan now checks each commit out and asks that product the same
  question a rollout asks, before any workspace is built and before either arm
  starts; a refusal names the arm, the commit, the offending key and the two
  ways to fix it, and an accepted block prints one verdict line per arm under
  the block it judges. New `campaign/before_after_block_probe.py`; the
  worktree, the child environment and the "did the path really switch" check
  they share with the arm moved to `campaign/before_after_product.py`.
- **Per-turn metrics on the ask-your-docs path said nothing.** `turn` is a
  merge-time field the raw recorder never writes, and the binding read raw
  events with no turn, so a whole run collapsed into a single turn:
  `parallel_calls_per_turn` reported the run's total call count and the
  fan-out component charged calls that were never issued together. The
  binding is the only place holding both the conversation and the trace, so
  it now joins its finished message list to the trace it just wrote and
  leaves a `model_turns.json` sidecar (`seq → turn`) beside it;
  `trajectory/ask_events.py` reads the pair and produces the same `ToolEvent`s
  every metric already consumes. The join is by tool name and position within
  that name, which stays turn-correct when parallel calls are observed out of
  order, because every call of one model message carries one turn. A missing
  sidecar is refused rather than read as one turn — a collapsed turn looks
  like a measured number and is not one. The raw recorder's schema is
  unchanged, and `ToolCallRecord` gained no `turn` field: the contract suite
  pins the in-memory view against the trace, and the sidecar is what the
  metrics consume. Reading the raw capture and building a canonical tool event
  moved to `trajectory/server_capture.py`, now shared with the loop-join
  merger.
- **The MENTIONS configs capture MENTIONS edges when the pydocs system
  indexes in-process.** The product's reference-capture stage read a
  process-wide setting that only the product's own CLI and server install, and
  the eval suite never installed it, so every in-process index (the
  `pydocs-mcp` system's `_populate`) captured the shipped
  `calls, imports, inherits` whatever `reference_graph.capture` said. The
  stage now reads the config it is built from (the product's `[Unreleased]`
  fix for #347), so `mentions_on.yaml`, `dense_graph_mentions_f2llm330m.yaml`
  and `dense_graph_mentions_weighted_f2llm330m.yaml` index the MENTIONS edges
  their graph traversal is meant to measure. No result for those configs had
  been recorded, so no recorded number changes meaning. The bench cache still
  keys on the corpus and the ingestion pipeline hash only, which the capture
  settings do not reach, so with `--bench-cache on` an entry a stock-capture
  config built is reused for a MENTIONS config on the same corpus: run them
  with `--bench-cache off`, as the sweep's command in `EXPERIMENTS.md` does.
  A config that leaves `reference_graph.capture` at its default indexes
  exactly as before, and the campaign index cache's in-process seam
  (`index_project_in_process`: project-only, static, stock capture) builds
  byte-identical bundles.

## [0.2.0] — 2026-09-10

Headline: the eval suite grows from a retrieval sweep, a paired
agent-efficiency track and a harness optimizer into an evaluation platform.
New in this release: ask-agent auto-optimization scored per sample by a
rubric, data-valued experiment arms, trajectory instrumentation, SWE-bench
campaign infrastructure with a pre-registered GEPA optimizer, four task-framed
datasets plus the vendored `crosscommitvuln` corpus, and one console command
per entry point. Every library-coupled path now needs **pydocs-mcp 0.6.0**.

### Upgrade notes

Upgrading from 0.1.1:

- **Upgrade the product with the eval suite.** The `[retrieval]`, `[ask]` and
  `[all]` extras now require `pydocs-mcp>=0.6.0` (see Changed), because the
  optimize layer imports modules that first shipped in 0.6.0. Upgrade both in
  one command, e.g.
  `pip install -U pydocs-mcp "pydocs-mcp-eval[retrieval]"`. The base install
  (the black-box agent track) still needs only the `pydocs-mcp` CLI on
  `PATH`, but upgrade that CLI to 0.6.0 as well: trajectory capture reads
  the server trace (`PYDOCS_TRACE__*` settings, `server_events.jsonl`) that
  pydocs-mcp first writes in 0.6.0. Under pydocs-mcp 0.6.0, pydocs-mcp-eval
  0.1.x rejects its own `usage_skill` seed.
- **Seven flat module paths moved, with no shim.** `pydocs_eval.ast_match`,
  `corpus`, `report`, `baseline_record`, `ci_compare`, `plotting` and
  `serialization` now live under `metrics/`, `datasets/`, `reporting/` and
  `registries` (full map under Changed). Old imports raise
  `ModuleNotFoundError`, and `python -m pydocs_eval.ci_compare` /
  `python -m pydocs_eval.plotting` stop working: switch to the new paths or
  the `pydocs-eval-ci-compare` / `pydocs-eval-plot` commands.
- **Optimize run configs reject unknown top-level keys.** A misspelled or
  stale top-level section, or a rubric section that no arm binds, now fails
  at load. Remove stray keys from existing run configs before upgrading.
- **Ledgers and checkouts are redone once.** Agent-track ledger rows written
  by 0.1.x carry no `arm_hash`, so their tasks re-run (and are paid for)
  once. Repo-checkout cache directories under
  `~/.cache/pydocs-mcp/swe-qa-repos` are now keyed by a URL digest, so
  `swe-qa` and `swe-qa-pro` re-clone each repository once (network and disk
  needed); the old 0.1.x directories are orphaned, not corrupted, and can be
  deleted by hand.
- **Re-run `swe-qa` / `swe-qa-pro` retrieval baselines** recorded with 0.1.x:
  every retrieval on those file-set corpora scored 0.0 (see Fixed). RepoQA,
  `repoqa-structural` and DS-1000 scores are unaffected.
- **Six-tool candidates no longer validate.** A `tool_docs` or `usage_skill`
  candidate saved from a 0.1.x run describes only six tools and fails
  `validate()`; re-seed from the shipped nine-tool seed.
- **Repository checkouts:** overlay configs under `benchmarks/configs/` were
  renamed to dataset-free stems (see Changed). Update scripts that pass the
  old `repoqa_*` / `swe_qa_pro_*` / `ds1000_*` filenames.

### Added

- **Ask-agent auto-optimization** — three new optimizable artifacts:
  `ask_prompt` (the ask agent's system and query-rewrite prompts as one
  delimited document), `ask_architecture` (a cell over three searchable
  dimensions: `architecture`, `retrieval_config`, `max_agent_turns`) and
  `retrieval_config` (a literal `AppConfig` YAML overlay). A new `ask_rubric`
  fitness scores each sample: it runs the agent, applies the rubric section's
  boolean `gates:`, skips the judge on a failed gate when `fail_fast` is set,
  otherwise has an LLM judge grade the section's `criteria`, and records a
  weighted verdict in a per-sample ledger, so a resumed run skips samples
  already scored. Judge spend is capped by `budget.max_judge_calls`. Gate
  kinds: `min_answer_chars`, `answer_regex`, `gold_substring`,
  `gold_substring_all` (every gold candidate must appear verbatim — by
  default `gold.file_set` plus each string value of `gold.extra`; a
  `params.keys` list selects the candidates instead, `"file_set"` for the
  file set and any other name for that `gold.extra` key; vacuous pass when
  none remain), `used_indexed_tools` (at least `n` calls, default 1, to any
  of the nine pydocs-mcp tools as the server recorded them in its trace,
  `grep` / `glob` / `read_file` included, so a harness-local tool or a
  shell-out to the system `grep` binary never counts), `max_turns` and
  `max_wall_seconds`. A new `config_search` optimizer walks an architecture
  grid, configured by a `config_search:` run-config section (`strategy`:
  `grid` | `random` | `halving`, `seed`, `sample_size`, `dimensions`) that
  is required when `optimizer: config_search`; a new top-level `rng_seed`,
  recorded in provenance, seeds its draw when the section sets no `seed`.
  Shipped configs: `optimize_ask_prompt.yaml`,
  `optimize_ask_architecture.yaml`. The new `[ask]` extra installs
  `pydocs-mcp[harness-ask-your-docs]>=0.6.0` for the in-process agent; it is
  not part of `[all]`, so install it explicitly. The agent is driven through
  the product's `build_agent(prompts=...)` and
  `reformulate(rewrite_template=...)` seams, first shipped in pydocs-mcp
  0.6.0. The ask binding resolves the product's `ask_your_docs.llm` block
  from the run's pydocs config and warns when a `PYDOCS_ASK_YOUR_DOCS`
  environment variable overlays it. The eval tasks are single questions, so
  an `ask_prompt` candidate's rewrite section is carried and validated but
  not yet exercised.
- **Experiment arms in optimize run configs** — a run config may declare an
  `arms:` block. Each arm is a seven-key cell (unknown keys rejected):
  `runner` (a `module.path:attribute` harness factory, imported only when
  the arm is scored), its `settings`, `tool_names` (a subset of the frozen
  nine tools; `null` = all nine), a registered `dataset`, a `task_name`
  framing, a `guidance` artifact family, and a `scoring` block whose `rubric`
  names a top-level rubric section (`ask_rubric`, `ask_rubric_localization`
  or `ask_rubric_file_localization`). The orchestrator runs one pass per arm;
  `max_usd` and `max_judge_calls` are enforced against one shared budget,
  and `max_trials` is divided across arms. The trials and sample ledgers add
  an `arm_hash` to their resume key (a SHA-256 over the arm's canonical cell,
  the candidate's fingerprint and the harness's guidance delivery map), so
  two arms never resume each other's rows. A new `search_skill` artifact
  family optimizes the product's packaged search-guidance document,
  validating every candidate with the product's `parse_skill_artifact`.
  Shipped configs: `optimize_search_skill.yaml`,
  `optimize_search_skill_repo_qa.yaml`, `optimize_search_skill_bug_loc.yaml`.
  A config with no `arms:` block runs one implicit arm scoring `ask_rubric`,
  and its ledger lines keep their previous bytes, so existing ledgers keep
  resuming.
- **External-harness guidance delivery** — the headless-CLI track can now
  actually receive a candidate's sectioned guidance. Its `BACKBONE`,
  `TASK_HEAD: <task>` and `HARNESS_TASK_HEAD: external.<task>` sections fold
  — in that order, single newline, byte-identical to the in-process
  harness's fold — onto `claude --append-system-prompt`, leaving the shared
  task scaffold untouched so the only difference between the two measured
  arms stays the tool surface. Another harness's sections are recognized and
  dropped; an unrecognized one — or a task-scoped one handed in with no task
  named — raises rather than being silently discarded. Which task's sections
  fold is a new `AgentTrackConfig.task_name`. Runs that attach no candidate
  guidance build byte-identical argv to before. The delivery map, the task
  name and the channel a pass delivered on are all arm state, so the
  external default arm hash moves (`f5b2649c…` → `0576f4de…`); no recorded
  campaign or committed ledger is affected. This entry describes the
  **standalone paired-efficiency CLI**, which stays library-free by contract
  and keeps its own copy of the command builder and transcript reader; the
  **optimization** path for the same arms runs through the product's
  external harness (pydocs-mcp 0.6.0), and an executed parity check keeps
  the two spellings identical.
- **Trajectory-grounded scoring** — the rubric's deterministic layer becomes
  *scored*. A rubric section may now spell a `checks:` block (weighted 0-1
  measures with `required` / `fail` policy) beside its boolean `gates:`.
  With no `checks:` the layer is exactly the gate pass fraction it always
  was, so no existing objective's verdicts move; with `checks:` the gates
  fall back to pure screens (still required, still sparing the judge) and
  the weighted measures own the layer's whole mass. Any registered gate kind
  also works as a check (pass 1.0, fail 0.0). A check row may set
  `applies_to` (the task types it runs on; on any other type it neither
  scores nor blocks) and `weight_by_type` (per-type weight overrides). A
  task's type is its task-id prefix before the first `/`, or the whole id
  when it has none. The composite renormalizes over the checks that apply,
  so task types graded on different check sets share one 0-1 scale
  (unweighted mean when none of them carries weight). A row with an unknown
  key or a mistyped value is rejected at load; duplicate check names raise.
  New check kind **`gold_location_evidenced`** measures what a run's
  *retrieval* named rather than what its answer *said* — the fraction of the
  gold file set named in a server-recorded tool call's arguments or returned
  among its distilled result identifiers, read off the trace. Named, not
  read: a recorded result identifier does not imply the model saw the item,
  and broad enumerations (a repo-wide `glob`, a package overview) are
  excluded so listing everything buys no evidence. The `search_skill`
  configs' `ask_rubric` and `ask_rubric_localization` sections apportion
  their deterministic layer `{gold_recall 0.75, gold_location_evidenced
  0.25}`, and the bug-localization config's `ask_rubric_file_localization`
  section splits it 0.5/0.5, all as pure measures that can never gate. A new
  per-section `keep_deterministic_on_skip` (default `false`) scores a sample
  whose judge was skipped by `fail_fast` as `gate_weight` × the
  deterministic score instead of 0.0, so never above `gate_weight`; the
  shipped repo-QA and bug-localization `search_skill` configs set it. Rubric
  objective hashes move accordingly (the new flag is hashed even at its
  default); no campaigns were recorded against the previous ones.
- **Task-framed evaluation datasets: `repoqa-qa`, `swe-qa-questions`,
  `swe-bench-verified-loc`, `lca-bug-loc`** — each mints rows under a task
  name with three-part ids (`<dataset>/<task_name>/<record_id>`); every
  pre-existing dataset's ids are unchanged. Under `repo_qa`: `repoqa-qa`
  turns RepoQA's needle descriptions into questions whose gold is the
  needle's symbol plus its repo-relative path, and `swe-qa-questions` keeps
  SWE-QA's question/answer pairs with their citation-resolved file-set gold;
  both delegate acquisition, caching, splits and pins to the wrapped
  dataset. Under `bug_loc` (file-level bug localization, arXiv:2607.11046):
  `swe-bench-verified-loc` covers SWE-bench Verified's 500 Python instances
  (gold: the fix patch's non-test files) and `lca-bug-loc` the 50-instance
  Python slice of Long Code Arena's `test` split (gold: the record's changed
  non-test files). The Java/Kotlin slices are left out because `.java` /
  `.kt` are outside the indexer's allowlist. Both are revision-pinned
  HuggingFace parquet files with row counts checked on read, resolved
  through the new `[datasets-parquet]` extra (`huggingface_hub`, `pyarrow`;
  `[datasets-swe]` installs the same pair); `[all]` now includes both wheels.
  Their corpora materialize the product's default indexable extension set,
  since fix patches often touch `.rst`, `.cfg` and `.toml`; every other
  repo-backed dataset still materializes `.py` only.
- **`hit@k` and `map@k` retrieval metrics** — `hit@k` names what `recall@k`
  has always computed (a per-instance 1/0 hit rate, not fractional recall);
  both share one implementation, so no recorded `recall@k` number moves.
  `map@k` is mean average precision over the same top-`k` ranking, crediting
  each distinct gold item once. Both rank chunks, not files, so they are not
  directly comparable to file-level numbers in the literature. Neither is in
  the default `--metrics` set; request them, e.g.
  `--metrics recall@5,hit@1,hit@5,hit@10,map@5`.
- **Trajectory instrumentation** — new `pydocs_eval.trajectory` package. Its
  rollout driver runs one headless `claude -p` rollout under a runner-chosen
  trajectory UUID (passed as `--session-id`), hands the served `pydocs-mcp`
  its tracing settings through the `.mcp.json` server `env` block
  (`PYDOCS_TRACE__ENABLED` / `__TRAJECTORY_ID` / `__DIR`), and saves the raw
  stream-json output, a run record and the post-run `git diff` patch as a
  content-addressed blob. `merge_trajectory` / `write_events_jsonl` join the
  product's `server_events.jsonl` with that stream into one canonical
  `events.jsonl` (schema version 1). A correlation failure raises a typed
  error: a missing or corrupt server trace, a trajectory-id, schema-version
  or tool-call-count mismatch, or a fired suggestion that cannot be attached
  to its call. On the merged stream the package computes rule-based metrics,
  surfaced → inspected → used evidence tiers per file ("used" = touched by
  the agent's final patch; first-touch credit goes to gold-patch files), a
  failure taxonomy, a shaped score and feedback text; score weights and the
  taxonomy ship as package YAML. New console command
  `pydocs-eval-compute-metrics <trace-dir>` recomputes every derived metric
  from merged trajectories (`events.jsonl` + `facts.json`) and writes
  per-trajectory JSON records, `aggregate.json` and `report.txt`. Patch
  capture leaves out `__pycache__` / `*.pyc` / `*.pyo` and runs `git` with
  `core.fsmonitor` / `core.hooksPath` blanked and `--no-ext-diff
  --no-textconv`, so an agent-written workspace cannot run a program during
  capture. New base dependency: `unidiff>=0.7,<1.0`. Rationale:
  `docs/adr/0009`–`0012`.
- **SWE-bench campaign infrastructure** — `pydocs_eval.datasets_swe` pins
  SWE-bench-Live (`full`) and SWE-bench Pro (`test`) to fixed Hugging Face
  revision SHAs, excludes Live instances whose org appears in the Pro Python
  test set, and builds seeded, repo-disjoint dev/val splits (about 2:1, 10%
  per-repo cap on dev) plus a discriminative subset (dev instances the
  target model fails and a reference model solves, rounded down to a
  multiple of 12). Rebuilding them (`python -m pydocs_eval.datasets_swe
  overlap|splits|touch-log|all`) needs the new build-only `[datasets-swe]`
  extra (`huggingface_hub>=0.20`, `pyarrow>=15.0`); outputs are committed
  under `benchmarks/data/swe/`, not shipped in the wheel.
  `pydocs_eval.campaign` is a campaign runner loop (library code with an
  injected rollout function): bounded worker pool, a cost-ceiling guard that
  stops launching rollouts, one retry then exclusion on an infrastructure
  failure, JSONL-ledger resume, an immutable campaign lockfile whose
  canonical-JSON hash is the campaign ID, and a project-index cache of
  pristine checkouts keyed by (repo, base commit, scope). The scope
  component defaults to the product's pipeline hash, so a cached index built
  under one extension scope is never reused for a differently scoped cell.
  `python -m pydocs_eval.campaign` offers `prebuild-index`, `aggregate`,
  `build-strata` and `smoke-check`; it does not launch rollouts.
  `aggregate --stratum-map PATH` (a `.json` object, or JSONL rows with
  `instance_id` and `stratum`) adds a per-contrast `strata` block with one
  paired sub-contrast per stratum (unmapped instances fall into `unknown`);
  `build-strata --run-dir RUN [--out map.json]` writes such a map from a run
  dir's gold files (`gold_touches_non_python` / `gold_python_only`). A cell
  may name a serve overlay, resolved to a packaged
  `pydocs_eval/campaign/overlays/*.yaml` (shipped: `suggestions_off`, which
  turns the product's routing suggestions off) and passed to the served
  product through `pydocs-mcp --config`; an unknown name raises
  `UnknownOverlayError`. `agent_track.ArmConfig` gains `tools=`, an explicit
  tool grant replacing the profile grant (for drop-one arms); `tools=()` or
  a combination with `no_tools` is rejected, and arms leaving it unset are
  byte-identical. `pydocs_eval.metrics.aggregate` adds stdlib McNemar
  helpers: `mcnemar_exact_p` (two-sided exact), `mcnemar_sample_size`
  (per-cell sizing) and `mcnemar_from_pairs` (paired counts, resolve delta,
  p-value, bootstrap CI). Rationale: `docs/adr/0013`–`0016`.
- **GEPA optimizer and pre-registered campaign scaffolding** — a new `gepa`
  optimizer drives PyPI `gepa` through a thin adapter, installed with the
  new `[optimizers-gepa]` extra (pinned `gepa==0.1.4`, since the adapter
  binds to that release's API; also in `[all]`). Before a candidate costs a
  rollout it must pass a validity firewall: the product's strict
  `parse_sections` / `validate_sections` (so it needs the `[retrieval]`
  extra) plus a section-order check. Every proposed candidate, rejected ones
  included, is appended to a candidate ledger with its lineage. Acceptance
  never uses GEPA's shaped scores: a candidate is accepted only when a
  one-sided paired exact McNemar test on per-instance resolves
  (`mcnemar_exact_p_one_sided`) meets the pre-registered `alpha` and its
  cost is within the pre-registered threshold.
  `AcceptanceConfig.statistic = "signed_rank"` (code-only; no run-config or
  pre-registration key yet; other values raise `ValueError`) swaps in an
  exact one-sided Wilcoxon signed-rank test
  (`wilcoxon_signed_rank_p_one_sided`, stdlib-only) over
  `soft_resolve_fraction`, the fraction of FAIL_TO_PASS tests observed
  passing, which is 0.0 on any PASS_TO_PASS regression, infra error, failed
  or unapplied patch, or no observed FAIL_TO_PASS test. New console
  commands: `pydocs-eval-prereg` prints the pre-registration hash, whether
  the campaign can launch, and a power/false-accept table for
  `optimize/configs/campaign_preregistration.yaml` (with `--authorize` it
  exits 3 while measured slots are unfilled);
  `pydocs-eval-optimizer-preflight` dry-runs the whole candidate loop at no
  spend and exits 0 only when it reports `HEALTHY`; its default rollout
  fixture exists only in a source checkout, so an installed wheel must pass
  `--rollout-dir` (without it the command exits 2 and says so). A rollout can serve a
  candidate description document via `RolloutRequest.descriptions_path`,
  which sets the product's `PYDOCS_SERVE__DESCRIPTIONS_PATH` in the served
  server's env. Rationale: `docs/adr/0017`–`0020`.
- **`crosscommitvuln` dataset and the combined `swe-qa-pro+crosscommitvuln`
  corpus** — a single-repo, single-commit security needle-search QA corpus
  derived from CrossCommitVuln-Bench (CC BY 4.0; attribution in the vendored
  `NOTICE`): 25 records over 24 repositories, shipped in the wheel and sdist
  and read through `importlib.resources`, so loading them downloads nothing.
  Each record pins one `repo_url` and a full 40-hex pre-fix `prefix_sha`
  (malformed records are dropped and counted in a log line), and the
  snapshot is materialized without `.git`, so the agent sees no commit
  signal. Gold: the CVE id, the CWE ids, a source-to-sink mechanism
  description, and the vulnerability's `.py` files at `prefix_sha`. The
  checkout uses the network by default and runs offline per repo when a
  bundle directory (`$PYDOCS_CCV_BUNDLE_DIR`, else
  `~/.cache/pydocs-mcp/crosscommitvuln-bundles`) holds a prewarmed
  `<repo>-<first 8 hex of sha256(url)>.bundle` (the digest naming described
  under Changed, and the name the prewarm script under `benchmarks/tools/`
  writes); a set-but-missing directory logs that the airgap is not in
  effect. `CombinedDataset` (`swe-qa-pro+crosscommitvuln`) merges it with
  SWE-QA-Pro under disjoint prefixes (`sweqapro/…`, `ccv/…`), interleaved
  round-robin so a run truncated by `max_tasks` or budget still sees both,
  and split train/holdout by a hash of each record id; it takes no top-level
  `fixture_path`. The optimize CLI's `--dry-run` split report also breaks
  the train/holdout counts down by prefix whenever a split key carries one.
  The shipped
  `optimize_ask_prompt_combined.yaml` screens with `gold_substring_all` over
  `cve_id` + `cwe_id_0`, which passes vacuously on SWE-QA-Pro rows. The
  product's 0.6.0 discovery floor excludes every `crosscommitvuln`
  directory, which keeps this vendored gold out of any index built next to
  an installed copy. Build and bundle-prewarm scripts live under
  `benchmarks/tools/`, outside the wheel.
- **Multi-task sampling and run-plan arms** — `pydocs_eval.optimize.multitask`
  adds two comparable axes for mixed-dataset optimization. Within a run, a
  registered batch sampler orders or draws rows: `uniform` (the control, the
  existing seeded shuffle), `stratified` (proportional, at least one row per
  type, optional explicit weights) or `oversample` (replicates minority rows
  to a target share). Samplers take a row's type from its `task_type` key,
  else its task-id prefix, and refuse a row with neither. Across runs, a
  registered plan drives an injected train callable: `single` (the control),
  `per_dataset` (one run per type from the same seed, merged per guidance
  slot) or `curriculum` (sequential, each run seeded with the previous
  result); plans group rows by their `task_type` key. `AskRubricFitness`
  gains a `sampler` field that orders its train/holdout split by task-id
  prefix before the budget cutoff; the default `UniformSampler` keeps that
  order byte-identical. Programmatic only: no run-config or YAML key selects
  a sampler or plan yet.
- **Console commands** — the wheel now installs a console command for each
  of six existing module entry points (0.1.x installed none; the new
  `pydocs-eval-compute-metrics`, `pydocs-eval-prereg` and
  `pydocs-eval-optimizer-preflight` commands are described above):
  `pydocs-eval` (the retrieval sweep, `python -m pydocs_eval.runner`),
  `pydocs-eval-optimize` (`python -m pydocs_eval.optimize`),
  `pydocs-eval-agent-track` (`python -m pydocs_eval.agent_track`),
  `pydocs-eval-ci-compare` / `pydocs-eval-plot`
  (`pydocs_eval.reporting.ci_compare` / `.plotting`) and
  `pydocs-eval-bench-cache` (`python -m pydocs_eval.bench_cache_cli`). The
  `python -m` forms keep working, except for the two flat modules that moved
  (see Changed). `pydocs_eval.agent_track` now exports its public API from
  the package root (`from pydocs_eval.agent_track import ArmConfig,
  run_agent_track, …`; in 0.1.x the root exported nothing), including the
  run defaults `DEFAULT_MODEL`, `DEFAULT_MAX_TURNS`,
  `DEFAULT_TASK_TIMEOUT_SECONDS` and `DEFAULT_RNG_SEED`, which were
  underscore-private in `agent_track._types`.
- **New benchmark overlays (repository checkout only; not shipped in the
  wheel)** — `benchmarks/configs/parent_rollup.yaml` /
  `parent_rollup_baseline.yaml`, an A/B pair for the product's opt-in
  `parent_rollup` retrieval step, and three dense overlays that drive remote
  OpenAI-compatible embedding endpoints.

### Changed

- **BREAKING: the `pydocs-mcp` floor rises to 0.6.0.** The `[retrieval]`
  and `[all]` extras now declare `pydocs-mcp>=0.6.0` (0.1.x: `>=0.5.1`);
  the new `[ask]` extra declares the same floor. 0.2.0 imports five product
  modules that first shipped
  in 0.6.0 (`pydocs_mcp.harness.core.run_contract`,
  `harness.core.skill_artifact_loader`, `harness.ask_your_docs.binding`,
  `harness.ask_your_docs.prompts`, `application.description_source`); under
  the old floor pip kept an installed 0.5.x product and those imports failed
  at runtime. The version-skew hint now names the 0.6.0 floor (with
  `pydocs_mcp.harness.core.run_contract` as its example of a missing
  module), and a parity test keeps the three extras' floors equal to the
  hint's single source (`_REQUIRED_PYDOCS_MCP`). `[ask]` stays out of `[all]`
  because it pulls the agent stack into the union.
- **BREAKING: seven flat module paths from 0.1.x moved, with no
  compatibility shim.**
  `pydocs_eval.ast_match` → `pydocs_eval.metrics.ast_match`,
  `pydocs_eval.corpus` → `pydocs_eval.datasets.corpus`,
  `pydocs_eval.report` → `pydocs_eval.reporting.report`,
  `pydocs_eval.baseline_record` → `pydocs_eval.reporting.baseline_record`,
  `pydocs_eval.ci_compare` → `pydocs_eval.reporting.ci_compare`,
  `pydocs_eval.plotting` → `pydocs_eval.reporting.plotting`,
  `pydocs_eval.serialization` → `pydocs_eval.registries`. Old imports raise
  `ModuleNotFoundError`, and `python -m pydocs_eval.ci_compare` /
  `python -m pydocs_eval.plotting` stop working: use the
  `pydocs_eval.reporting.*` module paths or the `pydocs-eval-ci-compare` /
  `pydocs-eval-plot` console commands (see Added).
- **BREAKING: optimize run configs reject unknown top-level keys** —
  `OptimizeRunConfig` now loads with `extra="forbid"`, so a misspelled
  top-level section fails at load instead of being silently ignored (keys
  inside nested sections are still unchecked, except within an `arms:`
  cell). A rubric section that is declared but bound by no arm is also
  rejected, because it would never score: `ask_rubric_localization:` in a
  config with no `arms:` block fails, since such a config scores only
  `ask_rubric`. Remove stray top-level keys from existing run configs before
  upgrading.
- **Tool-list artifacts follow the nine-tool surface** — the `tool_docs` and
  `usage_skill` artifacts read the tool list from the installed product's
  `TOOL_DOCS`, so a `tool_docs` candidate must carry all nine tool sections
  in contract order and a `usage_skill` candidate must name all nine tools,
  `grep` / `glob` / `read_file` included. The shipped `usage_skill` and
  `ask_prompt` seeds now describe the three filesystem tools. With
  pydocs-mcp 0.6.0 installed, pydocs-mcp-eval 0.1.x rejects its own
  `usage_skill` seed, which names only six tools, and any six-tool candidate
  saved from a 0.1.x run fails `validate()` the same way. The `tool_docs`
  overlay server now binds a candidate through the product's own
  `description_source.apply_source` loader, the path behind
  `serve --descriptions`, so an overlay run and a production override bind
  the served descriptions the same way.
- **Agent-track resume is keyed by arm** — every paired-efficiency ledger
  row now records the `arm_hash` it ran under, and a rerun skips only tasks
  already recorded under the same arm. The hash covers `--dataset`, each
  arm's model, tool surface, `max_turns` and MCP attachment, the judge
  model, the RNG seed, the per-task timeout, the task-scaffold version, the
  task name, and any candidate guidance with its delivery channel; budget
  caps (`--max-tasks`, `--max-usd`) do not move it. A changed arm now
  re-runs every task instead of silently reusing answers recorded under
  other conditions, and the report footer counts discards and spend for its
  own arm only. Rows written by 0.1.x carry no `arm_hash`, so their tasks
  re-run, and are re-paid, once.
- **Repo-checkout cache keys include a URL digest** — base clones under
  `~/.cache/pydocs-mcp/swe-qa-repos` are now named
  `<repo>-<first 8 hex of sha256(url)>` rather than the bare repo name, which
  collided across organizations (`orgA/utils` and `orgB/utils` shared one
  clone). As before, URLs differing only by a trailing `/` or `.git` share
  one entry. Clones made by 0.1.x are orphaned, not corrupted: `swe-qa` and
  `swe-qa-pro` re-clone each repository once on first use, which needs
  network and disk; the old directories can be deleted by hand. The datasets
  new in this release (the bug-localization pair and `crosscommitvuln`,
  including its prewarmed bundle files) use digest names from the start.
- **The `retrieval` fitness scores the candidate** — in 0.1.x it was
  scaffolding that ignored the candidate; it now sweeps each candidate's
  config overlay on the run's train or holdout split, and is the free first
  rung of `optimize_ask_architecture.yaml`. Only overlay-carrying artifacts
  (`retrieval_config`, `ask_architecture`) can use it; a ladder that pairs
  it with any other artifact fails at load.
- **Registries populate themselves** — the dataset, metric, tracker and
  system registries (`pydocs_eval.registries`) and the artifact, fitness and
  optimizer registries (`pydocs_eval.optimize.registries`) import their
  implementations on first read (`names()` / `build()`); previously a
  registry read after importing only its module came back empty. Optional
  libraries (`skillopt`, `gepa`, `mlflow`) are still imported only when
  used; the optimize registries still need the `[retrieval]` extra. The
  `tool_docs` overlay artifact's `validate()` now goes through the same
  firewall as optimizer candidates, so its token budgets match the
  product's: only the nine tool sections count, and `SERVER_INSTRUCTIONS` no
  longer counts toward the per-tool cap or the surface total. An overlay
  with a long server-instructions block that was rejected before is now
  accepted, as a real `serve` accepts it.
- **Extras** — new `[ask]`, `[optimizers-gepa]`, `[datasets-parquet]` and
  the build-only `[datasets-swe]` (each described under Added). `[all]`
  adds `gepa==0.1.4`, `huggingface_hub` and `pyarrow`, and raises its
  product floor to `pydocs-mcp>=0.6.0`, beside the `mlflow` and
  `skillopt>=0.2,<0.3` it already carried; `[ask]` is deliberately left
  out. The base install gains `unidiff>=0.7,<1.0`.
- **Benchmark overlay configs renamed (repository checkout only; not
  shipped in the wheel)** — overlay filenames under `benchmarks/configs/` no
  longer encode a dataset: the `repoqa_` / `swe_qa_pro_` / `ds1000_`
  prefixes are dropped (38 renames), four byte-identical cross-dataset
  duplicates are deleted, and `swe_qa_pro_graph.yaml` survives as
  `dense_graph.yaml`. The six hybrid overlays for the 330M-parameter dense
  embedder also gain a `330m` model-size suffix. Pair an overlay with a
  dataset through the runner's `--dataset` flag. The full old→new map is in
  `benchmarks/EXPERIMENTS.md` §"Renamed configs". The stale
  `benchmarks/requirements.txt` (a pinned duplicate of `pyproject.toml`) is
  removed.

### Fixed

- **File-set retrieval scores no longer collapse to 0.0** — the relevance
  predicate took the resolved-chunk-id branch whenever that key was present,
  and the runner injects it for every system exposing a gold resolver, which
  in 0.1.x was every registered system (`pydocs-mcp` and its `-composite` /
  `-tree-only` / `-tree-parallel` variants, `pydocs-oracle`, and the
  external baseline systems). On file-set corpora whose gold carries no
  document contents (`swe-qa`, `swe-qa-pro`) the injected set is empty, so
  every retrieval there scored 0.0. The branch now fires only for a
  non-empty set. Re-run any `swe-qa` / `swe-qa-pro` retrieval baseline
  recorded with 0.1.x; RepoQA, `repoqa-structural` and DS-1000 scores are
  unaffected.

## [0.1.1] — 2026-07-10

### Added

- **`[optimizers-skillopt]` extra** — declares `skillopt>=0.2,<0.3`, a
  PyPI version range that installs from a PyPI mirror in air-gapped
  environments and is legal in published metadata. `[all]` now includes it.

### Changed

- **The `skillopt` optimizer adapter drives the released PyPI `skillopt`
  0.2.x** instead of a source install pinned to a commit. The adapter now
  invokes the `skillopt-train` console script (`scripts.train:main --config
  <yaml>`); the generated plugin ships a `run.py` that registers the adapter
  in `scripts.train._ENV_REGISTRY` before handing over its arguments, and
  the former dataloader / rollout / evaluator plugin files become one
  generated `PydocsEnvAdapter(EnvAdapter)` module (rollouts graded hard =
  gold containment, soft = token F1; `conversation.json` written for the
  reflection step). The generated config follows skillopt 0.2.x's structured
  `model` / `train` / `gradient` / `optimizer` / `evaluation` / `env` schema.
  skillopt 0.2.x has **no spend key**: `max_trials` maps onto rollout counts
  (selection + epochs × (batch + selection) ≤ `max_trials`), and `max_usd`
  is recorded in the generated YAML as a comment only and is **not
  enforced** — the outer holdout-gate cap remains the only USD bound.
  `best_skill.md` parsing and the candidate firewall are unchanged, and a
  canary test pins the consumed 0.2.x surface. A dry run reports `skillopt`
  as SKIPPED when the extra is absent.

## [0.1.0] — 2026-07-10

First release on PyPI. The benchmark suite, previously the unpublished
`pyctx7-benchmarks` distribution, ships as **`pydocs-mcp-eval`**.

### Added

- **Distribution and import package** — the import package is hoisted from
  `benchmarks.eval.*` / `benchmarks.optimize` to **`pydocs_eval.*`**
  (`pydocs_eval.datasets`, `pydocs_eval.systems`, `pydocs_eval.optimize`,
  …); the `benchmarks/` directory name is unchanged. Requires Python 3.11+.
- **Extras split by coupling, not by feature** — the base install serves
  the black-box agent-efficiency track, which needs only the `pydocs-mcp`
  CLI on `PATH`. The `[retrieval]` extra declares `pydocs-mcp>=0.5.1` (the
  first release exporting the `tool_docs` contract constants the artifacts
  consume) for the library-coupled parts: the in-process retrieval systems,
  the optimize overlay server and the `tool_docs` / `usage_skill`
  artifacts. Also `[mlflow]` and the `[all]` union. Import guards at those
  boundaries raise an actionable `pip install "pydocs-mcp-eval[retrieval]"`
  hint when the extra is missing, and a separate hint naming the required
  floor and the installed version when `pydocs-mcp` is too old.
- **Retrieval-quality track** — a dataset × system × config-overlay sweep
  (`python -m pydocs_eval.runner`) over RepoQA-SNF (arXiv:2406.06025),
  DS-1000 (arXiv:2211.11501), SWE-QA, SWE-QA-Pro and `repoqa-structural`,
  including a `small_dev` split. Systems: `pydocs-mcp` and its `-composite`
  / `-tree-only` / `-tree-parallel` variants, `pydocs-oracle`, and the
  external baseline systems. Metrics include recall@k, precision@1, MRR,
  nDCG@k, needle pass@1, library resolution@k and coverage; runs are
  tracked in JSONL or MLflow, with report, baseline-record, CI-compare and
  plotting helpers and a bench-cache CLI.
- **Paired agent-efficiency track** (`python -m pydocs_eval.agent_track`) —
  indexed versus bare agent runs on the same tasks, graded by a blind judge,
  with spend guardrails (`--max-tasks`, `--max-usd`) and a resumable ledger.
- **Harness optimization layer** (`python -m pydocs_eval.optimize`) —
  optimizes the `tool_docs` (the tool descriptions, as one delimited
  document) and `usage_skill` (a usage guide with a shipped seed) artifacts
  under the product's tool-docs validation rules. Parts: a paired-agent
  fitness with a parity pre-gate, a retrieval fitness scaffold, a
  deterministic train/holdout split, a fitness ladder, a trials ledger that
  resumes by (fingerprint, split), and an orchestrator that accepts a
  candidate only through a holdout gate. Optimizers: `critique_refine`
  (with a constraint firewall) and a `skillopt` adapter. The adapter needs
  `skillopt` installed from source at a pinned commit, which cannot be an
  extra because PyPI rejects direct-URL requirements; `ensure_available()`
  prints the exact install command. `--dry-run` walks the whole pipeline at
  no spend. The seed and the shipped run-config YAMLs ship as package data.

[Unreleased]: https://github.com/msobroza/pydocs-mcp/compare/eval-v0.2.0...HEAD
[0.2.0]: https://github.com/msobroza/pydocs-mcp/compare/eval-v0.1.1...eval-v0.2.0
[0.1.1]: https://github.com/msobroza/pydocs-mcp/releases/tag/eval-v0.1.1
[0.1.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/eval-v0.1.0
