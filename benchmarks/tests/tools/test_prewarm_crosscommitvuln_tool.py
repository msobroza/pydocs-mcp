"""Hermetic tests for the airgap prewarm tool + bundle-aware RepoCache checkout.

All git runs against LOCAL paths only (no network). The prewarm tool is not an
installed module — load it from its file path (mirrors the build-tool test). The
offline proof: build a bundle, DELETE the source repo, then materialize the
corpus from the bundle alone — any network/origin access would fail.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

from pydocs_eval.datasets._repo_cache import RepoCache, read_checkout_files

_TOOL = Path(__file__).parents[2] / "tools" / "prewarm_crosscommitvuln_corpus.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("prewarm_crosscommitvuln_corpus", _TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.stdout.strip()


def _make_origin(tmp_path: Path, name: str = "origin") -> tuple[Path, str, str]:
    """Build a 2-commit origin repo; return (origin, first_sha, second_sha)."""
    origin = tmp_path / name
    origin.mkdir()
    _run(origin, "init", "-q")
    _run(origin, "config", "user.email", "test@example.com")
    _run(origin, "config", "user.name", "Test")
    (origin / "a.py").write_text("print('a')\n")
    _run(origin, "add", "a.py")
    _run(origin, "commit", "-q", "-m", "first")
    first = _run(origin, "rev-parse", "HEAD")
    (origin / "b.py").write_text("print('b')\n")
    _run(origin, "add", "b.py")
    _run(origin, "commit", "-q", "-m", "second")
    second = _run(origin, "rev-parse", "HEAD")
    return origin, first, second


def _write_records(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "records.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def test_prewarm_builds_bundle_and_is_idempotent(tmp_path: Path) -> None:
    tool = _load_tool()
    origin, first, _ = _make_origin(tmp_path)
    url = "file://" + str(origin)
    records = _write_records(tmp_path, [{"task_id": "cve-x", "repo_url": url, "prefix_sha": first}])
    bundles = tmp_path / "bundles"

    rc = tool.main(
        [
            "--bundle-dir",
            str(bundles),
            "--records",
            str(records),
            "--cache-root",
            str(tmp_path / "build-cache"),
        ]
    )
    assert rc == 0
    bundle = bundles / "origin.bundle"
    assert bundle.exists() and bundle.stat().st_size > 0
    first_mtime = bundle.stat().st_mtime_ns

    # Idempotent: a second run finds the bundle and skips it (no rewrite).
    rc2 = tool.main(
        [
            "--bundle-dir",
            str(bundles),
            "--records",
            str(records),
            "--cache-root",
            str(tmp_path / "build-cache-2"),
        ]
    )
    assert rc2 == 0
    assert bundle.stat().st_mtime_ns == first_mtime


def test_repo_cache_checks_out_from_bundle_offline(tmp_path: Path) -> None:
    tool = _load_tool()
    origin, first, _ = _make_origin(tmp_path)
    url = "file://" + str(origin)
    records = _write_records(tmp_path, [{"task_id": "cve-x", "repo_url": url, "prefix_sha": first}])
    bundles = tmp_path / "bundles"
    assert (
        tool.main(
            [
                "--bundle-dir",
                str(bundles),
                "--records",
                str(records),
                "--cache-root",
                str(tmp_path / "build-cache"),
            ]
        )
        == 0
    )

    # PROVE offline: remove the source repo so any network/origin access fails.
    shutil.rmtree(origin)

    cache = RepoCache(root=tmp_path / "eval-cache", bundle_dir=bundles)
    checkout = cache.checkout(url, first)
    files = read_checkout_files(checkout)
    assert any(key.endswith("a.py") for key in files)
    assert not (checkout / "b.py").exists()  # pinned to the first commit only


def test_bundle_carries_every_sha_for_a_multi_cve_repo(tmp_path: Path) -> None:
    # mcp-atlassian appears twice with two prefix_shas: ONE bundle must carry BOTH,
    # and each checks out offline (the per-repo bundle-name collision case).
    tool = _load_tool()
    origin, first, second = _make_origin(tmp_path)
    url = "file://" + str(origin)
    records = _write_records(
        tmp_path,
        [
            {"task_id": "cve-a", "repo_url": url, "prefix_sha": first},
            {"task_id": "cve-b", "repo_url": url, "prefix_sha": second},
        ],
    )
    bundles = tmp_path / "bundles"
    assert (
        tool.main(
            [
                "--bundle-dir",
                str(bundles),
                "--records",
                str(records),
                "--cache-root",
                str(tmp_path / "build-cache"),
            ]
        )
        == 0
    )
    assert list(bundles.glob("*.bundle")) == [bundles / "origin.bundle"]  # one bundle, both shas

    shutil.rmtree(origin)  # offline proof
    cache = RepoCache(root=tmp_path / "eval-cache", bundle_dir=bundles)
    at_first = cache.checkout(url, first)
    at_second = cache.checkout(url, second)
    assert (at_first / "a.py").exists() and not (at_first / "b.py").exists()
    assert (at_second / "a.py").exists() and (at_second / "b.py").exists()


def test_no_usable_records_returns_nonzero(tmp_path: Path) -> None:
    tool = _load_tool()
    records = _write_records(tmp_path, [{"task_id": "bad", "repo_url": "", "prefix_sha": ""}])
    rc = tool.main(["--bundle-dir", str(tmp_path / "bundles"), "--records", str(records)])
    assert rc == 2
