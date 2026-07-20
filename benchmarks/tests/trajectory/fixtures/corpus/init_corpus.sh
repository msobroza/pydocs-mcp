#!/usr/bin/env bash
# Materialize the widgetlib fixture corpus as a fresh git workspace.
#
# The corpus ships as plain files under ``src/`` (NOT a nested git repo — a
# committed .git would be a submodule footgun). A rollout needs a real git
# workspace because the driver captures the final patch via ``git diff``
# (ADR 0009 loop-side capture). This script copies ``src/`` into a target dir,
# git-inits it, and makes one base commit so ``git diff`` after the rollout
# yields exactly the model's edits.
#
# Usage:
#   init_corpus.sh <target-dir>       # materialize into <target-dir>
#   init_corpus.sh                    # materialize into a fresh mktemp dir
#
# Prints the absolute workspace path as the last line (the rollout driver's
# ``workspace`` / ``corpus_dir``).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="$here/src"

target="${1:-$(mktemp -d)}"
mkdir -p "$target"
cp -R "$src/." "$target/"

git -C "$target" init -q
git -C "$target" add -A
# Fixed identity so the base commit is reproducible and never depends on the
# caller's global git config.
git -C "$target" -c user.email=fixtures@pydocs -c user.name=fixtures \
    commit -qm "widgetlib fixture corpus — buggy base state"

echo "$(cd "$target" && pwd)"
