"""Regression coverage for ``pydocs-mcp serve --workspace`` / ``--db`` dispatch.

``_cmd_serve`` computes ``multi = workspace is not None or bool(db_paths)`` and,
when true, must skip BOTH the Phase-1 indexing pass AND the watch branch,
jumping straight to ``server.run(...)`` over the pre-built read-only bundles
(see the docstring on ``_cmd_serve`` in ``__main__.py``). Nothing previously
drove this branch through ``main()`` end-to-end: the existing multirepo tests
exercise ``build_routers`` / ``discover_workspace`` directly, and
``TestServeCommand`` in ``tests/test_cli.py`` only covers the single-project
path. A regression in the ``multi`` shortcut (e.g. checking only ``workspace``
and forgetting ``db_paths``, or vice versa) would silently index the cwd the
user never asked to touch and serve the wrong corpus.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _tripwire_indexing(monkeypatch):
    """Fail loudly if the multi-repo serve path ever triggers Phase-1 indexing.

    ``--workspace`` / ``--db`` bundles are pre-built and read-only; indexing
    the cwd during a multi-repo serve would silently write into the local
    project cache the user never asked to touch.
    """
    import pydocs_mcp.application as _application

    async def _boom(**kwargs):
        raise AssertionError("multi-repo serve (--workspace/--db) must not run Phase-1 indexing")

    monkeypatch.setattr(_application, "run_index_pass", _boom)


class TestServeWorkspaceDispatch:
    def test_serve_with_db_flag_skips_indexing_and_forwards_kwargs(self, tmp_path):
        """``serve --db a.db --db b.db`` must route straight to ``server.run``
        with ``db_path=None`` and the two bundle paths in ``db_paths`` — never
        down the single-project index-then-serve path.
        """
        db_a = tmp_path / "a.db"
        db_b = tmp_path / "b.db"
        db_a.touch()
        db_b.touch()

        with patch("pydocs_mcp.server.run") as mock_run:
            argv = [
                "pydocs-mcp",
                "serve",
                "--db",
                str(db_a),
                "--db",
                str(db_b),
            ]
            with patch("sys.argv", argv):
                from pydocs_mcp.__main__ import main

                rc = main()

        assert rc == 0
        mock_run.assert_called_once()
        args_, kwargs = mock_run.call_args
        # ``db_path`` is passed positionally by ``_serve_run``.
        assert args_[0] is None
        assert kwargs["workspace"] is None
        assert kwargs["db_paths"] == [db_a, db_b]

    def test_serve_with_workspace_flag_skips_indexing_and_forwards_kwargs(self, tmp_path):
        """``serve --workspace <dir>`` must route straight to ``server.run``
        with ``db_path=None`` and the workspace dir threaded through —
        never down the single-project index-then-serve path.
        """
        workspace_dir = tmp_path / "bundles"
        workspace_dir.mkdir()

        with patch("pydocs_mcp.server.run") as mock_run:
            argv = ["pydocs-mcp", "serve", "--workspace", str(workspace_dir)]
            with patch("sys.argv", argv):
                from pydocs_mcp.__main__ import main

                rc = main()

        assert rc == 0
        mock_run.assert_called_once()
        args_, kwargs = mock_run.call_args
        # ``db_path`` is passed positionally by ``_serve_run``.
        assert args_[0] is None
        assert kwargs["workspace"] == workspace_dir
        assert kwargs["db_paths"] is None

    def test_serve_workspace_with_watch_does_not_construct_a_watcher(self, tmp_path):
        """``serve --workspace <dir> --watch`` — the multi-repo path is
        read-only (the real source tree may not even be checked out), so
        ``--watch`` must NOT spin up a ``FileWatcher``. Today the multi
        shortcut returns before the watch branch is ever reached, silently
        ignoring the flag; pin that no watcher is constructed so a
        behavior change (e.g. checking ``multi`` after the watch branch)
        is caught rather than starting a watcher against bundles with no
        writable source of truth.
        """
        import pydocs_mcp.serve.watcher as _watcher_module

        workspace_dir = tmp_path / "bundles"
        workspace_dir.mkdir()

        with patch.object(_watcher_module, "FileWatcher") as mock_watcher_cls:
            with patch("pydocs_mcp.server.run") as mock_run:
                argv = [
                    "pydocs-mcp",
                    "serve",
                    "--workspace",
                    str(workspace_dir),
                    "--watch",
                ]
                with patch("sys.argv", argv):
                    from pydocs_mcp.__main__ import main

                    rc = main()

        assert rc == 0
        mock_watcher_cls.assert_not_called()
        mock_run.assert_called_once()
        _, kwargs = mock_run.call_args
        assert kwargs["workspace"] == workspace_dir
