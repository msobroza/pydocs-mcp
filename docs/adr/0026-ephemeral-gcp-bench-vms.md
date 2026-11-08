# ADR 0026 — Bench VM: ephemeral GCP runs from versioned run specs

**Status:** Proposed — records the decisions of the approved bench VM design (`docs/superpowers/specs/2026-11-06-bench-vm-design.md`) before any bench VM code is built on them; the owner accepts it on review of its pull request. Amended 2026-11-08, owner-approved before its code: provider key values are redacted from the benchmark's output before that output reaches disk or Cloud Storage (see "Amendment (2026-11-08)"). ·
**Date:** 2026-11-06 · **Phase:** eval infrastructure

**Amends ADR 0014 Decision 1:** one ephemeral VM per run at the same x86_64 floor, in place of a single long-lived remote host; artifacts live only in the owner's private Cloud Storage buckets in `europe-west4`, in place of the host's local disk. ADR 0014 Decisions 3 (index-cache economics) and 4 (resumable-ledger orchestration) are unchanged.

- **Decision area:** where benchmark runs execute when the development machine cannot run them; how a run is described, named, launched, bounded in cost, persisted and resumed after a spot preemption; how the machine and the operator authenticate.
- **Siblings:** ADR 0014 (the x86_64 rollout floor, the canonical-checkout index cache and the resumable campaign ledger; this ADR amends its Decision 1), ADR 0013 (the datasets and splits a run spec names), ADR 0015 (metered API-key billing, which the agent track keeps on a bench VM), ADR 0016 (campaign design, whose long paid campaigns run on standard capacity rather than spot).

Vocabulary is the repo glossary (`CONTEXT.md`): *Run spec*, *Bench VM*. "Run record" and "relaunch" are used below in their plain sense; they are candidate glossary terms, not yet defined.

## Context

ADR 0014 measured the development machine (arm64, 8 GiB RAM, no container runtime) and set the floor for rollouts at x86_64, at least 8 cores, 32 GB RAM and 250 GB disk. Its Decision 1 answered with "a single remote Linux x86_64 host runs every rollout component" and "all artifacts stay on the host's local disk", and left provisioning that host to an owner checkpoint; its status still read "host provisioning pending".

Retrieval sweeps need the same kind of machine, and the GPU path needs CUDA hardware the owner does not have locally. A long-lived host bills while idle, and its installed environment drifts from what CI installs. Nothing records which commit, configs, dataset, split and machine produced a benchmark number: a run starts from a hand-typed command line, and the JSONL header's git SHA is empty wherever there is no git checkout. The cheapest capacity, spot VMs, can be reclaimed at any moment, so it is usable only if every benchmark resumes from state that outlives the machine.

## Evidence

- **The environment a run needs is already defined by CI, and it is fragile off CI.** The `benchmark-repoqa` job (`.github/workflows/benchmark.yml`) installs the pthread OpenBLAS variant and sets `LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libopenblas.so.0`, because turbovec links `cblas_sgemm`. The GPU path patches the CUDA library path in at run time (`benchmarks/scripts/run_eval_gpu.sh`). A hand-maintained host has to reproduce both by hand.
- **Dense indexing is slow on CPU.** It runs at 60–215 s per RepoQA needle, which is why `run_eval_gpu.sh` exists. A retrieval sweep on CPU is days of machine time.
- **The retrieval runner cannot resume.** `JsonlExperimentTracker` opens a new timestamped file per leg on every invocation (`benchmarks/src/pydocs_eval/trackers/jsonl_tracker.py`), and its per-task metric lines carry a step counter but no task id. The campaign ledger (`queue.jsonl`) and the agent-track ledger already resume, but only from the disk of the machine that wrote them.
- **Most index caches cannot be reused on another machine.** The bench index cache key is `sha256(resolved corpus dir, ingestion pipeline hash)`, and `materialize_corpus` (`benchmarks/src/pydocs_eval/datasets/corpus.py`) creates a fresh `mkdtemp` directory per call. RepoQA, structural recall, SWE-QA, SWE-QA-Pro, bug localization, the example-needle chat set and cross-commit vuln all build their per-task corpora through it. Some caches are reusable: DS-1000's committed reference project (passed by `--corpus-dir`), the before/after per-corpus workspaces with stable slugs, and ADR 0014's canonical-checkout cache. All three key on stable paths.
- **The index caches key on absolute paths.** The bench cache hashes the resolved corpus path and the product's per-project cache name hashes the project path (`db.py`, `cache_path_for_project`). A cache restored on another machine is valid only at the same paths.

