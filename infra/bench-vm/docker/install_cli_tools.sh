#!/usr/bin/env bash
# The pinned CLIs of BOTH bench VM images, each checked against its sha256:
#
#   * the Cloud SDK, because the supervisor syncs run state to GCS and deletes
#     its own VM from inside the container; the Linux tarball bundles its own
#     Python, so it does not touch the image's virtual environment;
#   * the `claude` CLI, which the agent track drives. The native build needs no
#     Node runtime.
#
# Bumping a version means bumping its sha256 in the same edit:
#   Cloud SDK: sha256sum of google-cloud-cli-<version>-linux-x86_64.tar.gz
#   claude:    "checksum" of linux-x64 in <CLAUDE_RELEASES>/<version>/manifest.json
set -euo pipefail

readonly GCLOUD_VERSION=592.0.0
readonly GCLOUD_SHA256=2d841a23a681ab94486af6841e5f1cd123cd300fb8c4be6ba5b51350fca3d50b
readonly GCLOUD_URL="https://dl.google.com/dl/cloudsdk/channels/rapid/downloads/google-cloud-cli-${GCLOUD_VERSION}-linux-x86_64.tar.gz"
readonly CLAUDE_CODE_VERSION=2.1.311
readonly CLAUDE_CODE_SHA256=86dc1fcc69498159eaf15f446613e847010f9036bfbbdffd4c49350be8489ada
readonly CLAUDE_RELEASES=https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/claude-code-releases
readonly CLAUDE_URL="${CLAUDE_RELEASES}/${CLAUDE_CODE_VERSION}/linux-x64/claude"

# download URL DEST SHA256: bounded retries and timeout, then a strict checksum.
download() {
  curl --fail --silent --show-error --location \
    --retry 5 --retry-all-errors --connect-timeout 30 --max-time 900 \
    --output "$2" "$1"
  echo "$3  $2" | sha256sum --check --strict
}

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

download "$GCLOUD_URL" "$scratch/gcloud.tar.gz" "$GCLOUD_SHA256"
tar -xzf "$scratch/gcloud.tar.gz" -C /opt
/opt/google-cloud-sdk/bin/gcloud config set --installation component_manager/disable_update_check true

download "$CLAUDE_URL" "$scratch/claude" "$CLAUDE_CODE_SHA256"
install -m 0755 "$scratch/claude" /usr/local/bin/claude

/opt/google-cloud-sdk/bin/gcloud --version | head -n 1
/usr/local/bin/claude --version
