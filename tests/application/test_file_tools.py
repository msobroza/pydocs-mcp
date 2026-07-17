"""FileToolsService — filesystem grep/glob/read_file cores (tool-contracts.md §3.7-3.9).

Payload stand-ins below mirror the wire contract's python-side field names;
the pydantic input models (GrepInput/GlobInput/ReadFileInput) land in a later
task and satisfy the same structural shape.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_mcp.application.file_tools import FileToolsService
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    ServiceUnavailableError,
)
from pydocs_mcp.extraction.config import DiscoveryScopeConfig
from pydocs_mcp.extraction.strategies.discovery import ProjectFileDiscoverer
from pydocs_mcp.retrieval.config import FilesConfig

# ── payload stand-ins (structural twins of the future input models) ──────


@dataclass
class GrepPayload:
    pattern: str
    path: str = ""
    glob: str = ""
    output_mode: str = "files_with_matches"
    case_insensitive: bool = False
    line_numbers: bool = True
    after_context: int | None = None
    before_context: int | None = None
    context: int | None = None
    head_limit: int | None = None
    multiline: bool = False
    scope: str = "project"
    project: str = ""


@dataclass
class GlobPayload:
    pattern: str
    path: str = ""
    head_limit: int | None = None
    project: str = ""


@dataclass
class ReadFilePayload:
    file_path: str
    offset: int | None = None
    limit: int | None = None
    project: str = ""


# ── fixtures ──────────────────────────────────────────────────────────────

_MAIN_PY = 'alpha_token = 1\n\ndef main():\n    print("Alpha_Token run")\n    return alpha_token\n'


def _build_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "private_docs").mkdir()
    (root / ".venv" / "lib").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[tool.pydocs-mcp]\nexclude_dirs = ["private_docs"]\n')
    (root / "main.py").write_text(_MAIN_PY)
    (root / "src" / "core.py").write_text(
        "def core_fn():\n    # alpha_token appears here\n    return 2\n"
    )
    (root / "src" / "util_test.py").write_text("def test_util():\n    assert True\n")
    (root / "src" / "notes.md").write_text("# Notes\n\nalpha_token in markdown\n")
    (root / "private_docs" / "hidden.py").write_text("alpha_token = 'secret'\n")
    (root / ".venv" / "lib" / "naughty.py").write_text("alpha_token = 'venv'\n")
    (root / "data.txt").write_text("alpha_token in txt\n")
    return root


def _make_service(
    root: Path | None,
    *,
    deps: tuple[str, ...] = (),
    files_config: FilesConfig | None = None,
) -> FileToolsService:
    scope = DiscoveryScopeConfig()

    async def _list_deps() -> tuple[str, ...]:
        return deps

    return FileToolsService(
        project_root=root,
        project_scope=scope,
        dependency_scope=scope,
        list_dependency_packages=_list_deps,
        files_config=files_config or FilesConfig(),
    )


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    return _build_project(tmp_path)


@pytest.fixture
def service(project_root: Path) -> FileToolsService:
    return _make_service(project_root)


# ── grep: discovery-scope parity ─────────────────────────────────────────


async def test_grep_walks_exactly_the_indexer_discovery_scope(
    service: FileToolsService, project_root: Path
) -> None:
    body, items, _ = await service.grep(GrepPayload(pattern="."))
    got = {str(i["path"]) for i in items}
    discovered, _, _ = ProjectFileDiscoverer(scope=DiscoveryScopeConfig()).discover(project_root)
    expected = {Path(p).relative_to(project_root).as_posix() for p in discovered}
    assert got == expected
    assert got == {"main.py", "src/core.py", "src/notes.md", "src/util_test.py"}
    # floor (.venv), pyproject exclude (private_docs), allowlist (.txt/.toml)
    assert not any("private_docs" in p or ".venv" in p or p.endswith(".txt") for p in got)
    assert body.splitlines() == sorted(got)


async def test_grep_project_scope_without_root_is_service_unavailable(
    tmp_path: Path,
) -> None:
    svc = _make_service(None)
    with pytest.raises(ServiceUnavailableError, match="read-only bundle"):
        await svc.grep(GrepPayload(pattern="x"))


# ── grep: output modes ────────────────────────────────────────────────────


async def test_grep_files_with_matches_lists_paths_only(
    service: FileToolsService,
) -> None:
    body, items, meta = await service.grep(GrepPayload(pattern="alpha_token"))
    assert body.splitlines() == ["main.py", "src/core.py", "src/notes.md"]
    assert [i["path"] for i in items] == ["main.py", "src/core.py", "src/notes.md"]
    # items carry the first-match span so the client can jump straight in
    first = items[0]
    assert first["start_line"] == 1 and first["end_line"] == 1
    assert first["text"] == "alpha_token = 1"
    assert meta == {}


async def test_grep_count_mode_reports_per_file_counts(
    service: FileToolsService,
) -> None:
    body, _, _ = await service.grep(GrepPayload(pattern="alpha_token", output_mode="count"))
    assert body.splitlines() == ["main.py: 2", "src/core.py: 1", "src/notes.md: 1"]


async def test_grep_content_mode_emits_file_line_content(
    service: FileToolsService,
) -> None:
    body, items, _ = await service.grep(GrepPayload(pattern="core_fn", output_mode="content"))
    assert body == "src/core.py:1:def core_fn():"
    assert items == (
        {
            "path": "src/core.py",
            "start_line": 1,
            "end_line": 1,
            "text": "def core_fn():",
        },
    )


async def test_grep_content_mode_without_line_numbers(
    service: FileToolsService,
) -> None:
    body, _, _ = await service.grep(
        GrepPayload(pattern="core_fn", output_mode="content", line_numbers=False)
    )
    assert body == "src/core.py:def core_fn():"


# ── grep: flags ───────────────────────────────────────────────────────────


async def test_grep_case_insensitive_flag(service: FileToolsService) -> None:
    body_cs, _, _ = await service.grep(GrepPayload(pattern="ALPHA_TOKEN"))
    assert body_cs == "No matches."
    body_ci, _, _ = await service.grep(GrepPayload(pattern="ALPHA_TOKEN", case_insensitive=True))
    assert "main.py" in body_ci.splitlines()


async def test_grep_context_groups_use_grep_conventions(
    service: FileToolsService,
) -> None:
    body, _, _ = await service.grep(
        GrepPayload(
            pattern="alpha_token",
            path="",
            glob="main.py",
            output_mode="content",
            context=1,
        )
    )
    assert body == (
        "main.py:1:alpha_token = 1\n"
        "main.py-2-\n"
        "--\n"
        'main.py-4-    print("Alpha_Token run")\n'
        "main.py:5:    return alpha_token"
    )


async def test_grep_c_overrides_a_and_b(service: FileToolsService) -> None:
    with_c, _, _ = await service.grep(
        GrepPayload(
            pattern="return alpha_token",
            glob="main.py",
            output_mode="content",
            context=0,
            after_context=3,
            before_context=3,
        )
    )
    assert with_c == "main.py:5:    return alpha_token"


async def test_grep_multiline_span_covers_multiple_lines(
    service: FileToolsService,
) -> None:
    body, items, _ = await service.grep(
        GrepPayload(
            pattern=r"def main\(\):\n\s+print",
            output_mode="content",
            multiline=True,
        )
    )
    assert items == (
        {
            "path": "main.py",
            "start_line": 3,
            "end_line": 4,
            "text": "def main():\n    print",
        },
    )
    assert body == 'main.py:3:def main():\nmain.py:4:    print("Alpha_Token run")'


async def test_grep_invalid_regex_carries_pattern(service: FileToolsService) -> None:
    with pytest.raises(InvalidArgumentError, match=r"\(\["):
        await service.grep(GrepPayload(pattern="(["))


# ── grep: path / glob filters, head_limit ─────────────────────────────────


async def test_grep_path_param_scopes_to_directory(service: FileToolsService) -> None:
    body, _, _ = await service.grep(GrepPayload(pattern="alpha_token", path="src"))
    assert body.splitlines() == ["src/core.py", "src/notes.md"]


async def test_grep_glob_param_filters_candidates(service: FileToolsService) -> None:
    body, _, _ = await service.grep(GrepPayload(pattern="alpha_token", glob="*.md"))
    assert body.splitlines() == ["src/notes.md"]


async def test_grep_head_limit_truncates_and_reports(
    service: FileToolsService,
) -> None:
    _, items, meta = await service.grep(GrepPayload(pattern="alpha_token", head_limit=2))
    assert len(items) == 2
    assert meta == {"truncated": True}


async def test_grep_yaml_default_head_limit_applies(project_root: Path) -> None:
    svc = _make_service(project_root, files_config=FilesConfig(grep_head_limit=1))
    _, items, meta = await svc.grep(GrepPayload(pattern="alpha_token"))
    assert len(items) == 1
    assert meta == {"truncated": True}


async def test_grep_head_limit_capped_at_ceiling(project_root: Path) -> None:
    # Defaults must also sit under the ceiling or FilesConfig itself rejects.
    svc = _make_service(
        project_root,
        files_config=FilesConfig(
            grep_head_limit=2, glob_head_limit=2, read_limit=2, max_head_limit=2
        ),
    )
    _, items, meta = await svc.grep(GrepPayload(pattern="alpha_token", head_limit=50))
    assert len(items) == 2
    assert meta == {"truncated": True}


async def test_grep_content_head_limit_caps_match_entries(
    service: FileToolsService,
) -> None:
    _, items, meta = await service.grep(
        GrepPayload(pattern="alpha_token", output_mode="content", head_limit=3)
    )
    assert len(items) == 3
    assert meta == {"truncated": True}


# ── grep: dependency scope ────────────────────────────────────────────────


async def test_grep_deps_scope_walks_installed_dependency(
    project_root: Path,
) -> None:
    svc = _make_service(project_root, deps=("pyyaml",))
    _, items, _ = await svc.grep(GrepPayload(pattern=r"^class YAMLError", scope="deps"))
    paths = [str(i["path"]) for i in items]
    assert any(p.endswith("yaml/error.py") for p in paths)
    # dependency paths are absolute — they live outside the project root
    assert all(Path(p).is_absolute() for p in paths)


async def test_grep_all_scope_includes_project_files(project_root: Path) -> None:
    svc = _make_service(project_root, deps=("pyyaml",))
    body, _, _ = await svc.grep(GrepPayload(pattern="alpha_token", scope="all"))
    assert "main.py" in body.splitlines()


# ── glob ──────────────────────────────────────────────────────────────────


async def test_glob_star_matches_root_level_only(service: FileToolsService) -> None:
    body, items, _ = await service.glob(GlobPayload(pattern="*.py"))
    assert [i["path"] for i in items] == ["main.py"]
    assert body == "main.py"


async def test_glob_double_star_recurses(service: FileToolsService) -> None:
    _, items, _ = await service.glob(GlobPayload(pattern="src/**/*.md"))
    assert [i["path"] for i in items] == ["src/notes.md"]
    _, items, _ = await service.glob(GlobPayload(pattern="**/*_test.py"))
    assert [i["path"] for i in items] == ["src/util_test.py"]


async def test_glob_orders_by_mtime_descending(
    service: FileToolsService, project_root: Path
) -> None:
    os.utime(project_root / "src" / "core.py", (2_000_000_000, 2_000_000_000))
    os.utime(project_root / "main.py", (1_000_000_000, 1_000_000_000))
    os.utime(project_root / "src" / "util_test.py", (1_500_000_000, 1_500_000_000))
    body, items, _ = await service.glob(GlobPayload(pattern="**/*.py"))
    assert [i["path"] for i in items] == [
        "src/core.py",
        "src/util_test.py",
        "main.py",
    ]
    assert body.splitlines() == ["src/core.py", "src/util_test.py", "main.py"]
    assert all(isinstance(i["mtime"], float) for i in items)


async def test_glob_path_param_matches_under_directory(
    service: FileToolsService,
) -> None:
    _, items, _ = await service.glob(GlobPayload(pattern="*.py", path="src"))
    assert {str(i["path"]) for i in items} == {"src/core.py", "src/util_test.py"}


async def test_glob_head_limit_truncates(service: FileToolsService) -> None:
    _, items, meta = await service.glob(GlobPayload(pattern="**/*.py", head_limit=1))
    assert len(items) == 1
    assert meta == {"truncated": True}


async def test_glob_no_matches(service: FileToolsService) -> None:
    body, items, meta = await service.glob(GlobPayload(pattern="*.rs"))
    assert body == "No files matched."
    assert items == ()
    assert meta == {}


async def test_glob_without_root_is_service_unavailable() -> None:
    svc = _make_service(None)
    with pytest.raises(ServiceUnavailableError, match="read-only bundle"):
        await svc.glob(GlobPayload(pattern="*.py"))


# ── read_file ─────────────────────────────────────────────────────────────


async def test_read_file_cat_n_style(service: FileToolsService) -> None:
    body, items, meta = await service.read_file(ReadFilePayload(file_path="main.py"))
    lines = body.splitlines()
    assert lines[0] == "     1\talpha_token = 1"
    assert lines[2] == "     3\tdef main():"
    assert items == ({"path": "main.py", "start_line": 1, "end_line": 5},)
    assert meta == {}


async def test_read_file_offset_limit_paging(service: FileToolsService) -> None:
    body, items, meta = await service.read_file(
        ReadFilePayload(file_path="main.py", offset=2, limit=2)
    )
    assert body.splitlines()[:2] == ["     2\t", "     3\tdef main():"]
    assert "file continues" in body.splitlines()[-1]
    assert items == ({"path": "main.py", "start_line": 2, "end_line": 3},)
    assert meta == {"truncated": True}


async def test_read_file_yaml_default_limit(project_root: Path) -> None:
    svc = _make_service(project_root, files_config=FilesConfig(read_limit=2))
    body, items, meta = await svc.read_file(ReadFilePayload(file_path="main.py"))
    assert items == ({"path": "main.py", "start_line": 1, "end_line": 2},)
    assert meta == {"truncated": True}


async def test_read_file_offset_past_end_is_invalid(
    service: FileToolsService,
) -> None:
    with pytest.raises(InvalidArgumentError, match="offset=99"):
        await service.read_file(ReadFilePayload(file_path="main.py", offset=99))


async def test_read_file_outside_boundary_is_invalid(
    service: FileToolsService, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("secret = 1\n")
    with pytest.raises(InvalidArgumentError, match="outside"):
        await service.read_file(ReadFilePayload(file_path=str(outside)))
    with pytest.raises(InvalidArgumentError, match="outside"):
        await service.read_file(ReadFilePayload(file_path="../outside.py"))


async def test_read_file_missing_file_is_invalid(service: FileToolsService) -> None:
    with pytest.raises(InvalidArgumentError, match="nope.py"):
        await service.read_file(ReadFilePayload(file_path="nope.py"))


async def test_read_file_binary_is_invalid(service: FileToolsService, project_root: Path) -> None:
    (project_root / "blob.py").write_bytes(b"\x00\x01\x02binary")
    with pytest.raises(InvalidArgumentError, match="binary"):
        await service.read_file(ReadFilePayload(file_path="blob.py"))


async def test_read_file_without_root_is_service_unavailable() -> None:
    svc = _make_service(None)
    with pytest.raises(ServiceUnavailableError, match="read-only bundle"):
        await svc.read_file(ReadFilePayload(file_path="main.py"))


async def test_read_file_dependency_path_inside_boundary(
    project_root: Path,
) -> None:
    import yaml

    svc = _make_service(project_root, deps=("pyyaml",))
    dep_file = Path(yaml.__file__).parent / "error.py"
    body, items, _ = await svc.read_file(ReadFilePayload(file_path=str(dep_file)))
    assert "class YAMLError" in body
    assert str(items[0]["path"]).endswith("error.py")
    assert Path(str(items[0]["path"])).is_absolute()