## Options considered

- **ADR 0014's long-lived remote host.** Rejected as the execution model. It bills while idle, its dependencies drift from CI, its results live on one disk, and a result produced on it cannot be traced to an exact commit and machine. Nothing in this ADR forbids keeping one, but no tooling targets it.
- **A GitHub Actions launcher.** Deferred. Runs are rare, owner-triggered and spend money; a local `make` target is enough and puts no cloud credentials into CI. A later launcher can call the same command.
- **A managed batch service (Cloud Batch, Vertex AI custom jobs).** Rejected. Each puts its own job model between the run spec and the machine. The design needs direct control of the VM: spot or standard capacity, the maximum run duration with its DELETE action, zone fallback on a stockout, instance metadata and the preempted flag. It also needs one run record across attempts. A job service would own the retry and preemption semantics that the run record must own, so the launcher would need the Compute API anyway.
- **Automatic relaunch (a managed instance group of size one, or a watcher).** Rejected. A group recreates a VM that the runtime cap deleted too, so a failing run could loop at full cost. Relaunch stays a manual re-run of the same make command.
- **Building the environment on the VM at boot, with no image.** Rejected. Installs repeat on every attempt and pick up whatever an unpinned dependency resolves to that day. An image per commit pays the build once and pins the environment to the commit's `uv.lock`.
- **A third-party experiment or artifact service.** Rejected. Results, run state and caches stay in buckets the owner's project owns, in one region.

## Decision

