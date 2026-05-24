"""DS-1000 reference-project fixture tests.

This fixture is a tiny PEP 621 project whose ``pyproject.toml`` pins the
exact library versions DS-1000 was authored against (the ``ds1000-py310``
environment). An operator installs it into a venv so the native
pydocs-mcp run indexes those pinned versions. CI only *parses* the
manifest here — no install, no network.

The frozen ``_EXPECTED_PINS`` dict is a regression guard: if anyone edits
a version pin in the fixture, the equality assertion below fails loudly.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

# parents[0] = tests/eval, parents[1] = tests, parents[2] = benchmarks
_PYPROJECT_PATH = (
    Path(__file__).parents[2]
    / "fixtures"
    / "ds1000_reference_project"
    / "pyproject.toml"
)

# The 11 pins lifted verbatim from the DS-1000 ``ds1000-py310``
# environment.yml (conda→pip already translated). Frozen here so an
# accidental edit to the fixture is caught in review.
_EXPECTED_PINS: dict[str, str] = {
    "gensim": "4.3.2",
    "matplotlib": "3.8.4",
    "numpy": "1.26.4",
    "pandas": "1.5.3",
    "torch": "2.2.0",
    "seaborn": "0.13.2",
    "scikit-learn": "1.4.0",
    "scipy": "1.12.0",
    "statsmodels": "0.14.1",
    "xgboost": "2.0.3",
    "tensorflow-cpu": "2.16.1",
}

# The 7 primary DS-1000 retrieval categories (the extras gensim, seaborn,
# statsmodels, xgboost are solution deps for faithful env parity).
_PRIMARY_CATEGORIES = frozenset(
    {
        "numpy",
        "pandas",
        "matplotlib",
        "scikit-learn",
        "scipy",
        "tensorflow-cpu",
        "torch",
    }
)


def _load_pyproject() -> dict:
    with _PYPROJECT_PATH.open("rb") as handle:
        return tomllib.load(handle)


def _parse_pins() -> dict[str, str]:
    """Split each ``name==version`` dependency string into a {name: version} map."""
    deps = _load_pyproject()["project"]["dependencies"]
    pins: dict[str, str] = {}
    for dep in deps:
        name, _, version = dep.partition("==")
        pins[name.strip()] = version.strip()
    return pins


def test_pyproject_exists() -> None:
    assert _PYPROJECT_PATH.is_file(), f"missing fixture pyproject: {_PYPROJECT_PATH}"


def test_dependencies_match_frozen_pins_exactly() -> None:
    """Full [project.dependencies] set must equal the frozen 11-pin dict.

    Regression guard — editing any pin (or adding/removing a dep) fails here.
    """
    assert _parse_pins() == _EXPECTED_PINS


def test_primary_retrieval_categories_present() -> None:
    """All 7 primary DS-1000 retrieval categories must be pinned."""
    pins = _parse_pins()
    missing = _PRIMARY_CATEGORIES - pins.keys()
    assert not missing, f"missing primary DS-1000 categories: {sorted(missing)}"


def test_requires_python_starts_with_3_10() -> None:
    requires_python = _load_pyproject()["project"]["requires-python"]
    assert requires_python.startswith(">=3.10")
