"""Unit tests for ``extraction/_dep_helpers.py`` (Task 12 — sub-PR #5).

Pins:
- ``find_installed_distribution`` resolves installed packages + returns None
  for unknown names. Parity with the underscore-prefixed original in
  ``indexer.py`` so callers can swap later without behavior drift.
- ``find_site_packages_root`` walks up to ``site-packages`` / ``dist-packages``
  and falls back to ``parent.parent`` when neither is present.
- ``_extract_by_import`` is re-exported (smoke): callable, and uses the
  deferred ``pydocs_mcp.indexer`` imports without blowing up at module load.
"""
from __future__ import annotations

import importlib.metadata
from pathlib import Path

import pytest

from pydocs_mcp.extraction import _dep_helpers


# ── find_installed_distribution ───────────────────────────────────────────

def test_find_installed_distribution_known_pkg_returns_dist() -> None:
    """pytest is in dev deps — must be resolvable."""
    dist = _dep_helpers.find_installed_distribution("pytest")
    assert dist is not None
    assert isinstance(dist, importlib.metadata.Distribution)
    name = dist.metadata["Name"] or ""
    assert name.lower().replace("-", "_") == "pytest"


def test_find_installed_distribution_handles_hyphen_underscore_normalisation() -> None:
    """PEP 503 normalisation: hyphen and underscore variants resolve identically."""
    # pytest-cov is a dev dep (see pyproject); verify both forms if installed
    # otherwise skip gracefully so CI parity is preserved.
    dist_under = _dep_helpers.find_installed_distribution("pytest_cov")
    dist_hyphen = _dep_helpers.find_installed_distribution("pytest-cov")
    if dist_under is None and dist_hyphen is None:
        pytest.skip("pytest-cov not installed; skipping normalisation check")
    assert (dist_under is None) == (dist_hyphen is None)


def test_find_installed_distribution_unknown_returns_none() -> None:
    assert (
        _dep_helpers.find_installed_distribution("nonexistent-xyz-pkg-2026")
        is None
    )


# ── find_site_packages_root ───────────────────────────────────────────────

def test_find_site_packages_root_walks_up_to_site_packages(tmp_path: Path) -> None:
    fake_sp = tmp_path / "site-packages"
    pkg = fake_sp / "somepkg" / "sub"
    pkg.mkdir(parents=True)
    leaf = pkg / "mod.py"
    leaf.write_text("")
    assert _dep_helpers.find_site_packages_root(str(leaf)) == str(fake_sp)


def test_find_site_packages_root_accepts_dist_packages(tmp_path: Path) -> None:
    fake_dp = tmp_path / "dist-packages"
    pkg = fake_dp / "foo"
    pkg.mkdir(parents=True)
    leaf = pkg / "__init__.py"
    leaf.write_text("")
    assert _dep_helpers.find_site_packages_root(str(leaf)) == str(fake_dp)


def test_find_site_packages_root_fallback_without_marker(tmp_path: Path) -> None:
    """When no site-packages / dist-packages ancestor exists, fall back to
    ``parent.parent`` of the input file. Matches the original behavior that
    callers (InspectMemberExtractor fallback paths) depend on."""
    leaf = tmp_path / "a" / "b" / "mod.py"
    leaf.parent.mkdir(parents=True)
    leaf.write_text("")
    assert _dep_helpers.find_site_packages_root(str(leaf)) == str(tmp_path / "a")


def test_find_site_packages_root_uses_pytest_for_smoke() -> None:
    """Sanity check against a known-installed distribution: the ancestor chain
    of any pytest .py file ends at a directory named ``site-packages`` or
    ``dist-packages`` (unless pytest is vendored outside site-packages, in
    which case the fallback still returns a valid directory).
    """
    import pytest as _pytest  # noqa: WPS433 — intentional local import

    root = _dep_helpers.find_site_packages_root(_pytest.__file__)
    assert Path(root).exists()
    assert Path(root).is_dir()


# ── _extract_by_import smoke ──────────────────────────────────────────────

def test_extract_by_import_is_callable() -> None:
    """_extract_by_import is exported and callable. Smoke-only — deeper
    behavior is exercised by InspectMemberExtractor tests in Task 20."""
    assert callable(_dep_helpers._extract_by_import)


# ── re-export surface ─────────────────────────────────────────────────────

def test_module_surface_matches_all() -> None:
    """Guards against accidental renames of the public helpers."""
    assert "find_installed_distribution" in _dep_helpers.__all__
    assert "find_site_packages_root" in _dep_helpers.__all__
    assert "_extract_by_import" in _dep_helpers.__all__
    # SKIP_IMPORT + IMPORT_ALIASES need to be accessible to strategies so they
    # can decide whether to attempt a live import.
    assert "SKIP_IMPORT" in _dep_helpers.__all__
    assert "IMPORT_ALIASES" in _dep_helpers.__all__


def test_constants_re_exported() -> None:
    """SKIP_IMPORT + IMPORT_ALIASES are copied verbatim from indexer.py so
    extraction strategies don't need to reach back into indexer for them."""
    assert "pip" in _dep_helpers.SKIP_IMPORT
    assert "setuptools" in _dep_helpers.SKIP_IMPORT
    assert _dep_helpers.IMPORT_ALIASES["pyyaml"] == "yaml"
    assert _dep_helpers.IMPORT_ALIASES["scikit-learn"] == "sklearn"
