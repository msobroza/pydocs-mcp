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


def _prewarm(tool, bundles: Path, records: Path, cache: Path, *extra: str) -> int:
    """Run the tool with the four args every case here passes."""
    return int(
        tool.main(
            [
                "--bundle-dir",
                str(bundles),
                "--records",
                str(records),
                "--cache-root",
                str(cache),
                *extra,
            ]
        )
    )


def _bundle_refs(bundle: Path) -> set[str]:
    """The ref names inside ``bundle`` (``git bundle list-heads`` runs repo-less)."""
    listing = subprocess.run(
        ["git", "bundle", "list-heads", str(bundle)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    return {line.split()[-1] for line in listing.splitlines() if line.strip()}


# --------------------------------------------------------------------------- #
# The skip must be CONTENT-aware, not existence-only
# --------------------------------------------------------------------------- #


def test_prewarm_rebuilds_when_records_pin_a_new_sha_for_a_bundled_repo(tmp_path: Path) -> None:
    """A repo gaining a second CVE must get its bundle rebuilt, not skipped.

    The existence-only skip made this the silent-corruption case: prewarm logged
    `skip`, printed AIRGAP READY and exited 0, and the eval only failed later —
    with `git worktree add failed ... invalid reference` — for a sha the bundle
    never carried. `sooperset/mcp-atlassian` already contributes two shas, so
    "same repo, more shas later" is the documented norm for this corpus.
    """
    tool = _load_tool()
    origin, first, second = _make_origin(tmp_path)
    url = "file://" + str(origin)
    bundles = tmp_path / "bundles"

    one = _write_records(tmp_path, [{"task_id": "cve-a", "repo_url": url, "prefix_sha": first}])
    assert _prewarm(tool, bundles, one, tmp_path / "c1") == 0
    bundle = bundles / "origin.bundle"
    assert _bundle_refs(bundle) == {f"refs/heads/ccv-{first}"}

    # A later commit adds a second CVE on the SAME repo.
    both = _write_records(
        tmp_path,
        [
            {"task_id": "cve-a", "repo_url": url, "prefix_sha": first},
            {"task_id": "cve-b", "repo_url": url, "prefix_sha": second},
        ],
    )
    assert _prewarm(tool, bundles, both, tmp_path / "c2") == 0
    assert _bundle_refs(bundle) == {f"refs/heads/ccv-{first}", f"refs/heads/ccv-{second}"}

    # And the newly-pinned sha now materializes with the origin gone (airgap).
    shutil.rmtree(origin)
    cache = RepoCache(root=tmp_path / "eval-cache", bundle_dir=bundles)
    assert (cache.checkout(url, second) / "b.py").exists()


def test_prewarm_still_skips_when_the_bundle_already_carries_every_sha(tmp_path: Path) -> None:
    """The content-aware skip must not cost idempotency: same shas => no rewrite."""
    tool = _load_tool()
    origin, first, second = _make_origin(tmp_path)
    url = "file://" + str(origin)
    bundles = tmp_path / "bundles"
    records = _write_records(
        tmp_path,
        [
            {"task_id": "cve-a", "repo_url": url, "prefix_sha": first},
            {"task_id": "cve-b", "repo_url": url, "prefix_sha": second},
        ],
    )

    assert _prewarm(tool, bundles, records, tmp_path / "c1") == 0
    mtime = (bundles / "origin.bundle").stat().st_mtime_ns

    assert _prewarm(tool, bundles, records, tmp_path / "c2") == 0
    assert (bundles / "origin.bundle").stat().st_mtime_ns == mtime


def test_prewarm_rebuilds_an_unreadable_bundle(tmp_path: Path) -> None:
    """A truncated/corrupt bundle must be rebuilt, never trusted as complete."""
    tool = _load_tool()
    origin, first, _ = _make_origin(tmp_path)
    url = "file://" + str(origin)
    bundles = tmp_path / "bundles"
    records = _write_records(tmp_path, [{"task_id": "cve-a", "repo_url": url, "prefix_sha": first}])

    assert _prewarm(tool, bundles, records, tmp_path / "c1") == 0
    bundle = bundles / "origin.bundle"
    bundle.write_bytes(b"not a git bundle")

    assert _prewarm(tool, bundles, records, tmp_path / "c2") == 0
    assert _bundle_refs(bundle) == {f"refs/heads/ccv-{first}"}


# --------------------------------------------------------------------------- #
# A relative --bundle-dir must not resolve against two different cwds
# --------------------------------------------------------------------------- #


def test_relative_bundle_dir_is_resolved_against_the_process_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    """`git bundle create` runs with cwd=<base clone>, so a relative dir must be
    made absolute first — otherwise the dir is created next to the process cwd
    while git writes (and fails) relative to the clone."""
    tool = _load_tool()
    origin, first, _ = _make_origin(tmp_path)
    url = "file://" + str(origin)
    records = _write_records(tmp_path, [{"task_id": "cve-a", "repo_url": url, "prefix_sha": first}])

    workdir = tmp_path / "workdir"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    assert _prewarm(tool, Path("bundles"), records, tmp_path / "c1") == 0
    assert (workdir / "bundles" / "origin.bundle").exists()


def test_airgap_ready_banner_is_withheld_when_a_repo_failed(tmp_path: Path, caplog) -> None:
    """The AIRGAP READY banner must not appear over a run that failed a repo.

    That banner is precisely what made the original stale-bundle bug misleading:
    a reassuring "you are ready to go offline" printed over an incomplete corpus.
    The non-zero exit code was always right; the message contradicted it.
    """
    tool = _load_tool()
    records = _write_records(
        tmp_path,
        [{"task_id": "cve-x", "repo_url": "file:///nonexistent/repo", "prefix_sha": "0" * 40}],
    )
    with caplog.at_level("INFO"):
        rc = _prewarm(tool, tmp_path / "bundles", records, tmp_path / "cache")

    assert rc == 1
    assert "AIRGAP READY" not in caplog.text
    assert "FAILED" in caplog.text


def test_no_usable_records_returns_nonzero(tmp_path: Path) -> None:
    tool = _load_tool()
    records = _write_records(tmp_path, [{"task_id": "bad", "repo_url": "", "prefix_sha": ""}])
    rc = tool.main(["--bundle-dir", str(tmp_path / "bundles"), "--records", str(records)])
    assert rc == 2
