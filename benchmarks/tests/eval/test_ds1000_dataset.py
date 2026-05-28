"""DS-1000 (CodeRAG-Bench flavor) dataset loader tests.

Mirrors the ``test_repoqa_loader.py`` shape: hermetic fixture-driven by
default (no HF network calls inside ``pytest``), exercising the loader's
contract — row count, query-stripping, gold shape (both ``doc_ids`` and
``doc_contents``), library / perturbation filters, and library-name
normalization to PyPI-canonical lowercase.
"""

from __future__ import annotations

from pathlib import Path

from benchmarks.eval.datasets.base_dataset import Dataset
from benchmarks.eval.datasets.ds1000 import (
    _PINNED_DS1000_REVISION,
    _PINNED_LIBDOCS_REVISION,
    Ds1000Dataset,
)
from benchmarks.eval.serialization import dataset_registry

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ds1000_mini.json"


async def test_dataset_yields_eight_rows_from_mini_fixture() -> None:
    """The hand-crafted mini fixture ships 8 rows
    (3 pandas + 2 numpy + 1 matplotlib + 1 sklearn + 1 scipy) → 8 EvalTasks."""
    dataset = Ds1000Dataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    assert len(tasks) == 8


async def test_query_stripping_removes_solution_blocks() -> None:
    """The raw DS-1000 ``prompt`` field embeds the canonical solution after
    ``A:``. The loader drops everything from ``A:`` onward and strips
    ``<code>`` / ``</code>`` / ``BEGIN SOLUTION`` / ``END SOLUTION`` markers
    from the remaining NL question. The full raw prompt is preserved in
    ``task.metadata["_raw_query"]`` for diagnostics."""
    dataset = Ds1000Dataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    # The first fixture row carries an "A: <code>...</code>" canonical-solution
    # block — the stripper must drop it from `.query` but preserve it in
    # `_raw_query`.
    task = tasks[0]
    assert "A:" not in task.query
    assert "<code>" not in task.query
    assert "</code>" not in task.query
    assert "BEGIN SOLUTION" not in task.query
    assert "END SOLUTION" not in task.query
    raw = task.metadata["_raw_query"]
    assert "A:" in raw  # The raw is preserved verbatim with the solution block.


async def test_query_stripping_preserves_in_body_answer_label() -> None:
    """REGRESSION: DS-1000's answer delimiter is a LINE-LEADING ``A:``, not a
    bare ``A:`` substring. Real question bodies reference in-body labels like
    ``"DataFrame A:"`` — splitting on the first bare ``A:`` would amputate the
    question. The merge fixture row's NL body contains ``"DataFrame A:"``; the
    stripped query must STILL contain that in-body reference (question intact)
    while STILL excluding the ``<code>`` answer block."""
    dataset = Ds1000Dataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    # The merge row (perturbation_origin_id=5) carries an in-body "DataFrame A:"
    # label in its question, plus a trailing line-leading "A:\n<code>" answer.
    merge_tasks = [t for t in tasks if t.metadata["perturbation_origin_id"] == "5"]
    assert merge_tasks, "fixture must include the merge row (origin_id=5)"
    merge_task = merge_tasks[0]
    # The in-body label must survive — the question was not truncated.
    assert "DataFrame A:" in merge_task.query
    # ...but the canonical-solution block is still gone.
    assert "<code>" not in merge_task.query
    assert "pd.merge" not in merge_task.query


async def test_library_filter_slices_rows() -> None:
    """``library_filter`` accepts PyPI-canonical lowercase names (the same
    form used by ``task.metadata["library"]``). The mini fixture has 3
    pandas rows; filtering to ``("pandas",)`` yields exactly 3 tasks."""
    dataset = Ds1000Dataset(fixture_path=FIXTURE_PATH, library_filter=("pandas",))
    tasks = [t async for t in dataset.tasks()]
    assert len(tasks) == 3
    assert all(t.metadata["library"] == "pandas" for t in tasks)


async def test_library_filter_normalizes_title_case_input() -> None:
    """REGRESSION: ``library_filter`` values are normalized through the same
    ``_normalize_library`` canonicalization as the row library before
    comparing, so the casing a user sees in DS-1000 (title-case ``"Pandas"``)
    matches. ``("Pandas",)``, ``("pandas",)``, and ``("PANDAS",)`` must all
    select exactly the 3 pandas rows, each tagged with the normalized
    lowercase form."""
    for filter_value in (("Pandas",), ("pandas",), ("PANDAS",)):
        dataset = Ds1000Dataset(
            fixture_path=FIXTURE_PATH,
            library_filter=filter_value,
        )
        tasks = [t async for t in dataset.tasks()]
        assert len(tasks) == 3, f"{filter_value!r} should match 3 pandas rows"
        assert all(t.metadata["library"] == "pandas" for t in tasks)


async def test_library_filter_normalizes_alias_input() -> None:
    """``library_filter`` accepts DS-1000 title-case aliases AND their
    PyPI-canonical form interchangeably. ``("Sklearn",)`` and
    ``("scikit-learn",)`` both normalize to ``scikit-learn`` and select the
    one sklearn fixture row."""
    for filter_value in (("Sklearn",), ("scikit-learn",)):
        dataset = Ds1000Dataset(
            fixture_path=FIXTURE_PATH,
            library_filter=filter_value,
        )
        tasks = [t async for t in dataset.tasks()]
        assert len(tasks) == 1, f"{filter_value!r} should match the sklearn row"
        assert all(t.metadata["library"] == "scikit-learn" for t in tasks)


