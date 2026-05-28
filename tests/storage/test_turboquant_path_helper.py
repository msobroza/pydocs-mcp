"""turboquant_path_for_project mirrors cache_path_for_project (Task 8)."""

from pathlib import Path

from pydocs_mcp.db import cache_path_for_project, turboquant_path_for_project


def test_returns_sibling_with_tq_suffix(tmp_path: Path) -> None:
    project = tmp_path / "myproj"
    project.mkdir()
    db_p = cache_path_for_project(project)
    tq_p = turboquant_path_for_project(project)
    # Same parent dir, same stem, different suffix.
    assert tq_p.parent == db_p.parent
    assert tq_p.stem == db_p.stem
    assert tq_p.suffix == ".tq"
    assert db_p.suffix == ".db"


def test_different_projects_get_different_paths(tmp_path: Path) -> None:
    p1 = tmp_path / "a"
    p2 = tmp_path / "b"
    p1.mkdir()
    p2.mkdir()
    assert turboquant_path_for_project(p1) != turboquant_path_for_project(p2)
