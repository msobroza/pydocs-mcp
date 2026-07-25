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
from collections.abc import Callable, Sequence
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
# Per-record LLM query generation (design §5.2 v3): bound the CLI call and the
# regenerate-on-leak retries so the one-time build cannot hang or spin forever.
_GEN_TIMEOUT = 120.0
_GEN_ATTEMPTS = 3
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


def _query_prompt(annotation: dict) -> str:
    """Build the LLM prompt that generates ONE varied, needle-hiding query (§5.2).

    It MAY name the repo slug / ecosystem / severity a real reviewer would know,
    but HARD-FORBIDS anything that reveals where the flaw is, what class it is, or
    how it arose. This prompt only biases the model toward clean output; the
    banned-token leak-check (:func:`generate_clean_query`) is the hard gate.
    """
    slug = repo_slug(annotation)
    ecosystem = str(annotation.get("ecosystem", "PyPI"))
    severity = str(annotation.get("severity_combined", "high")).lower()
    return (
        "Write ONE natural-sounding security-audit request, 2-4 sentences, asking a "
        f"reviewer to audit the {ecosystem} project {slug} for a single "
        f"{severity}-severity vulnerability. Ask them to locate the exploitable "
        "condition: trace where untrusted input enters, follow it to the dangerous "
        "operation it reaches, and classify the vulnerability by how it is exploited. "
        "Vary the phrasing so it reads like a distinct human request.\n\n"
        "The request MUST NOT name or hint at any of: a CWE id, a CVE id, a file name "
        "or path, a function / API / method / sink name, the vulnerability-class word "
        "(injection, traversal, deserialization, SSRF, XSS, etc.), any commit or commit "
        "hash, any date, or how or when the flaw was introduced. Reveal neither where "
        "the flaw is nor what class it belongs to.\n\n"
        "Output ONLY the request text — no preamble, no quotes, no markdown."
    )


