#!/usr/bin/env bash
# Build and smoke-test ONE bench VM image flavour from a `git archive` of ONE
# commit with a local Docker -- the steps of infra/bench-vm/cloudbuild.yaml,
# minus the push. Needs an x86_64 container runtime.
#
#   usage: infra/bench-vm/docker/build-local.sh <commit-ish> <cpu|cuda>
#
# The image is tagged bench-<flavour>:<sha> locally only after its smoke step
# passes; nothing is pushed to any registry. The CUDA smoke needs no GPU.
set -euo pipefail

usage="usage: $0 <commit-ish> <cpu|cuda>"
ref=${1:?$usage}
flavour=${2:?$usage}
if [[ $flavour != cpu && $flavour != cuda ]]; then
  echo "build-local: flavour must be cpu or cuda, got '$flavour'" >&2
  exit 2
fi

repo_root=$(git rev-parse --show-toplevel)
commit=$(git -C "$repo_root" rev-parse --verify "${ref}^{commit}")
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

git -C "$repo_root" archive --format=tar --output="$scratch/commit.tar" "$commit"
archived=$(git get-tar-commit-id <"$scratch/commit.tar")
if [[ $archived != "$commit" ]]; then
  echo "build-local: archive names commit '$archived', expected '$commit'" >&2
  exit 1
fi
mkdir "$scratch/repo"
tar -xf "$scratch/commit.tar" -C "$scratch/repo"

candidate="bench-${flavour}:candidate-${commit}"
docker build \
  --file="$scratch/repo/infra/bench-vm/docker/Dockerfile.${flavour}" \
  --build-arg="COMMIT=${commit}" \
  --tag="$candidate" \
  "$scratch/repo"

if ! docker run --rm "$candidate" python /opt/pydocs-mcp/infra/bench-vm/docker/smoke.py \
  --flavour="$flavour" --commit="$commit"; then
  docker image rm "$candidate" >/dev/null
  echo "build-local: smoke step failed; no bench-${flavour}:${commit} tag" >&2
  exit 1
fi
docker tag "$candidate" "bench-${flavour}:${commit}"
docker image rm "$candidate" >/dev/null
echo "build-local: bench-${flavour}:${commit} built and smoke-tested (local only, not pushed)"
