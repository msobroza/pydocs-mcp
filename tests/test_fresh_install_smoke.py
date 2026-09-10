"""Unit tests for the pure helpers of scripts/fresh_install_smoke.py.

The end-to-end path (index + MCP stdio + search) runs in the nightly
fresh-install workflow and the release pre-publish gate; these tests pin the
helpers that decide pass/fail so a refactor can't make the gate vacuous.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "fresh_install_smoke.py"


def _load_smoke():
    spec = importlib.util.spec_from_file_location("fresh_install_smoke", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke()


def test_expected_tools_is_the_frozen_nine_tool_surface() -> None:
    assert len(smoke.EXPECTED_TOOLS) == 9
    assert {"search_codebase", "grep", "glob", "read_file", "get_why"} <= smoke.EXPECTED_TOOLS


def test_write_smoke_project_creates_a_documented_module(tmp_path: pathlib.Path) -> None:
    project = smoke.write_smoke_project(tmp_path)
    source = (project / "numbers_demo.py").read_text(encoding="utf-8")
    compile(source, "numbers_demo.py", "exec")
    assert f"def {smoke.SMOKE_QUERY}(" in source


def test_build_cli_args_skips_deps_and_pins_cache(tmp_path: pathlib.Path) -> None:
    args = smoke.build_cli_args("serve", tmp_path / "p", tmp_path / "c")
    assert args == ["serve", str(tmp_path / "p"), "--skip-deps", "--cache-dir", str(tmp_path / "c")]


def test_find_missing_tools_reports_absent_names_sorted() -> None:
    present = smoke.EXPECTED_TOOLS - {"grep", "get_why"}
    assert smoke.find_missing_tools(present) == ["get_why", "grep"]
    assert smoke.find_missing_tools([*smoke.EXPECTED_TOOLS, "extra"]) == []


def test_extract_text_keeps_only_text_blocks() -> None:
    blocks = [
        SimpleNamespace(type="text", text="a"),
        SimpleNamespace(type="image", data="x"),
        SimpleNamespace(type="text", text="b"),
    ]
    assert smoke.extract_text(blocks) == "a\nb"


@pytest.mark.parametrize("text", ["", "   \n", "no matching symbol here"])
def test_check_search_text_rejects_empty_or_unrelated(text: str) -> None:
    with pytest.raises(smoke.SmokeFailure):
        smoke.check_search_text(text)


def test_check_search_text_accepts_a_hit() -> None:
    smoke.check_search_text("numbers_demo.Fibonacci — Return the n-th Fibonacci number")


def test_root_cause_unwraps_nested_exception_groups() -> None:
    inner = smoke.SmokeFailure("boom")
    wrapped = ExceptionGroup("outer", [ExceptionGroup("inner", [inner])])
    assert smoke.root_cause(wrapped) is inner
    assert smoke.root_cause(inner) is inner


def test_parse_args_defaults_and_flags() -> None:
    assert smoke.parse_args([]).with_agent is False
    assert smoke.parse_args([]).timeout == smoke.DEFAULT_TIMEOUT_S
    parsed = smoke.parse_args(["--with-agent", "--timeout", "5"])
    assert (parsed.with_agent, parsed.timeout) == (True, 5.0)


def test_main_reports_a_clear_failure_when_cli_is_missing(monkeypatch, capsys) -> None:
    def _no_cli() -> str:
        raise smoke.SmokeFailure("pydocs-mcp console script not found")

    monkeypatch.setattr(smoke, "find_pydocs_cli", _no_cli)
    assert smoke.main([]) == 1
    assert "FRESH-INSTALL SMOKE FAILED" in capsys.readouterr().err
