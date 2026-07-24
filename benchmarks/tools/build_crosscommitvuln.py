#!/usr/bin/env python3
"""Build the vendored CrossCommitVuln QA records (design §6.3). NOT shipped.

ONE-TIME NETWORK-HEAVY construction step run by the implementer: clones each
included CVE's repo once via RepoCache (~28 distinct repos; pytorch-scale
worst case), resolves and pins ``prefix_sha = fix_commit^``, verifies the
contributing commits are ancestors, runs the co-resident ancestry drop over
multi-CVE repos, generates + leak-checks every query, and writes
``records.jsonl`` + ``banned_tokens.jsonl`` into the floor-protected vendored
dir. The written artifacts are COMMITTED; re-running is idempotent.

Usage:
    cd <repo root>
    PYTHONPATH=benchmarks/src python benchmarks/tools/build_crosscommitvuln.py \
        /path/to/CrossCommitVuln-Bench
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from pydocs_eval.datasets._crosscommitvuln_build import (
    assert_query_clean,
    build_file_set,
    build_mechanism,
    build_query,
    is_included,
    mine_banned_tokens,
    repo_slug,
)
from pydocs_eval.datasets._repo_cache import RepoCache

log = logging.getLogger("build_crosscommitvuln")

_VENDORED_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "pydocs_eval"
    / "datasets"
    / "data"
    / "crosscommitvuln"
)
_GIT_TIMEOUT = 600
_SOURCE_ATTRIBUTION = "CrossCommitVuln-Bench (CC BY 4.0, Arunabh Majumdar); transformed to QA"


def _git(checkout: Path, *args: str) -> str:
    # git is a trusted local binary; args are repo paths + literal git verbs from
    # the vendored annotations, never free-form user text, and check=True + the
    # timeout keep a hung/missing git from stalling the one-time build.
    result = subprocess.run(  # noqa: S603
        ["git", "-C", str(checkout), *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    return result.stdout.strip()


def load_annotations(source: Path) -> list[dict]:
    paths = sorted(source.glob("dataset/*/annotation.json"))
    if not paths:
        raise ValueError(
            f"no dataset/*/annotation.json under {source} — "
            "expected a CrossCommitVuln-Bench clone root"
        )
    return [json.loads(p.read_text()) for p in paths]


def resolve_prefix_sha(cache: RepoCache, url: str, fix_commit: str) -> tuple[Path, str]:
    """Checkout at fix_commit and pin its parent — the assembled pre-fix state."""
    checkout = cache.checkout(url, fix_commit)
    return checkout, _git(checkout, "rev-parse", f"{fix_commit}^")


def is_ancestor(checkout: Path, ancestor: str, descendant: str) -> bool:
    try:
        _git(checkout, "merge-base", "--is-ancestor", ancestor, descendant)
    except subprocess.CalledProcessError:
        return False
    return True


def contributing_hashes_present(checkout: Path, prefix_sha: str, annotation: dict) -> bool:
    return all(
        is_ancestor(checkout, commit["hash"], prefix_sha)
        for commit in annotation.get("contributing_commits") or []
    )


def chain_assembled_at(checkout: Path, prefix_sha: str, other: dict) -> bool:
    """True iff OTHER's full chain is present AND still unfixed at prefix_sha."""
    if not contributing_hashes_present(checkout, prefix_sha, other):
        return False
    return not is_ancestor(checkout, other["fix_commit"], prefix_sha)


def co_resident_cves(
    record_annotation: dict,
    siblings: list[dict],
    is_assembled: Callable[[dict], bool],
) -> tuple[str, ...]:
    """Other included CVEs of the same repo fully assembled at this snapshot."""
    return tuple(
        s["cve_id"]
        for s in siblings
        if s["cve_id"] != record_annotation["cve_id"] and is_assembled(s)
    )


