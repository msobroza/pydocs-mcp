"""Pseudo-qrel citation extraction (spec §D14): answers → file-level labels."""

from benchmarks.eval.datasets._citations import extract_path_citations, resolve_bare_filenames

_PRO_ANSWER = (
    "First, it calls the parent constructor super().__init__() to inherit QAOA's "
    "initialization (src/qibo/models/variational.py: line 583-590). It also overrides "
    "the minimize method (src/qibo/models/variational.py: lines 601-640)."
)
_SWEQA_ANSWER = (
    "Implementation details (from `backend_pgf.py` lines 240-252):\n"
    "1. **Character-by-character reading**: reads from `self.latex.stdout`."
)


def test_extracts_relative_paths_with_line_ranges() -> None:
    cites = extract_path_citations(_PRO_ANSWER)
    assert ("src/qibo/models/variational.py", 583, 590) in cites
    assert ("src/qibo/models/variational.py", 601, 640) in cites


def test_extracts_bare_filenames_and_backticked_paths() -> None:
    cites = extract_path_citations(_SWEQA_ANSWER)
    assert ("backend_pgf.py", 240, 252) in cites


def test_dedupes_paths_keeps_first_range() -> None:
    cites = extract_path_citations(_PRO_ANSWER + " " + _PRO_ANSWER)
    assert len([c for c in cites if c[0] == "src/qibo/models/variational.py"]) == 2


def test_no_citation_returns_empty() -> None:
    assert extract_path_citations("Pure prose answer with no files.") == ()


def test_dotted_module_names_are_not_citations() -> None:
    """Backtracking must not carve 'matplotlib.py' out of 'matplotlib.pyplot' —
    dotted module references are prose, not file citations; false cites either
    resolve to a WRONG repo file or dilute the gold label set."""
    assert extract_path_citations("Use matplotlib.pyplot to draw the figure.") == ()
    assert extract_path_citations("scipy.pytools has helpers for this.") == ()


def test_pyx_and_pyc_filenames_are_not_citations() -> None:
    """Cython sources and bytecode files are not indexable .py files — the
    regex must not truncate '_speedups.pyx' / 'mod.pyc' to a phantom .py."""
    assert extract_path_citations("The hot loop lives in _speedups.pyx now.") == ()
    assert extract_path_citations("Delete the stale mod.pyc and retry.") == ()


def test_dotted_module_in_prose_does_not_mask_real_citation() -> None:
    """A real citation elsewhere in the same answer must survive the
    dotted-module exclusion."""
    cites = extract_path_citations(
        "matplotlib.pyplot delegates to `lib/matplotlib/pyplot.py` lines 10-20."
    )
    assert cites == (("lib/matplotlib/pyplot.py", 10, 20),)


def test_resolve_bare_filenames_by_unique_basename() -> None:
    tree = ("lib/matplotlib/backends/backend_pgf.py", "lib/matplotlib/pyplot.py")
    resolved, dropped = resolve_bare_filenames((("backend_pgf.py", 240, 252),), tree)
    assert resolved == (("lib/matplotlib/backends/backend_pgf.py", 240, 252),)
    assert dropped == ()


def test_ambiguous_basename_is_dropped_and_reported() -> None:
    tree = ("a/util.py", "b/util.py")
    resolved, dropped = resolve_bare_filenames((("util.py", 1, 2),), tree)
    assert resolved == () and dropped == ("util.py",)
