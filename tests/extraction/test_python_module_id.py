"""Pins the single-source Python module-id rule (spec 2026-09-10 §2).

``extraction/strategies/python_module_id.py`` is the only home of the
package-root walk (project roots) and the import-root rule (``sys.path``
roots such as site-packages). The chunker, the reference analyzer and the
member extractor all call it, so project member ids equal chunk/tree ids.
"""

from __future__ import annotations

import ast
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from pydocs_mcp.extraction.strategies import python_module_id
from pydocs_mcp.extraction.strategies.python_module_id import (
    import_root_module_id,
    package_rooted_module_id,
    python_package_root,
    relative_module_parts,
)

_PKG_DIR = Path(python_module_id.__file__).parents[2]  # .../pydocs_mcp
_CHUNKERS_PKG = "pydocs_mcp.extraction.strategies.chunkers"
_ROOT_NAME = "myroot"

# Every name that ever held (part of) the module-id rule. Only
# python_module_id.py may define any of them (AC-9).
_RULE_FUNCTION_NAMES = frozenset(
    {
        "python_package_root",
        "_python_package_root",
        "package_rooted_module_id",
        "_module_from_path",
        "import_root_module_id",
        "_module_from_rel_path",
        "relative_module_parts",
        "_relative_module_parts",
        "_join_module_parts",
    }
)


def _materialize(root: Path, rel_files: tuple[str, ...]) -> Path:
    for rel in rel_files:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n", encoding="utf-8")
    return root


# One case per spec §1 divergence-table row (plus the rows that must not move).
_LAYOUTS = [
    pytest.param(
        (
            "src/needle/__init__.py",
            "src/needle/scoring/__init__.py",
            "src/needle/scoring/strategies.py",
        ),
        "src/needle/scoring/strategies.py",
        "needle.scoring.strategies",
        id="src-layout",
    ),
    pytest.param(
        ("python/pkg/__init__.py", "python/pkg/steps.py"),
        "python/pkg/steps.py",
        "pkg.steps",
        id="maturin-python",
    ),
    pytest.param(
        ("src/nsroot/sub/__init__.py", "src/nsroot/sub/mod.py"),
        "src/nsroot/sub/mod.py",
        "sub.mod",
        id="namespace-root",
    ),
    pytest.param(
        ("pkg/__init__.py", "pkg/data/sub/__init__.py", "pkg/data/sub/m.py"),
        "pkg/data/sub/m.py",
        "sub.m",
        id="non-package-subdir",
    ),
    pytest.param(("__init__.py",), "__init__.py", _ROOT_NAME, id="root-init"),
    pytest.param(("__init__.py", "a.py"), "a.py", f"{_ROOT_NAME}.a", id="root-init-sibling"),
    pytest.param(
        ("__init__.py", "tests/__init__.py", "tests/test_x.py"),
        "tests/test_x.py",
        f"{_ROOT_NAME}.tests.test_x",
        id="root-init-tests-package",
    ),
    pytest.param(
        ("__init__.py", "tests/test_x.py"),
        "tests/test_x.py",
        "tests.test_x",
        id="root-init-tests-plain-dir",
    ),
    pytest.param(("mod.py",), "mod.py", "mod", id="flat-module"),
    pytest.param(("pkg/__init__.py", "pkg/mod.py"), "pkg/mod.py", "pkg.mod", id="flat-package"),
    pytest.param(("tests/test_a.py",), "tests/test_a.py", "tests.test_a", id="tests-no-init"),
    pytest.param(
        ("tests/__init__.py", "tests/test_a.py"),
        "tests/test_a.py",
        "tests.test_a",
        id="tests-with-init",
    ),
    pytest.param(("scripts/run.py",), "scripts/run.py", "scripts.run", id="scripts-no-init"),
    pytest.param(
        ("scripts/__init__.py", "scripts/run.py"),
        "scripts/run.py",
        "scripts.run",
        id="scripts-with-init",
    ),
    pytest.param(("setup.py",), "setup.py", "setup", id="setup-py"),
    pytest.param(("src/app.py",), "src/app.py", "src.app", id="loose-src-dir"),
    pytest.param(
        ("resources/x/src/midpoint.py",),
        "resources/x/src/midpoint.py",
        "resources.x.src.midpoint",
        id="loose-nested-src",
    ),
    pytest.param(("pkg/__init__.py",), "pkg/__init__.py", "pkg", id="package-init"),
]


