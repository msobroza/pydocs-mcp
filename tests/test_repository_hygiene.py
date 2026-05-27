def test_license_file_exists() -> None:
    """P0-1: LICENSE file at the repo root carries the MIT text."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    license_path = root / "LICENSE"
    assert license_path.is_file(), "LICENSE file must exist at repo root (P0-1)"
    text = license_path.read_text()
    assert "MIT License" in text
    assert "Permission is hereby granted, free of charge" in text


def test_version_matches_pyproject() -> None:
    """P0-2: pydocs_mcp.__version__ matches the pyproject.toml version.

    Sourced from installed metadata via importlib.metadata so a future
    bump touches one place (pyproject.toml) and propagates.
    """
    import tomllib
    from pathlib import Path

    import pydocs_mcp

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    declared = pyproject["project"]["version"]
    assert pydocs_mcp.__version__ == declared, (
        f"__version__ drift: pyproject={declared!r} vs pkg={pydocs_mcp.__version__!r}"
    )


def test_pyproject_uses_pep639_license_form() -> None:
    """P1-1: license is the SPDX string form, license-files lists LICENSE."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    project = pyproject["project"]

    # SPDX string form (PEP 639), not the legacy `{ text = "MIT" }` table.
    assert isinstance(project["license"], str), (
        f"license must be a SPDX string (PEP 639), got {project['license']!r}"
    )
    assert project["license"] == "MIT"

    # license-files explicitly lists LICENSE so the wheel metadata
    # references it. LICENSE-third-party covers vendored attributions.
    assert "license-files" in project, "license-files entry required"
    license_files = project["license-files"]
    assert "LICENSE" in license_files, (
        f"LICENSE must be in license-files; got {license_files}"
    )


def test_py_typed_marker_exists() -> None:
    """P1-2: PEP 561 py.typed marker so downstream type-checkers
    use our type hints instead of silently inferring Any."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    marker = root / "python" / "pydocs_mcp" / "py.typed"
    assert marker.is_file(), (
        f"py.typed marker missing at {marker}. "
        "PEP 561 requires this file for downstream type-checkers to "
        "trust the annotations in this package."
    )


def test_pyproject_includes_py_typed_in_maturin() -> None:
    """P1-2: maturin must bundle py.typed into the wheel.

    Without this, `pip install pydocs-mcp` ships the wheel WITHOUT
    py.typed and downstream type-checkers still see Any.
    """
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    maturin = pyproject.get("tool", {}).get("maturin", {})
    include = maturin.get("include", [])
    assert any("py.typed" in entry for entry in include), (
        f"tool.maturin.include must list py.typed; got {include}"
    )
