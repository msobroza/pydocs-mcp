"""pyproject.toml declares main + extras correctly (Task 6 + AC-16)."""
from pathlib import Path

import tomllib

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _load():
    return tomllib.loads(PYPROJECT.read_text())


def test_turbovec_in_main_dependencies() -> None:
    cfg = _load()
    deps = cfg["project"]["dependencies"]
    assert any("turbovec" in d for d in deps)


def test_numpy_in_main_dependencies() -> None:
    cfg = _load()
    deps = cfg["project"]["dependencies"]
    assert any(d.startswith("numpy") for d in deps)


def test_fastembed_extra_exists() -> None:
    cfg = _load()
    extras = cfg["project"]["optional-dependencies"]
    assert "fastembed" in extras
    assert any("fastembed" in d for d in extras["fastembed"])


def test_openai_extra_exists() -> None:
    cfg = _load()
    extras = cfg["project"]["optional-dependencies"]
    assert "openai" in extras
    assert any(d.startswith("openai") for d in extras["openai"])


def test_all_embedders_extra_unions_both() -> None:
    cfg = _load()
    extras = cfg["project"]["optional-dependencies"]
    assert "all-embedders" in extras
    items = extras["all-embedders"]
    assert any("fastembed" in d for d in items)
    assert any(d.startswith("openai") for d in items)
