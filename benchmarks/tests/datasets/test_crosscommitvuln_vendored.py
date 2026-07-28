"""Pins on the REAL packaged vendored corpus (design §9.1) — these read
``pydocs_eval.datasets.data.crosscommitvuln`` via importlib.resources and go
green only after the one-time network build run
(``benchmarks/tools/build_crosscommitvuln.py``) has produced + committed the
records. The count is a BOUND (24 always-clean single-CVE-repo records + up
to 9 multi-CVE-repo records surviving the ancestry drop), not a hard pin.

Until that gated network run lands, the vendored ``records.jsonl`` ships EMPTY
(placeholder), so every records-dependent pin SKIPS with a clear reason and the
suite stays green; the NOTICE pin below is unconditional (the NOTICE ships
now). Once records are populated the same pins ENFORCE the construction bound —
no test edit needed."""

from __future__ import annotations

import hashlib
import importlib.resources as ir
import json
import re

import pytest

from pydocs_eval.datasets._crosscommitvuln_build import assert_query_clean
from pydocs_eval.optimize._split import partition_task_ids

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_PENDING = "vendored records pending the network build tool run (design §6.3)"

# Golden split membership over the REAL vendored ids, bare and as
# ``CombinedDataset`` prefixes them. Regenerated ONLY when the corpus itself
# changes — a vocabulary or taxonomy change must leave both digests untouched,
# which is the whole point of keeping ``ccv/`` a corpus prefix rather than a
# task name (2026-07-28 consolidation). sha256 of the newline-joined SORTED
# train side, first 16 hex chars.
_GOLDEN_BARE_TRAIN = (10, 15, "8673a5054308729c")
_GOLDEN_PREFIXED_TRAIN = (13, 12, "c432ac367a99e3a4")


def _train_shape(task_ids: list[str]) -> tuple[int, int, str]:
    train, holdout = partition_task_ids(task_ids)
    digest = hashlib.sha256("\n".join(sorted(train)).encode("utf-8")).hexdigest()[:16]
    return len(train), len(holdout), digest


def _rows(name: str) -> list[dict]:
    text = ir.files("pydocs_eval.datasets.data.crosscommitvuln").joinpath(name).read_text()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _records() -> list[dict]:
    """Vendored records, or skip when the gated build hasn't populated them."""
    records = _rows("records.jsonl")
    if not records:
        pytest.skip(_PENDING)
    return records


def test_vendored_count_within_construction_bound() -> None:
    assert 24 <= len(_records()) <= 33


def test_the_vendored_split_membership_is_pinned_bare_and_prefixed() -> None:
    # The taxonomy consolidation renamed the TASK ``ccv`` -> ``vuln`` and
    # retired the TASK ``sweqapro`` while leaving every task-id spelling alone.
    # Split membership is sha256 of the record id, so this pin is the direct
    # evidence that no record changed sides: renaming a task cannot move a
    # corpus prefix, and only the prefix reaches the hash.
    ids = [rec["task_id"] for rec in _records()]
    assert _train_shape(ids) == _GOLDEN_BARE_TRAIN
    assert _train_shape([f"ccv/{task_id}" for task_id in ids]) == _GOLDEN_PREFIXED_TRAIN


def test_every_record_single_repo_single_commit() -> None:
    for rec in _records():
        assert isinstance(rec["repo_url"], str) and rec["repo_url"], rec["task_id"]
        assert _SHA40.fullmatch(rec["prefix_sha"]), rec["task_id"]


def test_all_records_banned_token_sweep() -> None:
    records = _records()
    banned_by_id = {row["task_id"]: row["banned"] for row in _rows("banned_tokens.jsonl")}
    for rec in records:
        assert_query_clean(rec["query"], banned_by_id[rec["task_id"]])  # raises on any leak


def test_gold_always_non_empty_and_co_residence_cleared() -> None:
    for rec in _records():
        gold = rec["gold"]
        assert gold["files"] and gold["cve_id"] and gold["cwe_ids"], rec["task_id"]
        assert rec["metadata"]["co_resident_cves"] == "", rec["task_id"]


