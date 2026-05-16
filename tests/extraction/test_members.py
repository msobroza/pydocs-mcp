"""Unit tests for ``extraction/strategies/members.py`` (sub-PR #5, spec §9).

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

from pydocs_mcp.extraction.strategies.members import (
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
        "pydocs_mcp.extraction.strategies.members.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.members.find_site_packages_root",
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
        "pydocs_mcp.extraction.strategies.members.find_installed_distribution",
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
        "pydocs_mcp.extraction.strategies.members.find_installed_distribution",
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
        "pydocs_mcp.extraction.strategies.members.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.members.find_site_packages_root",
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
        "pydocs_mcp.extraction.strategies.members.find_installed_distribution",
        lambda name: dist,
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.members.find_site_packages_root",
        lambda p: str(sp),
    )

    def _boom(_dist, _depth, **_kwargs):
        raise RuntimeError("simulated import failure")

    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.members._extract_by_import", _boom,
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
        "pydocs_mcp.extraction.strategies.members.find_installed_distribution",
        lambda name: _Dist(),
    )
    monkeypatch.setattr(
        "pydocs_mcp.extraction.strategies.members._extract_by_import",
        lambda _dist, _depth, **_kwargs: {"symbols": fake_members},
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
        "pydocs_mcp.extraction.strategies.members.find_installed_distribution",
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


# -- F4 + F12 — InspectMemberExtractor cap + truncation -----------------------


def test_dep_helpers_collect_symbols_enforces_per_module_cap() -> None:
    """F4: pre-refactor the inspect path capped members per module at 120
    to bound FTS bloat; the refactor lost this enforcement. Direct
    unit-test against _collect_symbols so any future regression
    instantly fails."""
    from types import ModuleType
    from pydocs_mcp.extraction.strategies._dep_helpers import _collect_symbols

    mod = ModuleType("hugemod")
    # Pump >cap public functions onto the synthetic module so the
    # collected count exceeds the limit if the cap is dropped.
    for i in range(50):
        def _f(_=i):  # noqa: ARG001 -- closure capture by default arg
            pass
        _f.__name__ = f"fn{i}"
        setattr(mod, f"fn{i}", _f)

    symbols: list = []
    _collect_symbols(
        mod, "hugemod", "hugemod", symbols, remaining_depth=1,
        members_per_module_cap=10,  # tighter cap so the assertion is unambiguous
    )
    assert len(symbols) == 10, (
        f"members_per_module_cap=10 not honoured — got {len(symbols)} symbols"
    )


def test_dep_helpers_truncate_signature_to_max_chars() -> None:
    """F12: pre-refactor the inspect path truncated long signatures
    before persisting; the refactor dropped that. Confirm the
    MAX_SIGNATURE_CHARS limit is applied with an ellipsis marker."""
    from pydocs_mcp.extraction.strategies._dep_helpers import (
        MAX_SIGNATURE_CHARS, _truncate,
    )
    raw = "(" + ", ".join(f"arg{i}: int = 0" for i in range(50)) + ")"
    assert len(raw) > MAX_SIGNATURE_CHARS
    capped = _truncate(raw, MAX_SIGNATURE_CHARS)
    assert len(capped) == MAX_SIGNATURE_CHARS
    assert capped.endswith("…")


def test_dep_helpers_truncate_docstring_to_max_chars() -> None:
    """F12 (docstring branch): docstrings get the same treatment."""
    from pydocs_mcp.extraction.strategies._dep_helpers import (
        MAX_DOCSTRING_CHARS, _truncate,
    )
    raw = "Docstring line.\n" * 200  # >> 1024 chars
    assert len(raw) > MAX_DOCSTRING_CHARS
    capped = _truncate(raw, MAX_DOCSTRING_CHARS)
    assert len(capped) == MAX_DOCSTRING_CHARS
    assert capped.endswith("…")


def test_dep_helpers_truncate_under_limit_unchanged() -> None:
    """Short strings pass through identically (no ellipsis appended)."""
    from pydocs_mcp.extraction.strategies._dep_helpers import _truncate
    assert _truncate("short", 100) == "short"
    assert _truncate("", 100) == ""


# -- F14 — Member-side project walk aligned with _EXCLUDED_DIRS ---------------


@pytest.mark.asyncio
async def test_ast_project_skips_excluded_dirs_post_walk(tmp_path: Path) -> None:
    """F14: walk_py_files (Rust + Python fallback) has its own hardcoded
    SKIP_DIRS that's narrower than ``_EXCLUDED_DIRS``. The member-side
    walk used to pick up files from dirs the chunker policy excludes —
    most notably checked-in 'site-packages' / 'target' / '.hg'.
    Post-filter check enforces the canonical exclusion."""
    # Layout: real project module + a directory that's in _EXCLUDED_DIRS
    # but NOT in walk_py_files's SKIP_DIRS (so the post-filter is the
    # only thing keeping it out).
    (tmp_path / "src.py").write_text("def kept(): pass\n")
    vendored = tmp_path / "site-packages" / "leaky"
    vendored.mkdir(parents=True)
    (vendored / "__init__.py").write_text("def leaked(): pass\n")
    other_vendored = tmp_path / "target" / "build_pkg"
    other_vendored.mkdir(parents=True)
    (other_vendored / "__init__.py").write_text("def target_leaked(): pass\n")

    extractor = AstMemberExtractor()
    members = await extractor.extract_from_project(tmp_path)
    names = {m.metadata[ModuleMemberFilterField.NAME.value] for m in members}

    assert "kept" in names, "real project module must be picked up"
    assert "leaked" not in names, (
        "site-packages content leaked into member index — F14 regression"
    )
    assert "target_leaked" not in names, (
        "target/ content leaked into member index — F14 regression"
    )


def test_path_under_excluded_helper_unit() -> None:
    """Unit test the bridge helper directly so the matching logic is
    pinned independently of file-system fixtures."""
    from pydocs_mcp.extraction.strategies.members import _path_under_excluded

    excluded = frozenset({"site-packages", ".hg"})
    assert _path_under_excluded("repo/src/main.py", excluded) is False
    assert _path_under_excluded("repo/site-packages/x.py", excluded) is True
    # Rust always emits forward slashes; helper must handle that too.
    assert _path_under_excluded("repo/.hg/foo.py", excluded) is True
    # Backslashes from Windows fallback also normalised.
    assert _path_under_excluded("repo\\site-packages\\x.py", excluded) is True


# -- T3: F14 full _EXCLUDED_DIRS + case sensitivity coverage ------------------


def test_path_under_excluded_covers_full_excluded_dirs_set() -> None:
    """T3: F14's existing test exercised only 'site-packages' + 'target'.
    Parametrise the full set so adding/removing an entry in
    _EXCLUDED_DIRS flips this test, not a stale subset."""
    from pydocs_mcp.extraction.config import _EXCLUDED_DIRS, path_under_excluded

    # Every blocklisted name must be detected as a path component.
    for excluded_name in _EXCLUDED_DIRS:
        path = f"repo/{excluded_name}/file.py"
        assert path_under_excluded(path), (
            f"_EXCLUDED_DIRS entry {excluded_name!r} not detected by "
            f"path_under_excluded — drift between policy + enforcer"
        )

    # Non-blocklisted names must NOT match (no substring false-positive).
    assert not path_under_excluded("repo/src/main.py")
    assert not path_under_excluded("repo/my_eggs_pkg/x.py"), (
        "substring false-positive: 'my_eggs_pkg' contains 'eggs' but "
        "isn't an excluded dir — only exact path-component matches count"
    )


def test_path_under_excluded_is_case_sensitive_by_design() -> None:
    """T3: pinning the documented case-sensitivity contract. Pre-fix,
    Windows-style 'Site-Packages' (mixed case) WOULD evade the filter
    because the frozenset has lowercase 'site-packages' only. We
    decided NOT to lowercase (the user's filesystem owns case;
    inventing a case match could mask real different-cased dirs).
    Document via test so the trade-off is explicit if someone
    reconsiders."""
    from pydocs_mcp.extraction.config import path_under_excluded

    # Lowercase canonical form: excluded.
    assert path_under_excluded("repo/site-packages/x.py")
    # Mixed-case variant: NOT excluded (by design — see above).
    assert not path_under_excluded("repo/Site-Packages/x.py")
    assert not path_under_excluded("repo/.HG/foo.py")


def test_path_under_excluded_egg_info_as_component_not_substring() -> None:
    """T3: real PyPI dists often have 'mypkg.egg-info/' (with stem-dot
    prefix). The whole component 'mypkg.egg-info' is NOT the bare
    'egg-info' — it shouldn't match. But 'foo/egg-info/x.py' (the
    bare 'egg-info' component) SHOULD match."""
    from pydocs_mcp.extraction.config import path_under_excluded

    assert path_under_excluded("foo/egg-info/x.py"), (
        "bare 'egg-info' path component must be excluded"
    )
    assert not path_under_excluded("foo/mypkg.egg-info/x.py"), (
        "'mypkg.egg-info' is a different component from 'egg-info' — "
        "substring match would over-exclude real PyPI dist metadata dirs"
    )
