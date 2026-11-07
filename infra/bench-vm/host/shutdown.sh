#!/bin/bash
# Bench VM host shutdown script: the instance's `shutdown-script` metadata
# value, read from the run's commit by the launcher (ADR 0026). Compute
# Engine's guest agent runs it as root when the VM is preempted, stopped or
# deleted, the maximum run duration included.
#
# It stops the supervisor's container with a grace period: `docker stop`
# sends the supervisor (the container's PID 1) SIGTERM, which is its signal to
# flush. The supervisor reads the preempted flag, stops the benchmark, pushes
# the files a resume needs before the rest, and records `preempted` or
# `stopped` (pydocs_eval.bench_vm.shutdown_signal). Docker kills the container
# only if it outlives the grace.
#
# Needs only Docker (curl is the startup script's; the supervisor reads the
# metadata server itself).
set -euo pipefail

# The startup script's container (a test pins the two names together).
readonly CONTAINER_NAME=bench-supervisor
# Compute Engine gives a shutdown script about 90 s on a stop or delete and
# a spot VM 30 s after its preemption notice. The grace fits the longer one
# and covers, for an ordinary incremental push, the supervisor's 10 s child
# stop (supervisor_ports.py, shutdown_stop_grace_s), the preempted-flag
# read, the two final pushes and the record write. On a preemption the VM
# goes first, which is why the ledgers are pushed first.
readonly STOP_GRACE_S=80

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

main() {
  if ! docker container inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    log bench_vm_host_shutdown_skipped detail="no container $CONTAINER_NAME to stop"
    return 0
  fi
  log bench_vm_host_shutdown container="$CONTAINER_NAME" grace_s="$STOP_GRACE_S"
  if ! docker stop --time "$STOP_GRACE_S" "$CONTAINER_NAME" >/dev/null; then
    log bench_vm_host_failed step=stop detail="docker stop $CONTAINER_NAME failed"
    exit 1
  fi
  log bench_vm_container_stopped container="$CONTAINER_NAME"
}

main "$@"
