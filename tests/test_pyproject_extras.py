"""pyproject.toml declares main + extras correctly (Task 6 + AC-16)."""

from pathlib import Path

import tomllib

from packaging.requirements import Requirement

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _load():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


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
    assert any("openai" in d for d in main_deps), f"openai not in main dependencies: {main_deps}"


def test_jinja2_in_main_deps() -> None:
    """LLM tree reasoning loads Jinja2 prompt templates; jinja2 must be a
    required runtime dep, not a transitive accident."""
    cfg = _load()
    main_deps = cfg["project"]["dependencies"]
    assert any("jinja2" in d.lower() for d in main_deps), (
        f"jinja2 not in main dependencies: {main_deps}"
    )


def test_watch_extras_group_present() -> None:
    """AC-9: ``watchdog`` ships behind ``[watch]`` extras, not main deps."""
    cfg = _load()
    extras = cfg["project"].get("optional-dependencies", {})
    assert "watch" in extras, f"watch extras group missing. Got: {list(extras)}"
    watch_deps = extras["watch"]
    assert any("watchdog" in d for d in watch_deps), f"watchdog not in watch extras: {watch_deps}"


def test_watchdog_not_in_main_dependencies() -> None:
    """AC-9: ``watchdog`` is opt-in via ``[watch]`` — never pulled by default
    ``pip install pydocs-mcp``."""
    cfg = _load()
    main_deps = cfg["project"]["dependencies"]
    assert not any("watchdog" in d for d in main_deps), (
        f"watchdog leaked into main dependencies: {main_deps}"
    )


def test_watch_extras_pins_watchdog_version_range() -> None:
    """Pin the version range so a future watchdog 6.x breaking change
    doesn't silently break ``--watch``."""
    cfg = _load()
    watch_deps = cfg["project"]["optional-dependencies"]["watch"]
    spec = next(d for d in watch_deps if "watchdog" in d)
    assert ">=4.0" in spec and "<6.0" in spec, f"watchdog spec must pin >=4.0,<6.0; got {spec!r}"


def test_mcp_capped_below_2() -> None:
    """mcp 2.x removed ``mcp.server.fastmcp``; an uncapped ``mcp>=1.0`` let a
    fresh ``pip install pydocs-mcp`` resolve mcp 2.2.0 and crash ``serve`` at
    startup (CI installs from uv.lock, so it never saw the 2.x resolve)."""
    deps = _load()["project"]["dependencies"]
    req = next(Requirement(d) for d in deps if Requirement(d).name == "mcp")
    assert req.specifier.contains("1.28.1"), f"mcp floor moved unexpectedly: {req}"
    assert not req.specifier.contains("2.0.0"), f"mcp must stay below 2.0; got {req}"
