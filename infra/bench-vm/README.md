# Bench VM runbook

A bench VM is an ephemeral Compute Engine VM in `europe-west4` that runs one
benchmark run described by a committed **run spec** (`runs/<name>.yaml`), writes
everything to your own Cloud Storage bucket, and deletes itself when the run
ends. Spot capacity is the default; a preempted run resumes from its own ledger
when you launch it again. The design and its trade-offs are in
`docs/adr/0026-ephemeral-gcp-bench-vms.md`.

Every `make` command below runs from the repository root. Only the commands in
"One-time setup" and the four operating targets touch the cloud;
`make bench-vm-check` and `make bench-vm ... DRY_RUN=1` never do.

## Contents of this tree

| Path | What it is |
| --- | --- |
| `runs/*.yaml` | Run specs. `cpu-smoke.yaml` and `gpu-smoke.yaml` are the two smoke runs. |
| `terraform/` | The cloud project: network, buckets, image repository, service accounts, secrets, budget. |
| `docker/`, `cloudbuild.yaml` | The two image recipes (`bench-cpu`, `bench-cuda`) Cloud Build builds per commit. |
| `host/` | The VM's startup and shutdown scripts. |
| `check.sh` | What `make bench-vm-check` runs. |

## What you need on your machine

- `git`, `make` and Python 3.11+ with the eval suite's base dependencies
  (`uv pip install -r benchmarks/pyproject.toml`, or any dev install of the
  repository), reachable as `python` (an activated virtualenv), like every
  other make target. The bench VM commands import nothing from the product.
- The Google Cloud CLI (`gcloud`) and Terraform 1.9 or newer.
- For `make bench-vm-check` only: `shellcheck` and `hadolint`.

You need no service-account key, ever. You log in twice: `gcloud auth login`
for the launcher and the operating targets, and Application Default
Credentials (ADC) for Terraform. The operating targets also read the
deployment's names with `terraform output`, so they need both.

## One-time setup

Do these once per project, in order. Replace `PROJECT_ID`, `BILLING_ACCOUNT`
and `STATE_BUCKET` with your values. A dedicated project is recommended: the
budget watches the whole project.

### 1. Project, logins and the Terraform state bucket

```bash
gcloud auth login
gcloud projects create PROJECT_ID                     # skip if the project exists
gcloud billing projects link PROJECT_ID --billing-account=BILLING_ACCOUNT

gcloud auth application-default login
gcloud auth application-default set-quota-project PROJECT_ID
```

The last line matters: the budget resource calls the Billing Budgets API, which
refuses end-user ADC credentials unless a quota project is named. The
Terraform provider names this project on its requests (`billing_project` in
`terraform/versions.tf`); setting it as the ADC quota project too means your
ADC always carries one. Your account needs the
Billing Account Administrator (or Costs Manager) role on the billing account to
create the budget.

Bootstrap the bucket that holds the Terraform state. It is the only resource
Terraform does not create itself:

```bash
gcloud storage buckets create gs://STATE_BUCKET --project=PROJECT_ID --location=europe-west4 --uniform-bucket-level-access --public-access-prevention
gcloud storage buckets update gs://STATE_BUCKET --versioning
```

Versioning keeps every earlier state, so a bad write can be rolled back.

### 2. Apply the Terraform root

```bash
cd infra/bench-vm/terraform
cp terraform.tfvars.example terraform.tfvars          # git-ignored; set project_id, billing_account, notification_emails
terraform init -backend-config="bucket=STATE_BUCKET"
terraform apply
terraform output
```

`terraform.tfvars` takes `project_id`, `billing_account` and
`notification_emails` (one to five addresses the budget alerts email); the
region, zones, budget amount (50) and currency (EUR) have defaults, shown in
the example file. The currency must be the billing account's own. The first
`apply` enables the APIs it needs, so it can take a few minutes; if it fails
because an API was enabled moments before, run it again.

