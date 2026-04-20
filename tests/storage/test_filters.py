"""Tests for Filter tree + MultiFieldFormat + MetadataSchema (spec §5.1, AC #4/#5/#6)."""
from __future__ import annotations

import pytest

from pydocs_mcp.storage.filters import (
    All,
    Any_,
    FieldEq,
    FieldIn,
    FieldLike,
    FieldSpec,
    MetadataFilterFormat,
    MetadataSchema,
    MultiFieldFormat,
    Not,
    format_registry,
)


def test_filter_dataclasses_frozen_slots():
    f = FieldEq(field="package", value="fastapi")
    assert f.field == "package"
    assert f.value == "fastapi"
    with pytest.raises(Exception):
        f.field = "x"  # frozen


def test_multifield_format_parse_bare_values_to_eq():
    fmt = MultiFieldFormat()
    tree = fmt.parse({"package": "fastapi", "origin": "dependency_doc_file"})
    assert isinstance(tree, All)
    fields = {c.field: c for c in tree.clauses}
    assert isinstance(fields["package"], FieldEq)
    assert fields["package"].value == "fastapi"


def test_multifield_format_parse_op_dict():
    fmt = MultiFieldFormat()
    tree = fmt.parse({"title": {"like": "routing"}, "package": {"eq": "fastapi"}})
    assert isinstance(tree, All)
    fields = {c.field: c for c in tree.clauses}
    assert isinstance(fields["title"], FieldLike)
    assert fields["title"].substring == "routing"


def test_multifield_format_parse_in_op():
    fmt = MultiFieldFormat()
    tree = fmt.parse({"scope": {"in": ["project_only", "all"]}})
    c = tree.clauses[0]
    assert isinstance(c, FieldIn)
    assert c.values == ("project_only", "all")


def test_multifield_format_rejects_boolean_ops():
    fmt = MultiFieldFormat()
    with pytest.raises(ValueError, match=r"\$and|filter_tree"):
        fmt.validate({"$and": [{"package": "fastapi"}]})


def test_multifield_format_rejects_unknown_operator():
    fmt = MultiFieldFormat()
    with pytest.raises(ValueError, match="unknown operator"):
        fmt.validate({"package": {"regex": ".*"}})


def test_multifield_rejects_multi_op_value():
    """An op-mapping with two operators silently drops one on parse —
    reject it during validation so callers see the typo immediately."""
    fmt = MultiFieldFormat()
    with pytest.raises(ValueError, match="exactly one operator"):
        fmt.validate({"package": {"eq": "x", "like": "y"}})


def test_multifield_rejects_empty_op_mapping():
    """An empty op-mapping would crash ``parse`` at ``next(iter(...))``."""
    fmt = MultiFieldFormat()
    with pytest.raises(ValueError, match="empty op-mapping"):
        fmt.validate({"package": {}})


def test_multifield_rejects_scalar_in_op():
    """``{"in": "abc"}`` would iterate the string as characters —
    force callers to pass a list/tuple explicitly."""
    fmt = MultiFieldFormat()
    with pytest.raises(ValueError, match="requires a list or tuple"):
        fmt.validate({"package": {"in": "abc"}})


def test_multifield_format_rejects_non_mapping():
    fmt = MultiFieldFormat()
    with pytest.raises(ValueError, match="mapping"):
        fmt.validate([{"package": "fastapi"}])


def test_metadata_schema_validate_rejects_unknown_field():
    schema = MetadataSchema(
        fields=(FieldSpec(name="package"), FieldSpec(name="origin")),
    )
    ok = All(clauses=(FieldEq(field="package", value="x"),))
    bad = All(clauses=(FieldEq(field="language", value="python"),))
    schema.validate(ok)  # no raise
    with pytest.raises(ValueError, match="language"):
        schema.validate(bad)


def test_format_registry_has_multifield():
    assert MetadataFilterFormat.MULTIFIELD in format_registry
    fmt = format_registry[MetadataFilterFormat.MULTIFIELD]
    assert fmt.format is MetadataFilterFormat.MULTIFIELD


def test_all_filter_composition():
    tree = All(clauses=(
        FieldEq(field="package", value="fastapi"),
        FieldLike(field="title", substring="route"),
    ))
    assert len(tree.clauses) == 2


def test_future_classes_exist():
    # Any_ and Not exist for future FilterTreeFormat, unused in MultiFieldFormat
    assert Any_(clauses=()).clauses == ()
    assert Not(clause=FieldEq(field="x", value="y")).clause is not None
