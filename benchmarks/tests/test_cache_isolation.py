"""The benchmark suite must never write the developer's real bundle cache.

Regression guard for a defeatable sandbox. ``benchmarks/tests/campaign/
test_index_cache.py`` relocated the bundle root with
``monkeypatch.setattr(db, "CACHE_DIR", tmp_path / "user_cache")`` alone, but
``db.default_cache_dir()`` consults ``PYDOCS_CACHE_DIR`` FIRST and only falls
back to the module constant. So on any machine with that variable exported,
``preseed_workspace`` — which resolves its destination through
``workspace_cache_paths`` → ``cache_path_for_project`` → ``default_cache_dir``
— silently copied ``.db`` / ``.tq`` bundles into the exported root while every
assertion still passed, because the assertions resolve through the same
function. A sandbox that an ambient environment variable can steer is not a
sandbox.

The autouse fixture in ``benchmarks/tests/conftest.py`` now owns the variable
for the whole suite, so the per-test ``tmp_path`` wins over whatever the
developer's shell exported. These assertions stay cheap on purpose: the real
cache directory holds tens of thousands of entries, so nothing here enumerates
it — every check is a resolved-path comparison or a single ``exists()`` stat on
the one filename the unsandboxed code would have created.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydocs_eval.campaign.index_cache import preseed_workspace, workspace_cache_paths
from pydocs_mcp.db import CACHE_DIR_ENV_VAR, default_cache_dir


def _real_cache_root() -> Path:
    """The developer's genuine bundle directory — off limits to the suite."""
    return Path.home() / ".pydocs-mcp"


def _assert_hermetic(resolved: Path, tmp_path: Path) -> None:
    assert resolved.is_relative_to(tmp_path), (
        f"cache path escaped the per-test sandbox: got {resolved}, expected a path under {tmp_path}"
    )
    assert not resolved.is_relative_to(_real_cache_root()), (
        f"cache path landed in the developer's real bundle directory: {resolved}"
    )


class TestBundleRootIsSandboxed:
    def test_default_cache_dir_is_sandboxed(self, tmp_path: Path) -> None:
        _assert_hermetic(default_cache_dir(), tmp_path)

    def test_the_fixture_owns_the_env_var_not_the_shell(self, tmp_path: Path) -> None:
        """An ambient ``PYDOCS_CACHE_DIR`` export must not reach the suite.

        ``tmp_path`` is unique per test, so a value under it can only have been
        written by the autouse fixture — never inherited from the environment
        the run was launched in.
        """
        exported = os.environ.get(CACHE_DIR_ENV_VAR)
        assert exported is not None, f"{CACHE_DIR_ENV_VAR} is unset — the suite is unsandboxed"
        _assert_hermetic(Path(exported), tmp_path)


class TestPreseedWritesStayInTmpPath:
    def test_preseed_workspace_writes_inside_the_sandbox(self, tmp_path: Path) -> None:
        """The write, not just the resolved path.

        ``preseed_workspace`` is the benchmark harness's one bundle-writing
        call, and the site that actually dropped copies into an exported root.
        This test deliberately patches NOTHING: it is green only because the
        suite-wide fixture holds.
        """
        canonical_db = tmp_path / "canon.db"
        canonical_tq = tmp_path / "canon.tq"
        canonical_db.write_bytes(b"DBDATA")
        canonical_tq.write_bytes(b"TQDATA")
        workspace = tmp_path / "ws"
        workspace.mkdir()

        dst_db, dst_tq = preseed_workspace(canonical_db, canonical_tq, workspace)

        assert (dst_db, dst_tq) == workspace_cache_paths(workspace)
        _assert_hermetic(dst_db, tmp_path)
        _assert_hermetic(dst_tq, tmp_path)
        assert dst_db.read_bytes() == b"DBDATA"
        # One stat, not a directory walk: the exact bundle the unsandboxed code
        # would have dropped into the developer's real cache must be absent.
        assert not (_real_cache_root() / dst_db.name).exists()
        assert not (_real_cache_root() / dst_tq.name).exists()
