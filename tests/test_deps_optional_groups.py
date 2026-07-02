"""parse_pyproject_dependencies also reads optional-dependencies + dependency-groups.

Beyond ``[project].dependencies``, a project's indexable deps can be declared in
PEP 621 ``[project.optional-dependencies]`` (extras) and PEP 735
``[dependency-groups]`` (dev/test groups, e.g. what ``uv add --group`` writes).
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.deps import parse_pyproject_dependencies


def _write(tmp_path: Path, body: str) -> str:
    p = tmp_path / "pyproject.toml"
    p.write_text(body)
    return str(p)


def test_reads_project_dependencies(tmp_path: Path) -> None:
    path = _write(tmp_path, '[project]\nname = "x"\ndependencies = ["requests>=2.0"]\n')
    assert parse_pyproject_dependencies(path) == ["requests"]


def test_reads_optional_dependencies(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '[project]\nname = "x"\ndependencies = ["a"]\n'
        "[project.optional-dependencies]\n"
        'dev = ["pytest>=8", "ruff"]\n'
        'docs = ["sphinx"]\n',
    )
    assert set(parse_pyproject_dependencies(path)) == {"a", "pytest", "ruff", "sphinx"}


def test_reads_dependency_groups_pep735(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '[dependency-groups]\ntest = ["pytest", "coverage[toml]"]\nlint = ["ruff>=0.5"]\n',
    )
    assert set(parse_pyproject_dependencies(path)) == {"pytest", "coverage", "ruff"}


def test_dependency_groups_include_ref_is_skipped(tmp_path: Path) -> None:
    # PEP 735 ``{include-group = "..."}`` entries are group references, not package
    # specs — the referenced group's members are collected on their own iteration.
    path = _write(
        tmp_path,
        '[dependency-groups]\ntest = ["pytest"]\nall = [{include-group = "test"}, "mypy"]\n',
    )
    assert set(parse_pyproject_dependencies(path)) == {"pytest", "mypy"}


def test_all_three_sources_combined(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '[project]\nname = "x"\ndependencies = ["a"]\n'
        '[project.optional-dependencies]\nextra = ["b"]\n'
        '[dependency-groups]\ndev = ["c"]\n',
    )
    assert set(parse_pyproject_dependencies(path)) == {"a", "b", "c"}


def test_dependency_groups_only_no_project_table(tmp_path: Path) -> None:
    path = _write(tmp_path, '[dependency-groups]\ndev = ["ruff"]\n')
    assert parse_pyproject_dependencies(path) == ["ruff"]
