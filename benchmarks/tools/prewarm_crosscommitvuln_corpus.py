#!/usr/bin/env python3
"""Prewarm local git bundles for the crosscommitvuln eval corpus (airgap). NOT shipped.

Owner-run, network ONCE. Reads the vendored ``records.jsonl`` (or an argv path
override), and for every distinct ``repo_url`` writes ONE ``<repo>.bundle`` into
a local cache dir carrying every pinned ``prefix_sha`` that repo needs (a repo
with several CVEs — e.g. mcp-atlassian — gets both shas in the same bundle). The
eval loader (``CrossCommitVulnDataset``) then materializes its corpus OFFLINE from
these bundles. Nothing third-party ships in the wheel — bundles live only here.

Idempotent, and **content-aware**: a repo is skipped only when its existing
bundle already carries a ref for every sha the records pin for it. A bundle that
predates a newly-pinned sha (a second CVE on an already-bundled repo), or one
that is truncated/corrupt, is REBUILT — an existence-only skip would report
success for a corpus that silently lacks a commit the eval later demands.
Failures are logged per-repo and counted (no silent caps); the run exits non-zero
if any repo failed so an operator notices.

Re-running after adding records is therefore the supported way to extend the
corpus; it needs network again for the repos that changed.

Usage:
    cd <repo root>
    PYTHONPATH=benchmarks/src python benchmarks/tools/prewarm_crosscommitvuln_corpus.py
    # override the destination (else $PYDOCS_CCV_BUNDLE_DIR or the default cache dir):
    PYTHONPATH=benchmarks/src python benchmarks/tools/prewarm_crosscommitvuln_corpus.py \
        --bundle-dir /path/to/bundles
"""

from __future__ import annotations

import argparse
import importlib.resources as ir
import json
import logging
import subprocess
import sys
from pathlib import Path

from pydocs_eval.datasets._repo_cache import (
    _DEFAULT_BUNDLE_DIR,
    RepoCache,
    _git,
    _repo_name,
    _stderr_tail,
    bundle_path,
    resolve_bundle_dir,
)

log = logging.getLogger("prewarm_crosscommitvuln")

# A pinned commit is bundled under a branch ref (``refs/heads/ccv-<sha>``) so that
# ``git clone <bundle>`` transfers its objects — a bare sha or a non-heads ref is
# refused / not fetched by clone. See the module docstring of the checkout path.
_BUNDLE_REF_PREFIX = "ccv"

# Any of these while building ONE repo's bundle drops that repo (loud + counted)
# instead of aborting the whole prewarm; the run still exits non-zero at the end.
_BUNDLE_FAILURES = (
    RuntimeError,
    subprocess.CalledProcessError,
    subprocess.TimeoutExpired,
    OSError,
)


def read_records(path: Path | None) -> list[dict]:
    """Vendored records (default) or an explicit override path — one JSON per line."""
    if path is not None:
        text = path.read_text()
    else:
        text = (
            ir.files("pydocs_eval.datasets.data.crosscommitvuln")
            .joinpath("records.jsonl")
            .read_text()
        )
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def group_shas_by_repo(records: list[dict]) -> dict[str, set[str]]:
    """Collect every distinct ``prefix_sha`` per ``repo_url`` (one bundle per repo)."""
    by_repo: dict[str, set[str]] = {}
    for rec in records:
        url = rec.get("repo_url")
        sha = rec.get("prefix_sha")
        if not isinstance(url, str) or not url or not isinstance(sha, str) or not sha:
            log.info("prewarm: skipping malformed record %r", rec.get("task_id"))
            continue
        by_repo.setdefault(url, set()).add(sha)
    return by_repo


def bundle_carries(target: Path, shas: set[str]) -> bool:
    """True when ``target`` is a readable bundle holding a ref for every ``sha``.

    WHY not a bare ``target.exists()``: an existence-only skip silently accepts a
    bundle built *before* ``records.jsonl`` pinned a new sha for that repo — the
    "second CVE on an already-bundled repo" case, which this corpus already has
    (``sooperset/mcp-atlassian``). The shortfall would not surface here at all;
    it would surface much later at eval time as ``git worktree add ... invalid
    reference``, long after this tool printed AIRGAP READY and exited 0.

    Reading the bundle's own ref list also rejects a truncated or corrupt file,
    which ``exists()`` would happily trust as complete.
    """
    try:
        listing = _git("bundle", "list-heads", str(target))
    except _BUNDLE_FAILURES:
        return False  # unreadable or not a bundle -> rebuild it
    have = {line.split()[-1] for line in listing.splitlines() if line.strip()}
    return all(f"refs/heads/{_BUNDLE_REF_PREFIX}-{sha}" in have for sha in shas)


def _mark_refs(base: Path, shas: set[str]) -> list[str]:
    """Point a branch ref at each sha in the base clone; return the ref names."""
    refs: list[str] = []
    for sha in sorted(shas):
        ref = f"{_BUNDLE_REF_PREFIX}-{sha}"
        _git("branch", "-f", ref, sha, cwd=base)
        refs.append(ref)
    return refs


