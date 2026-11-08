# Bench VM: ephemeral GCP benchmark runs from versioned run specs, resumable after preemption

**Amended 2026-11-08 (owner-approved):** the benchmark's output is now kept verbatim except for known provider key values. The supervisor redacts those values in the stream, before any byte reaches disk or GCS, and a relaunch never restores or replays log bytes. The decisions and acceptance criteria are in the last section, "Amendment (2026-11-08): provider key values are redacted from the benchmark's output", which also lists the sections it touched.

## Problem Statement

The benchmarks that decide what ships cannot run where the owner works, and nothing records how a run was produced.

- **The development machine is the wrong machine.** ADR 0014 measured it: arm64, 8 GiB RAM, no container runtime. That is far under the floor ADR 0014 sets for rollouts: x86_64, at least 8 cores, 32 GB RAM and 250 GB disk. Retrieval sweeps are CPU-bound for days (dense indexing of the RepoQA repos runs at 60–215 s per needle on CPU, which is why `run_eval_gpu.sh` exists), and the GPU path needs CUDA hardware the owner does not have locally. ADR 0014 left provisioning a remote host to an owner checkpoint, and its status still reads "host provisioning pending".
- **A long-lived host is the wrong answer too.** It bills while idle. Its installed dependencies drift from what CI installs: the OpenBLAS CBLAS install plus `LD_PRELOAD` in the `benchmark-repoqa` workflow, and the CUDA library path that `run_eval_gpu.sh` patches in. A result produced on it cannot be traced back to an exact commit, config and machine.
- **A run has no identity.** A benchmark starts from a hand-typed command line. Nothing versions which commit, configs, dataset, split and machine produced a number. The JSONL header records the flattened config and a best-effort git SHA, and that SHA is empty wherever there is no git checkout.
- **Interrupted runs start over.** The retrieval runner (`pydocs_eval.runner`) opens a new timestamped file per leg on every invocation through `JsonlExperimentTracker`, and keeps every observation in memory until the leg ends. Its per-task metric lines carry a step counter but no task id. An interrupted sweep restarts from zero, and even a hand-written resume could not tell which tasks are done. The campaign ledger (`queue.jsonl`) and the agent-track ledger already resume, but only from the local disk of the machine that wrote them. That rules out the cheapest capacity: spot VMs, which the cloud can reclaim at any moment.
- **Nothing caps cloud spend.** A forgotten VM, or one whose run hung, bills until someone notices.

## Solution

`make bench-vm RUN=runs/<name>.yaml` launches one ephemeral Compute Engine VM in `europe-west4`. The VM runs exactly what a versioned run spec under `infra/bench-vm/runs/` names: the commit, the benchmark, its configs, dataset and split, the machine type and GPU, spot or standard capacity, and a runtime cap. It runs inside a Docker image that Cloud Build builds once per commit and stores in Artifact Registry. It writes its results to `gs://<runs-bucket>/<run-id>/` next to a copy of the run spec, then deletes itself.

Spot capacity is the default. A run's state syncs to GCS every few minutes, and once more when the VM is told to shut down. A preempted run is marked `preempted`. When the owner re-runs the same make command, the new VM pulls the run's state back and the benchmark resumes from its own ledger. The retrieval runner gains what the other two benchmarks already have: a stable output path and a resume that skips work already recorded. For the runner, a run id names both. All three benchmark kinds then resume the same way. Long paid campaigns set `spot: false`.

Three guards bound spend:

- The VM deletes itself when its run ends.
- The platform deletes it when the run spec's `max_runtime` elapses (default 6 hours), whatever the guest is doing.
- A budget alert managed by Terraform fires at 50 EUR a month.

Index caches that another machine can reuse persist in GCS, so a second run does not pay for them again. On the laptop, access uses the owner's short-lived `gcloud auth login` and Application Default Credentials. On the VM it uses an attached service account. No service-account key file exists anywhere.

## User Stories

1. As the owner, I want to launch a benchmark run with one make command naming a run spec, so that starting a run takes seconds and no hand-typed command line.
2. As the owner, I want every run described by a YAML file committed under `infra/bench-vm/runs/`, so that any result traces back to a reviewed, versioned description of what produced it.
3. As the owner, I want the run spec to name the commit, the benchmark kind, the configs, the dataset and split, the machine type, the GPU, spot or standard capacity, and the runtime cap, so that one file holds everything that determines a run.
4. As the owner, I want a typo in a run spec to fail on my laptop within seconds, so that I never pay for a VM that boots only to fail. Typos include an unknown key, a bad enum value, and a config file missing at the commit.
5. As the owner, I want the run id derived from the run spec itself, so that re-running the same make command after a preemption finds the same run instead of starting a new one.
6. As the owner, I want any meaningful edit to a run spec to produce a new run id, so that a changed run never mixes with an old one's results.
7. As the owner, I want the commit a branch or tag resolves to frozen at the first launch, so that a relaunch after the branch moved still runs the original code.
8. As the owner, I want the launcher to refuse a commit that is not on the remote, so that every result points at a commit anyone can check out.
9. As the owner, I want results written to `gs://<runs-bucket>/<run-id>/` together with a copy of the run spec, so that results and their recipe never separate.
10. As the owner, I want the Docker image for a commit built only when Artifact Registry does not already hold it, so that relaunches and repeated runs of one commit start right away.
11. As the owner, I want a CPU image and a CUDA image, each built only for the runs that need it, so that CPU runs do not pull gigabytes of CUDA libraries.
12. As the owner, I want both images to carry the OpenBLAS CBLAS setup the benchmark CI workflow uses, so that dense retrieval never fails on `undefined symbol: cblas_sgemm` in the cloud.
13. As the owner, I want a GPU run to fail at start if FastEmbed or torch cannot reach CUDA, so that a GPU run never quietly computes on the CPU for hours.
14. As the owner, I want GPU VMs to boot an image whose NVIDIA driver is already installed, so that no driver install runs on the clock of every launch.
15. As the owner, I want spot capacity by default, so that most runs cost a fraction of the standard price.
16. As the owner, I want a preempted run marked `preempted` in its run record, so that I can see why it stopped without reading logs.
17. As the owner, I want to relaunch a preempted run by re-running the same make command, so that recovery needs no run id copied by hand.
18. As the owner, I want a relaunched run to resume from its synced state, so that completed work (indexed tasks, judged answers, paid rollouts) is never redone.
19. As the owner, I want to set `spot: false` for a long paid campaign, so that a preemption cannot cost me the spend recorded in its last unsynced minutes.
20. As the owner, I want a run whose VM vanished without reporting recorded as lost and allowed to relaunch, so that no run stays "running" forever. A host crash, or a shutdown script that ran out of time, can cause this.
21. As the owner, I want the launcher to refuse a second VM for a run that already has one alive, so that two machines never write one run's state.
22. As the owner, I want the launcher to refuse to relaunch a run that already succeeded and to tell me where its results are, so that a finished run stays final.
23. As the owner, I want the VM to delete itself when its run ends, so that I never pay for an idle machine.
24. As the owner, I want the platform to delete the VM when `max_runtime` elapses even if the run or the guest hangs, so that a stuck run costs at most its cap.
25. As the owner, I want `max_runtime` to default to six hours, so that a run spec that says nothing about time is still bounded.
26. As the owner, I want an email when the project's spend crosses the thresholds of a 50 EUR monthly budget, so that a mistake surfaces within the month.
27. As the owner, I want an optional `self_delete: false` that keeps the VM after its run ends, so that I can inspect a failed run over SSH. The platform still deletes the VM at `max_runtime`.
28. As the owner, I want `make bench-vm-stop` to stop a run on purpose and record it as stopped, so that I can cancel a mistake and still resume the run later.
29. As the owner, I want `make bench-vm-status` to print a run's status history, commit, image and machine plus the tail of its log, so that I always know what a run is doing. With no run named, it should list every live bench VM.
30. As the owner, I want `make bench-vm-pull` to download a run's results into my local results directory, so that I can report on them with the existing tools.
31. As the owner, I want a run's log kept with its synced state, so that a deleted VM's output is still readable.
32. As the owner, I want a capacity error in one zone to move the launch to the next zone of the region, so that a GPU stockout does not fail the launch.
33. As the owner, I want machine types below ADR 0014's floor or not x86_64 refused, so that a run never lands on a machine the benchmarks were not sized for.
34. As the owner, I want a run spec for a paid benchmark kind refused unless it carries `confirm_spend: true` and a `max_usd`, so that spend is authorized by a committed file, not by a default.
35. As the owner, I want model-provider API keys read from Secret Manager at boot and handed only to the benchmark process, so that no key sits in an image, a run spec, a log or the run record.
36. As the owner, I want to authenticate with `gcloud auth login` and Application Default Credentials only, so that no long-lived credential file exists on my laptop or in the repository.
37. As the owner, I want the VM to have no public IP and to accept SSH only through IAP, so that the benchmark machine is not reachable from the internet.
38. As the owner, I want the VM's service account to hold only the permissions a run needs, and to be able to delete only bench VMs, so that a compromised run cannot touch the rest of the project.
39. As the owner, I want the reusable caches persisted in GCS, so that a second run does not pay for embeddings, downloads or judge calls again. These are the before/after index workspaces, the reference-project indexes, the dataset and repository downloads, the embedder weights, and the answer-judge cache.
40. As the owner, I want a cache entry in GCS to be either whole or absent, so that a preemption mid-upload can never hand a later run a half-written index.
41. As the owner, I want the cloud infrastructure declared in Terraform, so that the project can be reviewed and rebuilt from code.
42. As the owner, I want a runbook covering one-time setup and every make target, so that I can operate the bench VM without tribal knowledge. Setup means the state bucket, `terraform apply`, the secrets, and GPU quota.
43. As a benchmark author, I want to pass `--run-id` to the retrieval runner on any machine, so that a crashed long sweep resumes instead of restarting.
44. As a benchmark author, I want a resumed retrieval run to produce exactly the aggregates an uninterrupted run would have, so that a preemption never changes a reported number.
45. As a benchmark author, I want resuming a run under a different config, metric list, dataset revision or commit to fail before any task runs, naming the field that differs, so that two experiments never share one result file.
46. As a benchmark author, I want a truncated last line in a run file skipped with a warning and its task re-run, so that a process killed mid-write never blocks a resume.
47. As a benchmark author, I want the runner without `--run-id` to behave byte-for-byte as it does today, so that CI, the regression gate and existing scripts are untouched.
48. As a benchmark author, I want each recorded task to carry its task id, scores, latencies, cache-hit flag and metadata, so that per-task analysis no longer depends on step counters.
49. As a reviewer, I want every result file's header to name the commit, even when it was written inside an image with no git checkout, so that provenance survives the move to the cloud.
50. As a reviewer, I want the regression gate (`ci_compare`) to read the full-leg aggregate from a resumed run's file, so that resumed runs compare like any other.
51. As an implementer, I want the decisions recorded as ADR 0026 and the new terms (run spec, bench VM) defined in `CONTEXT.md`, so that later work uses the same words and knows what ADR 0014 now means.
52. As an implementer, I want the launcher and the in-VM supervisor testable offline against one fake cloud, so that the whole lifecycle can be checked without a GCP account. The lifecycle is launch, preemption, relaunch and self-delete.

