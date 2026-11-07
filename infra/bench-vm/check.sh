#!/usr/bin/env bash
# `make bench-vm-check`: check the whole bench VM tree (ADR 0026) with no
# cloud credentials and no Terraform backend. Its only network use is
# `terraform init` downloading the provider the committed lock file pins
# (TF_PLUGIN_CACHE_DIR, when set, saves even that).
#
# Every step runs even after one fails, so one pass reports every problem;
# each tool names the offending file. The summary is the last line:
# `bench-vm-check: ok` on stdout, or `bench-vm-check: failed: <steps>` on
# stderr with exit 1. CI (.github/workflows/bench-vm-check.yml) runs the same
# target.
#
# Needs: python with the eval suite's base dependencies, terraform, shellcheck
# and hadolint on PATH.

set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root" || exit 1

readonly bench_vm=infra/bench-vm
readonly terraform_dir="$bench_vm/terraform"
failed_steps=()

# WHY a throwaway TF_DATA_DIR: `terraform init` then never leaves .terraform/
# in the tree. With -lockfile=readonly it never rewrites the committed lock
# file either; a provider the lock does not pin fails the init instead.
TF_DATA_DIR="$(mktemp -d)" || exit 1
export TF_DATA_DIR TF_IN_AUTOMATION=1
trap 'rm -rf "$TF_DATA_DIR"' EXIT

# run_step NAME TOOL COMMAND...: run COMMAND when TOOL is on PATH, and record
# NAME as failed when it is not or when COMMAND fails.
run_step() {
  local name=$1 tool=$2
  shift 2
  echo "== $name"
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "$tool: not found on PATH; $bench_vm/README.md lists what the check needs" >&2
    failed_steps+=("$name")
    return 1
  fi
  if ! "$@"; then
    failed_steps+=("$name")
    return 1
  fi
}

check_run_specs() {
  PYTHONPATH=benchmarks/src python -m pydocs_eval.bench_vm check "$bench_vm"/runs/*.yaml
}

# A read loop, not mapfile: macOS ships bash 3.2, which has no mapfile.
check_shell_scripts() {
  local scripts=() script
  while IFS= read -r script; do
    scripts+=("$script")
  done < <(find "$bench_vm" -name '*.sh' -not -path '*/.terraform/*' | sort)
  if ((${#scripts[@]} == 0)); then
    echo "$bench_vm: got no *.sh file, expected the host and image scripts" >&2
    return 1
  fi
  shellcheck "${scripts[@]}"
}

check_dockerfiles() {
  hadolint "$bench_vm"/docker/Dockerfile.*
}

# Run from the repository root so every unformatted file is named by its
# repository path.
check_terraform_format() {
  terraform fmt -check -recursive -diff -no-color "$terraform_dir"
}

# init prints a page on success; show it only when it fails.
init_terraform_without_backend() {
  local output
  if ! output="$(terraform -chdir="$terraform_dir" init -backend=false \
    -lockfile=readonly -input=false -no-color 2>&1)"; then
    printf '%s\n' "$output" >&2
    return 1
  fi
}

run_step "run specs" python check_run_specs
run_step "shellcheck" shellcheck check_shell_scripts
run_step "hadolint" hadolint check_dockerfiles
run_step "terraform fmt" terraform check_terraform_format
# validate and the mocked `terraform test` need the provider init installs.
if run_step "terraform init" terraform init_terraform_without_backend; then
  run_step "terraform validate" terraform terraform -chdir="$terraform_dir" validate -no-color
  run_step "terraform test" terraform terraform -chdir="$terraform_dir" test -no-color
fi

if ((${#failed_steps[@]})); then
  joined="$(printf '%s, ' "${failed_steps[@]}")"
  echo "bench-vm-check: failed: ${joined%, }" >&2
  exit 1
fi
echo "bench-vm-check: ok"