def _claude_generate(prompt: str) -> str:
    """Generate one candidate query via the ``claude`` CLI (design §5.2).

    Mirrors the coding-agent-playbook LLM judge's invocation. ``claude`` is a
    trusted local binary and ``prompt`` is our own constructed audit request,
    never free-form external text; ``check=False`` + the timeout keep a hung
    CLI from stalling the one-time build. A timeout or empty stdout returns ``""``
    so the caller treats it as a failed attempt (regenerate, then template fallback).
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["claude", "-p", prompt],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=_GEN_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return ""
    return result.stdout.strip()


def generate_clean_query(
    annotation: dict,
    banned: Sequence[str],
    generator: Callable[[dict], str],
    attempts: int = _GEN_ATTEMPTS,
) -> tuple[str, str]:
    """SAFETY CORE (design §5.2): a leak-free generated query, else the template.

    Each candidate from ``generator`` is gated by :func:`assert_query_clean`
    against ``banned``; an empty/blank candidate or one that leaks a banned token
    is rejected and regenerated up to ``attempts`` times. If every attempt is
    empty or leaks, return the deterministic :func:`build_query` template —
    guaranteed clean — so a LEAKING QUERY CAN NEVER SHIP. ``generator`` is
    injected so tests pass a fake; production passes :func:`_production_generator`.

    Returns ``(query, source)`` where ``source`` is ``"llm"`` or ``"template"``.
    WHY the second value: a broken generator degrades EVERY record to the
    template, and the distinctness test cannot see it (the template interpolates
    the repo name, so it stays unique per record). Recording provenance makes the
    silent-death case assertable (review M3).
    """
    cve = annotation.get("cve_id")
    for _ in range(attempts):
        query = generator(annotation)
        if not query.strip():
            continue  # empty/blank stdout = a failed attempt
        try:
            assert_query_clean(query, banned)
        except ValueError as leak:
            log.info("regenerating query for %s: %s", cve, leak)
            continue
        return query, "llm"
    log.info(
        "query generation exhausted for %s after %d attempt(s); using deterministic "
        "template fallback (design §5.2)",
        cve,
        attempts,
    )
    return build_query(annotation), "template"


def _production_generator(annotation: dict) -> str:
    """The real per-record generator wired in :func:`main`: prompt -> ``claude`` CLI."""
    return _claude_generate(_query_prompt(annotation))


def build_record(
    annotation: dict,
    prefix_sha: str,
    *,
    generator: Callable[[dict], str] | None = None,
    extra_banned: tuple[str, ...] = (),
) -> tuple[dict, dict]:
    """One annotation + pinned sha -> (vendored record, banned-token row).

    ``generator`` (injected LLM query generator) is optional: absent -> the
    deterministic :func:`build_query` template (back-compat for the hermetic
    tests); present -> a varied natural-language query, each candidate gated by
    the banned-token leak-check with a template fallback (:func:`generate_clean_query`).
    Either way the trailing :func:`assert_query_clean` is the final belt-and-suspenders
    guard — both the gated generated query and the fallback already pass it.
    """
    # ``extra_banned`` carries tokens resolved from git (the fix-commit date),
    # which must be banned BEFORE the query is generated and leak-checked.
    banned = tuple(mine_banned_tokens(annotation)) + tuple(t for t in extra_banned if t)
    if generator is None:
        query, query_source = build_query(annotation), "template"
    else:
        query, query_source = generate_clean_query(annotation, banned, generator)
    assert_query_clean(query, banned)  # build-failing final leak check (design §5.2)
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
        "metadata": _metadata(annotation) | {"query_source": query_source},
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


def _write_corpus(path: Path, rows: list[dict]) -> None:
    """Write the vendored corpus, refusing to SHRINK an existing one.

    WHY: every resolution step needs GitHub. On a rate-limited or offline box
    every record is tallied as broken and dropped, and an unconditional write
    truncated the committed records.jsonl to EMPTY — with CI staying green,
    because the vendored-pin tests skip on an empty file. The corpus is a
    reviewed artifact, so a degraded build must fail loudly rather than erase it.

    A monotonicity guard rather than an absolute floor: it needs no magic
    number, it self-adjusts as the corpus grows, and a first build (or a build
    redirected elsewhere for inspection) is unaffected.
    """
    existing = len(path.read_text().splitlines()) if path.exists() else 0
    if len(rows) < existing:
        raise SystemExit(
            f"refusing to shrink {path} from {existing} to {len(rows)} record(s): "
            "the build degraded, most likely no/rate-limited GitHub access. "
            "Re-run with network access, or delete the file deliberately to rebuild."
        )
    _write_jsonl(path, rows)


class _DropRecord(Exception):
    """A single record fails a construction gate (design §5.2) — drop it, keep going.

    ``category`` routes the drop to the right tally: ``"ancestry"`` (a co-resident
    CVE is assembled at this snapshot) or ``"broken"`` (its own chain is absent).
    """

    def __init__(self, category: str, reason: str) -> None:
        super().__init__(reason)
        self.category = category


# Any of these from a single record's resolution/ancestry git calls drops THAT
# record (loud + counted) instead of aborting the whole one-time build. A query
# leak (ValueError from build_record) is deliberately NOT here — it stays
# build-failing so a leaked query can never be silently vendored (design §5.2).
_GIT_FAILURES = (RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired)


def apply_gold_file_gate(record: dict, tracked: tuple[str, ...]) -> None:
    """Trim gold files to ``.py`` paths that actually exist at ``prefix_sha``.

    WHY: the model-visible corpus materializes ONLY ``.py`` files
    (``_repo_cache.read_checkout_files``), and the ``gold_substring_all`` gate
    needs every gold path to be answerable — so a non-``.py`` gold path, or one
    that was renamed/deleted before ``prefix_sha``, makes the task unwinnable and
    silently deflates the ccv slice. Trim such paths in place; if NONE survive,
    the whole record is unwinnable — drop it (loud + counted, design §5.2).
    """
    present = set(tracked)
    files: list[str] = list(record["gold"]["files"])
    kept = [f for f in files if f.endswith(".py") and f in present]
    removed = [f for f in files if f not in kept]
    if removed:
        log.info(
            "gold-file gate %s: removed %d non-.py/absent gold path(s) %s",
            record["task_id"],
            len(removed),
            removed,
        )
    if not kept:
        raise _DropRecord(
            "nogold",
            f"no .py gold file present at prefix_sha (gold was {files})",
        )
    record["gold"]["files"] = kept


def _resolve_and_build(
    cache: RepoCache,
    annotation: dict,
    siblings: list[dict],
    *,
    generator: Callable[[dict], str] | None = None,
    extra_banned: tuple[str, ...] = (),
) -> tuple[dict, dict]:
    """Resolve prefix_sha, run both ancestry gates, and build one vendored record.

    Raises :class:`_DropRecord` on a gate rejection; lets ``_GIT_FAILURES`` from any
    git call propagate so the caller drops only this record. The whole
    resolve → ancestry → finalize phase is ONE guarded unit — a git timeout/error
    in the later ancestry calls must not abort the build (design §5.2). ``generator``
    is threaded down to :func:`build_record` (see its docstring).
    """
    checkout, prefix_sha = resolve_prefix_sha(
        cache, str(annotation["repo"]), annotation["fix_commit"]
    )
    if not contributing_hashes_present(checkout, prefix_sha, annotation):
        raise _DropRecord("broken", "contributing commit missing at prefix_sha")
    co = co_resident_cves(
        annotation,
        siblings,
        # Default-arg capture pins THIS iteration's checkout/prefix into the
        # predicate (B023-clean); it is invoked synchronously inside the helper.
        is_assembled=lambda other, ck=checkout, ps=prefix_sha: chain_assembled_at(ck, ps, other),
    )
    if co:
        raise _DropRecord("ancestry", f"co-resident CVE(s) {', '.join(co)} at {prefix_sha[:12]}")
    # Resolve temporal metadata BEFORE build_record, so the fix date can join the
    # banned-token list the query leak-check runs against (design §9.1). Resolved
    # afterwards it was structurally unbannable, and a generated query naming the
    # patch date passed both gates unchallenged.
    fix_commit_date = _git(checkout, "show", "-s", "--format=%cs", annotation["fix_commit"])
    record, banned_row = build_record(
        annotation, prefix_sha, generator=generator, extra_banned=(fix_commit_date,)
    )
    # Gold-file gate (design §5.3): keep only .py gold present at the snapshot the
    # model actually sees; a record with no answerable gold left is dropped here.
    apply_gold_file_gate(record, cache.file_tree(str(annotation["repo"]), prefix_sha))
    record["metadata"]["fix_commit_date"] = fix_commit_date
    return record, banned_row


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
    # Per-record LLM query generation (design §5.2 v3): wire the real generator
    # once; each record's varied query is leak-gated with a template fallback, so
    # a gen failure for one record falls back (never aborts the build).
    generator = _production_generator
    records: list[dict] = []
    banned_rows: list[dict] = []
    dropped_ancestry: list[str] = []
    dropped_broken: list[str] = []
    dropped_no_gold: list[str] = []
    for a in included:
        cve = a["cve_id"]
        try:  # gates 2/3 (design §5.2): the whole resolve→ancestry→finalize phase
            record, banned_row = _resolve_and_build(
                cache, a, by_repo[repo_slug(a)], generator=generator
            )
        except _DropRecord as drop:
            log.info("drop %s: %s", cve, drop)
            tally = {"ancestry": dropped_ancestry, "nogold": dropped_no_gold}.get(
                drop.category, dropped_broken
            )
            tally.append(cve)
            continue
        except _GIT_FAILURES as exc:  # widened guard: one bad repo never aborts the build
            log.info("drop %s: git failure during resolution/ancestry (%s)", cve, exc)
            dropped_broken.append(cve)
            continue
        records.append(record)
        banned_rows.append(banned_row)

    _write_corpus(_VENDORED_DIR / "records.jsonl", records)
    _write_jsonl(_VENDORED_DIR / "banned_tokens.jsonl", banned_rows)
    log.info(
        "vendored %d record(s); ancestry-dropped %d (%s); broken-dropped %d (%s); "
        "no-py-gold-dropped %d (%s)",
        len(records),
        len(dropped_ancestry),
        ", ".join(dropped_ancestry) or "-",
        len(dropped_broken),
        ", ".join(dropped_broken) or "-",
        len(dropped_no_gold),
        ", ".join(dropped_no_gold) or "-",
    )
    log.info(
        "MANUAL REVIEW (hard v1 step, design §5.2): read every query in %s "
        "before committing — automated token mining has synonym blind spots",
        _VENDORED_DIR / "records.jsonl",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
