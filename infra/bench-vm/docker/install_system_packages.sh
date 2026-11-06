#!/usr/bin/env bash
# System packages of the final stage of BOTH bench VM images (Ubuntu 24.04,
# the distribution the CI benchmark job runs on).
#
# The OpenBLAS lines mirror .github/workflows/benchmark.yml exactly. WHY:
# turbovec's compiled extension links cblas_sgemm (the CBLAS C ABI). Ubuntu's
# default libopenblas-dev provides only the Fortran ABI (sgemm_);
# libopenblas-pthread-dev adds the CBLAS interface, and the
# update-alternatives selection makes the dynamic linker resolve it. The
# Dockerfiles then preload libopenblas.so.0 (LD_PRELOAD), as the CI job does,
# so the symbol resolves at dlopen() time.
set -euo pipefail

readonly OPENBLAS_LIBRARY=/usr/lib/x86_64-linux-gnu/libopenblas.so.0

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git libopenblas-pthread-dev liblapack-dev
update-alternatives --set libblas.so.3-x86_64-linux-gnu \
  /usr/lib/x86_64-linux-gnu/openblas-pthread/libblas.so.3 || true
rm -rf /var/lib/apt/lists/*

# Fail the build, not the VM: the preload target must be the pthread variant.
resolved=$(readlink -f "$OPENBLAS_LIBRARY")
if [[ $resolved != *openblas-pthread* ]]; then
  echo "install_system_packages: $OPENBLAS_LIBRARY resolves to '$resolved', expected the openblas-pthread variant" >&2
  exit 1
fi
echo "install_system_packages: $OPENBLAS_LIBRARY -> $resolved"
