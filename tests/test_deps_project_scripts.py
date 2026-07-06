"""[project.scripts] parsing for overview entry points (spec §D17 block 4)."""

from pydocs_mcp.deps import parse_project_scripts


def test_parses_scripts_table(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\n\n'
        '[project.scripts]\ndemo-cli = "demo.__main__:main"\nother = "demo.app:run"\n'
    )
    assert parse_project_scripts(str(tmp_path / "pyproject.toml")) == {
        "demo-cli": "demo.__main__:main",
        "other": "demo.app:run",
    }


def test_missing_file_or_table_returns_empty(tmp_path) -> None:
    assert parse_project_scripts(str(tmp_path / "absent.toml")) == {}
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n')
    assert parse_project_scripts(str(tmp_path / "pyproject.toml")) == {}


def test_malformed_toml_returns_empty(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("not [ valid")
    assert parse_project_scripts(str(tmp_path / "pyproject.toml")) == {}
