"""pyproject.toml declares main + extras correctly (Task 6 + AC-16)."""

from pathlib import Path

import tomllib

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


def test_watch_extra_is_empty_backcompat_alias() -> None:
    """watchdog moved into the required deps (spec
    2026-07-11-watch-default-install); [watch] stays as an empty alias so
    existing `pip install pydocs-mcp[watch]` commands keep resolving."""
    cfg = _load()
    extras = cfg["project"].get("optional-dependencies", {})
    assert "watch" in extras, f"watch alias extra missing. Got: {list(extras)}"
    assert extras["watch"] == [], f"watch extra must be an empty alias; got {extras['watch']}"


def test_watchdog_in_main_dependencies() -> None:
    """`pip install pydocs-mcp` (no extras) suffices for `serve --watch`."""
    cfg = _load()
    main_deps = cfg["project"]["dependencies"]
    watchdog_entries = [d for d in main_deps if d.startswith("watchdog")]
    assert len(watchdog_entries) == 1, f"exactly one watchdog entry expected, got: {main_deps}"


def test_watchdog_main_dep_pins_version_range() -> None:
    """Pin moved verbatim from the extra — a future watchdog 6.x breaking
    change must not silently break ``--watch``."""
    cfg = _load()
    spec = next(d for d in cfg["project"]["dependencies"] if d.startswith("watchdog"))
    assert ">=4.0" in spec and "<6.0" in spec, f"watchdog spec must pin >=4.0,<6.0; got {spec!r}"


def test_no_watch_install_hint_left() -> None:
    """No shipped code may instruct `pip install pydocs-mcp[watch]` — the
    extra is an empty back-compat alias, not an install requirement."""
    pkg_root = Path(__file__).resolve().parents[1] / "python" / "pydocs_mcp"
    offenders = [
        str(p)
        for p in pkg_root.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".yaml"}
        and "pydocs-mcp[watch]" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"stale [watch] install hints in shipped code: {offenders}"