@pytest.mark.parametrize(("rel_files", "target", "expected"), _LAYOUTS)
def test_package_rooted_module_id_layout_table(
    tmp_path: Path, rel_files: tuple[str, ...], target: str, expected: str
) -> None:
    root = _materialize(tmp_path / _ROOT_NAME, rel_files)
    assert package_rooted_module_id(str(root / target), root) == expected


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        pytest.param("google/cloud/storage/blob.py", "google.cloud.storage.blob", id="namespace"),
        pytest.param("regpkg/core.py", "regpkg.core", id="regular-package"),
        pytest.param("six.py", "six", id="top-level-module"),
        pytest.param("pkg/__init__.py", "pkg", id="package-init"),
    ],
)
def test_import_root_module_id_table(tmp_path: Path, rel: str, expected: str) -> None:
    site_packages = _materialize(tmp_path / "site-packages", (rel,))
    assert import_root_module_id(str(site_packages / rel), site_packages) == expected


def _cross_drive_relpath(path: str, start: str | None = None) -> str:
    """Named fake for Windows' cross-drive ``os.path.relpath`` failure."""
    raise ValueError(f"path is on mount 'D:', start on mount 'C:': {path!r} vs {start!r}")


def test_import_root_module_id_propagates_relpath_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The member extractor skips a file on ``ValueError``, so the rule must
    raise it, not swallow it. POSIX ``relpath`` never raises here, which is
    why the Windows cross-drive failure is injected through a named fake
    rather than skipped on non-Windows platforms."""
    monkeypatch.setattr(os.path, "relpath", _cross_drive_relpath)
    with pytest.raises(ValueError, match="mount"):
        import_root_module_id(str(tmp_path / "x.py"), tmp_path)


def test_relative_module_parts_outside_root_falls_back_to_basename(tmp_path: Path) -> None:
    path = tmp_path / "a" / "x.py"
    parts, as_path = relative_module_parts(str(path), tmp_path / "b")
    assert (parts, as_path) == (["x"], path)


def test_python_package_root_stops_at_first_non_package_dir(tmp_path: Path) -> None:
    root = _materialize(tmp_path, ("src/nsroot/sub/__init__.py", "src/nsroot/sub/mod.py"))
    assert python_package_root(root / "src/nsroot/sub/mod.py") == root / "src/nsroot"


def test_chunker_aliases_are_the_neutral_functions() -> None:
    from pydocs_mcp.extraction.strategies import chunkers
    from pydocs_mcp.extraction.strategies.chunkers import _shared, ast_python

    assert ast_python._module_from_path is package_rooted_module_id
    assert ast_python._python_package_root is python_package_root
    assert chunkers._module_from_path is package_rooted_module_id
    assert chunkers._python_package_root is python_package_root
    assert _shared._relative_module_parts is relative_module_parts


def _imported_modules(source_path: Path) -> set[str]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_members_extractor_imports_nothing_from_chunkers() -> None:
    source = _PKG_DIR / "extraction" / "strategies" / "members" / "ast_extractor.py"
    leaked = {m for m in _imported_modules(source) if m.startswith(_CHUNKERS_PKG)}
    assert leaked == set()


def test_python_module_id_imports_stdlib_only() -> None:
    # stdlib-only because chunkers/ast_python imports this module while the
    # extraction.strategies package is still initialising (spec §2 cycle note).
    imported = _imported_modules(Path(python_module_id.__file__))
    non_stdlib = {m for m in imported if m.split(".")[0] not in sys.stdlib_module_names}
    assert non_stdlib == set()


def _function_defs(root: Path) -> Iterator[tuple[Path, str]]:
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defs = (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef))
        yield from ((path, node.name) for node in defs)


def test_module_id_rule_is_defined_only_in_python_module_id() -> None:
    offenders = sorted(
        f"{path.relative_to(_PKG_DIR)}:{name}"
        for path, name in _function_defs(_PKG_DIR)
        if name in _RULE_FUNCTION_NAMES and path.name != "python_module_id.py"
    )
    assert offenders == []
