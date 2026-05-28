def test_license_file_exists() -> None:
    """P0-1: LICENSE file at the repo root carries the MIT text."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    license_path = root / "LICENSE"
    assert license_path.is_file(), "LICENSE file must exist at repo root (P0-1)"
    text = license_path.read_text(encoding="utf-8")
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
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    declared = pyproject["project"]["version"]
    assert pydocs_mcp.__version__ == declared, (
        f"__version__ drift: pyproject={declared!r} vs pkg={pydocs_mcp.__version__!r}"
    )


def test_pyproject_uses_pep639_license_form() -> None:
    """P1-1: license is the SPDX string form, license-files lists LICENSE."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
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
    assert "LICENSE" in license_files, f"LICENSE must be in license-files; got {license_files}"


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
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
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
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

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
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject["project"].get("optional-dependencies", {})

    # Either the dev extras group is gone entirely (preferred) OR — if
    # kept for backward compat — it doesn't list pytest etc.
    if "dev" in extras:
        dev_extras_str = str(extras["dev"])
        assert "pytest" not in dev_extras_str, (
            "dev deps must live in [dependency-groups], not [project.optional-dependencies] (P1-5)"
        )


def test_ruff_target_version_matches_requires_python() -> None:
    """P2-2: Ruff target-version must match requires-python floor."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
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
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
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
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert "mypy" in pyproject.get("tool", {}), "[tool.mypy] required (P1-3)"
    mypy = pyproject["tool"]["mypy"]
    assert mypy["python_version"] == "3.11"
    files = mypy["files"]
    assert isinstance(files, list)
    assert any("pydocs_mcp" in f for f in files)


def test_ci_matrix_includes_macos_and_windows() -> None:
    """P1-4: ci.yml tests on macOS + Windows, not just Linux.

    release.yml builds wheels for Linux + macOS + Windows; ci.yml
    must test on the same matrix so broken non-Linux wheels can't
    ship undetected.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ci_yml = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    # The matrix block should mention each OS we want covered.
    # Substring assertion stays robust to formatting changes.
    assert "ubuntu-latest" in ci_yml, "Linux row must remain"
    assert re.search(r"macos-1[34]", ci_yml), "macos-13 or macos-14 row missing"
    assert "windows-latest" in ci_yml, "windows-latest row missing"


def test_changelog_exists() -> None:
    """P2-1: CHANGELOG.md follows Keep-a-Changelog format."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    cl = root / "CHANGELOG.md"
    assert cl.is_file(), "CHANGELOG.md required at repo root (P2-1)"
    text = cl.read_text(encoding="utf-8")
    assert "Keep a Changelog" in text or "keepachangelog.com" in text


def test_pre_commit_config_exists() -> None:
    """P2-5: .pre-commit-config.yaml configures ruff + yaml/toml hooks."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    cfg = root / ".pre-commit-config.yaml"
    assert cfg.is_file(), ".pre-commit-config.yaml required (P2-5)"
    text = cfg.read_text(encoding="utf-8")
    assert "ruff-pre-commit" in text or "astral-sh/ruff-pre-commit" in text


def test_makefile_exists() -> None:
    """P2-6: top-level Makefile orchestrates dev commands."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    mk = root / "Makefile"
    assert mk.is_file(), "Makefile required at repo root (P2-6)"
    text = mk.read_text(encoding="utf-8")
    for target in ("test", "lint", "format", "typecheck", "build", "clean"):
        assert f"\n{target}:" in text or text.startswith(f"{target}:"), (
            f"Makefile missing `{target}:` target"
        )


def test_editorconfig_exists() -> None:
    """P2-7: .editorconfig enforces indent / EOL / final-newline cross-editor."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ec = root / ".editorconfig"
    assert ec.is_file(), ".editorconfig required at repo root (P2-7)"
    text = ec.read_text(encoding="utf-8")
    assert "root = true" in text
    assert "end_of_line = lf" in text
    assert "insert_final_newline = true" in text


def test_uv_lock_exists_and_pinned() -> None:
    """P2-8: uv.lock committed for reproducible builds.

    Defer-able: if uv lock generation has friction (turbovec/fastembed
    platform-specific wheels can stall it), the test should be marked
    skip with a documented WHY. This commit assumes it generated cleanly.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    lock = root / "uv.lock"
    assert lock.is_file(), (
        "uv.lock required for reproducible CI installs (P2-8). "
        "If uv lock has friction, mark this test skip with a WHY note."
    )
    # Sanity: lockfile pins at least the project + main runtime deps.
    text = lock.read_text(encoding="utf-8")
    assert 'name = "pydocs-mcp"' in text or "pydocs-mcp" in text


def test_ci_uses_uv_sync_frozen() -> None:
    """P2-8 lockfile + Task 8: CI installs from uv.lock via `uv sync --frozen`.

    Prevents a future PR from silently dropping back to
    `uv pip install --system` (which resolves fresh each run and
    defeats the lockfile). The rust job is intentionally exempt — it
    has a different lifecycle (maturin develop on a hand-rolled venv).
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    ci_yml = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "uv sync --frozen" in ci_yml, (
        "ci.yml must use `uv sync --frozen` for lockfile reproducibility"
    )
    # And NOT fall back to pip-install --system in any `run:` line of the
    # python job (the rust job is intentionally exempt — it needs a real
    # venv). Match the actual command, not WHY-comment text that may
    # explain the migration history.
    for line in ci_yml.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue  # skip comments (they may cite the old command)
        assert "uv pip install --system" not in stripped, (
            f"ci.yml must not invoke `uv pip install --system` "
            f"(found in line: {stripped!r}); "
            f"use `uv sync --frozen` (lockfile-pinned) instead"
        )
