"""Every spelling an answer may use for a gold site: the file's and the symbol's.

``needle cited`` is an exact match, so the gold side is expanded into the forms
an answer really writes: the path with or without its source prefix and its
trailing parts, the module the path is, the bare and qualified symbol.
"""

from __future__ import annotations

from pydocs_eval.judge.needle_citation import NeedleSite, gold_aliases


def test_a_python_site_is_known_by_its_path_its_module_and_its_symbol() -> None:
    aliases = gold_aliases(NeedleSite("src/needle/scoring/strategies.py", "MaxSimScorer"))

    assert aliases.paths == {
        "src/needle/scoring/strategies.py",
        "needle/scoring/strategies.py",
        "scoring/strategies.py",
        "strategies.py",
    }
    assert aliases.names == {
        "MaxSimScorer",
        "needle.scoring.strategies",
        "needle.scoring.strategies.MaxSimScorer",
    }


def test_a_qualified_symbol_is_also_known_by_its_bare_name() -> None:
    aliases = gold_aliases(NeedleSite("pkg/mod.py", "Response.json"))

    assert {"Response.json", "json", "pkg.mod.Response.json", "pkg.mod.json"} <= aliases.names


def test_a_package_init_is_its_package_and_never_a_bare_init_file() -> None:
    aliases = gold_aliases(NeedleSite("src/needle/__init__.py", "__getattr__"))

    assert "needle" in aliases.names
    assert "needle.__getattr__" in aliases.names
    assert "__init__.py" not in aliases.paths, "every package has one"
    assert "needle/__init__.py" in aliases.paths


def test_a_text_file_has_no_module() -> None:
    aliases = gold_aliases(NeedleSite("docs/filters.md", "matches_filter"))

    assert aliases.paths == {"docs/filters.md", "filters.md"}
    assert aliases.names == {"matches_filter"}


def test_a_root_file_is_known_by_its_name() -> None:
    assert gold_aliases(NeedleSite("README.md")).paths == {"README.md"}


def test_a_site_without_a_symbol_is_known_by_its_file_alone() -> None:
    aliases = gold_aliases(NeedleSite("lib/matplotlib/backends/backend_pgf.py"))

    assert aliases.names == {"lib.matplotlib.backends.backend_pgf"}
    assert "backends/backend_pgf.py" in aliases.paths
