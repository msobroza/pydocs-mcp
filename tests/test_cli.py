"""Tests for CLI entry point (__main__.py)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pydocs_mcp.db import open_db, rebuild_fts


@pytest.fixture
def seeded_project(tmp_path):
    """Create a minimal project with source files and a pyproject.toml."""
    project = tmp_path / "myproject"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\ndependencies = []\n'
    )
    (project / "app.py").write_text(
        'def hello():\n    """Say hello."""\n    return "hi"\n'
    )
    return project


class TestMainNoArgs:
    def test_no_command_prints_help(self, capsys):
        with patch("sys.argv", ["pydocs-mcp"]):
            from pydocs_mcp.__main__ import main
            main()
        captured = capsys.readouterr()
        assert "pydocs-mcp" in captured.out or "usage" in captured.out.lower()


class TestIndexCommand:
    def test_index_creates_database(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project)]):
            from pydocs_mcp.__main__ import main
            main()
        # Verify DB was created
        from pydocs_mcp.db import db_path_for
        db_path = db_path_for(seeded_project)
        assert db_path.exists()

    def test_index_with_force_flag(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--force"]):
            from pydocs_mcp.__main__ import main
            main()
        from pydocs_mcp.db import db_path_for
        db_path = db_path_for(seeded_project)
        assert db_path.exists()

    def test_index_skip_project(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--skip-project"]):
            from pydocs_mcp.__main__ import main
            main()
        from pydocs_mcp.db import db_path_for
        db_path = db_path_for(seeded_project)
        conn = open_db(db_path)
        pkg = conn.execute("SELECT * FROM packages WHERE name='__project__'").fetchone()
        conn.close()
        assert pkg is None

    def test_index_verbose(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "-v", "index", str(seeded_project)]):
            from pydocs_mcp.__main__ import main
            main()

    def test_index_no_inspect(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--no-inspect"]):
            from pydocs_mcp.__main__ import main
            main()

    def test_index_with_depth_and_workers(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--depth", "2", "--workers", "2"]):
            from pydocs_mcp.__main__ import main
            main()


class TestQueryCommand:
    def test_query_runs_and_prints_results(self, seeded_project, capsys, monkeypatch):
        """Index then query — uses monkeypatch to change cwd so project='.' resolves correctly."""
        (seeded_project / "app.py").write_text(
            'def hello():\n    """Say hello to the world with a greeting message."""\n    return "hi"\n'
        )
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main
            main()
        with patch("sys.argv", ["pydocs-mcp", "query", "hello"]):
            main()
        captured = capsys.readouterr()
        assert "hello" in captured.out.lower() or "─" in captured.out

    def test_query_with_package_filter(self, seeded_project, capsys, monkeypatch):
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main
            main()
        with patch("sys.argv", ["pydocs-mcp", "query", "hello", "-p", "__project__"]):
            main()


class TestApiCommand:
    def test_api_runs_and_prints_results(self, seeded_project, capsys, monkeypatch):
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main
            main()
        with patch("sys.argv", ["pydocs-mcp", "api", "hello"]):
            main()
        captured = capsys.readouterr()
        assert "hello" in captured.out.lower() or "─" in captured.out

    def test_api_with_package_filter(self, seeded_project, capsys, monkeypatch):
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main
            main()
        with patch("sys.argv", ["pydocs-mcp", "api", "hello", "-p", "__project__"]):
            main()

    def test_api_prints_symbol_details(self, seeded_project, capsys, monkeypatch):
        """Ensure api command covers the symbol printing path."""
        (seeded_project / "app.py").write_text(
            'def greet(name: str) -> str:\n    """Greet a person by name."""\n    return f"Hello {name}"\n'
        )
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main
            main()
        with patch("sys.argv", ["pydocs-mcp", "api", "greet"]):
            main()
        captured = capsys.readouterr()
        assert "greet" in captured.out.lower() or "─" in captured.out


class TestServeCommand:
    def test_serve_indexes_then_starts_server(self, seeded_project):
        """Test that serve indexes and calls run() — we mock run() to avoid blocking."""
        with patch("pydocs_mcp.__main__.run") as mock_run:
            with patch("sys.argv", ["pydocs-mcp", "serve", str(seeded_project)]):
                from pydocs_mcp.__main__ import main
                main()
            mock_run.assert_called_once()