async def test_library_filter_mixed_case_multi_element() -> None:
    """A mixed-case multi-element filter normalizes every element before
    comparing. ``("Pandas", "NUMPY")`` selects the union of the 3 pandas +
    2 numpy rows (5 total), each tagged with the normalized lowercase form."""
    dataset = Ds1000Dataset(
        fixture_path=FIXTURE_PATH,
        library_filter=("Pandas", "NUMPY"),
    )
    tasks = [t async for t in dataset.tasks()]
    assert len(tasks) == 5
    assert all(t.metadata["library"] in {"pandas", "numpy"} for t in tasks)


async def test_library_filter_multi_element_selects_union_of_libraries() -> None:
    """``library_filter`` with multiple PyPI-canonical names returns the
    union of matching rows. The mini fixture has 3 pandas + 2 numpy rows;
    filtering to ``("pandas", "numpy")`` yields exactly 5 tasks, each
    tagged with one of the two requested libraries (guards against
    off-by-one in the filter loop)."""
    dataset = Ds1000Dataset(
        fixture_path=FIXTURE_PATH,
        library_filter=("pandas", "numpy"),
    )
    tasks = [t async for t in dataset.tasks()]
    assert len(tasks) == 5
    assert all(t.metadata["library"] in {"pandas", "numpy"} for t in tasks)


async def test_perturbation_filter_slices_rows() -> None:
    """``perturbation_filter`` selects rows by perturbation bucket. The mini
    fixture has rows tagged ``"Origin"`` and ``"Surface"``; filtering to
    ``("Origin",)`` returns the Origin subset."""
    dataset_all = Ds1000Dataset(fixture_path=FIXTURE_PATH)
    all_tasks = [t async for t in dataset_all.tasks()]
    origin_count = sum(1 for t in all_tasks if t.metadata.get("perturbation_type") == "Origin")
    assert origin_count > 0  # Sanity-check that the fixture exercises the filter.

    dataset_origin = Ds1000Dataset(
        fixture_path=FIXTURE_PATH,
        perturbation_filter=("Origin",),
    )
    origin_tasks = [t async for t in dataset_origin.tasks()]
    assert len(origin_tasks) == origin_count
    assert all(t.metadata.get("perturbation_type") == "Origin" for t in origin_tasks)


async def test_gold_has_doc_ids_and_doc_contents_aligned() -> None:
    """The gold ships BOTH ``doc_ids`` (verbatim from DS-1000 ``docs[*].doc_id``,
    used by oracle-indexing's exact-match resolver) AND ``doc_contents`` (verbatim
    from ``docs[*].doc_content``, used by fuzzy resolvers). They MUST be aligned
    1:1 — same length, same order."""
    dataset = Ds1000Dataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    # At least one fixture row has multi-doc gold (>1 entry under "docs").
    multi_doc = [
        t
        for t in tasks
        if len(t.gold.extra["doc_ids"]) > 1  # type: ignore[index]
    ]
    assert multi_doc, "fixture must include >=1 row with multi-doc gold"
    for t in multi_doc:
        doc_ids = t.gold.extra["doc_ids"]
        doc_contents = t.gold.extra["doc_contents"]
        assert isinstance(doc_ids, tuple)
        assert isinstance(doc_contents, tuple)
        assert len(doc_ids) == len(doc_contents) > 0


async def test_library_name_normalized_to_lowercase() -> None:
    """DS-1000's ``library`` field is title-case (``"Pandas"``, ``"Pytorch"``);
    pydocs / PyPI use lowercase canonical names. ``task.metadata["library"]``
    must hold the PyPI-canonical form (``"pandas"``, ``"torch"`` — note the
    PyTorch→torch remap)."""
    dataset = Ds1000Dataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    # The fixture has a "Pandas" row.
    pandas_tasks = [t for t in tasks if t.metadata["library"] == "pandas"]
    assert pandas_tasks, "expected at least one normalized 'pandas' task"
    # No task should leak the title-case form.
    for t in tasks:
        assert t.metadata["library"] == t.metadata["library"].lower(), (
            f"library must be lowercase; got {t.metadata['library']!r}"
        )
    # PyTorch normalization: a "Pytorch" raw field → "torch" (PyPI canonical).
    # The fixture may not include a pytorch row (count constraint: 3+2+1+1+1=8),
    # so verify the normalization map directly.
    from benchmarks.eval.datasets.ds1000 import _LIBRARY_NORMALIZATION

    assert _LIBRARY_NORMALIZATION["Pytorch"] == "torch"
    assert _LIBRARY_NORMALIZATION["Pandas"] == "pandas"
    assert _LIBRARY_NORMALIZATION["Scikit-learn"] == "scikit-learn"


# --- Auxiliary protocol-level / registry-level smoke tests ------------------


async def test_dataset_satisfies_protocol() -> None:
    """The concrete loader implements the ``Dataset`` Protocol (so the runner
    can look it up by name and iterate ``async for``)."""
    dataset = Ds1000Dataset(fixture_path=FIXTURE_PATH)
    assert isinstance(dataset, Dataset)


def test_dataset_is_registered_under_ds1000() -> None:
    """Importing ``benchmarks.eval.datasets`` fires the registry decorator —
    the runner looks the loader up by name (``"ds1000"``)."""
    # Force-import the package so the decorator fires.
    import benchmarks.eval.datasets

    assert "ds1000" in dataset_registry.names()


def test_pinned_revisions_are_real_strings() -> None:
    """Both HF revision pins must be non-empty strings — they're passed
    through to ``datasets.load_dataset(revision=...)`` in full runs."""
    assert isinstance(_PINNED_DS1000_REVISION, str)
    assert isinstance(_PINNED_LIBDOCS_REVISION, str)
    assert _PINNED_DS1000_REVISION  # non-empty
    assert _PINNED_LIBDOCS_REVISION  # non-empty
