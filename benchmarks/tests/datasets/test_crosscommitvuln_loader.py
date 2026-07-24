"""CrossCommitVuln dataset loader tests — hermetic via ``fixture_path`` +
a fake ``RepoCache`` (no network, no git). Gold-bearing fixtures live under
the floor-covered dir tests/fixtures/crosscommitvuln/ (design §6.6)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.datasets._crosscommitvuln_build import assert_query_clean
from pydocs_eval.datasets._repo_cache import RepoCache
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
    assert [t.task_id for t in tasks] == ["cve-2099-0001", "cve-2099-0002"]
    t0 = tasks[0]
    assert t0.gold.file_set == (
        "app/jobs.py",
        "app/models/mailbox.py",
        "app/sysutils.py",
        "app/webmail/models.py",
    )
    assert t0.gold.extra["cve_id"] == "CVE-2099-0001"
    assert t0.gold.extra["cwe_id_0"] == "CWE-78"
    assert t0.gold.ast_body and "shell" in t0.gold.ast_body
    assert t0.metadata["intro_window"] == "2099-01-15..2099-11-23"
    assert t0.metadata["fix_commit_date"] == "2100-01-08"
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


def test_default_repo_cache_uses_bundle_dir_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Airgap: an EXISTING $PYDOCS_CCV_BUNDLE_DIR makes the default RepoCache
    # bundle-aware, so the corpus materializes offline from prewarmed bundles.
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    monkeypatch.setenv("PYDOCS_CCV_BUNDLE_DIR", str(bundles))
    ds = CrossCommitVulnDataset(fixture_path=_MINI)
    assert isinstance(ds.repo_cache, RepoCache)
    assert ds.repo_cache.bundle_dir == bundles


def test_default_repo_cache_no_bundle_dir_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No prewarmed dir -> bundle_dir stays None -> unchanged lazy network clone.
    monkeypatch.setenv("PYDOCS_CCV_BUNDLE_DIR", str(tmp_path / "does-not-exist"))
    ds = CrossCommitVulnDataset(fixture_path=_MINI)
    assert ds.repo_cache.bundle_dir is None


def test_absent_bundle_dir_warns_that_the_airgap_is_not_in_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Asking for the airgap and silently not getting it must be loud.

    Setting $PYDOCS_CCV_BUNDLE_DIR is an explicit request to run offline. If the
    dir is missing (never prewarmed, or a relative path resolved against a
    different cwd) the loader falls back to NETWORK mode — on a networked box
    that silently defeats the whole prewarm, and nothing said so.
    """
    monkeypatch.setenv("PYDOCS_CCV_BUNDLE_DIR", str(tmp_path / "does-not-exist"))
    with caplog.at_level("WARNING"):
        ds = CrossCommitVulnDataset(fixture_path=_MINI)

    assert ds.repo_cache.bundle_dir is None
    assert "does-not-exist" in caplog.text
    assert "network" in caplog.text.lower()


def test_no_warning_when_the_bundle_dir_was_never_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The warning is for an unmet *request*, so an unset env var stays quiet."""
    monkeypatch.delenv("PYDOCS_CCV_BUNDLE_DIR", raising=False)
    with caplog.at_level("WARNING"):
        CrossCommitVulnDataset(fixture_path=_MINI)
    assert caplog.text == ""


def test_injected_repo_cache_overrides_bundle_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The test injection seam wins even when the env would pick a bundle dir.
    monkeypatch.setenv("PYDOCS_CCV_BUNDLE_DIR", str(tmp_path))  # exists
    fake = _FakeRepoCache()
    ds = CrossCommitVulnDataset(fixture_path=_MINI, repo_cache=fake)
    assert ds.repo_cache is fake