## Implementation Decisions

### Constraints that shape everything

- **The product is untouched.** Nothing changes in `pydocs_mcp`, the frozen nine-tool MCP surface, its response envelope or its YAML. Everything lands in the eval suite (`pydocs-mcp-eval`), the root Makefile and a new `infra/bench-vm/` tree.
- **Repo conventions apply to the new code.**
  - Closed vocabularies are `StrEnum`s with UPPER_SNAKE members (run kind, run status).
  - Every default lives once: a pydantic `Field(default=…)` on the run-spec model, or a module constant.
  - Error messages carry the offending value and the expected shape.
  - Logs are structured JSON with named fields.
  - Protocols carry no `Protocol` suffix.
  - External I/O sits behind named fakes.
  - Every external call (each `gcloud` invocation, GCS transfer and metadata-server request) has a timeout and a bounded retry.
- **No new Python dependency.** The cloud adapter shells out to the `gcloud` CLI, the way the agent track shells out to the `claude` CLI. The run-spec loader uses the pydantic and PyYAML the eval base install already carries. The new code imports nothing from `pydocs_mcp`, so the eval base-install floor holds.
- **One commit for everything on the VM.** These all come from the run's commit:
  - the image recipe;
  - the startup and shutdown scripts;
  - the supervisor;
  - the measured product.

  The launcher on the laptop only orchestrates, so a newer laptop checkout can launch an older run commit. Only one contract crosses that line: the run record, which carries a schema version that both sides check. A run's commit must contain the bench-VM tooling. Older product commits are measured through the before/after kind, which already measures two product commits from one harness commit.
- **ADR conflicts, flagged.** _Contradicts ADR 0014 Decision 1 ("a single remote Linux x86_64 host runs every rollout component"; "all artifacts stay on the host's local disk"), but worth reopening because an ephemeral VM has no durable local disk and no idle cost._ ADR 0026 amends it in two ways:
  - one ephemeral VM per run, at the same x86_64 floor;
  - artifacts live only in the owner's private GCS buckets in `europe-west4`, never in a third-party service.

  ADR 0014's index-cache economics (Decision 3) and its resumable-ledger orchestration (Decision 4) carry over unchanged. They are what make an ephemeral machine affordable.

### Retrieval runner resume (`pydocs_eval.runner`, the sweep, `JsonlExperimentTracker`)

- **Two new CLI flags.**
  - `--run-id <id>` is threaded through `run_sweep_detailed` and `run_sweep` as an optional `run_id` (default `None`). The id is restricted to the tracker's filename-safe characters. Anything else fails before any leg runs, naming the value and the allowed shape.
  - `--results-dir <dir>` sets the JSONL tracker's output directory. Its default stays the tracker's own field default. The flag only moves files; it never changes their content. The supervisor needs it because it runs the CLI from the repository root, so that repository-relative paths resolve, while the results must land in the run's state directory.
- **Without `--run-id`, nothing changes.** Each leg gets one new timestamped file with the same events, byte-identical, so the `benchmark-repoqa` workflow and `ci_compare` are untouched.
- **With `--run-id`, a run is a directory and a leg is a stable file**, named `<results dir>/<run-id>/<system>_<config>_<dataset>.jsonl` with no timestamp. Two configs that share a file stem would share a leg file, so a run-id sweep fails at start when leg names collide.
- **Resume is a capability of the JSONL tracker, behind one new opt-in Protocol** (`ResumableTracker`, in the `IndexesDependencies` / `HasLibrary` opt-in idiom). It offers three operations:
  - open a named run, creating its file or reopening it;
  - list the task records already in it;
  - append one task record.

  The `ExperimentTracker` Protocol is unchanged, so the MLflow tracker and every test fake keep working. A run-id sweep requires exactly one tracker with the capability among `--trackers`, and fails loud otherwise. Other trackers receive the newly run tasks' metric events and the full-leg aggregates; nothing is replayed to them.
- **Schema additions, in run-id mode only.**
  - The `run_start` header gains the run id and the leg's identity: its components and their fingerprint, a canonical-JSON sha256 in the `arm_identity` idiom.
  - The components are everything that changes a recorded task's scores:
    - the system and the config name;
    - the flattened config parameters the header already records, which include the embedding device;
    - the dataset name and revision;
    - the dataset arguments (split, library filter, full prompt, fixture);
    - the metric specs;
    - the product commit.
  - A new `task` event records one finished task: task id, leg position, scores, index and search seconds, cache-hit flag and metadata (a full `TaskObservation`). It is written after that task's metric lines, which makes it the commit point: a task without a task record is not recorded.
  - Reopening a run appends a `run_resume` event with the recorded-task count, instead of a second `run_start`.
  - Each invocation ends with a fresh aggregate block and `run_end`, so the last aggregate block in a file always covers the whole leg.
- **Resume semantics.**
  - Before the task loop, the sweep reads the leg's task records. If the header's fingerprint differs from the current leg's, it fails before running anything. The error names the run id, both fingerprints and the first component that differs.
  - The sweep then walks the dataset in its usual order. A task with a record is not indexed, searched or scored; its observation is rebuilt from the record. Every other task runs.
  - Observations are assembled in dataset order, so the seeded bootstrap sees the same sequence. A resumed leg's aggregates are therefore bit-identical to an uninterrupted leg's.
  - `--limit` counts recorded tasks. The task-id filter and the limit set which tasks run; they are not part of identity.
  - A leg whose tasks are all recorded runs nothing and re-emits its aggregates. The markdown report covers every task of every leg.
  - A truncated trailing line is skipped with a warning (the corrupt-line policy of the campaign and agent-track ledgers), so the task it described runs again. A re-run task's per-task metric lines may therefore repeat; readers key per-task data on task records.
- **Run tags name the commit without git.** When `git rev-parse` fails, the run tags fall back to the commit SHA the image sets in its environment. The image is built from a `git archive` and has no `.git`, so without this fallback its headers would carry no commit and the identity check would have nothing to compare.

### Run spec and run id

- **A run spec is one YAML file under `infra/bench-vm/runs/`, loaded into a strict pydantic model** that rejects unknown keys. Fields:
  - `git_ref` (required): a commit SHA, tag or branch.
  - `kind` (required, `RunKind`). Three values:
    - `retrieval` runs `pydocs_eval.runner`;
    - `agent_track` runs `pydocs_eval.agent_track`;
    - `before_after` runs the campaign `before-after` measurement.
  - Benchmark inputs, which the launcher validates and the supervisor maps to each CLI's flags:
    - `dataset` and `split`;
    - `configs`, as repository-relative paths at the commit (exactly one for `before_after`);
    - for `retrieval`, an optional `corpus_dir` (a repository-relative path, such as the committed DS-1000 reference project);
    - for `before_after`, `baseline_ref` and `candidate_ref`, resolved and frozen like `git_ref`;
    - `args`, a passthrough list for every other flag of that kind's CLI.
  - Machine fields:
    - `machine_type`;
    - `gpu`, a type and count (absent means CPU);
    - `spot` (default true);
    - `zone` (optional);
    - `disk_gb` (default 250, from ADR 0014).
  - Guards:
    - `max_runtime` (default 6 hours);
    - `self_delete` (default true);
    - `confirm_spend` and `max_usd`, required together by the paid kinds (`agent_track`, `before_after`).
  - `sync_interval` (default 5 minutes).
