"""Tests for the SQLite filter-adapter classes (spec §5.3 AC #7, §C5).

Two public surfaces:

* :class:`pydocs_mcp.storage.sqlite._SqliteFilterTranslator` — internal
  per-table translator used by every concrete repository
  (``SqlitePackageRepository``, ``SqliteChunkRepository``, etc.). One
  instance per table; the ``safe_columns`` whitelist and
  ``column_prefix`` are bound at construction.
* :class:`pydocs_mcp.storage.sqlite.SqliteFilterAdapter` — public
  :class:`pydocs_mcp.storage.protocols.FilterAdapter`-conforming wrapper
  used by retrieval-time steps (``PreFilterStep`` and, post-C5 commit 2,
  the fetchers). Stores BOTH chunk and member configs and dispatches on
  the ``target_field`` kwarg.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.storage.filters import All, Any_, FieldEq, FieldIn, FieldLike, Not
from pydocs_mcp.storage.sqlite import (
    CHUNK_COLUMNS,
    SqliteFilterAdapter,
    _SqliteFilterTranslator,
)


# ── _SqliteFilterTranslator (internal per-table) ─────────────────────────


def test_translator_field_eq():
    translator = _SqliteFilterTranslator(safe_columns=frozenset({"package", "origin"}))
    where, params = translator.adapt(FieldEq(field="package", value="fastapi"))
    assert where == "package = ?"
    assert params == ["fastapi"]


def test_translator_field_in():
    translator = _SqliteFilterTranslator(safe_columns=frozenset({"scope"}))
    where, params = translator.adapt(FieldIn(field="scope", values=("a", "b")))
    assert where == "scope IN (?, ?)"
    assert params == ["a", "b"]


def test_translator_field_like():
    translator = _SqliteFilterTranslator(safe_columns=frozenset({"title"}))
    where, params = translator.adapt(FieldLike(field="title", substring="routing"))
    assert "title LIKE ?" in where
    assert params == ["%routing%"]


def test_translator_all_joins_with_and():
    translator = _SqliteFilterTranslator(safe_columns=frozenset({"package", "origin"}))
    tree = All(
        clauses=(
            FieldEq(field="package", value="x"),
            FieldEq(field="origin", value="y"),
        )
    )
    where, params = translator.adapt(tree)
    assert "AND" in where
    assert params == ["x", "y"]


def test_translator_rejects_unsafe_column():
    translator = _SqliteFilterTranslator(safe_columns=frozenset({"package"}))
    with pytest.raises(ValueError, match="not in safe_columns"):
        translator.adapt(FieldEq(field="foo_bar; DROP TABLE", value="x"))


def test_translator_escapes_like_metacharacters():
    """Literal ``%`` / ``_`` / backslash in a LIKE substring must not act
    as SQL wildcards — the translator escapes them and emits ``ESCAPE '\\'``.

    Regression: ``my_module`` previously matched ``myXmodule`` because
    ``_`` is a wildcard in ``LIKE``.
    """
    import sqlite3

    translator = _SqliteFilterTranslator(safe_columns=frozenset({"title"}))
    where, params = translator.adapt(FieldLike(field="title", substring="my_module"))
    assert "ESCAPE '\\'" in where
    assert params == ["%my\\_module%"]

    # And the escape actually takes effect inside SQLite.
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t(title TEXT)")
    conn.executemany(
        "INSERT INTO t(title) VALUES(?)",
        [("my_module",), ("myXmodule",)],
    )
    rows = conn.execute(f"SELECT title FROM t WHERE {where}", params).fetchall()
    titles = {r[0] for r in rows}
    assert titles == {"my_module"}
    conn.close()


def test_translator_empty_all_matches_everything():
    """``All(clauses=())`` compiles to ``1 = 1`` — the match-all sentinel."""
    translator = _SqliteFilterTranslator(safe_columns=frozenset({"package"}))
    where, params = translator.adapt(All(clauses=()))
    assert where == "1 = 1"
    assert params == []


def test_translator_emits_column_prefix():
    """``column_prefix`` is prepended verbatim to every column reference.

    Covers the ``chunks_fts JOIN chunks`` case where filters must emit
    ``c.<col>`` to disambiguate the duplicated column names; regression
    for the former ``_walk_with_prefix`` copy-of-_adapt helper.
    """
    translator = _SqliteFilterTranslator(
        safe_columns=frozenset({"package", "title"}),
        column_prefix="c.",
    )
    where, params = translator.adapt(FieldEq(field="package", value="x"))
    assert where == "c.package = ?"
    assert params == ["x"]

    # LIKE escape survives the prefix.
    where, params = translator.adapt(FieldLike(field="title", substring="my_module"))
    assert where == "c.title LIKE ? ESCAPE '\\'"
    assert params == ["%my\\_module%"]

    # IN with prefix.
    where, params = translator.adapt(FieldIn(field="package", values=("a", "b")))
    assert where == "c.package IN (?, ?)"
    assert params == ["a", "b"]

    # Safe-column check still runs on the unprefixed name.
    with pytest.raises(ValueError, match="not in safe_columns"):
        translator.adapt(FieldEq(field="bogus", value="x"))


# ── SqliteFilterAdapter (public, Protocol-conforming) ────────────────────


def test_adapter_dispatch_chunk_uses_chunk_columns_and_prefix():
    """``target_field='chunk'`` emits ``c.<col>`` for the chunks_fts JOIN shape."""
    adapter = SqliteFilterAdapter()
    where, params = adapter.adapt(
        FieldEq(field="package", value="fastapi"),
        target_field="chunk",
    )
    assert where == "c.package = ?"
    assert params == ("fastapi",)


def test_adapter_dispatch_member_uses_bare_member_columns():
    """``target_field='member'`` emits bare column names (no JOIN aliasing)."""
    adapter = SqliteFilterAdapter()
    where, params = adapter.adapt(
        FieldEq(field="kind", value="function"),
        target_field="member",
    )
    assert where == "kind = ?"
    assert params == ("function",)


def test_adapter_chunk_rejects_member_column():
    """Cross-table column references are blocked by the per-target whitelist —
    ``kind`` is a member column, NOT a chunk column.
    """
    adapter = SqliteFilterAdapter()
    with pytest.raises(ValueError, match="not in safe_columns"):
        adapter.adapt(FieldEq(field="kind", value="function"), target_field="chunk")


def test_adapter_member_rejects_chunk_column():
    """``title`` belongs to chunks; member dispatch must reject it."""
    adapter = SqliteFilterAdapter()
    with pytest.raises(ValueError, match="not in safe_columns"):
        adapter.adapt(FieldEq(field="title", value="x"), target_field="member")


def test_adapter_unknown_target_field_raises():
    """A ``target_field`` outside the {chunk, member} literal raises ValueError."""
    adapter = SqliteFilterAdapter()
    with pytest.raises(ValueError, match="target_field"):
        adapter.adapt(FieldEq(field="package", value="x"), target_field="bogus")  # type: ignore[arg-type]


def test_adapter_returns_tuple_params_not_list():
    """Spec C5: the Protocol contract is ``tuple[str, tuple[Any, ...]]``.

    Returning an immutable tuple lets callers cache the result without a
    defensive ``tuple(...)`` copy.
    """
    adapter = SqliteFilterAdapter()
    _where, params = adapter.adapt(
        FieldIn(field="package", values=("a", "b")),
        target_field="chunk",
    )
    assert isinstance(params, tuple)
    assert params == ("a", "b")


def test_adapter_default_chunk_columns_match_module_constant():
    """``SqliteFilterAdapter.chunk_columns`` defaults to the ``CHUNK_COLUMNS`` constant.

    Single source of truth — the constant lives at module scope; the
    dataclass field reuses it as its default rather than re-listing the
    set inline.
    """
    adapter = SqliteFilterAdapter()
    assert adapter.chunk_columns == CHUNK_COLUMNS


# ── Any_ / Not (the #346 dependency-decision exclusion) ──────────────────


def test_translator_any_joins_with_or_inside_its_own_parentheses():
    """Wrapped whole, so a caller's ``MATCH ? AND {where}`` keeps MATCH out of
    the OR (FTS5 refuses MATCH under an OR, and the rows would be wrong)."""
    translator = _SqliteFilterTranslator(safe_columns=frozenset({"package", "origin"}))
    tree = Any_(clauses=(FieldEq(field="package", value="x"), FieldEq(field="origin", value="y")))
    where, params = translator.adapt(tree)
    assert where == "((package = ?) OR (origin = ?))"
    assert params == ["x", "y"]


def test_translator_empty_any_matches_nothing():
    translator = _SqliteFilterTranslator(safe_columns=frozenset({"package"}))
    assert translator.adapt(Any_(clauses=())) == ("(0 = 1)", [])


def test_translator_not_treats_an_unknown_comparison_as_false():
    """``NOT (col = ?)`` is NULL on a NULL column and would drop the row; the
    translation keeps it, as the in-memory ``None != value`` does."""
    translator = _SqliteFilterTranslator(safe_columns=frozenset({"origin"}), column_prefix="c.")
    where, params = translator.adapt(Not(clause=FieldEq(field="origin", value="y")))
    assert where == "NOT IFNULL((c.origin = ?), 0)"
    assert params == ["y"]


def test_dependency_decision_exclusion_keeps_every_row_but_a_dependency_decision():
    """The exclusion ``PreFilterStep`` composes (#346), run in SQLite: only a
    decision record from a package other than the project goes — rows whose
    ``origin`` is NULL stay."""
    import sqlite3

    from pydocs_mcp.retrieval.filter_helpers import DEPENDENCY_DECISION_EXCLUSION

    rows = [
        (1, "dep", "decision_record"),
        (2, "__project__", "decision_record"),
        (3, "dep", "python_def"),
        (4, "dep", None),
        (5, "__project__", None),
    ]
    where, params = _SqliteFilterTranslator(safe_columns=CHUNK_COLUMNS).adapt(
        DEPENDENCY_DECISION_EXCLUSION
    )
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE chunks(id INTEGER, package TEXT, origin TEXT)")
    conn.executemany("INSERT INTO chunks VALUES(?, ?, ?)", rows)
    kept = {r[0] for r in conn.execute(f"SELECT id FROM chunks WHERE {where}", params)}
    conn.close()
    assert kept == {2, 3, 4, 5}
