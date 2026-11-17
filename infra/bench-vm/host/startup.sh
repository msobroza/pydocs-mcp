#!/bin/bash
# Bench VM host startup script: the instance's `startup-script` metadata value,
# read from the run's commit by the launcher (ADR 0026). Compute Engine's guest
# agent runs it as root at boot.
#
# The host does as little as possible and needs only Docker and curl (both on
# Container-Optimized OS and on the Deep Learning VM image). It reads the
# instance metadata, logs Docker in to Artifact Registry with the VM service
# account's token from the metadata server, pulls the run's image by digest,
# and starts ONE detached container whose entrypoint is the supervisor
# (`python -m pydocs_eval.bench_vm supervise`), with the GPUs exposed on the
# CUDA flavour and the run's state and cache directories mounted at fixed
# absolute paths. Everything else (the run record, the child, the syncs, the
# self-delete) is the supervisor's.
#
# Fixed paths are a correctness requirement: the index caches key on absolute
# paths, so a cache restored on another VM is valid only at the same path.
#
# Never `set -x`: the registry token passes through this script.
set -euo pipefail

readonly METADATA_URL=http://metadata.google.internal/computeMetadata/v1
# Writable on both boot images (COS keeps / read-only but /var stateful). The
# override exists for the script's own tests.
readonly BENCH_HOST_DIR=${BENCH_HOST_DIR:-/var/lib/bench}
# The container side: pydocs_eval.bench_vm.container_paths STATE_DIR and
# CACHE_DIR (a test pins the two together).
readonly CONTAINER_STATE_DIR=/bench/state
readonly CONTAINER_CACHE_DIR=/bench/cache
readonly CONTAINER_NAME=bench-supervisor
readonly PULL_ATTEMPTS=3
# A CUDA image is several GB; a pull that stalls past this is retried.
readonly PULL_TIMEOUT_S=1800

# log EVENT [KEY=VALUE...]: one JSON object per line on stderr.
log() {
  local event=$1 fields="" pair value
  shift
  for pair in "$@"; do
    value=${pair#*=}
    value=${value//[\"\\]/}
    fields+=",\"${pair%%=*}\":\"${value}\""
  done
  printf '{"event":"%s"%s}\n' "$event" "$fields" >&2
}

fail() {
  log bench_vm_host_failed step="$1" detail="$2"
  exit 1
}

# Every metadata read has a timeout and a bounded retry.
metadata() {
  curl -fsS --retry 5 --retry-delay 2 --retry-connrefused --max-time 10 \
    -H 'Metadata-Flavor: Google' "$METADATA_URL/$1"
}

attribute() {
  metadata "instance/attributes/$1"
}

# The VM service account's access token, for Artifact Registry read.
registry_token() {
  local answer
  answer=$(metadata instance/service-accounts/default/token)
  if [[ $answer =~ \"access_token\":\ ?\"([^\"]+)\" ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
    return 0
  fi
  log bench_vm_host_failed step=token detail="no access_token in the metadata server answer"
  return 1
}

pull_image() {
  local image=$1 attempt
  for ((attempt = 1; attempt <= PULL_ATTEMPTS; attempt++)); do
    if timeout "$PULL_TIMEOUT_S" docker pull --quiet "$image" >/dev/null; then
      return 0
    fi
    log bench_vm_pull_retry image="$image" attempt="$attempt"
    sleep $((attempt * 10))
  done
  return 1
}

main() {
  local run_id image digest registry
  local gpu_flags=()
  run_id=$(attribute bench-run-id)
  image=$(attribute bench-image)
  digest=$(attribute bench-image-digest)
  if [[ $image != *"@$digest" ]]; then
    fail image "got image ${image}, expected one named by its digest ${digest}"
  fi
  # The guest agent re-runs this script on a reboot; one run, one supervisor.
  if docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    log bench_vm_host_skipped run_id="$run_id" detail="container $CONTAINER_NAME already exists"
    return 0
  fi
  export DOCKER_CONFIG=$BENCH_HOST_DIR/docker
  mkdir -p "$DOCKER_CONFIG" "$BENCH_HOST_DIR/state" "$BENCH_HOST_DIR/cache"
  registry=${image%%/*}
  registry_token |
    docker login --username oauth2accesstoken --password-stdin "https://$registry" >/dev/null ||
    fail login "docker login to $registry failed"
  pull_image "$image" || fail pull "pulling $image failed $PULL_ATTEMPTS times"
  if [[ $image == */bench-cuda@* ]]; then
    gpu_flags=(--gpus all)
  fi
  # Host networking: the supervisor reads the metadata server and calls GCS
  # and the Compute API exactly as the host would.
  docker run --detach --name "$CONTAINER_NAME" --network host "${gpu_flags[@]}" \
    --volume "$BENCH_HOST_DIR/state:$CONTAINER_STATE_DIR" \
    --volume "$BENCH_HOST_DIR/cache:$CONTAINER_CACHE_DIR" \
    --entrypoint python "$image" \
    -m pydocs_eval.bench_vm supervise \
    --state-dir "$CONTAINER_STATE_DIR" --cache-dir "$CONTAINER_CACHE_DIR" >/dev/null ||
    fail run "starting the supervisor container failed"
  log bench_vm_container_started run_id="$run_id" image="$image" container="$CONTAINER_NAME"
}

main "$@"