`terraform output` prints every name the launcher reads: the buckets, the image
repository, the service accounts, the subnet, the zones and the secret ids.
Keep this directory initialized: the operating targets run `terraform output`
here on every call.

### 3. Add the provider keys

Terraform declares two Secret Manager secrets but never their values, so no
key ever enters Terraform state. Add each value out of band. `read -rs` keeps
the key out of your shell history and your terminal:

```bash
read -rs KEY && printf '%s' "$KEY" | gcloud secrets versions add bench-anthropic-api-key --project=PROJECT_ID --data-file=- && unset KEY
read -rs KEY && printf '%s' "$KEY" | gcloud secrets versions add bench-openrouter-api-key --project=PROJECT_ID --data-file=- && unset KEY
```

- `bench-anthropic-api-key` is read by `agent_track` runs (metered API-key
  billing).
- `bench-openrouter-api-key` is read by `before_after` runs (the shipped
  serving and chat configs use OpenRouter).

A `retrieval` run reads neither. A run reads only the keys its kind declares
and passes them only to the benchmark process, never to a log, the run record
or the synced state. To rotate a key, add a new version the same way.

### 4. Request GPU quota (before the first GPU run)

New projects usually start with zero GPU quota. `gpu-smoke.yaml` runs one
NVIDIA L4 on spot capacity, and a run spec with `spot: false` runs on standard
capacity; each draws on its own quota. Check the current limits:

```bash
gcloud compute regions describe europe-west4 --project=PROJECT_ID --flatten=quotas --filter="quotas.metric~NVIDIA_L4" --format="table(quotas.metric,quotas.limit,quotas.usage)"
```

In the console, under IAM & Admin, Quotas & System Limits, request for region
`europe-west4`:

- `PREEMPTIBLE_NVIDIA_L4_GPUS` (spot), at least 1;
- `NVIDIA_L4_GPUS` (standard), at least 1;
- and the global `GPUS_ALL_REGIONS`, at least 1, if it is 0.

CPU runs draw on the region's N2 CPU quota (`n2-standard-8` is 8 vCPUs). If a
launch fails on a CPU quota, check and request it the same way
(`quotas.metric~N2_CPUS`).

### 5. First launch

A run spec's `git_ref` must resolve to a commit that is reachable from a
remote-tracking branch (push it first) and that carries this tree. The two
smoke specs use `git_ref: main`.

A `before_after` spec's `baseline_ref` and `candidate_ref` must also be
reachable from a remote-tracking branch of the product repository itself,
`https://github.com/msobroza/pydocs-mcp`: the VM clones that repository to
check both commits out. Any remote whose fetch URL is that repository counts,
over https or ssh; a branch pushed only to a fork is refused, and the error
lists the remotes it checked. A relaunch checks the recorded commits the same
way.

```bash
make bench-vm RUN=runs/cpu-smoke.yaml DRY_RUN=1
make bench-vm RUN=runs/cpu-smoke.yaml
make bench-vm-status RUN=runs/cpu-smoke.yaml
```

The dry run makes every check your machine can make alone (the spec, the run
id, the commits, the config files at the commit) and prints the plan, without
running `gcloud` or `terraform` and without writing anything. The real launch
prints the run id, the results location and the status command, and returns
without waiting for the run.

## The make targets

| Target | What it does |
| --- | --- |
| `make bench-vm RUN=runs/<name>.yaml` | Launch the run, or relaunch it as its next attempt (see "Endings and relaunch"). Builds the run commit's image with Cloud Build first when its tag is missing. |
| `make bench-vm RUN=runs/<name>.yaml DRY_RUN=1` | The laptop-only checks and the plan. No `gcloud`, no `terraform`, nothing written. |
| `make bench-vm-status RUN=runs/<name>.yaml` | The run's status history, commits, image, machine and the tail of its synced log. |
| `make bench-vm-status` | Every live bench VM in the project. |
| `make bench-vm-stop RUN=runs/<name>.yaml` | Delete the run's live VM. A running supervisor pushes the run's state and records `stopped`. |
| `make bench-vm-pull RUN=runs/<name>.yaml` | Download `gs://<runs-bucket>/<run-id>/` into `benchmarks/results/bench-vm/<run-id>/` (git-ignored). |
| `make bench-vm-check` | Check this whole tree without credentials (see "Checking the tree"). |