- **Defaults meet ADR 0014's floor.** CPU runs default to `n2-standard-8` (8 vCPU, 32 GB). GPU runs default to `g2-standard-8` with one NVIDIA L4.
- **Each kind is one row of a kind table.** A row holds:
  - the CLI the kind runs;
  - the flags the supervisor owns: those it fills from typed inputs (dataset, split, configs, the frozen commits, `max_usd`, `--gpu`), and those it points into the run's state and cache directories;
  - the provider keys the kind needs;
  - the kind's own pre-flight;
  - whether the kind may use a GPU.

  Passthrough `args` may not name an owned flag, and a GPU on a kind that cannot use one is refused. Adding a kind, such as the campaign's `prebuild-index` or `write-references`, is a new enum member plus a table row.
- **Run id** = `<spec file stem>-<first 8 hex of the sha256 of the canonical JSON of the fields the file sets explicitly>`.
  - Comments, key order and whitespace do not change it; any field value does.
  - A changed code default does not change it either, so a tooling upgrade between a preemption and its relaunch still finds the run.
  - The stem must be lowercase letters, digits and hyphens, and short enough that three names stay within Compute Engine's 63-character rule: the run id, the GCS prefix and the VM name (`bench-<run-id>-<attempt>`).

### Run record and status lifecycle

- **One run record per run**, at `gs://<runs-bucket>/<run-id>/run.json`. It sits next to the verbatim run spec (uploaded once, at the first launch) and the run's synced state. It carries:
  - a schema version, the run id, the spec digest and the kind;
  - the `git_ref` as written and the commit it resolved to (plus the frozen baseline and candidate commits for `before_after`);
  - the image name and digest;
  - the current status and attempt;
  - the full transition history (status, time, attempt, instance, zone, machine, detail);
  - the last sync time and the exit code;
  - the cache bytes restored and pushed.

  Writers use GCS generation preconditions, so a stale writer fails instead of overwriting a newer status.
- **`RunStatus`** values: `launching`, `running`, `succeeded`, `failed`, `timed_out`, `preempted`, `stopped`, `lost`.
  - The launcher writes `launching`.
  - The supervisor writes `running` and every ending status.
  - The launcher writes `lost` when it finds a `launching` or `running` record whose VM no longer exists.
- **Relaunch rules.**
  - A `preempted`, `failed`, `timed_out`, `stopped` or `lost` run may be relaunched. The relaunch is attempt n+1 on the frozen commits, and it resumes.
  - A `succeeded` run is final: the launcher refuses and prints the results location.
  - A run with a live VM is refused. The launcher looks VMs up by their run-id label across the region's zones.
  - Relaunch is always manual: the owner re-runs the make command, and nothing relaunches on its own.

### Launcher (`make bench-vm RUN=runs/<name>.yaml`)

- **It lives in a new eval-suite subpackage, `pydocs_eval.bench_vm`,** whose CLI the Makefile calls the way the other benchmark commands run. In order, it:
  1. loads and validates the run spec;
  2. reads the deployment's names (project, buckets, registry, service account, subnet, zones) from `terraform output`, the single source of those values;
  3. computes the run id and reads the run record;
  4. resolves the commits. On a first launch it resolves each ref and requires the commit to be reachable from a remote-tracking branch; the run commit must also contain the bench-VM recipe. On a relaunch it reuses the recorded commits;
  5. checks that every config file and `corpus_dir` exists at the commit;
  6. checks the machine type through the Compute API: x86_64, at least 8 vCPU and 32 GB;
  7. ensures the image exists;
  8. writes the run record;
  9. creates the VM.

  It then prints the run id, the results URI and the status command, and returns without waiting for the run.
- **VM creation.**
  - The VM is named `bench-<run-id>-<attempt>` and labelled with the run id and attempt.
  - Its provisioning model is spot or standard.
  - The platform's maximum run duration is set to `max_runtime`, with termination action DELETE. A spot preemption also deletes the VM.
  - It has no external IP, runs as the bench service account, and boots the image for its flavour with a `disk_gb` disk.
  - Its metadata names the run id, the buckets, the image digest, and the run commit's startup and shutdown scripts.
  - On a zone capacity error it tries the next zone: the spec's `zone` first when set, then the region's zones from Terraform. Any other error fails the launch and records `failed`.

### Images (Cloud Build, Artifact Registry)

- **Two images per commit, `bench-cpu:<sha>` and `bench-cuda:<sha>`.** Both live in one Artifact Registry Docker repository in `europe-west4` with immutable tags. A launch builds only the flavour it needs, and only when the tag is missing. Two launches racing on one commit are both fine: the immutable tag refuses the losing push, and the launcher treats the tag's existence as success.
- **Cloud Build builds them in `europe-west4` from a `git archive` of the commit**, never from the working tree, using the recipe from that same commit. The image holds:
  - the repository at the commit (configs, fixtures, committed data);
  - the product installed from source with its Rust extension built (the shipped-wheel behaviour), with the toolchain kept out of the final stage;
  - the eval suite with every runtime extra the three kinds need;
  - the dependency set pinned by `uv.lock`, never resolved fresh;
  - the Cloud SDK, because the supervisor's sync and self-delete run from inside the container;
  - git;
  - the `claude` CLI at a pinned version, for the agent track;
  - the commit SHA, in its environment.
- **Both images carry the OpenBLAS CBLAS setup of the benchmark CI workflow:** the pthread OpenBLAS variant, preloaded, because turbovec links `cblas_sgemm`.
- **The CUDA image** starts from an NVIDIA CUDA runtime base. It swaps in the GPU runtimes the GPU-inference design names: `fastembed-gpu` in place of `fastembed`, `onnxruntime-gpu`, and a CUDA build of torch. It bakes in the library-path fix that `run_eval_gpu.sh` applies at run time, so the runner reaches CUDA without the wrapper.
- **Every build ends with a smoke step**, so a broken image never gets a tag. The step imports the product with turbovec loaded and runs `pydocs-mcp --help` plus the runner's and the supervisor's `--help`. Cloud Build has no GPU, so the in-VM pre-flight checks the CUDA path instead.

### VM shape and boot

- **Region `europe-west4`.** The zones come from Terraform, and a run spec may override them.
- **Boot images.** CPU VMs boot Container-Optimized OS, which ships Docker. GPU VMs boot a public Deep Learning VM image family whose NVIDIA driver and container toolkit are already installed, pinned in Terraform, so no driver install runs at boot. If no public family qualifies when this is built, the fallback is a custom image baked once, recorded in ADR 0026.
- **The host does as little as possible.** The startup script needs only Docker and curl. It:
  - reads the metadata;
  - logs Docker in to Artifact Registry with a token from the metadata server;
  - pulls the image by digest;
  - starts one container, with the GPU exposed on the CUDA flavour, the run's state and cache directories mounted at fixed absolute paths, and the supervisor as its entrypoint.

  The shutdown script stops that container with a grace period, which is the supervisor's signal to flush.
- **Fixed in-container paths are a correctness requirement.** The index caches key on absolute paths. The bench index cache hashes the resolved corpus path. The product's per-project cache name hashes the project path, which ADR 0014's canonical-checkout cache and the before/after workspaces rely on. A cache restored on another VM is valid only at the same paths, and the in-image repository path must be fixed too, so that a `corpus_dir` resolves the same everywhere.

### Supervisor (inside the container)

- **One process per run** (`pydocs_eval.bench_vm` supervise). In order, it:
  1. reads the run record and the spec copy, refusing an unknown record schema version;
  2. records `running` with the host facts: machine type, zone, GPU, spot, image digest;
  3. restores the run's state (on a relaunch) and its caches;
  4. runs the pre-flight;
  5. runs the kind's CLI as a child process. The working directory is the in-image repository, so repository-relative paths in the spec (configs, `corpus_dir`, a `--fixture` in `args`) resolve as they do on the laptop. The owned flags point into the state directory, and `--gpu` is added on a GPU run:
     - the retrieval runner gets `--run-id`, `--results-dir` and `--report`;
     - the agent track gets `--ledger` and `--report`;
     - before/after gets `--out`, `--workspace`, `--repo`, `--max-usd` and `--confirm-spend`;
  6. keeps the child's output in the state directory, verbatim except for known provider key values, which it redacts before any byte is written (amended 2026-11-08), and syncs on a timer;
  7. finishes with one of the endings below.
- **Pre-flight, before any spend.**
  - On a GPU run, torch must see CUDA and onnxruntime must list the CUDA execution provider. Otherwise the run ends `failed`, naming what is missing. This is a pure check over an injected probe, following the `campaign.smoke` host-probe precedent.
  - Then the kind's own pre-flight runs, when it has one: the agent track's `--preflight`, or before/after's plan-only invocation, which is logged. Its output is redacted the same way as the child's, before any byte is written (amended 2026-11-08).
  - Before/after also gets a blobless clone of the product repository, which is public, so that its worktrees can check out both commits.
- **Endings.**
  - The child exits 0: final sync, `succeeded`.
  - The child exits non-zero: final sync, `failed` with the exit code.
  - The soft deadline passes (`max_runtime` minus a margin long enough for the final sync): stop the child, final sync, `timed_out`.
  - A shutdown signal arrives: read the metadata server's preempted flag, stop the child, then push the files a resume needs first (ledgers and run files, then the rest). Record `preempted` when the flag is set, `stopped` otherwise.

  After `succeeded`, `failed` or `timed_out` with `self_delete` on, the supervisor deletes its own VM through the Compute API. A spot preemption and the maximum run duration delete the VM without it.
