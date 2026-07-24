"""CrossCommitVuln dataset loader tests — hermetic via ``fixture_path`` +
a fake ``RepoCache`` (no network, no git). Gold-bearing fixtures live under
the floor-covered dir tests/fixtures/crosscommitvuln/ (design §6.6)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_eval.datasets._crosscommitvuln_build import assert_query_clean
from pydocs_eval.datasets.base_dataset import Dataset
from pydocs_eval.datasets.crosscommitvuln import CrossCommitVulnDataset
from pydocs_eval.registries import dataset_registry

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_MINI = _FIXTURES / "crosscommitvuln" / "mini.jsonl"
_BANNED = _FIXTURES / "crosscommitvuln" / "banned_tokens.jsonl"
# Reuse the existing checked-in fake corpus tree (no gold inside it).
_CORPUS_DIR = _FIXTURES / "swe_qa_corpus"

_SHA40 = re.compile(r"^[0-9a-f]{40}$")


@dataclass
class _FakeRepoCache:
    """Stand-in for ``RepoCache`` — no git, no network."""

    corpus_dir: Path = field(default=_CORPUS_DIR)

    def checkout(self, url: str, sha: str) -> Path:
        return self.corpus_dir

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return ()


def _dataset() -> CrossCommitVulnDataset:
    return CrossCommitVulnDataset(fixture_path=_MINI, repo_cache=_FakeRepoCache())


def _raw_records() -> list[dict]:
    return [json.loads(line) for line in _MINI.read_text().splitlines() if line.strip()]


async def test_satisfies_dataset_protocol() -> None:
    assert isinstance(_dataset(), Dataset)


async def test_registered_and_buildable_with_fixture() -> None:
    ds = dataset_registry.build("crosscommitvuln", fixture_path=_MINI, repo_cache=_FakeRepoCache())
    assert isinstance(ds, CrossCommitVulnDataset)
    assert ds.name == "crosscommitvuln" and ds.revision == "1.0"


async def test_yields_tasks_with_gold_and_metadata() -> None:
    tasks = [t async for t in _dataset().tasks()]
    assert [t.task_id for t in tasks] == ["cve-2026-27602", "cve-2026-26198"]
    t0 = tasks[0]
    assert t0.gold.file_set == (
        "modoboa/admin/jobs.py",
        "modoboa/admin/models/mailbox.py",
        "modoboa/lib/sysutils.py",
        "modoboa/webmail/models.py",
    )
    assert t0.gold.extra["cve_id"] == "CVE-2026-27602"
    assert t0.gold.extra["cwe_id_0"] == "CWE-78"
    assert t0.gold.ast_body and "shell" in t0.gold.ast_body
    assert t0.metadata["intro_window"] == "2024-01-15..2024-11-23"
    assert t0.metadata["fix_commit_date"] == "2025-01-08"
    assert t0.metadata["co_resident_cves"] == ""


async def test_malformed_record_dropped_and_counts_logged(caplog) -> None:
    with caplog.at_level(logging.INFO):
        tasks = [t async for t in _dataset().tasks()]
    assert len(tasks) == 2  # 3 fixture rows, 1 short-sha row -> excluded
    assert any(
        "1" in rec.getMessage() and "exclud" in rec.getMessage().lower() for rec in caplog.records
    )


async def test_single_repo_single_commit_invariant() -> None:
    # §5.0 hard invariant: one record <-> one repo_url <-> one 40-hex sha,
    # and the loader emits exactly one task per well-formed record.
    tasks = {t.task_id: t async for t in _dataset().tasks()}
    for rec in _raw_records():
        if not _SHA40.fullmatch(str(rec["prefix_sha"])):
            assert rec["task_id"] not in tasks  # malformed row never becomes a task
            continue
        assert isinstance(rec["repo_url"], str) and rec["repo_url"].count("github.com") == 1
        assert rec["task_id"] in tasks


async def test_temporal_metadata_never_in_query() -> None:
    async for task in _dataset().tasks():
        for key in ("intro_window", "fix_commit_date", "commit_span_days"):
            value = task.metadata[key]
            assert value and value not in task.query


async def test_query_clean_against_stored_banned_tokens() -> None:
    banned_by_id = {
        row["task_id"]: row["banned"]
        for row in (json.loads(line) for line in _BANNED.read_text().splitlines() if line.strip())
    }
    count = 0
    async for task in _dataset().tasks():
        assert_query_clean(task.query, banned_by_id[task.task_id])  # raises on any leak
        count += 1
    assert count == 2


async def test_mechanism_rides_ast_body_not_extra_and_gold_non_empty() -> None:
    async for task in _dataset().tasks():
        assert task.gold.file_set and task.gold.extra["cve_id"]  # never vacuous (design §6.5)
        assert task.gold.ast_body not in task.gold.extra.values()  # no prose in extra


async def test_corpus_source_materializes_history_less_checkout() -> None:
    tasks = [t async for t in _dataset().tasks()]
    corpus = tasks[0].corpus_source()
    assert (corpus / "src/qibo/models/variational.py").exists()
    assert not (corpus / ".git").exists()  # §5.0: history-less snapshot