`RUN` is relative to `infra/bench-vm/`. The commands, ready to paste:

```bash
make bench-vm RUN=runs/cpu-smoke.yaml
make bench-vm RUN=runs/gpu-smoke.yaml DRY_RUN=1
make bench-vm-status RUN=runs/cpu-smoke.yaml
make bench-vm-status
make bench-vm-stop RUN=runs/cpu-smoke.yaml
make bench-vm-pull RUN=runs/cpu-smoke.yaml
make bench-vm-check
```

The targets call the eval suite's CLI, which you can also run directly, for
example to validate a spec you are writing or to pull into another directory:

```bash
PYTHONPATH=benchmarks/src python -m pydocs_eval.bench_vm check infra/bench-vm/runs/cpu-smoke.yaml
PYTHONPATH=benchmarks/src python -m pydocs_eval.bench_vm pull infra/bench-vm/runs/cpu-smoke.yaml --results-dir /tmp/bench-results
```

A run's results live under `gs://<runs-bucket>/<run-id>/`: the spec copied at
the first launch, the run record `run.json`, and `state/`, which mirrors
everything the run writes. The run id is the spec's file name plus a hash of
the fields it sets, so any edit to a spec makes a new run.

## Endings and relaunch

Nothing relaunches on its own: a relaunch is you running
`make bench-vm RUN=runs/<name>.yaml` again. A relaunch is the run's next
attempt, on the commits frozen at its first launch (even if the branch has
moved since), and each benchmark resumes from the state the last attempt
synced. Work done after the last completed sync (every `sync_interval`,
default 5 minutes) runs again.

| Status | What happened | What to do |
| --- | --- | --- |
| `succeeded` | The benchmark exited 0; final sync done; the VM deleted itself. | Final. `make bench-vm-pull`. A relaunch is refused and prints where the results are. |
| `failed` | The benchmark exited non-zero (exit code recorded), or the launch itself failed. | Read `make bench-vm-status`. Relaunch resumes on the same commits, so a code fix needs a new commit and an edited spec, which is a new run. |
| `timed_out` | The run reached its soft deadline before `max_runtime`; it was stopped and synced. | Relaunch to continue for another `max_runtime`. |
| `preempted` | Spot capacity was reclaimed; the supervisor pushed the resume files first. | Relaunch to resume. If it keeps happening, see "Cost model" on `spot: false`. |
| `stopped` | You ran `make bench-vm-stop`, or the VM was deleted from outside the run. | Relaunch to resume. |
| `lost` | The record said `launching` or `running`, but the VM was gone without reporting. | The launcher records `lost` itself on your next launch, then relaunches. |

A run that still has a live VM is refused: wait for it to end, or stop it.
Changing any field of the spec (`spot`, `max_runtime`, `git_ref`, ...) gives a
new run id, so it starts a new run from scratch rather than resuming the old
one.

## Inspecting a VM: `self_delete: false` and SSH through IAP

By default the VM deletes itself after `succeeded`, `failed` or `timed_out`,
and when its supervisor refuses the run. To keep it for a look, set `self_delete: false` in the run spec (a new run id).
The VM then stays up after the run ends, and bills, until you delete it or
`max_runtime` passes: the platform's maximum run duration still deletes it.

Bench VMs have no external IP; SSH goes through Identity-Aware Proxy, which
the firewall admits on port 22. `make bench-vm-status RUN=...` shows the VM's
name (`bench-<run-id>-<attempt>`) and zone:

