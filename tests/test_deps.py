"""Tests for recursive dependency resolution in deps.py."""
import os
import pytest
from pydocs_mcp.deps import resolve, _find_dep_files


@pytest.fixture
def project_tree(tmp_path):
    """Project with deps spread across subdirectories."""
    # Root pyproject.toml
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n"
        'dependencies = ["fastapi>=0.100", "uvicorn"]\n'
    )
    # Nested sub-project
    api_dir = tmp_path / "services" / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "pyproject.toml").write_text(
        "[project]\n"
        'dependencies = ["sqlalchemy>=2.0", "alembic"]\n'
    )
    # Nested requirements.txt
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "requirements.txt").write_text(
        "boto3>=1.20\n"
        "click\n"
        "# a comment\n"
        "\n"
    )
    # Should be ignored — inside .venv
    venv_dir = tmp_path / ".venv" / "lib" / "site-packages" / "fakepkg"
    venv_dir.mkdir(parents=True)
    (venv_dir / "requirements.txt").write_text("evil_dep\n")
    # Should be ignored — inside node_modules
    nm_dir = tmp_path / "node_modules" / "somelib"
    nm_dir.mkdir(parents=True)
    (nm_dir / "requirements.txt").write_text("evil_dep2\n")
    return tmp_path


class TestResolveRecursive:
    def test_collects_root_pyproject_deps(self, project_tree):
        result = resolve(str(project_tree))
        assert "fastapi" in result
        assert "uvicorn" in result

    def test_collects_nested_pyproject_deps(self, project_tree):
        result = resolve(str(project_tree))
        assert "sqlalchemy" in result
        assert "alembic" in result

    def test_collects_nested_requirements_txt_deps(self, project_tree):
        result = resolve(str(project_tree))
        assert "boto3" in result
        assert "click" in result

    def test_deduplicates_across_files(self, project_tree):
        (project_tree / "extra.txt").write_text("fastapi>=0.50\n")
        result = resolve(str(project_tree))
        assert result.count("fastapi") == 1

    def test_excludes_venv(self, project_tree):
        assert "evil_dep" not in resolve(str(project_tree))

    def test_excludes_node_modules(self, project_tree):
        assert "evil_dep2" not in resolve(str(project_tree))

    def test_returns_sorted_list(self, project_tree):
        result = resolve(str(project_tree))
        assert result == sorted(result)

    def test_empty_dir_returns_empty_list(self, tmp_path):
        assert resolve(str(tmp_path)) == []

    def test_root_only_pyproject_still_works(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'dependencies = ["httpx"]\n'
        )
        assert resolve(str(tmp_path)) == ["httpx"]

    def test_skips_comment_lines_in_requirements(self, tmp_path):
        (tmp_path / "requirements.txt").write_text(
            "# This is a comment\n"
            "requests>=2.0\n"
            "-r other.txt\n"
        )
        assert resolve(str(tmp_path)) == ["requests"]


class TestFindDepFiles:
    def test_finds_both_pyproject_files(self, project_tree):
        found = _find_dep_files(str(project_tree))
        pyprojects = [p for p in found if os.path.basename(p) == "pyproject.toml"]
        assert len(pyprojects) == 2  # root + services/api

    def test_finds_requirements_txt(self, project_tree):
        found = _find_dep_files(str(project_tree))
        assert any(p.endswith("requirements.txt") for p in found)

    def test_skips_venv_directory(self, project_tree):
        found = _find_dep_files(str(project_tree))
        assert not any(".venv" in p for p in found)

    def test_skips_node_modules_directory(self, project_tree):
        found = _find_dep_files(str(project_tree))
        assert not any("node_modules" in p for p in found)