1. **One ephemeral VM per run, at ADR 0014's floor.** `make bench-vm RUN=runs/<name>.yaml` launches one Compute Engine VM in `europe-west4`. The VM runs one attempt of one run and deletes itself when the run ends, is preempted or reaches its runtime cap. The launcher refuses any machine type that is not x86_64 or has fewer than 8 vCPU or 32 GB, checking the type through the Compute API. CPU runs default to `n2-standard-8` and GPU runs to `g2-standard-8` with one NVIDIA L4. The boot disk defaults to 250 GB (`disk_gb`) and is deleted with the VM. The VM has no external IP; it reaches the internet through Cloud NAT and accepts SSH only through IAP. A capacity error in one zone moves the launch to the next zone of the region. One run never spans more than one VM.
2. **Artifacts live only in the owner's private Cloud Storage buckets in `europe-west4`, never in a third-party service.** The buckets use uniform access and public access prevention.
   - A runs bucket holds each run under `gs://<runs-bucket>/<run-id>/`: the run spec copied verbatim at first launch, `run.json` (the run record), and `state/`, which mirrors everything the run writes (result files, ledgers, reports, trajectories, logs). The VM syncs it every `sync_interval` (default 5 minutes) and at every ending. The sync is incremental, never deletes and skips temporary files. When the VM is told to shut down, it pushes the files a resume needs first.
   - A cache bucket, partitioned by dataset, holds the caches another machine can reuse: the before/after per-corpus workspaces, bench index entries for a committed `corpus_dir` (also partitioned by the image's `uv.lock` digest), dataset and repository downloads, embedder weights, and the answer-judge cache. An index entry is uploaded as one archive object, only after its producer has marked it complete, and is never overwritten. Bench index entries from per-task corpora are not persisted, because their keys can never be hit again. A lifecycle rule deletes cache objects by age.
   - Paths inside the container are fixed and absolute, because the index caches key on them.
3. **Versioned run specs, and run ids derived from them.**
   - A run spec is one YAML file under `infra/bench-vm/runs/`, loaded into a strict pydantic model that rejects unknown keys. Its fields:
     - `git_ref` (required);
     - `kind` (required), one of `retrieval`, `agent_track` or `before_after`;
     - the benchmark's inputs (`dataset`, `split`, `configs`, plus `corpus_dir` for `retrieval` and `baseline_ref` / `candidate_ref` for `before_after`);
     - a passthrough `args` list, which may not name a flag the supervisor owns;
     - the machine (`machine_type`, `gpu`, `spot`, `zone`, `disk_gb`);
     - the guards (`max_runtime`, `self_delete`, `confirm_spend`, `max_usd`);
     - `sync_interval`.
   - The launcher validates a run spec on the operator's machine before anything is billed. That covers the fields, the config files and `corpus_dir` at the commit, and the machine type.
   - **Run id** = `<spec file stem>-<first 8 hex of the sha256 of the canonical JSON of the fields the file sets explicitly>`. Comments, key order and whitespace do not change it, and neither does a changed code default. Any field value does. The stem is lowercase letters, digits and hyphens, short enough that the run id, its bucket prefix and the VM name `bench-<run-id>-<attempt>` all fit Compute Engine's 63-character rule.
   - The first launch resolves `git_ref` (and the before/after refs) to commits. It requires each commit to be reachable from a remote-tracking branch and the run commit to contain the bench VM tooling, then freezes the commits in the run record. Every later attempt runs the frozen commits, even after the branch moves.
   - The run record carries a schema version that the launcher and the in-VM supervisor both check. Writers use generation preconditions, so a stale writer fails instead of overwriting.
   - A run's status is one of `launching`, `running`, `succeeded`, `failed`, `timed_out`, `preempted`, `stopped` or `lost`. A `succeeded` run is final. A run with a live VM is refused. Any other run may be relaunched as the next attempt.
4. **One image per commit and flavour, built by Cloud Build.**
   - There are two flavours, `bench-cpu:<sha>` and `bench-cuda:<sha>`, in one Artifact Registry repository in `europe-west4` with immutable tags and a cleanup policy. Each is built only when its tag is missing, and only for the flavour a run needs.
   - Cloud Build builds in `europe-west4` from a `git archive` of the commit, using that commit's recipe. The image holds:
     - the repository at the commit;
     - the product built from source with its Rust extension;
     - the eval suite with its runtime extras;
     - the dependency set pinned by `uv.lock`;
     - the Cloud SDK, git and a pinned `claude` CLI;
     - the commit SHA in its environment, which run tags fall back to when `git rev-parse` fails.
   - Both flavours carry CI's OpenBLAS CBLAS setup. The CUDA flavour adds the GPU runtimes and bakes in the library-path fix `run_eval_gpu.sh` applies.
   - Every build ends with a smoke step, so a broken image never gets a tag.
   - Everything on the VM comes from the run's commit: image, startup and shutdown scripts, supervisor and the measured product. The operator's checkout only orchestrates.
5. **Spot capacity by default, with each benchmark resuming from its own ledger.**
   - `spot` defaults to true. On a shutdown signal the supervisor reads the metadata server's preempted flag, stops the benchmark and pushes its state. It records `preempted` when the flag is set and `stopped` otherwise.
   - A relaunch restores `state/` into the same fixed path before the benchmark starts:
     - the retrieval runner resumes from its run files;
     - the agent track resumes from its ledger;
     - before/after resumes from its `queue.jsonl`.
   - The retrieval runner gains this ability here. `--run-id <id>` gives each leg a stable file, `<results dir>/<run-id>/<system>_<config>_<dataset>.jsonl`. Each finished task appends a `task` event after its metric lines, and that event is the task's commit point.
   - A reopened leg appends a `run_resume` event, skips the recorded tasks, and assembles observations in dataset order, so its aggregates are bit-identical to an uninterrupted leg's.
   - A leg whose identity fingerprint differs is refused before any task runs. The fingerprint covers system, config, flattened parameters, dataset and revision, dataset arguments, metric specs and product commit.
   - Resume is an opt-in `ResumableTracker` capability of the JSONL tracker, so `ExperimentTracker` is unchanged. `--results-dir` only moves files. Without `--run-id` the runner is byte-for-byte what it was.
   - A VM that vanished without reporting is recorded `lost` by the launcher and may be relaunched. Relaunch is always the operator re-running the same make command.
   - Long paid campaigns set `spot: false`. A preemption re-runs any rollout that finished after the last completed sync, and the budget guard undercounts that spend.
6. **The spend guards and the budget alert.**
   - The supervisor deletes its own VM after `succeeded`, `failed` or `timed_out` unless `self_delete: false`. It stops the benchmark at a soft deadline, early enough for the final sync.
   - Compute Engine's maximum run duration equals `max_runtime` (default 6 hours) with termination action DELETE. It is the backstop for a hung guest, and it still applies when `self_delete` is false.
   - The other guards: spot capacity, no external IP, boot disks deleted with the VM, the image cleanup policy and the cache lifecycle rule.
   - Terraform declares a `google_billing_budget` of 50 EUR a month on the project. It alerts at 50%, 90% and 100% of actual spend and at 100% of forecast. It only notifies, and billing data lags by hours, so the per-run caps above are what bound a single run.
   - The paid kinds (`agent_track`, `before_after`) require `confirm_spend: true` and a `max_usd` in the committed run spec, and `max_usd` reaches the kind's own spend guard.
7. **Access with no service-account key anywhere.**
   - The operator authenticates with `gcloud auth login` for the launcher and `gcloud auth application-default login` (ADC) for Terraform.
   - The VM runs as a dedicated attached service account, with credentials from the metadata server only. It holds only what a run needs: object read and write on the two buckets, read on the image repository, access to the declared secrets, log writing, and instance delete restricted by an IAM condition to instances named `bench-…`.
   - Cloud Build pushes images as its own service account.
   - No service-account key is created, downloaded or referenced.
   - Terraform declares the Secret Manager secrets, and their values are added out of band with `gcloud`, so they never enter Terraform state. The supervisor reads only the provider keys the run's kind declares and puts them only in the benchmark process's environment, never in a log, the run record or the synced state. ADR 0014's API-key billing mode is unchanged.
   - The cloud side is one Terraform root with its state in a GCS backend bucket.
8. **The GPU boot image is a public Deep Learning VM image family.** Its NVIDIA driver and container toolkit are installed already. The family is pinned in Terraform, so no driver install runs at boot. If no public family qualifies when the image is built, the fallback is a custom image baked once from such a base. The custom image is pinned in Terraform the same way, and an amendment to this ADR records the choice. CPU VMs boot Container-Optimized OS, which ships Docker. The host only pulls the image by digest and starts one container with the supervisor as its entrypoint.

**What this does to ADR 0014.**
- **Decision 1** is amended as stated in the header. The x86_64 floor and the API-key billing mode carry over. The single long-lived host and the local-disk-only artifact rule do not, and the owner checkpoint to provision that host is replaced by Terraform and the bench VM.
- **Decision 2** is not exercised. This design adds no grading containers, and giving the bench container access to a container runtime is a follow-up decision.
- **Decisions 3 and 4** carry over unchanged. They are what make an ephemeral machine affordable: the canonical-checkout index cache is paid once and restored from the cache bucket, and the resumable ledger is what a relaunch resumes from.

## Consequences

- A benchmark that needs a big machine or a GPU is one command and bills only its own minutes. An interrupted run loses at most the work done since the last completed sync.
- Every result sits next to the run spec that produced it, and its run id and header name the commit even inside an image with no `.git`.
- A relaunch never moves to newer code. Running a spec on a new commit needs an edited spec, so a new run id. A run's commit must contain the bench VM tooling, and older product commits are measured through the `before_after` kind.
- The operator's launcher and the VM's supervisor may come from different commits. Only the versioned run record crosses that line.
- The budget alert only notifies. Model-provider spend is billed by the provider and stays bounded by each kind's own `max_usd`. On spot, a paid run can undercount up to one sync interval of rollouts per preemption, which is why long paid campaigns run on standard capacity.
- JSONL run files written with `--run-id` gain `task` and `run_resume` events. Readers that filter on `metric` events are unaffected, and the last aggregate block in a file covers the whole leg.
- Per-task corpora are re-indexed on every run until corpus paths become stable. That follow-up would let them into the cache bucket unchanged.
- The product, the frozen nine-tool MCP surface and its YAML are untouched. Everything lands in the eval suite, the root Makefile and a new `infra/bench-vm/` tree, with no new Python dependency: the cloud adapter shells out to `gcloud`.

## Action items

1. Land the bench VM in the order the design gives: Terraform foundation; image recipes and Cloud Build config; run spec, run id, run record and launcher with the `BenchCloud` port and its fake; supervisor and host scripts; make targets, the `bench-vm-check` CI job and the runbook. The retrieval runner resume is independent and may land first. One trailing change carries every changelog entry, in the eval suite's changelog.
2. Owner: create the Terraform state bucket, set the project, billing account and notification variables, and `terraform apply` with ADC. Then add the secret values out of band, and request spot and standard GPU quota in `europe-west4` before the first GPU run.
3. When the Terraform root lands, pin the Deep Learning VM image family and confirm it qualifies. Driver, container toolkit and CUDA reach both FastEmbed and torch on the L4. If it does not qualify, bake the fallback custom image and amend this ADR.
4. Owner: run the live acceptance (the CPU and GPU smoke specs, a stopped-then-resumed run, the refused relaunch of a succeeded run), confirm the budget alert's first email, and re-measure ADR 0014's sizing figures on the target machine types.

## Amendment (2026-11-08) — the benchmark's output is verbatim except for provider key values, which are redacted before it reaches disk or Cloud Storage

The owner approved this on 2026-11-08, before its code. The design spec holds the details and the acceptance criteria (`docs/superpowers/specs/2026-11-06-bench-vm-design.md`, "Amendment (2026-11-08)").

**Why.** Two decisions left a gap between them:

- Decision 2 mirrors everything a run writes, logs included, to the runs bucket.
- Decision 7 keeps provider keys out of every log and out of the synced state.

The supervisor never writes a key itself, but it kept the benchmark's own output byte for byte. A benchmark that printed its key, for example in a configuration dump or a debug log of request headers, would have had the key synced. Only the kind pre-flight's output was redacted, and only after its raw copy had been written to a temporary file.

**Decision.**

- **Verbatim except known values.** This covers the output of every process the supervisor runs for the benchmark: the child and the kind pre-flight, stdout and stderr. A known value is a provider key the run's kind declares, as read from Secret Manager for the attempt.
  - Each occurrence of a known value is replaced by the key's name in angle brackets (`<ANTHROPIC_API_KEY>`).
  - Occurrences that share a byte become one placeholder, so no fragment of one survives.
  - Every other byte is kept as written.
- **The redaction happens in the stream, before any byte is written.**
  - A process's stdout and stderr share one pipe. The supervisor reads it on a thread of its own and writes the attempt's log itself. No raw byte therefore reaches any file, a temporary one included, nor the runs bucket.
  - It holds back only bytes that could still begin a value, and releases them on later bytes or at the end of the stream, never on a timer.
  - It gives the same result wherever the pipe's reads split the output.
  - Its memory stays bounded, whatever the output's size or line length.
- **Never replayed.** A relaunch restores `state/` except `state/logs/`. Earlier attempts' logs stay in the bucket. No log bytes are read back, redacted a second time, uploaded again or appended to another attempt's log.
- **Values the redaction cannot handle safely are refused.** A value shorter than a floor, or with leading or trailing whitespace, fails the run at the pre-flight. The error names the key, never the value.

**Options considered.** All rejected:

- Scrubbing the log at each sync leaves raw bytes on the VM's disk between syncs.
- A line filter grows without bound on a line with no newline.
- Redacting inside each benchmark CLI cannot reach third-party CLIs such as `claude`.
- Dropping the output loses the only record of a deleted VM's output.

**Consequences.**

- A key the benchmark prints reaches neither the VM's disk, nor the runs bucket, nor `make bench-vm-status`. The guarantee covers only exact occurrences in those streams.
  - A transformed value (wrapped, encoded, masked or cut off) is not recognized, and neither is one interleaved with other writes.
  - Files the benchmark writes itself (run files, ledgers, trajectories, reports) are not filtered.
- The log lags the benchmark by at most the held-back bytes. A process that holds the pipe open past a short drain bound, after the benchmark exits, loses its later output.
- Runs at commits before this amendment keep verbatim logs on every attempt, because a relaunch runs the frozen commit. Objects already in a bucket are not scrubbed.
- These stay as they are: which keys a kind reads, that they reach only the benchmark's environment, the log names, the sync, and the run record and its schema version.
