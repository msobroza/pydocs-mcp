"""Pin ``materialize_corpus``: write a ``{relative_path: source}`` mapping
into a fresh temporary directory and return that directory.

The runner relies on this helper to lay out one RepoQA long-context window
per task on disk so the standard ``ProjectIndexer.index_project(<tmp>)``
can run on a realistic project layout (spec §4.8). Each call must produce
an *isolated* directory — the runner does ``shutil.rmtree`` between tasks,
so two consecutive calls must not collide on the same path.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.datasets.corpus import materialize_corpus


def test_materialize_writes_files(tmp_path: Path) -> None:
    base = materialize_corpus(
        {"pkg/__init__.py": "", "pkg/mod.py": "x = 1\n"},
        parent=tmp_path,
    )
    assert (base / "pkg" / "__init__.py").read_text() == ""
    assert (base / "pkg" / "mod.py").read_text() == "x = 1\n"


def test_materialize_creates_nested_paths(tmp_path: Path) -> None:
    # WHY: RepoQA tasks ship multi-level package layouts (e.g.
    # ``pkg/subpkg/leaf.py``). The helper must mkdir intermediate dirs;
    # otherwise the write_text call would raise FileNotFoundError.
    base = materialize_corpus(
        {"a/b/c/leaf.py": "VALUE = 42\n"},
        parent=tmp_path,
    )
    leaf = base / "a" / "b" / "c" / "leaf.py"
    assert leaf.exists()
    assert leaf.read_text() == "VALUE = 42\n"


def test_returned_path_is_under_parent(tmp_path: Path) -> None:
    base = materialize_corpus({"x.py": "y = 1\n"}, parent=tmp_path)
    # WHY: tests run in parallel CI lanes; isolating the corpus under
    # ``tmp_path`` (per-test pytest fixture) keeps leaks scoped.
    assert tmp_path in base.parents
    assert base.name.startswith("repoqa_")


def test_returns_unique_dirs_on_repeated_calls(tmp_path: Path) -> None:
    # WHY: the runner materializes one corpus per task in a loop. Two
    # tasks must never share an on-disk dir or the second mkdir would
    # raise FileExistsError mid-iteration.
    a = materialize_corpus({"f.py": "1\n"}, parent=tmp_path)
    b = materialize_corpus({"f.py": "2\n"}, parent=tmp_path)
    assert a != b
    assert (a / "f.py").read_text() == "1\n"
    assert (b / "f.py").read_text() == "2\n"


def test_empty_mapping_still_returns_a_dir(tmp_path: Path) -> None:
    # WHY: degenerate corpus (no source files) is still a valid corpus dir
    # — the indexer just sees zero modules. Don't make the helper assert
    # non-emptiness; that's the caller's policy.
    base = materialize_corpus({}, parent=tmp_path)
    assert base.is_dir()
    assert tmp_path in base.parents
