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


def test_public_api_all_declared() -> None:
    """P1-6: __init__.py declares __all__ + the public exception hierarchy."""
    import pydocs_mcp

    assert hasattr(pydocs_mcp, "__all__"), "__init__.py must declare __all__"
    assert hasattr(pydocs_mcp, "__version__")

    # Public exception hierarchy — these are what embedders catch.
    assert hasattr(pydocs_mcp, "PydocsMCPError")
    assert hasattr(pydocs_mcp, "MCPToolError")
    assert hasattr(pydocs_mcp, "InvalidArgumentError")
    assert hasattr(pydocs_mcp, "NotFoundError")
    assert hasattr(pydocs_mcp, "ServiceUnavailableError")


def test_all_entries_are_importable() -> None:
    """Every name in __all__ must resolve. Prevents __all__ from
    silently listing typos / removed symbols."""
    import pydocs_mcp

    for name in pydocs_mcp.__all__:
        assert hasattr(pydocs_mcp, name), (
            f"__all__ lists {name!r} but pydocs_mcp has no such attribute"
        )


def test_all_entries_unique() -> None:
    """No duplicate names in __all__."""
    import pydocs_mcp

    assert len(pydocs_mcp.__all__) == len(set(pydocs_mcp.__all__))


def test_dependency_groups_defined() -> None:
    """P1-5: PEP 735 [dependency-groups] holds dev/test/lint deps.

    Keeping these out of [project.optional-dependencies] means:
      (1) they don't ship in wheel METADATA,
      (2) users can't accidentally `pip install pydocs-mcp[dev]`
          and pull in pytest,
      (3) tools (uv, pip 25.1+, PDM) can distinguish "user-installable
          extra" from "developer-only group".
    """
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())

    assert "dependency-groups" in pyproject, "PEP 735 [dependency-groups] section required"
    groups = pyproject["dependency-groups"]
    assert "dev" in groups
    # Dev group should pull in pytest one way or another (either directly
    # or via include-group: "test").
    dev_str = str(groups["dev"])
    assert "pytest" in dev_str or "test" in dev_str


def test_dev_deps_not_in_user_facing_extras() -> None:
    """Embedder safety: `pip install pydocs-mcp[dev]` must NOT work
    silently (no dev extras group user-installable). PEP 735 groups
    are explicit-only via uv / pip --group.
    """
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    extras = pyproject["project"].get("optional-dependencies", {})

    # Either the dev extras group is gone entirely (preferred) OR — if
    # kept for backward compat — it doesn't list pytest etc.
    if "dev" in extras:
        dev_extras_str = str(extras["dev"])
        assert "pytest" not in dev_extras_str, (
            "dev deps must live in [dependency-groups], not "
            "[project.optional-dependencies] (P1-5)"
        )


def test_ruff_target_version_matches_requires_python() -> None:
    """P2-2: Ruff target-version must match requires-python floor."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    requires_python = pyproject["project"]["requires-python"]
    ruff_target = pyproject["tool"]["ruff"]["target-version"]

    assert ruff_target == "py311", (
        f"ruff target {ruff_target!r} must match requires-python {requires_python!r}"
    )


def test_ruff_select_includes_quality_rules() -> None:
    """P2-3: Ruff select must include B/UP/S/SIM/RUF for bug + upgrade
    + security + simplification coverage."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    select = pyproject["tool"]["ruff"]["lint"]["select"]

    required = {"E", "F", "W", "I", "B", "UP", "S", "SIM", "RUF"}
    missing = required - set(select)
    assert not missing, f"ruff select missing: {missing}; current: {select}"


def test_mypy_config_present() -> None:
    """P1-3: [tool.mypy] section configured in pyproject.toml.

    Lenient initial config (disallow_untyped_defs=False); ratchet plan
    lives in the section's comment.
    """
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text())
    assert "mypy" in pyproject.get("tool", {}), (
        "[tool.mypy] required (P1-3)"
    )
    mypy = pyproject["tool"]["mypy"]
    assert mypy["python_version"] == "3.11"
    files = mypy["files"]
    assert isinstance(files, list)
    assert any("pydocs_mcp" in f for f in files)
