"""ds1000_schema: shared library canonicalization + pinned revisions."""

from __future__ import annotations

from benchmarks.eval.datasets.ds1000_schema import (
    PINNED_DS1000_REVISION,
    PINNED_LIBDOCS_REVISION,
    to_pypi_canonical,
)


def test_title_case_ds1000_values_canonicalize() -> None:
    assert to_pypi_canonical("Sklearn") == "scikit-learn"
    assert to_pypi_canonical("Pytorch") == "torch"
    assert to_pypi_canonical("Tensorflow") == "tensorflow"
    assert to_pypi_canonical("Pandas") == "pandas"


def test_lowercase_doc_id_prefixes_canonicalize() -> None:
    # Deliberate behavior delta vs the oracle's old hand-rolled map: an
    # explicit fixture ``library``/``source`` value of "pytorch" now
    # canonicalizes to "torch" (previously passed through verbatim).
    assert to_pypi_canonical("sklearn") == "scikit-learn"
    assert to_pypi_canonical("pytorch") == "torch"
    assert to_pypi_canonical("numpy") == "numpy"


def test_canonicalization_is_case_and_whitespace_insensitive() -> None:
    assert to_pypi_canonical("SKLEARN") == "scikit-learn"
    assert to_pypi_canonical("  Scikit-Learn  ") == "scikit-learn"
    assert to_pypi_canonical("Unknown-Lib") == "unknown-lib"


def test_pinned_revisions_are_real_strings() -> None:
    assert isinstance(PINNED_DS1000_REVISION, str) and PINNED_DS1000_REVISION
    assert isinstance(PINNED_LIBDOCS_REVISION, str) and PINNED_LIBDOCS_REVISION
