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

import importlib.resources as ir
import json
import re

import pytest

from pydocs_eval.datasets._crosscommitvuln_build import assert_query_clean

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_PENDING = "vendored records pending the network build tool run (design §6.3)"


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


def test_notice_ships_with_the_vendored_data() -> None:
    notice = ir.files("pydocs_eval.datasets.data.crosscommitvuln").joinpath("NOTICE").read_text()
    for required in (
        "Arunabh Majumdar",
        "CC BY 4.0",
        "arXiv:2604.21917",
        "10.5281/zenodo.19338596",
    ):
        assert required in notice
