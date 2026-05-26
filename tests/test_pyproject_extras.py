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


def test_fastembed_in_main_deps_not_optional() -> None:
    """AC-14: fastembed is a required dep, not an extra."""
    cfg = _load()
    main_deps = cfg["project"]["dependencies"]
    assert any("fastembed" in d for d in main_deps), (
        f"fastembed not in main dependencies: {main_deps}"
    )
    extras = cfg["project"].get("optional-dependencies", {})
    assert "fastembed" not in extras
    assert "openai" not in extras
    assert "all-embedders" not in extras


def test_openai_in_main_deps_not_optional() -> None:
    """AC-14: openai is a required dep, not an extra."""
    cfg = _load()
    main_deps = cfg["project"]["dependencies"]
    assert any("openai" in d for d in main_deps), (
        f"openai not in main dependencies: {main_deps}"
    )


def test_jinja2_in_main_deps() -> None:
    """LLM tree reasoning loads Jinja2 prompt templates; jinja2 must be a
    required runtime dep, not a transitive accident."""
    cfg = _load()
    main_deps = cfg["project"]["dependencies"]
    assert any("jinja2" in d.lower() for d in main_deps), (
        f"jinja2 not in main dependencies: {main_deps}"
    )
