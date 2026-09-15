#!/usr/bin/env bash
# Create — or refresh the assets of — the GitHub Release for ONE release tag.
#
# WHY one script: release.yml runs it twice (after a tag's publish, and by hand
# for a tag whose run predates the job) and release-eval.yml runs it for the eval
# distribution; three copies of the same twenty lines of `gh release` glue drift.
# The notes come from the matching changelog section when there is one
# (scripts/release_notes_from_changelog.py), else from GitHub's generated notes
# started at the previous tag of the same prefix. Idempotent: an existing
# Release only gets its assets refreshed.
#
#   scripts/github_release.sh --tag v0.8.1 --dist dist --changelog CHANGELOG.md \
#       --tag-prefix v --title-prefix pydocs-mcp [--not-latest] [--dry-run]
#
# Needs `gh` authenticated (GH_TOKEN) and GITHUB_REPOSITORY (or --repo).
set -euo pipefail

usage() {
  echo "usage: $0 --tag <tag> --dist <dir> --changelog <file> --tag-prefix <v|eval-v>" \
    "--title-prefix <name> [--repo owner/name] [--not-latest] [--dry-run]" >&2
  exit 2
}

REPO="${GITHUB_REPOSITORY:-}"
LATEST_ARGS=()
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --dist) DIST="$2"; shift 2 ;;
    --changelog) CHANGELOG="$2"; shift 2 ;;
    --tag-prefix) TAG_PREFIX="$2"; shift 2 ;;
    --title-prefix) TITLE_PREFIX="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --not-latest) LATEST_ARGS=(--latest=false); shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) usage ;;
  esac
done
: "${TAG:?--tag is required}" "${DIST:?--dist is required}" "${CHANGELOG:?--changelog is required}"
: "${TAG_PREFIX:?--tag-prefix is required}" "${TITLE_PREFIX:?--title-prefix is required}"
: "${REPO:?set GITHUB_REPOSITORY or pass --repo}"

VERSION="${TAG#"$TAG_PREFIX"}"
if [ "$VERSION" = "$TAG" ]; then
  echo "tag '$TAG' does not start with the prefix '$TAG_PREFIX'" >&2
  exit 2
fi
PREVIOUS_TAG="$(git describe --tags --abbrev=0 --match "${TAG_PREFIX}*" "${TAG}^" 2>/dev/null || true)"

shopt -s nullglob
ASSETS=("$DIST"/*.whl "$DIST"/*.tar.gz)
shopt -u nullglob
if [ "${#ASSETS[@]}" -eq 0 ]; then
  echo "no wheel or sdist under '$DIST'" >&2
  exit 2
fi

NOTES_FILE="$(mktemp)"
NOTES_ARGS=(--notes-file "$NOTES_FILE")
if python scripts/release_notes_from_changelog.py --changelog "$CHANGELOG" --version "$VERSION" \
     --repo "$REPO" --tag "$TAG" --previous-tag "$PREVIOUS_TAG" > "$NOTES_FILE"; then
  NOTES_SOURCE="the [$VERSION] section of $CHANGELOG"
else
  NOTES_SOURCE="GitHub-generated (no [$VERSION] section in $CHANGELOG)"
  NOTES_ARGS=(--generate-notes)
  if [ -n "$PREVIOUS_TAG" ]; then
    NOTES_ARGS+=(--notes-start-tag "$PREVIOUS_TAG")
  fi
fi
echo "release $TAG: ${#ASSETS[@]} asset(s), previous tag '${PREVIOUS_TAG:-none}', notes $NOTES_SOURCE"

if [ "$DRY_RUN" = 1 ]; then
  echo "DRY RUN — would run:"
  echo "  gh release create $TAG ${ASSETS[*]} --verify-tag --repo $REPO --title '$TITLE_PREFIX $VERSION' ${LATEST_ARGS[*]:-} ${NOTES_ARGS[*]}"
  [ -s "$NOTES_FILE" ] && { echo "--- notes ---"; cat "$NOTES_FILE"; }
  exit 0
fi

if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  echo "Release $TAG exists; refreshing its assets only"
  gh release upload "$TAG" "${ASSETS[@]}" --clobber --repo "$REPO"
  exit 0
fi
gh release create "$TAG" "${ASSETS[@]}" --verify-tag --repo "$REPO" \
  --title "$TITLE_PREFIX $VERSION" "${LATEST_ARGS[@]}" "${NOTES_ARGS[@]}"
echo "Release $TAG created"
