"""Project member module ids follow the package-root rule; dependency ids do not move.

Spec 2026-09-10 §1: project members used to take their module id from the
path relative to the project directory. A ``src/`` or ``python/`` layout then
gave ``src.needle.x`` / ``python.pkg.x`` while chunks, trees and the
reference graph said ``needle.x`` / ``pkg.x``, and get_symbol could not
resolve the published member ids.

A new file, not ``test_members.py``: that one is already over the 500-line rule.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.extraction.strategies.members import (
    AstMemberExtractor,
    InspectMemberExtractor,
)
from pydocs_mcp.extraction.strategies.python_module_id import package_rooted_module_id
from pydocs_mcp.models import ModuleMember, ModuleMemberFilterField

_MODULE = ModuleMemberFilterField.MODULE.value
_API_SOURCE = "def api():\n    return 1\n"
_EXTRACTOR_MODULE = "pydocs_mcp.extraction.strategies.members.ast_extractor"
_INSPECT_MODULE = "pydocs_mcp.extraction.strategies.members.inspect_extractor"


def _write_tree(root: Path, rel_files: Iterable[str], source: str = _API_SOURCE) -> Path:
    for rel in rel_files:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
    return root


async def _project_modules(root: Path) -> set[str]:
    members = await AstMemberExtractor().extract_from_project(root)
    return {str(m.metadata[_MODULE]) for m in members}


# ── project side: the fix ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_src_layout_members_are_package_rooted(tmp_path: Path) -> None:
    root = _write_tree(
        tmp_path,
        (
            "src/needle/__init__.py",
            "src/needle/scoring/__init__.py",
            "src/needle/scoring/strategies.py",
        ),
    )
    modules = await _project_modules(root)
    assert modules == {"needle", "needle.scoring", "needle.scoring.strategies"}
    assert not any(m.startswith("src.") for m in modules)


@pytest.mark.asyncio
async def test_maturin_python_layout_members(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, ("python/myproj/__init__.py", "python/myproj/db.py"))
    assert await _project_modules(root) == {"myproj", "myproj.db"}


@pytest.mark.asyncio
async def test_root_init_members_use_root_dir_name(tmp_path: Path) -> None:
    root = _write_tree(tmp_path / "myroot", ("__init__.py", "a.py"))
    assert await _project_modules(root) == {"myroot", "myroot.a"}


@pytest.mark.parametrize(
    ("rel_files", "expected"),
    [
        pytest.param(
            ("src/nsroot/sub/__init__.py", "src/nsroot/sub/mod.py"),
            {"sub", "sub.mod"},
            id="namespace-root",
        ),
        pytest.param(
            ("pkg/__init__.py", "pkg/data/sub/__init__.py", "pkg/data/sub/m.py"),
            {"pkg", "sub", "sub.m"},
            id="non-package-subdir",
        ),
        pytest.param(
            ("src/app.py", "resources/x/src/midpoint.py"),
            {"src.app", "resources.x.src.midpoint"},
            id="loose-dirs",
        ),
        pytest.param(
            ("mod.py", "setup.py", "tests/test_a.py", "scripts/__init__.py", "scripts/run.py"),
            {"mod", "setup", "tests.test_a", "scripts", "scripts.run"},
            id="no-root-init",
        ),
    ],
)
@pytest.mark.asyncio
async def test_member_modules_equal_chunker_ids(
    tmp_path: Path, rel_files: tuple[str, ...], expected: set[str]
) -> None:
    """AC-3/4/5: each member module is exactly the id the chunker computes
    for the same file (the chunker's ``_module_from_path`` IS
    ``package_rooted_module_id``, pinned in test_python_module_id.py)."""
    root = _write_tree(tmp_path, rel_files)
    chunk_ids = {package_rooted_module_id(str(root / rel), root) for rel in rel_files}
    assert await _project_modules(root) == expected == chunk_ids


def _cross_drive_module_id(path: str, root: Path) -> str:
    """Named fake: Windows ``relpath`` raises ValueError across drives."""
    raise ValueError(f"path {path!r} is on a different drive than root {str(root)!r}")


def test_parse_files_skips_file_when_module_id_raises(tmp_path: Path) -> None:
    root = _write_tree(tmp_path, ("mod.py",))
    members = AstMemberExtractor()._parse_files(
        "__project__",
        [str(root / "mod.py")],
        root,
        module_id_for=_cross_drive_module_id,
    )
    assert members == ()


# ── dependency side: byte-identical (AC-7) ───────────────────────────────


@dataclass(frozen=True, slots=True)
class FakeDistribution:
    """Named stand-in for ``importlib.metadata.Distribution`` over a real tree."""

    site_packages: Path
    rel_files: tuple[str, ...]

    @property
    def files(self) -> list[str]:
        # PackagePath stringifies to its site-packages-relative path.
        return list(self.rel_files)

    def locate_file(self, rel: object) -> Path:
        return self.site_packages / str(rel)


_DEP_SOURCES = {
    "google/cloud/storage/blob.py": (
        'def upload_blob(bucket, name):\n    """Upload."""\n    return 1\n'
    ),
    "regpkg/__init__.py": 'class Client:\n    """Client."""\n',
    "regpkg/core.py": "def run(x: int) -> int:\n    return x\n",
    "six.py": "def with_metaclass(meta, *bases):\n    return meta\n",
}


def _dep_member(module: str, name: str, kind: str, signature: str, doc: str) -> dict[str, object]:
    return {
        "package": "regpkg",
        "module": module,
        "name": name,
        "kind": kind,
        "signature": signature,
        "return_annotation": "",
        "parameters": (),
        "docstring": doc,
    }


# Frozen from the unmodified extractor (HEAD 272e43af), sorted by (module, name).
_EXPECTED_DEP_MEMBERS = (
    _dep_member("google.cloud.storage.blob", "upload_blob", "def", "(bucket, name)", "Upload."),
    _dep_member("regpkg", "Client", "class", "()", "Client."),
    _dep_member("regpkg.core", "run", "def", "(x: int)", ""),
    _dep_member("six", "with_metaclass", "def", "(meta, *bases)", ""),
)


@pytest.fixture
def fake_distribution(tmp_path: Path) -> FakeDistribution:
    site_packages = tmp_path / "site-packages"
    for rel, source in _DEP_SOURCES.items():
        _write_tree(site_packages, (rel,), source)
    return FakeDistribution(site_packages=site_packages, rel_files=tuple(_DEP_SOURCES))


def _install_distribution(
    monkeypatch: pytest.MonkeyPatch, dist: FakeDistribution, *modules: str
) -> None:
    for module in modules:
        monkeypatch.setattr(f"{module}.find_installed_distribution", lambda _name: dist)


def _sorted_metadata(members: tuple[ModuleMember, ...]) -> tuple[dict[str, object], ...]:
    rows = (dict(m.metadata) for m in members)
    return tuple(sorted(rows, key=lambda row: (str(row["module"]), str(row["name"]))))


def test_dep_sync_output_is_byte_identical(
    fake_distribution: FakeDistribution, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_distribution(monkeypatch, fake_distribution, _EXTRACTOR_MODULE)
    members = AstMemberExtractor()._dep_sync("regpkg")
    assert _sorted_metadata(members) == _EXPECTED_DEP_MEMBERS


def _failing_live_import(_dist: object, _depth: int, **_kwargs: object) -> dict[str, object]:
    raise RuntimeError("simulated import failure")


@pytest.mark.asyncio
async def test_inspect_failure_fallback_output_is_byte_identical(
    fake_distribution: FakeDistribution, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_distribution(monkeypatch, fake_distribution, _EXTRACTOR_MODULE, _INSPECT_MODULE)
    monkeypatch.setattr(f"{_INSPECT_MODULE}._extract_by_import", _failing_live_import)
    extractor = InspectMemberExtractor(static_fallback=AstMemberExtractor())
    members = await extractor.extract_from_dependency("regpkg")
    assert _sorted_metadata(members) == _EXPECTED_DEP_MEMBERS
