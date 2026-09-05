"""RepoQADataset tests — fixture-only by default, urllib stubbed for the
release path (no network in tests)."""

from __future__ import annotations

import gzip
import json
import urllib.request
from pathlib import Path

import pytest
from pydocs_eval.datasets.base_dataset import Dataset
from pydocs_eval.datasets.repoqa import (
    RepoQADataset,
    _extract_body,
)

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "repoqa_mini.json"


async def test_fixture_yields_five_tasks() -> None:
    """2 Python repos (3 + 2 needles) → 5 EvalTasks (one per needle)."""
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    assert len(tasks) == 5


async def test_each_task_has_required_fields() -> None:
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    async for task in dataset.tasks():
        assert task.task_id
        assert task.query
        assert task.gold.ast_body
        assert task.metadata["repo"]
        assert task.metadata["language"] == "python"


async def test_gold_body_extracted_from_content() -> None:
    """The gold body comes from content[path] sliced by start_line/end_line."""
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    assert "def factorial" in tasks[0].gold.ast_body
    assert "def fibonacci" in tasks[1].gold.ast_body


async def test_task_id_includes_repo_sha_path_name() -> None:
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    assert "@" in tasks[0].task_id
    assert "::" in tasks[0].task_id


async def test_corpus_source_materializes_repo_content() -> None:
    """Each task's corpus_source returns a tmp dir containing the repo files."""
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    corpus_dir = tasks[0].corpus_source()
    assert (corpus_dir / "fixture_repo" / "math_helpers.py").exists()


async def test_dataset_satisfies_protocol() -> None:
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    assert isinstance(dataset, Dataset)


def test_revision_is_pinned_release_version() -> None:
    dataset = RepoQADataset()
    assert dataset.revision == "2024-06-23"


def test_extract_body_handles_mixed_line_endings() -> None:
    """RepoQA needles can carry any of \\n / \\r\\n / \\r line endings — the
    extraction must produce the same body regardless of source convention.
    (Spec §8 risk row.)"""
    source_unix = "line1\nline2\nline3\nline4\n"
    source_win = "line1\r\nline2\r\nline3\r\nline4\r\n"
    source_old_mac = "line1\rline2\rline3\rline4\r"
    # 1-indexed inclusive lines 2..3 → "line2", "line3"
    assert _extract_body(source_unix, 2, 3) == "line2\nline3"
    assert _extract_body(source_win, 2, 3) == "line2\nline3"
    assert _extract_body(source_old_mac, 2, 3) == "line2\nline3"


async def test_release_download_path_uses_urllib(monkeypatch, tmp_path) -> None:
    """When no fixture is provided, the loader fetches the GitHub release via
    urllib. Stub urlopen to return a tiny gzipped JSON — no network."""
    fake_payload = json.dumps({"python": [], "go": []}).encode()
    fake_gz = gzip.compress(fake_payload)

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def read(self):
            return fake_gz

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: _FakeResp(),
    )

    dataset = RepoQADataset(cache_dir=tmp_path)
    tasks = [t async for t in dataset.tasks()]
    assert tasks == []
    assert (tmp_path / "repoqa-2024-06-23.json").exists()


async def test_release_download_corrupt_payload_does_not_clobber_cache(
    monkeypatch,
    tmp_path,
) -> None:
    """A gzipped download that decompresses to non-JSON must NOT leave a
    'good-looking' cache file behind. Atomic-write contract:
    write-to-tmp → validate-JSON → os.replace into place."""
    fake_gz = gzip.compress(b"not valid json{][")

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def read(self):
            return fake_gz

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: _FakeResp(),
    )

    dataset = RepoQADataset(cache_dir=tmp_path)
    with pytest.raises(json.JSONDecodeError):
        _ = [t async for t in dataset.tasks()]
    # The final cache file must not exist — atomic write aborts before replace.
    assert not (tmp_path / "repoqa-2024-06-23.json").exists()