def test_vendored_queries_are_distinct_not_one_repeated_template() -> None:
    # Queries are now LLM-generated per record (design §5.2 v3): each is a
    # distinct natural-language security-audit request, gated by the banned-token
    # leak-check with a deterministic template fallback. A single repeated string
    # across all records would mean generation silently regressed to the fallback
    # for every record. Skips (like the count gate) until the build populates them.
    records = _records()
    assert len({rec["query"] for rec in records}) == len(records)


def test_every_vendored_gold_file_is_python() -> None:
    # The model-visible corpus materializes ONLY .py files (design §5.3), so the
    # build tool's gold-file gate must have left every gold path a .py file — a
    # non-.py gold path would be structurally unanswerable. Enforced once real
    # records are vendored; skips (like the count gate) until then.
    for rec in _records():
        for path in rec["gold"]["files"]:
            assert path.endswith(".py"), (rec["task_id"], path)


def test_notice_ships_with_the_vendored_data() -> None:
    notice = ir.files("pydocs_eval.datasets.data.crosscommitvuln").joinpath("NOTICE").read_text()
    for required in (
        "Arunabh Majumdar",
        "CC BY 4.0",
        "arXiv:2604.21917",
        "10.5281/zenodo.19338596",
    ):
        assert required in notice


def test_every_shipped_cwe_has_flaw_class_words() -> None:
    """An unmapped CWE makes the query leak-gate a NO-OP for that record (M1).

    `mine_banned_tokens` looks each CWE up in `_CWE_CLASS_KEYWORDS`; a miss
    contributes no phrases, so a generated query may name the vulnerability class
    outright ("an authentication bypass in the certificate path") and pass both
    `generate_clean_query` and the final `assert_query_clean` unchallenged.
    A silent no-op is the worst failure mode a leak gate has.
    """
    from pydocs_eval.datasets._crosscommitvuln_build import _CWE_CLASS_KEYWORDS

    shipped = {cwe for rec in _records() for cwe in rec["gold"]["cwe_ids"]}
    unmapped = sorted(str(c) for c in shipped if str(c) not in _CWE_CLASS_KEYWORDS)
    assert unmapped == [], (
        f"no flaw-class words for {unmapped}; the query leak-gate is a no-op for "
        "records carrying them"
    )


def test_mining_an_unmapped_cwe_fails_loudly() -> None:
    """Adding a record with an unknown CWE must break the BUILD, not ship silently."""
    from pydocs_eval.datasets._crosscommitvuln_build import mine_banned_tokens

    with pytest.raises(KeyError, match="CWE-9999"):
        mine_banned_tokens({"cve_id": "CVE-2099-1", "cwe_ids": ["CWE-9999"], "summary": ""})


def test_no_shipped_query_is_a_template_fallback() -> None:
    """Distinctness is a proxy; PROVENANCE is the invariant (review M3).

    If `_claude_generate` breaks — the CLI is missing or renamed, or `_query_prompt`
    starts tripping the leak gate on every attempt — `generate_clean_query` falls
    back to the deterministic template for every record and the LLM-varied-query
    feature is silently dead. The existing distinctness test still passes, because
    the template interpolates the repo name and so stays unique per record.

    Checked structurally against the LOCKED template, so it holds for the corpus
    as shipped today rather than only after the next rebuild.
    """
    from pydocs_eval.datasets._crosscommitvuln_build import _QUERY_TEMPLATE

    skeleton = re.escape(_QUERY_TEMPLATE)
    for field in ("ecosystem", "repo_slug", "severity"):
        skeleton = skeleton.replace(re.escape("{" + field + "}"), ".+")

    fallbacks = [r["task_id"] for r in _records() if re.fullmatch(skeleton, r["query"], re.S)]
    assert fallbacks == [], (
        f"{len(fallbacks)} record(s) shipped the deterministic template instead of a "
        f"generated query, so the LLM path was dead at build time: {fallbacks[:5]}"
    )


def test_shipped_records_record_their_query_provenance() -> None:
    """Records built after M3 carry `query_source`; older ones are grandfathered.

    The structural check above is the guard that works today; this one becomes
    load-bearing after the next corpus rebuild.
    """
    for rec in _records():
        source = rec["metadata"].get("query_source")
        assert source in (None, "llm", "template"), f"{rec['task_id']}: {source!r}"
        assert source != "template", f"{rec['task_id']} shipped a template fallback"