def _create_bundle(base: Path, target: Path, refs: list[str]) -> None:
    """Bundle ``refs`` into ``target`` atomically (temp file then rename).

    WHY temp+rename: a crash mid-``git bundle create`` must never leave a partial
    ``<repo>.bundle`` that a later run's idempotent skip would trust as complete.
    """
    tmp = target.parent / f"{target.name}.tmp"
    try:
        _git("bundle", "create", str(tmp), *refs, cwd=base)
    except subprocess.CalledProcessError as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"git bundle create failed for {target}: {_stderr_tail(exc)}") from exc
    tmp.replace(target)


def prewarm_repo(
    bundle_dir: Path,
    url: str,
    shas: set[str],
    cache_root: Path | None = None,
) -> Path:
    """Build ``<bundle_dir>/<repo>.bundle`` carrying every ``sha`` for ``url``.

    Clones ``url`` once (network) into a base clone, materializes each pinned
    commit so it lands in that base's object store, marks each with a branch ref,
    and bundles those refs. ``cache_root`` overrides the base-clone dir (tests use
    an ephemeral one so the prewarm never touches the shared user cache).
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)
    cache = RepoCache(root=cache_root) if cache_root is not None else RepoCache()
    for sha in sorted(shas):
        cache.checkout(url, sha)  # network once — ensures sha present in the base clone
    base = cache.root / _repo_name(url)
    refs = _mark_refs(base, shas)
    target = bundle_path(bundle_dir, url)
    _create_bundle(base, target, refs)
    return target


def run(bundle_dir: Path, by_repo: dict[str, set[str]], cache_root: Path | None) -> int:
    """Prewarm every repo; log per-repo + totals, exit non-zero on any failure."""
    total_shas = sum(len(s) for s in by_repo.values())
    log.info(
        "prewarm: %d repo(s), %d distinct pinned commit(s) -> %s",
        len(by_repo),
        total_shas,
        bundle_dir,
    )
    built, skipped, failures = 0, 0, []
    for url, shas in sorted(by_repo.items()):
        name = _repo_name(url)
        target = bundle_path(bundle_dir, url)
        if bundle_carries(target, shas):
            log.info(
                "skip %s: bundle already carries all %d pinned commit(s) (%s)",
                name,
                len(shas),
                target.name,
            )
            skipped += 1
            continue
        if target.exists():
            log.info(
                "rebuild %s: existing bundle is stale or unreadable (records now pin %d commit(s))",
                name,
                len(shas),
            )
        try:
            path = prewarm_repo(bundle_dir, url, shas, cache_root)
        except _BUNDLE_FAILURES as exc:
            log.error("FAILED %s: %s", name, exc)
            failures.append(name)
            continue
        log.info(
            "built %s: %d commit(s), %d bytes -> %s",
            name,
            len(shas),
            path.stat().st_size,
            path,
        )
        built += 1
    log.info(
        "prewarm done: built %d, skipped %d, failed %d (%s)",
        built,
        skipped,
        len(failures),
        ", ".join(failures) or "-",
    )
    _print_usage(bundle_dir)
    return 1 if failures else 0


def _print_usage(bundle_dir: Path) -> None:
    """Tell the operator how to run the eval OFFLINE from the bundles just built."""
    log.info(
        "AIRGAP READY: point the eval at these bundles with\n"
        "    export PYDOCS_CCV_BUNDLE_DIR=%s\n"
        "The crosscommitvuln loader auto-detects this dir (or the default %s) and "
        "then materializes its corpus with NO network. Bundles stay in this local "
        "cache dir — nothing third-party ships in the wheel.",
        bundle_dir,
        _DEFAULT_BUNDLE_DIR,
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prewarm crosscommitvuln corpus git bundles for offline eval (airgap).",
    )
    parser.add_argument(
        "--bundle-dir",
        # Resolved to an absolute path: `git bundle create` runs with
        # cwd=<base clone>, so a relative dir would mean two different places.
        type=lambda s: Path(s).expanduser().resolve(),
        default=None,
        help="destination for <repo>.bundle files "
        "(default: $PYDOCS_CCV_BUNDLE_DIR or ~/.cache/pydocs-mcp/crosscommitvuln-bundles)",
    )
    parser.add_argument(
        "--records",
        type=lambda s: Path(s).expanduser(),
        default=None,
        help="override path to records.jsonl (default: the vendored file)",
    )
    parser.add_argument(
        "--cache-root",
        type=lambda s: Path(s).expanduser(),
        default=None,
        help="base-clone cache dir (default: RepoCache's ~/.cache/pydocs-mcp/swe-qa-repos)",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    bundle_dir = args.bundle_dir or resolve_bundle_dir()
    by_repo = group_shas_by_repo(read_records(args.records))
    if not by_repo:
        log.error("prewarm: no usable records found; nothing to do")
        return 2
    return run(bundle_dir, by_repo, args.cache_root)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
