"""Extended tests for deps.py — covers regex fallback and exception paths."""
import os
from unittest.mock import patch

import pytest

from pydocs_mcp.deps import _parse_toml, _parse_requirements, normalize, resolve


class TestParseTomlRegexFallback:
    """Tests for the regex fallback path when tomllib is not available."""

    def test_regex_fallback_parses_deps(self, tmp_path):
        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text(
            '[project]\n'
            'name = "myproject"\n'
            'dependencies = [\n'
            '    "requests>=2.0",\n'
            '    "click",\n'
            ']\n'
        )
        # Force the regex fallback by making tomllib import fail
        with patch.dict("sys.modules", {"tomllib": None}):
            with patch("builtins.__import__", side_effect=_import_without_tomllib):
                result = _parse_toml(str(toml_file))
        assert "requests" in result
        assert "click" in result

    def test_regex_fallback_no_dependencies(self, tmp_path):
        toml_file = tmp_path / "pyproject.toml"
        toml_file.write_text(
            '[project]\nname = "myproject"\n'
        )
        with patch.dict("sys.modules", {"tomllib": None}):
            with patch("builtins.__import__", side_effect=_import_without_tomllib):
                result = _parse_toml(str(toml_file))
        assert result == []

    def test_regex_fallback_file_read_error(self):
        with patch.dict("sys.modules", {"tomllib": None}):
            with patch("builtins.__import__", side_effect=_import_without_tomllib):
                result = _parse_toml("/nonexistent/path/pyproject.toml")
        assert result == []


class TestParseRequirementsEdge:
    def test_handles_file_not_found(self):
        result = _parse_requirements("/nonexistent/requirements.txt")
        assert result == []

    def test_skips_flag_lines(self, tmp_path):
        req_file = tmp_path / "requirements.txt"
        req_file.write_text(
            "-r base.txt\n"
            "-c constraints.txt\n"
            "-e ./local-pkg\n"
            "requests\n"
        )
        result = _parse_requirements(str(req_file))
        assert result == ["requests"]

    def test_handles_empty_file(self, tmp_path):
        req_file = tmp_path / "requirements.txt"
        req_file.write_text("")
        result = _parse_requirements(str(req_file))
        assert result == []


class TestNormalizeEdge:
    def test_strips_extras(self):
        assert normalize("package[extra1,extra2]") == "package"

    def test_strips_version_specifiers(self):
        assert normalize("package>=1.0,<2.0") == "package"

    def test_strips_semicolons(self):
        assert normalize("package; python_version>='3.8'") == "package"


def _import_without_tomllib(name, *args, **kwargs):
    """Raise ImportError only for tomllib, let everything else through."""
    if name == "tomllib":
        raise ImportError("No module named 'tomllib'")
    return original_import(name, *args, **kwargs)


import builtins
original_import = builtins.__import__
