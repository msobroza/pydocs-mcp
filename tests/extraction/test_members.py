"""Unit tests for ``extraction/members.py`` (Task 18 — sub-PR #5, spec §9).

Pins:
- ``AstMemberExtractor`` parses .py files via ``parse_py_file`` (Rust or fallback)
  for both project source AND dependencies — never imports code (safe on
  untrusted packages).
- ``InspectMemberExtractor.extract_from_project`` delegates to the AST fallback
  (spec §9.2 — "we never import project-under-test").
- ``InspectMemberExtractor.extract_from_dependency`` uses ``importlib.import_module``
  and falls back to the composed ``AstMemberExtractor`` on any exception
  (spec §9.2 — fallback allowlist).
- Both dataclasses are frozen + slots.

Spec §9.1 ``AstMemberExtractor.extract_from_project(project_dir)`` is the
entrypoint. Signature matches sub-PR #4's ``MemberExtractor`` Protocol —
``tuple[ModuleMember, ...]`` return.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.extraction.members import (
    AstMemberExtractor,
    InspectMemberExtractor,
)
from pydocs_mcp.models import ModuleMember, ModuleMemberFilterField


# ── AstMemberExtractor — project ──────────────────────────────────────────

_SIMPLE_MODULE = '''
"""Module docstring."""

def public_fn(x, y):
    """Public function."""
    return x + y


class Public():
    """Public class."""

    def method(self):
        return 1


def _private():
    """Private function — should be skipped by parser."""
    return 0
'''


@pytest.fixture
def simple_project(tmp_path: Path) -> Path:
    (tmp_path / "mod.py").write_text(_SIMPLE_MODULE)
    return tmp_path


@pytest.mark.asyncio
async def test_ast_project_extraction_yields_module_members(
    simple_project: Path,
) -> None:
    """One file, one top-level function + one class → at least both members,
    tagged with package = ``__project__`` + module = ``mod``."""
    extractor = AstMemberExtractor()
    members = await extractor.extract_from_project(simple_project)

    assert isinstance(members, tuple)
    assert all(isinstance(m, ModuleMember) for m in members)

    names = {m.metadata[ModuleMemberFilterField.NAME.value] for m in members}
    assert "public_fn" in names
    assert "Public" in names

    # Package + module metadata are stamped on every row.
    for m in members:
        assert m.metadata[ModuleMemberFilterField.PACKAGE.value] == "__project__"
        assert m.metadata[ModuleMemberFilterField.MODULE.value] == "mod"


@pytest.mark.asyncio
async def test_ast_project_empty_dir_returns_empty_tuple(tmp_path: Path) -> None:
    """No .py files → empty tuple, not None."""
    extractor = AstMemberExtractor()
    members = await extractor.extract_from_project(tmp_path)
    assert members == ()


# ── AstMemberExtractor — dependency ───────────────────────────────────────


@dataclass(frozen=True)
class _FakeFile:
    rel: str

    def __str__(self) -> str:
        return self.rel


@dataclass
class _FakeDist:
    site_packages: Path
    rel_files: tuple[str, ...]

    @property
    def files(self) -> list[_FakeFile]:
        return [_FakeFile(r) for r in self.rel_files]

    def locate_file(self, f) -> Path:
        return self.site_packages / str(f)


@pytest.mark.asyncio
async def test_ast_dependency_extraction_yields_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fake Distribution-stub with one .py file → members tagged with the
    dep-normalized package name."""
    sp = tmp_path / "site-packages"
    (sp / "foo").mkdir(parents=True)
    (sp / "foo" / "__init__.py").write_text(_SIMPLE_MODULE)

    dist = _FakeDist(site_packages=sp, rel_files=("foo/__init__.py",))
    monkeypatch.setattr(
        "pydocs_mcp.extraction.members.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.members.find_site_packages_root",
        lambda p: str(sp),
    )

    extractor = AstMemberExtractor()
    members = await extractor.extract_from_dependency("foo")

    names = {m.metadata[ModuleMemberFilterField.NAME.value] for m in members}
    assert "public_fn" in names
    # dep name is normalized (hyphens → underscores) on the package metadata.
    for m in members:
        assert m.metadata[ModuleMemberFilterField.PACKAGE.value] == "foo"


@pytest.mark.asyncio
async def test_ast_dependency_missing_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Declared-but-not-installed dep → empty tuple, never raises."""
    monkeypatch.setattr(
        "pydocs_mcp.extraction.members.find_installed_distribution",
        lambda name: None,
    )
    extractor = AstMemberExtractor()
    members = await extractor.extract_from_dependency("nonexistent-xyz")
    assert members == ()


@pytest.mark.asyncio
async def test_ast_dependency_no_py_files_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dist whose files are .so / .md only → empty tuple (no py to parse)."""
    dist = _FakeDist(site_packages=tmp_path, rel_files=("foo/ext.so",))
    monkeypatch.setattr(
        "pydocs_mcp.extraction.members.find_installed_distribution",
        lambda name: dist,
    )
    extractor = AstMemberExtractor()
    members = await extractor.extract_from_dependency("foo")
    assert members == ()


