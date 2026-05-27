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