```bash
gcloud compute ssh bench-RUN_ID-1 --zone=europe-west4-a --project=PROJECT_ID --tunnel-through-iap
```

On the VM, the supervisor runs in the `bench-supervisor` container
(`sudo docker logs bench-supervisor`), and the run's state and caches are
under `/var/lib/bench/`. When you are done:

```bash
make bench-vm-stop RUN=runs/cpu-smoke.yaml
```

Stopping a run that already ended deletes the VM and leaves its record as it
was.

## Cost model

You pay for VM time, the disk while the VM exists, Cloud Build minutes when an
image is missing, Cloud NAT egress and bucket storage. Model-provider spend
(Anthropic, OpenRouter) is billed by the provider, not by Google Cloud.

**Three guards bound a run:**

1. **The VM deletes itself** when its run ends (`succeeded`, `failed`,
   `timed_out`), unless the spec sets `self_delete: false`. Its boot disk is
   deleted with it. A VM whose supervisor refuses its run deletes itself too,
   writing nothing: its record has moved on, as when the launcher recorded a
   create as `failed` that came up anyway, or a second launch of the same run
   took over. Only a VM that cannot read its own metadata stays up until
   `max_runtime`.
2. **The platform deletes it at `max_runtime`** (default 6 hours): Compute
   Engine's maximum run duration with the DELETE action. It is the backstop for
   a hung guest and still applies with `self_delete: false`. The supervisor
   stops the benchmark at a soft deadline before it, early enough for the final
   sync.
3. **A 50 EUR monthly budget** on the project emails `notification_emails` at
   50%, 90% and 100% of actual spend and at 100% of forecast. It only notifies:
   it never stops anything, and billing data lags real spend by hours. The two
   guards above are what bound a single run.

**Spot by default.** `spot: true` is the default and the cheapest capacity; a
preemption costs only the work since the last completed sync, which re-runs on
relaunch. `spot: false` runs on standard capacity, which is not preempted.

**Paid kinds.** `agent_track` and `before_after` spend provider money, so their
specs must set `confirm_spend: true` and `max_usd`, which reaches the kind's
own spend guard.

**The paid window.** In a paid kind, a rollout that finished after the last
completed sync re-runs on relaunch, and its first cost appears in no restored
ledger line. The spend guard therefore undercounts provider spend by up to one
sync interval of rollouts per preemption. The run record lists every
preemption, so you can reconcile against the provider's billing. Long paid
campaigns set `spot: false`.

**Caches.** Model weights, dataset downloads, answer-judge responses and
reusable index entries persist in the cache bucket across runs. A run restores
them before its benchmark starts, using at most half of the time left to its
soft deadline, and pushes what it adds while it runs and once more after it
ends, so even an hour-long smoke run starts warm the second time. The cache
bucket is never overwritten or cleaned by a run.

**What else is bounded:** no external IPs; the image repository's cleanup
policy deletes old images (they are rebuilt on demand); the cache bucket
deletes objects after 60 days (`cache_retention_days`). The runs bucket keeps
results until you delete them.

## Checking the tree

```bash
make bench-vm-check
```

It needs no credentials and no Terraform backend, and runs, reporting every
failure by file:

- `python -m pydocs_eval.bench_vm check` on every committed run spec;
- `shellcheck` on every shell script in this tree;
- `hadolint` on both Dockerfiles;
- `terraform fmt -check` on `terraform/`;
- `terraform init -backend=false -lockfile=readonly` into a throwaway data
  directory, then `terraform validate` and the mocked `terraform test`
  (`terraform/tests/`, which plans and applies against a fake provider only).

The init never leaves `.terraform/` in the tree and never rewrites the
committed lock file. Its only network use is downloading the Google provider
the lock file pins; set `TF_PLUGIN_CACHE_DIR` to download it once. The last
line is `bench-vm-check: ok`, or `bench-vm-check: failed:` with the failing
steps. CI runs the same target whenever this tree, the eval suite's code or its
dependencies change.
