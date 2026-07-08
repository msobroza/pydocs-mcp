"""SWE-QA / SWE-QA-Pro download-path tests — urllib stubbed, no network.

Mirrors ``test_repoqa_loader.py``'s corrupt-payload coverage, which only
exists for RepoQA's whole-file gzip validation. SWE-QA and SWE-QA-Pro
instead validate per-*line* JSON (``_download_split_atomic`` /
``_download_release_atomic``), which has a distinct failure mode RepoQA's
loader can't hit: an HTTP body truncated exactly on a ``\\n`` boundary
(proxy cut-off, connection drop flushed mid-stream) leaves every remaining
line individually well-formed. Per-line ``json.loads`` validation then
accepts the partial payload and commits it via ``tmp.replace(target)`` —
the truncated cache is keyed by the pinned revision and guarded by
``if not target.exists()``, so it is never re-fetched and the benchmark
silently evaluates a shrunken task set forever.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pytest
from benchmarks.eval.datasets._download import TruncatedDownloadError
from benchmarks.eval.datasets.swe_qa import SweQaDataset
from benchmarks.eval.datasets.swe_qa_pro import SweQaProDataset


@dataclass
class _FakeRepoCache:
    """Stand-in for ``RepoCache`` — no git, no network. Empty tree means
    every row's citations resolve to nothing, which is fine: these tests
    only assert on the download-cache file, not on task content."""

    def checkout(self, url: str, sha: str) -> Path:
        raise AssertionError("checkout() should not be reached in download tests")

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return ()


class _FakeResp:
    """Stand-in for the ``http.client.HTTPResponse`` context manager. Carries
    a ``headers`` mapping like the real response so ``Content-Length``
    checks can be exercised the same way they'd run against HuggingFace."""

    def __init__(self, payload: bytes, *, advertised_length: int | None = None) -> None:
        self._payload = payload
        self.headers = (
            {"Content-Length": str(advertised_length)} if advertised_length is not None else {}
        )

    def __enter__(self) -> _FakeResp:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def _full_jsonl_payload() -> str:
    # Rows only need to satisfy the download/cache path in these tests, not
    # produce a resolvable task — but _row_to_task still indexes "repo" /
    # "commit_id" unconditionally, so every row carries harmless placeholders.
    return (
        json.dumps(
            {
                "repo": "octo/example",
                "commit_id": "0" * 40,
                "question": "q1",
                "answer": "a1 (foo.py: line 1-2)",
            }
        )
        + "\n"
        + json.dumps(
            {
                "repo": "octo/example",
                "commit_id": "0" * 40,
                "question": "q2",
                "answer": "a2 (bar.py: line 3-4)",
            }
        )
        + "\n"
        + json.dumps(
            {
                "repo": "octo/example",
                "commit_id": "0" * 40,
                "question": "q3",
                "answer": "a3 (baz.py: line 5-6)",
            }
        )
        + "\n"
    )


def _line_truncated_jsonl_payload() -> tuple[bytes, int]:
    """Three complete JSON lines as the server would have sent them, then a
    body cut off exactly after the second '\\n' (proxy cut-off / dropped
    connection flushed mid-stream) — every remaining (surviving) line still
    parses, so per-line ``json.loads`` validation alone cannot detect the
    truncation. Returns ``(truncated_bytes, full_length)`` so the caller can
    advertise the SERVER's true Content-Length, exactly as HuggingFace would
    for a static ``resolve/`` URL even though the transfer was cut short."""
    full_bytes = _full_jsonl_payload().encode()
    cut_at = full_bytes.index(b"\n", full_bytes.index(b"\n") + 1) + 1  # end of line 2
    return full_bytes[:cut_at], len(full_bytes)


async def test_swe_qa_pro_line_truncated_download_is_rejected(monkeypatch, tmp_path) -> None:
    """A payload truncated exactly on a '\\n' boundary must NOT be silently
    cached — every remaining line parses, but the payload is incomplete.
    The server's advertised Content-Length (present in the response, as
    HuggingFace sends for static file resolves) reveals the shortfall."""
    truncated, full_length = _line_truncated_jsonl_payload()
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: _FakeResp(truncated, advertised_length=full_length),
    )

    dataset = SweQaProDataset(cache_dir=tmp_path, repo_cache=_FakeRepoCache())
    target = tmp_path / f"swe-qa-pro-{dataset.revision}.jsonl"

    with pytest.raises(TruncatedDownloadError):
        _ = [t async for t in dataset.tasks()]

    # Regression: today's per-line-only validation accepts every remaining
    # complete line and commits the truncated payload via tmp.replace().
    assert not target.exists(), (
        "line-boundary-truncated download must not be cached as if it were complete"
    )


async def test_swe_qa_line_truncated_download_is_rejected(monkeypatch, tmp_path) -> None:
    """Same edge case for the SWE-QA (non-Pro) per-repo-split download path."""
    truncated, full_length = _line_truncated_jsonl_payload()
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: _FakeResp(truncated, advertised_length=full_length),
    )

    dataset = SweQaDataset(cache_dir=tmp_path, split="matplotlib", repo_cache=_FakeRepoCache())
    target = tmp_path / f"swe-qa-{dataset.revision}-matplotlib.jsonl"

    with pytest.raises(TruncatedDownloadError):
        _ = [t async for t in dataset.tasks()]

    assert not target.exists(), (
        "line-boundary-truncated download must not be cached as if it were complete"
    )


async def test_swe_qa_pro_content_length_mismatch_is_rejected(monkeypatch, tmp_path) -> None:
    """A response whose actual body is shorter than its own advertised
    Content-Length must be rejected outright, independent of line parsing."""
    full_bytes = _full_jsonl_payload().encode()
    # Advertise more bytes than are actually delivered.
    short_resp = _FakeResp(full_bytes, advertised_length=len(full_bytes) + 500)
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: short_resp,
    )

    dataset = SweQaProDataset(cache_dir=tmp_path, repo_cache=_FakeRepoCache())
    target = tmp_path / f"swe-qa-pro-{dataset.revision}.jsonl"

    with pytest.raises(TruncatedDownloadError):
        _ = [t async for t in dataset.tasks()]

    assert not target.exists(), "a response shorter than its own Content-Length must not be cached"


async def test_swe_qa_pro_download_with_matching_length_still_succeeds(
    monkeypatch, tmp_path
) -> None:
    """Sanity check: the Content-Length guard must not reject a normal,
    complete download — only ones shorter than advertised."""
    full_bytes = _full_jsonl_payload().encode()
    ok_resp = _FakeResp(full_bytes, advertised_length=len(full_bytes))
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: ok_resp,
    )

    dataset = SweQaProDataset(cache_dir=tmp_path, repo_cache=_FakeRepoCache())
    target = tmp_path / f"swe-qa-pro-{dataset.revision}.jsonl"

    _ = [t async for t in dataset.tasks()]

    assert target.exists()