@pytest.mark.asyncio
async def test_ast_dependency_normalizes_hyphens_to_underscores(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dep ``some-pkg`` → package metadata key ``some_pkg`` (PEP 503)."""
    sp = tmp_path / "site-packages"
    (sp / "some_pkg").mkdir(parents=True)
    (sp / "some_pkg" / "__init__.py").write_text("def api(): pass\n")
    dist = _FakeDist(site_packages=sp, rel_files=("some_pkg/__init__.py",))
    monkeypatch.setattr(
        "pydocs_mcp.extraction.members.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.members.find_site_packages_root",
        lambda p: str(sp),
    )

    extractor = AstMemberExtractor()
    members = await extractor.extract_from_dependency("some-pkg")

    assert all(
        m.metadata[ModuleMemberFilterField.PACKAGE.value] == "some_pkg"
        for m in members
    )


# ── InspectMemberExtractor ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_inspect_extract_from_project_delegates_to_ast_fallback(
    simple_project: Path,
) -> None:
    """spec §9.2: ``extract_from_project`` NEVER imports the project-under-test.
    It must delegate entirely to the composed AST fallback."""
    ast_extractor = AstMemberExtractor()
    inspect_extractor = InspectMemberExtractor(static_fallback=ast_extractor)

    ast_members = await ast_extractor.extract_from_project(simple_project)
    inspect_members = await inspect_extractor.extract_from_project(simple_project)

    # Same result — delegation is verbatim.
    assert ast_members == inspect_members


@pytest.mark.asyncio
async def test_inspect_dependency_falls_back_to_ast_on_import_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inspect mode: any exception during ``_extract_by_import`` triggers
    fallback to the composed AST extractor (spec §9.2)."""
    sp = tmp_path / "site-packages"
    (sp / "foo").mkdir(parents=True)
    (sp / "foo" / "__init__.py").write_text(_SIMPLE_MODULE)
    dist = _FakeDist(site_packages=sp, rel_files=("foo/__init__.py",))

    monkeypatch.setattr(
        "pydocs_mcp.extraction.members.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.members.find_site_packages_root",
        lambda p: str(sp),
    )

    def _boom(_dist, _depth):
        raise RuntimeError("simulated import failure")

    monkeypatch.setattr(
        "pydocs_mcp.extraction.members._extract_by_import", _boom,
    )

    ast_extractor = AstMemberExtractor()
    inspect_extractor = InspectMemberExtractor(static_fallback=ast_extractor)
    members = await inspect_extractor.extract_from_dependency("foo")

    # Fallback ran successfully — non-empty, real ModuleMembers from AST.
    assert len(members) > 0
    assert all(isinstance(m, ModuleMember) for m in members)


@pytest.mark.asyncio
async def test_inspect_dependency_uses_live_import_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: ``_extract_by_import`` succeeds; its ``symbols`` list
    flows through unchanged."""
    fake_members = (
        ModuleMember(metadata={
            ModuleMemberFilterField.PACKAGE.value: "bar",
            ModuleMemberFilterField.MODULE.value: "bar",
            ModuleMemberFilterField.NAME.value: "api",
            ModuleMemberFilterField.KIND.value: "function",
        }),
    )

    class _Dist:
        pass

    monkeypatch.setattr(
        "pydocs_mcp.extraction.members.find_installed_distribution",
        lambda name: _Dist(),
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.members._extract_by_import",
        lambda _dist, _depth: {"symbols": fake_members},
    )

    inspect_extractor = InspectMemberExtractor(
        static_fallback=AstMemberExtractor(),
    )
    members = await inspect_extractor.extract_from_dependency("bar")
    assert members == fake_members


@pytest.mark.asyncio
async def test_inspect_dependency_missing_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No installed distribution → empty tuple (no exception, no fallback run)."""
    monkeypatch.setattr(
        "pydocs_mcp.extraction.members.find_installed_distribution",
        lambda name: None,
    )
    inspect_extractor = InspectMemberExtractor(
        static_fallback=AstMemberExtractor(),
    )
    members = await inspect_extractor.extract_from_dependency("nonexistent-xyz")
    assert members == ()


# ── Dataclass invariants ──────────────────────────────────────────────────

def test_ast_member_extractor_is_frozen_slotted() -> None:
    extractor = AstMemberExtractor()
    # Empty frozen+slots dataclasses raise TypeError (super(type, obj) path)
    # or AttributeError depending on field count. Accept both — the invariant
    # is "assignment must fail".
    with pytest.raises((AttributeError, TypeError)):
        extractor.foo = 999  # type: ignore[misc]
    assert not hasattr(extractor, "__dict__")


def test_inspect_member_extractor_is_frozen_slotted() -> None:
    extractor = InspectMemberExtractor(static_fallback=AstMemberExtractor())
    with pytest.raises(AttributeError):
        extractor.depth = 42  # type: ignore[misc]
    assert not hasattr(extractor, "__dict__")


def test_inspect_extractor_composes_ast_fallback() -> None:
    """static_fallback is required; can be swapped."""
    ast = AstMemberExtractor()
    inspect_mode = InspectMemberExtractor(static_fallback=ast)
    assert inspect_mode.static_fallback is ast
