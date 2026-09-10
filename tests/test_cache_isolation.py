"""The suite must never read or write the developer's real ``~/.pydocs-mcp``.

Regression guard for a test-hermeticity defect. ``db.CACHE_DIR`` was a
module-level constant resolved from ``Path.home()`` at import time, and
``tests/test_cli.py`` drives the CLI in-process (``__main__.main()`` under a
patched ``sys.argv``) WITHOUT ``--cache-dir`` — so ``index`` / ``search`` /
``serve`` tests read and wrote the developer's genuine index bundles. Orphaned
``.tq`` sidecars left there by earlier runs collided with freshly created
``.db`` files (the 40-bit path slug repeats across thousands of
``myproject_*`` leftovers), and four ``test_cli.py`` tests failed
deterministically against a real ``$HOME`` with ``Cache integrity mismatch:
embedded-flagged chunks=0 but TurboQuant index size=1`` followed by
``Error: id 1 already present in index``.

These assertions stay cheap on purpose: the real cache directory holds tens of
thousands of entries, so nothing here enumerates it. Every check is either a
resolved-path comparison or a single ``exists()`` stat on the one filename the
unfixed code would have created.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pydocs_mcp.db import (
    CACHE_DIR_ENV_VAR,
    cache_path_for_project,
    default_cache_dir,
    turboquant_path_for_project,
)


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


class TestResolvedPathsStayInTmpPath:
    def test_sqlite_cache_path_is_sandboxed(self, tmp_path: Path) -> None:
        _assert_hermetic(cache_path_for_project(tmp_path), tmp_path)

    def test_turboquant_sidecar_path_is_sandboxed(self, tmp_path: Path) -> None:
        _assert_hermetic(turboquant_path_for_project(tmp_path), tmp_path)

    def test_default_cache_dir_is_sandboxed(self, tmp_path: Path) -> None:
        _assert_hermetic(default_cache_dir(), tmp_path)

    def test_cross_link_overlay_path_is_sandboxed(self, tmp_path: Path) -> None:
        """The multi-repo cross-link overlay shares the one bundle root.

        Its location used to be a hardcoded ``~/.pydocs-mcp/links`` literal in
        two modules, so a full suite run still dropped a
        ``links/<digest>.sqlite3`` file into the developer's real cache after
        the ``.db`` / ``.tq`` bundles were already sandboxed.
        """
        from pydocs_mcp.storage.factories import (
            overlay_path_for,
            overlay_path_in_cache_root,
        )

        _assert_hermetic(overlay_path_in_cache_root("0123456789"), tmp_path)
        _assert_hermetic(
            overlay_path_for(None, (tmp_path / "a.db", tmp_path / "b.db")),
            tmp_path,
        )


class TestCacheDirSeam:
    def test_env_var_overrides_the_module_constant(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        override = tmp_path / "explicit-root"
        monkeypatch.setenv(CACHE_DIR_ENV_VAR, str(override))
        assert default_cache_dir() == override
        assert cache_path_for_project(tmp_path).parent == override

    def test_falls_back_to_the_module_constant_when_env_is_unset(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``monkeypatch.setattr(db, "CACHE_DIR", ...)`` must keep working.

        Several existing tests (and ``benchmarks/tests/campaign/
        test_index_cache.py``) patch the module attribute directly; the lazy
        resolver must honour that patch, not shadow it.
        """
        import pydocs_mcp.db as db_mod

        monkeypatch.delenv(CACHE_DIR_ENV_VAR, raising=False)
        monkeypatch.setattr(db_mod, "CACHE_DIR", tmp_path / "patched-root")
        assert default_cache_dir() == tmp_path / "patched-root"

    def test_seam_crosses_a_subprocess_boundary(self, tmp_path: Path) -> None:
        """A child ``python -m pydocs_mcp`` inherits the sandbox.

        ``monkeypatch.setattr`` cannot reach a subprocess; the env var can.
        ``tests/test_main_cli.py`` already spawns the real CLI entry point,
        so the seam has to survive ``os.environ`` inheritance.
        """
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent / "python")
        env[CACHE_DIR_ENV_VAR] = str(tmp_path / "child-root")
        probe = "from pydocs_mcp.db import default_cache_dir; print(default_cache_dir())"
        result = subprocess.run(
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(tmp_path / "child-root")


class TestInProcessCliLeavesTheRealCacheAlone:
    def test_index_writes_the_bundle_under_tmp_path(self, tmp_path: Path) -> None:
        project = tmp_path / "myproject"
        project.mkdir()
        (project / "pyproject.toml").write_text("[project]\ndependencies = []\n")
        (project / "app.py").write_text('def hello():\n    """Say hello."""\n    return "hi"\n')

        with patch("sys.argv", ["pydocs-mcp", "index", str(project)]):
            from pydocs_mcp.__main__ import main

            main()

        db_path = cache_path_for_project(project)
        _assert_hermetic(db_path, tmp_path)
        assert db_path.exists(), "the CLI run did not write where the suite says it does"
        # One stat, not a directory walk: the exact bundle the unfixed code
        # would have dropped into the developer's real cache must be absent.
        assert not (_real_cache_root() / db_path.name).exists()
        assert not (_real_cache_root() / turboquant_path_for_project(project).name).exists()