def build_record(annotation: dict, prefix_sha: str) -> tuple[dict, dict]:
    """One annotation + pinned sha -> (vendored record, banned-token row)."""
    query = build_query(annotation)
    banned = mine_banned_tokens(annotation)
    assert_query_clean(query, banned)  # build-failing leak check (design §5.2)
    task_id = str(annotation["cve_id"]).lower()
    record = {
        "task_id": task_id,
        "repo_url": str(annotation["repo"]).removesuffix(".git"),
        "prefix_sha": prefix_sha,
        "fix_commit": annotation["fix_commit"],
        "query": query,
        "gold": {
            "cve_id": annotation["cve_id"],
            "cwe_ids": list(annotation.get("cwe_ids") or []),
            "mechanism": build_mechanism(annotation),
            "files": list(build_file_set(annotation)),
        },
        "metadata": _metadata(annotation),
    }
    return record, {"task_id": task_id, "banned": list(banned)}


def _metadata(annotation: dict) -> dict[str, str]:
    commits = annotation.get("contributing_commits") or []
    dates = sorted(str(c.get("date", "")) for c in commits if c.get("date"))
    return {
        "ecosystem": str(annotation.get("ecosystem", "")),
        "severity": str(annotation.get("severity_combined", "")),
        "commit_span_days": str(annotation.get("commit_span_days", "")),
        # Temporal fields are METADATA ONLY — never interpolated into the query.
        "intro_window": f"{dates[0]}..{dates[-1]}" if dates else "",
        "fix_commit_date": "",  # resolved from git in main()
        "co_resident_cves": "",  # empty by construction after the ancestry drop
        "source": _SOURCE_ATTRIBUTION,
    }


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(argv) != 2:
        print("usage: build_crosscommitvuln.py <path-to-CrossCommitVuln-Bench-clone>")
        return 2
    annotations = load_annotations(Path(argv[1]))
    included = [a for a in annotations if is_included(a)]
    log.info(
        "gate 1 (inclusion filter): %d included of %d annotation(s), %d excluded",
        len(included),
        len(annotations),
        len(annotations) - len(included),
    )
    by_repo: dict[str, list[dict]] = {}
    for a in included:
        by_repo.setdefault(repo_slug(a), []).append(a)

    cache = RepoCache()
    records: list[dict] = []
    banned_rows: list[dict] = []
    dropped_ancestry: list[str] = []
    dropped_broken: list[str] = []
    for a in included:
        cve = a["cve_id"]
        try:
            checkout, prefix_sha = resolve_prefix_sha(cache, str(a["repo"]), a["fix_commit"])
        except (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            log.info("drop %s: prefix resolution failed (%s)", cve, exc)
            dropped_broken.append(cve)
            continue
        if not contributing_hashes_present(checkout, prefix_sha, a):
            log.info("drop %s: contributing commit missing at prefix_sha", cve)
            dropped_broken.append(cve)
            continue
        # Bind this iteration's checkout/prefix_sha into the predicate (the
        # lambda is invoked synchronously inside co_resident_cves, but the
        # default-arg capture keeps the loop-variable binding explicit and lint
        # -clean).
        co = co_resident_cves(
            a,
            by_repo[repo_slug(a)],
            is_assembled=lambda other, ck=checkout, ps=prefix_sha: chain_assembled_at(
                ck, ps, other
            ),
        )
        if co:  # gate 2 — co-resident ancestry DROP (design §5.2)
            log.info("drop %s: co-resident CVE(s) %s at %s", cve, ", ".join(co), prefix_sha[:12])
            dropped_ancestry.append(cve)
            continue
        record, banned_row = build_record(a, prefix_sha)
        record["metadata"]["fix_commit_date"] = _git(
            checkout, "show", "-s", "--format=%cs", a["fix_commit"]
        )
        records.append(record)
        banned_rows.append(banned_row)

    _write_jsonl(_VENDORED_DIR / "records.jsonl", records)
    _write_jsonl(_VENDORED_DIR / "banned_tokens.jsonl", banned_rows)
    log.info(
        "vendored %d record(s); ancestry-dropped %d (%s); broken-dropped %d (%s)",
        len(records),
        len(dropped_ancestry),
        ", ".join(dropped_ancestry) or "-",
        len(dropped_broken),
        ", ".join(dropped_broken) or "-",
    )
    log.info(
        "MANUAL REVIEW (hard v1 step, design §5.2): read every query in %s "
        "before committing — automated token mining has synonym blind spots",
        _VENDORED_DIR / "records.jsonl",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