- **Secrets.** Each kind declares the provider keys it needs:
  - the agent track needs the Anthropic API key (ADR 0014/0015's metered billing);
  - before/after needs the keys its serving config and chat model use (OpenRouter in the shipped configs).

  The supervisor reads only those from Secret Manager and puts them only in the child's environment, never in its own logs, the run record or the synced state. If the child or its pre-flight prints a key anyway, it is redacted from their output before that output is written, so it reaches neither the logs nor the synced state (amended 2026-11-08).

### Run state persistence and relaunch

- **Run state** is everything the run writes: result files, ledgers, reports, trajectories and logs. It mirrors to `gs://<runs-bucket>/<run-id>/state/` every `sync_interval` and at every ending. The sync is incremental, never deletes, and skips temporary files. A relaunch restores the state, except the supervisor's logs (amended 2026-11-08), into the same fixed path before the child starts, and each benchmark resumes from its own ledger:
  - the retrieval runner from its run files;
  - the agent track from its ledger keyed by arm;
  - before/after from its `queue.jsonl`.
- **Why a partial sync is safe.** Every ledger is append-only, with one record per line, and tolerates a truncated last line. Reports and aggregates are written atomically. At worst, the work done since the last completed sync re-runs.
- **The window that costs money.** In a paid kind, a rollout that finished after the last completed sync re-runs on relaunch. Its first cost appears in no restored ledger line, so the budget guard undercounts provider spend by up to one sync interval of rollouts. The run record records every preemption, so reconciliation against provider billing can account for them. Long paid campaigns use `spot: false`, as the design prescribes.

### Persisted caches

- **A separate cache bucket**, partitioned by dataset, restored at boot and pushed to at each sync. A VM restores only its run's dataset partition plus the shared ones. It never overwrites or deletes anything in GCS.
- **Persisted in v1,** because their keys can be reused on another machine:
  - the before/after per-corpus workspaces, which have stable corpus slugs under `--workspace`. Restoring them also saves the paid embeddings their bundles carry.
  - bench index cache entries from retrieval runs given a `corpus_dir`, such as the committed DS-1000 reference project. These are also partitioned by the image's `uv.lock` digest, because a reference-project index covers the installed dependencies, which the cache key does not see.
  - the dataset and repository downloads.
  - the embedder weights.
  - the answer-judge response cache, keyed by request digest.
- **Not persisted: bench index cache entries from per-task corpora.** The bench cache keys on the resolved corpus path, which is its design's documented limit. Every shipped retrieval dataset except DS-1000 materializes each task's corpus into a fresh temporary directory, so those entries can never be hit again on any machine. Uploading them would cost transfer and storage for nothing. Making corpus paths stable is the follow-up that would let them join the list unchanged.
- **Index entries are immutable and atomic in GCS.** The unit of upload is one finished entry, packed as one archive object: a promoted bench-cache key directory, or a before/after corpus whose served bundle is assembled. An entry is uploaded only after its producer has marked it complete, never while it is being built. A later run sees it whole or not at all. Download caches mirror as plain directories, because each download tool re-fetches a partial download on its own.
- **Lifecycle.** The cache bucket deletes objects by age; thanks to the archive rule, it deletes whole entries. The runs bucket keeps results.

### Cost guards

- The guards on a single run:
  - the VM deletes itself at the end of its run;
  - the platform's maximum run duration (equal to `max_runtime`, default 6 hours) with DELETE is the backstop for a hung guest;
  - spot capacity is the default;
  - no external IP, so no IP charges;
  - boot disks are deleted with their VM;
  - an Artifact Registry cleanup policy removes old images, which are rebuilt on demand;
  - an age-based lifecycle rule runs on the cache bucket.
- **Budget.** A Terraform `google_billing_budget` of 50 EUR a month on the project, alerting at 50%, 90% and 100% of actual spend and at 100% of forecast. It notifies and never stops anything, and billing data lags by hours. The caps above are what bound a single run.
- **Paid kinds** need `confirm_spend: true` and `max_usd` in the committed run spec. `max_usd` reaches the kind's own spend guard.

### Auth, identity and secrets

- **Operator.** `gcloud auth login` for the launcher, `gcloud auth application-default login` for Terraform. No service-account key is created, downloaded or referenced anywhere.
- **VM.** A dedicated bench service account attached to the VM, with credentials from the metadata server only. Its permissions are the least a run needs:
  - object read and write on the two buckets;
  - read on the Artifact Registry repository;
  - access to the declared secrets;
  - log writing;
  - instance delete, restricted by an IAM condition to instances named `bench-…`.

  Cloud Build pushes images with its own service account, which holds write on the repository.
- **Secrets.** Terraform declares the Secret Manager secrets. Their values are added out of band with `gcloud`, so they never enter Terraform state.

### Terraform foundation

- **One Terraform root for the bench VM.** Its state lives in a GCS backend bucket, created once by a documented bootstrap command. It enables the needed APIs: Compute, Artifact Registry, Cloud Build, Storage, Secret Manager, Billing Budgets and IAM. It declares:
  - the VPC and a `europe-west4` subnet with Private Google Access, plus Cloud Router and Cloud NAT for egress;
  - a firewall that admits SSH only from IAP's range;
  - the runs and cache buckets, with uniform access, public access prevention and the cache lifecycle rule;
  - the Artifact Registry repository, with immutable tags and a cleanup policy;
  - the bench and Cloud Build service accounts and their IAM;
  - the secrets;
  - the budget;
  - outputs for everything the launcher reads.
- **Variables:** project, billing account, region (default `europe-west4`), budget amount (default 50) and currency (EUR), and notification emails.

### Make targets, docs, ADR and glossary

- **Root Makefile targets:**
  - `bench-vm`: launch or relaunch;
  - `bench-vm-status`: one run, or every live bench VM;
  - `bench-vm-stop`;
  - `bench-vm-pull`: download to a local results directory that git ignores;
  - `bench-vm-check`: validate every committed run spec, run `terraform fmt -check` and `terraform validate` without a backend, and run `shellcheck` on the host scripts. It needs no credentials.

  A small CI job filtered to `infra/**` runs the same check.
- **The tree ships two run specs**, used by the live acceptance below: a CPU smoke (the bundled RepoQA fixture, a handful of tasks) and a GPU smoke.
- **A runbook README in the infra tree** covers one-time setup, every target, relaunch and the cost model. It follows the README rule: no PR or issue numbers.
- **ADR 0026** ("Bench VM: ephemeral GCP runs from versioned run specs") records these decisions and the ADR 0014 amendments. Its options considered include ADR 0014's long-lived host, a GitHub Actions launcher (deferred by the design), and managed batch services. The same change refreshes the domain-docs guide's stale "next free ADR number" line.
- **`CONTEXT.md`** gains a section on running benchmarks in the cloud, with two terms:
  - **Run spec**: "the versioned YAML file that fully describes one benchmark run on a bench VM: the commit, the benchmark and its inputs, the machine and the runtime cap". _Avoid_: job file, launch config, and run config, a name the trajectory run-config lockfile already owns.
  - **Bench VM**: "the ephemeral cloud machine that runs one run spec's run and deletes itself when the run ends, is preempted or reaches its runtime cap". _Avoid_: remote host (ADR 0014's long-lived machine), worker, runner.

  Run record and relaunch are candidate terms for `/domain-modeling` to settle.

## Testing Decisions

- **What a good test is here.** It observes the boundary that a user or another component sees: the files a run writes, the outcome a sweep returns, the requests the cloud received, the run record's history, a child process's argv and environment, an exit code, an error message. It never tests a private helper. Fakes are named classes in a fakes module. Git runs for real against a tiny fixture repository. Child processes run for real: a small fixture script appends to a ledger and skips recorded ids. Deadlines are injected short.
- **Seams.** They were approved with the design; one is new.
  - **The sweep's programmatic surface and its run files** (existing). Tests drive `run_sweep_detailed`, `run_sweep` and `python -m pydocs_eval.runner`, and observe the returned `SweepOutcome` and the JSONL files on disk. Every resume test lives here.
  - **One bench-VM cloud port** (new: `BenchCloud`, with `GcloudBenchCloud` and `FakeBenchCloud`). It is a Protocol over the cloud operations the launcher and the supervisor share:
    - on the two buckets: object reads, writes with generation preconditions, listings and directory syncs;
    - image-tag lookup and build submission;
    - VM create, list and delete;
    - machine-type facts;
    - the instance metadata, including the preempted flag.

    The real adapter shells out to `gcloud`. The fake keeps the buckets in a local directory, records every request, and can be scripted to fail with a zone stockout, a stale generation or a missing tag. Tests drive the launcher and the supervisor end to end through their entry points against it.
- **Retrieval resume (the sweep seam).**
  - Without `--run-id`, files keep today's exact shape: a timestamped name, and no `task` or `run_resume` events. `--results-dir` changes only where the files land.
  - Interrupt, then resume. A counting fake system raises on task k. A second invocation with the same run id indexes and searches only tasks k and later. The outcome's aggregates are bit-identical to an uninterrupted run's. The file holds one `run_start`, one `run_resume`, one task record per task, and a final aggregate block over the whole leg.
  - A truncated last line re-runs its task, logs a warning, and skips the rest.
  - Change each identity component in turn: config, metric specs, dataset revision, dataset arguments, commit. Each fails before any task runs, with an error naming the run id, both fingerprints and that component.
  - `--limit` counts recorded tasks.
  - A fully recorded leg runs nothing and re-emits equal aggregates.
  - Colliding leg names, an invalid run id, and a run id without the JSONL tracker each fail loud.
  - After a resume, the report covers every task.
  - A non-resumable fake tracker sees only the new tasks plus the full aggregates.
  - `ci_compare` reads the last aggregate of a resumed file.
  - Without git, run tags fall back to the image's commit variable.
- **Run spec.**
  - Loading is strict: unknown keys and bad enum values are refused, and field errors name the field and the expected shape.
  - Defaults.
  - Run-id determinism: comments, key order and whitespace do not change it; every field value does; a code default does not.
  - The name rule and the 63-character bound.
  - Refused specs: an owned flag in `args`; a GPU on a kind that cannot use one; a paid kind without `confirm_spend` and `max_usd`.
  - Every committed run spec under `infra/bench-vm/runs/` loads.
- **Launcher (the cloud seam plus a fixture git repository).**
  - A first launch:
    - resolves and freezes the commits;
    - refuses a commit not on a remote-tracking branch, and a run commit without the recipe;
    - builds an image only when its tag is missing (two launches, one build);
    - writes the run record and the spec copy;
    - creates exactly one VM. Its request carries the spot setting, the maximum run duration with the DELETE action, no external IP, the bench service account, the labels and the image digest.
  - A relaunch from each status that allows one reuses the frozen commits after the branch moved, and increments the attempt.
  - A `succeeded` run and a live VM are refused with the expected messages.
  - A stale run record is recorded as `lost` before the relaunch.
  - Zone fallback happens on a stockout only.
  - Machine types under the floor or not x86_64 are refused.
  - A stale generation precondition fails the write.
- **Supervisor (the cloud seam plus a real child process).**
  - The success path: `running`, restore, the child's argv and environment with the owned flags, periodic syncs, `succeeded`, self-delete requested.
  - A failing child ends `failed` with its exit code.
  - The soft deadline ends `timed_out`.
  - A shutdown signal with the preempted flag set pushes the files a resume needs before the rest, ends `preempted`, and requests no self-delete. With the flag unset, it ends `stopped`.
  - A relaunch restores state before the child starts, and the child resumes to a complete ledger.
  - Caches restore into the fixed paths. Only finished entries are pushed, one archive each, never overwriting. Bench index entries from per-task corpora are never pushed.
  - The GPU pre-flight fails a run whose probe reports no CUDA provider, before the child starts.
  - Only the kind's declared secrets reach the child, and no secret value appears in any synced file, log or the run record. The amendment's acceptance criteria extend this to a child and a pre-flight that print their keys, to temporary files, and to relaunches.
  - An unknown record schema version is refused.
- **Infrastructure** is checked statically by `bench-vm-check` and its CI job: Terraform format and validation without credentials, `shellcheck`, and run-spec loading. Each image checks itself in its build's smoke step.
- **Live acceptance (owner, after `terraform apply`).**
  - The CPU smoke spec on spot succeeds; its results and spec copy land under its run prefix, and the VM is gone.
  - Relaunching it is refused as succeeded.
  - A longer smoke stopped mid-run with `make bench-vm-stop` records `stopped` with partial results. Its relaunch resumes to the same aggregates as an uninterrupted local run of the same spec.
  - The GPU smoke's pre-flight passes, and its embeddings run on CUDA.
  - The budget alert's first notification arrives in the inbox.
- **Prior art.**
  - `test_sweep_decomposition` and `test_runner_smoke`: fake systems registered in the system registry, the RepoQA mini fixture, `tracker_kwargs` output directories.
  - `test_jsonl_tracker`: read the file back and assert the events.
  - The campaign `test_ledger` and the agent-track `test_orchestrator`: resume, and the corrupt-trailing-line policy.
  - `test_ci_compare`.
  - The campaign `test_index_cache`: real git against a tiny local repository.
  - The campaign `test_smoke`: an injectable host probe with pinned messages.
  - The campaign `_fakes` module: named fakes.
  - `test_fresh_install_smoke`: the pure decision helpers of a live gate, pinned so that the gate cannot go vacuous.
- **Where the tests run.** The new tests live in the eval suite and run in its local gate (`PYTHONPATH=benchmarks/src pytest benchmarks/tests/`), alongside the full CI gate set before every push.

## Out of Scope

- A GitHub Actions launcher, or any launch path other than local `make` (design decision 2).
- Automatic relaunch after a preemption; relaunch is the owner re-running the make command.
- Grading containers for SWE-bench-style F2P/P2P scoring (ADR 0014 Decision 2). The campaign code has no grading path yet. When it lands, giving the bench container access to the host's container runtime is a follow-up decision.
- More than one VM per run, or spreading one run's legs or cells across machines.
- A long-lived bench host (ADR 0014's original shape).
- Making materialized corpus paths stable (or content-addressed), so that the bench index cache becomes reusable for per-task corpora.
- Persisting the agent track's per-run corpus indexes; a relaunch re-indexes them, with no model spend.
- Run kinds beyond the three. The campaign's `prebuild-index` and `write-references` are each one table row away.
- Resuming the MLflow tracker. A resumed leg opens a new MLflow run with the new tasks and the full aggregates.
- Recording the VM's facts in the campaign lockfile's host fingerprint; they live in the run record.
- Price estimates or a spend confirmation for VM time.
- Cache pruning beyond the lifecycle rules.
- Any change to the product, the nine-tool MCP surface or its YAML.
- Other regions or clouds.

## Further Notes

**Owner actions, in order.**

1. Create the Terraform state bucket with the runbook's bootstrap command. Set the project, billing account and notification variables, then run `terraform apply` with ADC. The budget resource needs ADC with a quota project; the runbook covers it.
2. Add the secret values out of band with `gcloud secrets versions add`: the Anthropic key for the agent track, and the OpenRouter key for before/after.
3. Request GPU quota in `europe-west4`, both spot and standard, for the GPU smoke's GPU type, before the first GPU run. New projects usually start at zero.
4. Review ADR 0026 on its pull request.
5. Run the live acceptance and confirm the budget alert's first email.

**Ticket split, for `/to-tickets`.** The retrieval runner resume is independent and eval-only. It can land first and is useful locally on its own. The remaining tickets, in order:

1. ADR 0026 and the glossary terms.
2. The Terraform foundation.
3. The image recipes and the Cloud Build config.
4. The run spec, run id, run record and launcher, with the cloud port and its fake.
5. The supervisor and the host scripts.
6. The make targets, the CI check and the runbook.
7. The live acceptance (owner).

Every changelog entry goes to the eval suite's changelog, since the bench VM is eval tooling. Under the changelog serialization rule, the code tickets stay changelog-free and one trailing ticket carries every entry.

**Evidence behind the cache decision.**

- The bench index cache key is `sha256(resolved corpus dir, ingestion pipeline hash)`.
- `materialize_corpus` creates a fresh `mkdtemp` directory per call. RepoQA, structural recall, SWE-QA, SWE-QA-Pro, bug localization, the example-needle chat set and cross-commit vuln all build their per-task corpora through it.
- DS-1000 has no corpus of its own and takes `--corpus-dir`; its reference project is committed with the eval suite.
- The before/after workspace keeps stable per-corpus slugs, and ADR 0014's canonical-checkout cache keys on a canonical checkout path.

**Version skew.** The laptop's launcher and the VM's supervisor can come from different commits. Only the run record crosses that line, and it is versioned. Everything else on the VM comes from the run's commit, including the scripts the launcher passes as metadata, which it reads from that commit rather than from the working tree.

**Why the run's commit must contain the tooling.** Building an image for an older commit would need a recipe that commit does not have, and its runner would reject `--run-id`. Measuring an older product commit is what the before/after kind exists for.

## Amendment (2026-11-08): provider key values are redacted from the benchmark's output

The owner approved this on 2026-11-08, before any of its code exists. It replaces the verbatim-output policy of the supervisor's step 6 with "verbatim except for known provider key values". The policy covers the child's stdout and stderr and the kind pre-flight's stdout and stderr. It also rules that a relaunch never brings log bytes back or replays them. It resolves finding F6 of the series' final review, which found the conflict. Every other decision above stands. ADR 0026 records the same decision in its own 2026-11-08 amendment.

### Problem Statement

The spec promised two things that cannot both hold:

- The supervisor kept the child's output verbatim in the state directory, and the state directory syncs to the runs bucket.
- User story 35 and the secrets decision kept every provider key out of every log and out of the synced state.

The supervisor never writes a key itself, but the benchmark can. Say a CLI prints its configuration or environment, or a debug log prints request headers. The key then lands in the child's log, and the next sync copies it to GCS, where `make bench-vm-pull` and `make bench-vm-status` show it again.

The kind pre-flight's output was redacted, but late. Its raw copy was first written to a temporary file on the VM's disk, then read back whole into memory.

### Solution

The supervisor reads the output of every process it runs for the benchmark (the child and the kind pre-flight) through a pipe, and writes the log itself. On the way, it replaces every occurrence of a known value with the key's name in angle brackets, such as `<ANTHROPIC_API_KEY>`. It changes nothing else.

A known value is the value of a provider key the run's kind declares, as the supervisor read it from Secret Manager for this attempt.

No raw byte is ever written to a file, temporary files included. A printed key therefore reaches neither the VM's disk, nor the runs bucket, nor the owner's terminal.

The redaction works on the bytes as they arrive:

- a value split across two reads is still found;
- overlapping occurrences are masked whole;
- short output is written in full;
- memory stays bounded however much the benchmark prints.

A relaunch leaves earlier attempts' logs in the bucket and never brings them back to the VM. No log byte is read back, redacted twice or uploaded again.

### User Stories

53. As the owner, I want every known provider key value replaced by its name before the benchmark's output is written anywhere, so that a CLI that prints its key never puts it on the VM's disk, in the runs bucket or in my terminal.
54. As the owner, I want the logs otherwise to hold exactly the bytes the benchmark wrote, so that a failed run is still debuggable from its real output. That includes odd encodings, CR and CRLF, and a last line with no newline.
55. As the owner, I want both the child's stdout and its stderr covered, in the order they were written, so that the log stays one readable timeline.
56. As the owner, I want the kind pre-flight's output redacted the same way and never written raw, not even to a temporary file, so that the VM's disk never holds a key at any moment.
57. As the owner, I want a key cut in two by the pipe's reads still recognized, so that where the operating system splits the output can never defeat the redaction.
58. As the owner, I want a key written in two halves with a pause between them to stay invisible in the log and in every synced copy during the pause, so that a periodic sync can never publish the first half.
59. As the owner, I want overlapping or nested occurrences of keys masked whole, so that no fragment of a key survives beside its placeholder.
60. As the owner, I want back-to-back occurrences to show one placeholder each, so that the log still says how often a key was printed.
61. As the owner, I want short output written in full by the end of the stream, so that the last words of a crashing run are never swallowed. Short output means output shorter than a key, or output that ends in what might have been the start of one.
62. As the owner, I want output that cannot begin a key written at once, so that `make bench-vm-status` shows a live run's latest lines.
63. As the owner, I want held-back bytes released only by more output or by the end of the stream, never by a timer, so that a slow writer cannot get a key into the file in two pieces.
64. As the owner, I want the redaction's memory bounded whatever the output's size or line length, so that a run printing gigabytes without a newline cannot exhaust the supervisor.
65. As the owner, I want the benchmark never to stall on its output while the supervisor syncs, so that the redaction costs no benchmark time.
66. As the owner, I want a run's ending not to hang on a process that keeps the output pipe open, so that the final sync and the self-delete still happen on time.
67. As the owner, I want a log that cannot be written (a full disk) to discard output rather than block the benchmark or write the output elsewhere, so that a logging failure becomes neither a hang nor a leak.
68. As the owner, I want a relaunch never to bring earlier attempts' logs back to the VM, so that bytes already written are never read back, redacted twice, uploaded again or mixed into the new attempt's log.
69. As the owner, I want a run to end before its process starts when that process's log file already exists, so that one attempt's log never mixes with bytes the supervisor did not write in that attempt.
70. As the owner, I want an empty, too-short or whitespace-padded key value refused at the pre-flight, naming the key but never the value, so that a mis-added secret fails fast instead of garbling the logs or slipping past the redaction.
71. As the owner, I want the supervisor's log line about the pre-flight to repeat only the end of the redacted output, so that cutting it to length can never expose part of a key.
72. As the owner, I want runs whose kind declares no key to keep byte-identical logs, so that retrieval runs see no change.
73. As the owner, I want one redaction rule, implemented once, for every stream, so that the child's log, the pre-flight's log and the logged tail can never disagree.
74. As the owner, I want the guarantee's limits written down, so that I know exactly what it covers. The limits are transformed or partial key forms, interleaved writes, files the benchmark writes itself, and runs at older commits.
75. As a reviewer, I want the redaction proved on synthetic keys only, so that no test, fixture, log or failure message of the suite ever handles a real key.
76. As a reviewer, I want a scan taken while the child still runs, over every file the run can write and the fake bucket, so that a raw copy deleted before the end cannot hide a leak.
77. As an implementer, I want the redactor testable as a pure function of a byte stream against a naive whole-buffer oracle, so that chunk boundaries and overlaps can be tested exhaustively and deterministically.
78. As the owner, I want no real key looked up, printed or copied while this is built or tested, so that building the guard cannot itself leak a key.
79. As an implementer, I want the decision recorded in ADR 0026 before the code changes, so that the change to the child's output path has a recorded reason. The code had avoided routing the output through the supervisor on purpose.

### Implementation Decisions

#### The rule

- **Known values.** For one stream, the known values are the values of the provider keys the run's kind declares, as the supervisor read them from Secret Manager for this attempt. `retrieval` has none.
  - Each value is matched as the UTF-8 bytes of the string the child receives in its environment.
  - The redaction works on bytes and never decodes. Invalid UTF-8, NUL bytes, CR, CRLF and a missing final newline pass through untouched.
- **Occurrences and groups.**
  - An occurrence is any run of bytes equal to a known value, at any position.
  - Occurrences that share at least one byte form one group. Examples: a value overlapping itself (a value whose end repeats its start), two values overlapping, one value inside another.
  - Each group is replaced by one placeholder, `<NAME>`. NAME is the key name of the group's first occurrence: the one that starts first; among those, the longest; and, when two keys hold the same value, the name that sorts first.
  - Occurrences that only touch, without sharing a byte, are separate groups with one placeholder each.
  - Every byte outside a group is written unchanged and in order. So no byte of any occurrence survives, and the log still counts how many times a key was printed.
- **Chunking never changes the result.** However the pipe's reads cut the stream, the bytes written equal the redaction of the whole stream. No byte is written twice and none is lost. The bytes carried from one read to the next are never emitted again.
- **The hold-back.** The redaction holds back only bytes that could still be part of an occurrence. Call the first bytes of a known value, fewer than all of them, a *partial value*. What is held is never more than the longest end of the input received so far that is a partial value. Bytes already covered by a placeholder it has written are not held.
  - That is at most the longest value's length minus one. It is nothing at all when the input so far ends in no partial value, so a line that cannot begin a key reaches the log at once.
  - Held bytes leave the hold-back in one of three ways only. Later bytes rule them out, and they are written as they were. They complete an occurrence, and they are replaced. Or the stream ends, and they are written as they were.
  - A timer never releases them. A timed release could write the first half of a value, then the second half, which together put the whole value in the file.
- **The end of the stream.**
  - What is held at the end is a partial value. That is not a known value, so it is written as it was.
  - Short output is therefore never swallowed. A stream shorter than every value is written in full, an empty stream writes nothing, and a stream that is exactly one value becomes one placeholder.
- **No values, no change.** With no known values (every `retrieval` run), the stream is copied byte for byte and nothing is held.

#### The redactor

- **A new pure type**, `ProviderKeyRedactor`, in the bench VM subpackage, built from the run's provider keys (name to value). It has two operations:
  - feed a chunk, and get back the bytes that may be written now;
  - finish, and get back the rest.

  Feeding after finishing is an error. The type does no I/O and keeps no copy of what it has released. Its errors and its `repr` name keys, never values. It refuses an empty value, naming the key.
- **Linear time.** Its cost is linear in the output. It searches with the interpreter's built-in byte search, not a Python loop per byte. Its memory is the hold-back plus the chunk being processed.
- **The only redaction in the subpackage.** The text helper that redacted the pre-flight's output goes away together with its one caller. The child, the pre-flight and the logged tail therefore cannot follow different rules.
- **Why not reuse the product's redactor.** The product's ask-your-docs activity redaction already handles a secret split across stream chunks, but it is not reused, for two reasons:
  - The bench VM imports nothing from `pydocs_mcp`.
  - That redactor works on accumulated text with no bound. That suits a chat turn, but not hours of benchmark output.

#### The stream

- **One pipe per process.** The child's stdout and stderr share one pipe, with stderr joined to stdout, just as they share one file today. So the log keeps the exact order in which the kernel received the writes. The kind pre-flight runs the same way. The process still runs in its own session, and every descendant that inherits the pipe goes through the same redaction.
- **The output pump.** One thread per process reads the pipe in fixed-size chunks (a module constant) and feeds the redactor.
  - It appends each result to the log with a direct, unbuffered write, so at every sync the file holds everything but the hold-back.
  - It keeps no copy of the stream, apart from the last bytes it wrote, up to the logged cap; only the pre-flight's are ever logged.
  - Memory per stream is one read, the hold-back and that tail, whatever the output's size or line length. The pump never splits the stream into lines.
- **Never in the way.** The pump runs beside the supervisor's main loop, the way cache pushes already run on a worker thread. A sync that takes minutes therefore never leaves the pipe unread: the benchmark blocks on its output no longer than a local disk write.
- **The log file.**
  - The supervisor creates each process's log new when the process starts, before any output.
  - If the file already exists, the run ends `failed` before the process starts, and the error names the file. The supervisor never appends to, reads, truncates or replaces bytes it did not write in this attempt.
  - Within this design the file cannot exist: attempts are numbered once, a refused supervisor writes nothing, and a relaunch no longer restores logs.
- **The end.** The exit code is reported only after the process has exited and the pump has written the redactor's rest and closed the log, or once a short drain bound has passed (a module constant, 5 seconds, which fits the shutdown budget).
  - A descendant that still holds the pipe at the bound loses its later output. The pump finishes, closes the log and its end of the pipe, and logs one structured event. The descendant's next write fails with a broken pipe.
  - Every ending's final state push therefore starts after the last byte its log will ever receive.
  - A stop (at the soft deadline, or on a shutdown signal) keeps the same order: stop the process group, drain, push.
- **A failed write.** When appending to the log fails (a full disk), the pump logs one structured event that names the log and counts the bytes it discards. It keeps reading and discarding, so the benchmark never blocks on a pipe nobody reads. Discarded bytes are written nowhere else. The child decides the ending as usual.
- **A killed supervisor.** If the supervisor itself is killed (Docker's stop grace runs out), the held bytes and the bytes still in the pipe are lost. They are never written raw.

#### Every stream that can carry a key

- **The child.** Its stdout and stderr go through the redaction to `state/logs/child-attempt-<n>.log`, the same file as today.
- **The kind pre-flight** (the agent track's `--preflight`, before/after's plan-only invocation). Its stdout and stderr go through the same pump straight to `state/logs/preflight-attempt-<n>.log`. The raw temporary copy and the whole-file read go away. No raw byte touches any file, including a temporary directory and the container's writable layer.
- **The supervisor's pre-flight event.** It repeats the end of the redacted output, taken from the pump's tail, at most the existing cap of 16,000. The cap was counted in characters; it now counts bytes and decodes with replacement. The tail is cut from redacted bytes, so a cut can split a placeholder but can never expose part of a value.
- **Every kind takes the same path**, including `retrieval`, where the stream passes through unchanged. So there is one behavior to test.
- **Streams that can hold no value stay as they are.**
  - The GPU probe runs before the keys are read, with the supervisor's own environment, so no known value exists yet. Its output is parsed in memory as today.
  - The product clone runs `git` with the supervisor's own environment, which never holds a value.
  - No code path gives the supervisor's own log lines or the run record a value or raw output. The scans below keep proving it.

#### Key values the redaction can rely on

- **The check.** Right after it reads the keys, the pre-flight refuses two kinds of value:
  - a value shorter than a floor (a module constant, 20 bytes; the Anthropic and OpenRouter keys in use run to roughly 70–110 characters);
  - a value with leading or trailing whitespace.

  The run then ends `failed` before any kind pre-flight or child starts. The detail names the key and the expected shape, never the value or any part of it.
- **Why.** A tiny value would replace ordinary words throughout the log. A padded value is not what a CLI prints after trimming it, so the trimmed key would slip past the redaction. The runbook's `printf '%s'` already stores the key alone.
- **Why refuse instead of skip.** The ask-your-docs panel uses the same floor reasoning, but it skips a short value. A batch run fails instead, because a skipped value would be a silent hole in the guarantee.

#### A relaunch never replays log bytes

- **The restore leaves the logs out.** A relaunch restores the run's state except the supervisor's logs directory.
  - Earlier attempts' logs stay in the runs bucket (the sync never deletes) and never come back to a VM.
  - The `BenchCloud` restore takes the same kind of exclude pattern as the push, and both adapters honor it.
  - Ledgers, run files, manifests and reports restore as before, so every kind still resumes from its own ledger.
- **Nothing on disk feeds the redaction.** Every stream starts with an empty redactor, never primed from bytes on disk. The supervisor never:
  - opens an existing log to read it;
  - copies or appends one attempt's bytes to another's file;
  - rewrites a file, or redacts it a second time.

  Each attempt writes only its own two logs, as before.
- **Runs at older commits keep verbatim logs.** A run whose commit predates this amendment keeps verbatim logs on every attempt, because a relaunch runs the frozen commit's supervisor. Nothing scrubs objects already in a bucket. An existing run picks up the change only as a new run: an edited spec, launched at a commit that has the change.

#### What stays as it is

These stay as they are:

- which keys a kind reads, and that they go only into the benchmark process's environment;
- the log names and layout;
- the sync, its schedule and its exclusions;
- `make bench-vm-status`, which still tails the current attempt's log from the bucket;
- the run record and its schema version;
- the run spec and the run id;
- the product.

#### Docs that change with the code

These change in the implementing change, not in this amendment's commit:

- **The runbook.** Its provider-keys paragraph will say three things in plain words: a key that the benchmark or its pre-flight prints appears in the logs as its name in angle brackets; the logs are otherwise exactly what the benchmark wrote; and the limits listed under Out of Scope below.
- **The eval changelog.** Its unreleased bench VM entries say the child's output is "kept verbatim" and that the pre-flight output is redacted. They are restated in place to describe the new behavior and the restore exclusion. The work is a single change, so it carries its own changelog edit.
- **The docstrings.** They change with the behavior, pointing at ADR 0026's amendment: the child-process module (its "no pipe through the supervisor" reasoning), the pre-flight, the provider keys, the state sync and the supervisor.
- **ADR 0026 and the glossary.** ADR 0026 records the decision now, in its 2026-11-08 amendment. `CONTEXT.md` gains no term.

### Testing Decisions

#### What a good test is here

The same rule as above applies. A test observes what a user or another component sees:

- the bytes in each log;
- the objects in the fake bucket;
- the files on disk;
- the supervisor's stderr;
- the run record;
- an exit code.

It never tests a private helper.

The fixture child stands in for a careless CLI. It holds the synthetic values in its environment, prints them in the shapes a test asks for, and can scan the filesystem while it still runs.

Leak assertions report where a value was found, never the value.

#### Seams

- **The supervisor's entry point** (existing, and the highest). `python -m pydocs_eval.bench_vm supervise` runs under the supervise harness against `FakeBenchCloud`, with the real fixture child. Every end-to-end criterion lives here.
- **The child-process starter** (existing). `start_child` and `RunningChild` run real processes and real pipes. This seam covers what the supervisor seam cannot isolate: memory, the drain bound, failed writes and order. The fake cloud reads whole files, so memory is measured here, not through the supervisor.
- **The redactor's feed and finish** (new, and the only new seam). A pipe cannot pin where reads split, so chunk boundaries and overlaps are tested here, exhaustively and deterministically.

#### Acceptance criteria

The redactor (the pure seam):

- **AC-R1 Every chunking gives the whole-stream result.** For each input, the bytes written equal a naive whole-buffer oracle that the tests carry. The oracle finds every occurrence at every position, groups those that share a byte, and replaces each group by its placeholder. The test inputs and their chunkings:
  - every two-chunk split;
  - every three-chunk split of short inputs;
  - one-byte chunks;
  - seeded random chunkings (`random.Random` with fixed seeds; no new dependency) over seeded random inputs drawn from a small alphabet, which forces partial matches and overlaps;
  - inputs built from realistic synthetic keys.

  A value split at every one of its positions is replaced.
- **AC-R2 Overlaps.** Each of these becomes one placeholder per group, and no byte of any occurrence survives:
  - a value overlapping itself;
  - two values overlapping;
  - a value inside another;
  - a value that starts another.

  Adjacent occurrences get one placeholder each. The placeholder names the group's first occurrence, with the longest and then the name order breaking ties.
- **AC-R3 Short output.** An empty stream writes nothing. Output shorter than every value is written in full. A stream that is exactly one value is one placeholder. A stream that ends in a partial value writes it, unchanged, at finish.
- **AC-R4 Hold-back.**
  - A feed never writes a byte that later input could change. After every feed, what has been written is the start of the final output for every continuation the test tries, including ones that complete each value the input so far may be starting.
  - A feed writes everything the input so far determines. When that input ends in no partial value, the bytes written equal the oracle of it.
  - When the input so far is text with no occurrence, followed by a partial value m bytes long, exactly those m bytes are missing. They stay missing until later input rules them in or out, or until finish writes them unchanged.
  - Nothing is released between feeds.
- **AC-R5 Verbatim.** Input with no occurrence comes out identical. That includes invalid UTF-8, NUL, CR, CRLF, `\xc3\xa9` and a missing final newline. With no known values, every feed returns its input unchanged and nothing is held.
- **AC-R6 No value in its words.** An empty value is refused, naming the key. Feeding after finishing is refused. No message the redactor raises, and no `repr` of it, contains a value.

The child process (real processes and pipes):

- **AC-R7 Bounded memory.** A real child writes 64 MiB as one line with no newline, with synthetic values at known offsets, some of them split across writes.
  - The log equals the expected bytes, with each value replaced by its placeholder.
  - While the child runs, the peak memory traced in the process that runs the pump stays under one fixed ceiling, a small multiple of the read size. The same ceiling holds for 8 MiB and for 64 MiB.
  - The test also guards throughput: a per-byte loop would not finish in the test's time.
- **AC-R8 Prompt, and nothing early.** A child writes a line that cannot begin a value, then sleeps: during the sleep, the whole line is in the log. Another child writes text and the first half of a value, sleeps, then writes the rest: during the sleep, the log ends with the text before the half. Afterwards it holds the placeholder.
- **AC-R9 Order.** A child alternating flushed writes to stdout and stderr gets a log in write order. The existing verbatim-bytes test passes unchanged, for a keyless kind and for a keyed one.
- **AC-R10 Never stalled.** While the caller does nothing for a few seconds, a child writing far more than a pipe buffer still finishes its writes. At the supervisor seam, the same holds for a periodic sync that the fake holds open for a few seconds.
- **AC-R11 The drain bound.** A child exits while a descendant outside its process group still holds the pipe.
  - The exit code is reported within the drain bound plus a small margin.
  - The log holds everything written before the bound.
  - One structured event records the cut-off.
  - The descendant's later write fails with a broken pipe and lands nowhere.
- **AC-R12 A failed write.** With the log on a device that refuses writes (`/dev/full`), a child writing far more than a pipe buffer runs to completion with its own exit code. One structured event names the log and counts the discarded bytes, with no output bytes in it.

The supervisor (end to end):

- **AC-R13 No value anywhere, on every ending.** For `agent_track` and `before_after`, the fixture child and its pre-flight print every synthetic value they hold:
  - first in the stream, in the middle and last;
  - twice back to back;
  - split across two writes with a pause longer than the sync interval.

  Each ending is run separately: `succeeded`, `failed`, `timed_out`, and a shutdown signal while the child is still printing (`preempted` and `stopped`). After each run, the full scan finds no value in any of these places:
  - any object of the fake bucket;
  - any file under the state directory, the cache directory or the temporary directory the supervisor process uses (the tests point it at a directory they own);
  - any recorded cloud request;
  - the run record;
  - the supervisor's stderr.

  Each log holds one placeholder per printed occurrence, and every other byte the process wrote, in order.
- **AC-R14 No raw copy, even briefly.** The child and the pre-flight wait, while still running, until the placeholder of the value they printed has reached their log. Then they scan for the value in every file under the state directory, the cache directory and the temporary directory, and in the fake bucket. They find none. This catches a raw copy that is deleted before the run ends; today's pre-flight scratch file fails it.
- **AC-R15 A sync during a half-written value.** The pause between the two halves is longer than the sync interval. During the pause, both the local log and the object that sync uploaded end before the first half. After the second half, both hold the placeholder.
- **AC-R16 The pre-flight.** Its log is the redacted output. Its event in the supervisor's stderr carries at most 16,000 bytes of that output's end. With padding that puts the cut inside a value (the existing test), neither the value nor its last 10 bytes appear in stderr.
- **AC-R17 Unusable values refused.** Four synthetic values are tried: an empty one, a too-short one, one with a leading space and one with a trailing newline. Each ends the run `failed` before any pre-flight or child starts. The detail names the key and the expected shape. For the non-empty values, the full scan finds neither the value nor its trimmed form.
- **AC-R18 A relaunch never replays.** The test plants a sentinel line and a synthetic value in an earlier attempt's logs in the bucket. On the relaunch:
  - the restore brings no file under `logs/` to the VM;
  - the new attempt's logs hold only what its own processes wrote;
  - no sync of the new attempt uploads any `logs/` object but its own;
  - the earlier objects stay byte-identical;
  - neither the sentinel nor the planted value appears in any file, upload, log line or record detail that the new attempt produces.

  Every kind still resumes from its ledger; the existing relaunch tests keep passing.
- **AC-R19 A log that already exists.** The test plants the attempt's log file before its process would start. The run ends `failed` before the process starts, naming the file, and the file is left byte-identical.
- **AC-R20 Keyless kinds unchanged.** A `retrieval` child's log is byte-identical to what the child wrote, through the same pump.

Synthetic values only, and no real key looked up or printed:

- **AC-R21 Synthetic values only.** Every value in these tests is synthetic: either a constant in the test fakes that says FAKE, or a seeded random value built by the test.
  - An autouse fixture in the bench VM tests removes every provider-key variable from the test process's environment: each name the kind table declares, and any other `*_API_KEY`. A developer's real key can therefore reach no child, log or assertion message.
  - No test runs the real `gcloud`. The cloud adapter's secret access is tested only against a scripted command runner, as today.
- **AC-R22 The implementation never prints a value.** Every structured event, exception message, `repr` and record detail that the redactor, the pump and the value checks produce carries only key names, paths and byte counts. The scans of AC-R13 to AC-R19 check stderr and the run record for this, and AC-R6 checks the redactor.

**A process rule for whoever implements this** (it is not a test). Never look up, print or copy a real provider key:

- no `gcloud secrets versions access`;
- no unfiltered `env` or `printenv`;
- no reading credential files;
- no live cloud call.

#### Prior art

- `test_supervisor_secrets`: the `_places_holding` scan, extended here to the cache directory and the temporary directory. Its test whose padding puts the logged cut inside the key stays as is.
- `test_supervisor`: the verbatim-bytes test. It stays, and it also runs for a keyed kind.
- `test_provider_secrets`: the text redaction test, replaced by the redactor's tests.
- `_fixture_child`, extended to:
  - print values in the requested shapes;
  - split writes with pauses;
  - scan while alive;
  - leave a descendant holding the pipe.
- `FakeBenchCloud`'s per-sync `uploaded` lists and `restore_hooks`, and the relaunch tests in `test_supervisor_relaunch_restore`.
- The product's ask-your-docs activity redaction tests, which mask a secret split across stream chunks. They accumulate; this design bounds.

#### Where the tests run

In the eval suite's local gate, alongside the full CI gate set before any push.

### Out of Scope

- **Transformed or partial forms of a value.** Only an exact, contiguous occurrence of a known value in the byte stream is recognized. These are not:
  - a value wrapped across lines, escaped or encoded (base64, URL);
  - a value masked or truncated by a library (`sk-…xyz`);
  - a value cut off by other bytes or by the end of the stream, for instance a process killed while printing a value in pieces.
- **Interleaved writes.** A value written in pieces, with other writes landing between the pieces, is not contiguous, so it is not recognized. The other writes may come from the other stream, another thread or another process.
- **Files the benchmark writes itself.** These are not filtered and hold what the benchmark wrote: run files, ledgers, reports, trajectories, the agent track's transcripts, before/after's own logs under its output directory, and caches. Filtering them would rewrite the files a resume depends on. A pre-push leak detector over changed state files is a candidate follow-up.
- **Secrets other than the run's declared provider keys.** For example: metadata-server access tokens, the registry token the host script handles, or keys a CLI reads from somewhere else.
- **Filtering the supervisor's own log lines and the run record.** No code path gives them a value; the scans keep proving it.
- **Scrubbing existing logs.** Objects already in a bucket are not scrubbed, and runs at commits before this amendment keep their verbatim logs.
- **A separate log file per stream.** One merged log keeps the write order.
- **Everything else.** Any change to the run record schema, the run spec, the make targets or the product.

### Further Notes

**Why in the supervisor's stream.** These options were considered and rejected:

- *Scrub the log at each sync.* The raw bytes would sit on the VM's disk between syncs. A scrub racing a growing file can also miss a value written across it.
- *A line filter.* A line with no newline (a progress bar, a minified JSON blob) would grow without bound, and nothing would reach the log until the newline.
- *Redaction inside each benchmark CLI.* The agent track's `claude` CLI and the campaign's subprocesses are not ours to change, and the supervisor is the one place that knows the values.
- *Redact at the end of the run.* Periodic syncs would upload raw bytes first.
- *Drop the output.* The log is the only record of a deleted VM's output (user story 31).

**What it costs.** The child's output now passes through the supervisor. The child-process module avoided a pipe so the supervisor could not block, reorder or re-encode the output. This design answers all three:

- the pump's own thread means nothing blocks;
- one pipe means nothing is reordered;
- working on bytes means nothing is re-encoded.

The log lags the benchmark by at most the hold-back. A process that holds the pipe past the drain bound loses its later output.

**A deliberate choice: partial values are written as they were.** A partial value is written as it was, both at the end of the stream and when later bytes rule it out. That keeps the rule exact ("verbatim except known values") and never turns ordinary trailing text into a false placeholder. If the owner later wants a partial value masked too, it is one rule at the end of the stream: mask a held partial value at least as long as the floor. AC-R3's last case would then flip.

**Sections touched:**

- the header note;
- "Supervisor (inside the container)": step 6, the pre-flight bullet and the secrets bullet;
- "Run state persistence and relaunch": the restore now leaves out the logs;
- "Testing Decisions": the supervisor's secrets bullet;
- this section.

ADR 0026 gains a dated amendment and a note in its status line. The runbook, the eval changelog and the docstrings change with the code.

**Handoff to the implementation.** A fresh context can implement this from this section alone.

- **Read first:**
  - this amendment;
  - three parts of this spec above: "Supervisor (inside the container)", "Run state persistence and relaunch", and the supervisor bullets of "Testing Decisions";
  - ADR 0026 and its 2026-11-08 amendment;
  - the coding rules in `CLAUDE.md`.
- **Shape.** One change on the series branch, `infra/bench-vm`, in the eval suite only, with no new dependency. In git commands, spell the branch `refs/heads/infra/bench-vm`, because a directory shares its name. The change can be one ticket. If it is split: the redactor first, then the pump and its wiring, then the restore exclusion, with the docs and the changelog in the last part.
- **Order of work, tests first:**
  1. The autouse fixture that strips provider-key variables (AC-R21), so that no later test can see a real key.
  2. The oracle and the redactor's criteria (AC-R1 to AC-R6), then the redactor.
  3. The pump inside the child-process starter (AC-R7 to AC-R12).
  4. The supervisor's wiring (AC-R13 to AC-R17, AC-R19, AC-R20): the child, the kind pre-flight straight to its log, the logged tail, the value checks and the fresh log files.
  5. The restore exclusion through `BenchCloud`, both adapters and `StateSync` (AC-R18).
  6. The docstrings, the runbook paragraph and the eval changelog's unreleased entries.
- **Rules:**
  - synthetic values only;
  - never look up, print or copy a real key (the process rule above);
  - no live cloud, and no Docker needed;
  - no change to the product, `uv.lock`, the run record schema or the run spec;
  - files stay under 500 lines.
- **Gates before calling it done:**
  - the eval suite's local gate;
  - `ruff check` and `ruff format --check` over `benchmarks/`;
  - the README audit grep from `CLAUDE.md`, for the runbook edit;
  - the full CI gate set from `CLAUDE.md` before any push.

  Pushing, and updating PR #437, are the owner's call.
