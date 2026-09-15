"""The ``output.pointers`` config block — the pointer table and its batch bounds.

Sibling of ``tests/test_config_output_block.py``: same load-a-YAML-overlay seam,
scoped to the table issue #269 Track T1 introduces.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pydocs_mcp.pointer_table import (
    PointerAction,
    PointerTableConfig,
    PointerTableRow,
    ResponseKind,
    pointer_action,
    pointer_action_names,
    register_pointer_action,
    shipped_pointer_rows,
)
from pydocs_mcp.retrieval.config import AppConfig


def _overlay(tmp_path: Path, body: str) -> AppConfig:
    path = tmp_path / "pydocs-mcp.yaml"
    path.write_text(body)
    return AppConfig.load(explicit_path=path)


# ── defaults ───────────────────────────────────────────────────────────────


def test_pointer_table_defaults_present() -> None:
    pointers = AppConfig.load().output.pointers
    assert pointers.batch_threshold == 3
    assert pointers.batch_max == 8
    assert pointers.read_window == 40


def test_shipped_yaml_table_equals_the_python_default_rows() -> None:
    """``default_config.yaml`` restates the rows; the two must not drift."""
    assert AppConfig.load().output.pointers.table == shipped_pointer_rows()


def test_every_response_kind_has_a_row() -> None:
    assert set(AppConfig.load().output.pointers.table) == set(ResponseKind)


def test_read_window_is_tunable_per_deployment(tmp_path: Path) -> None:
    config = _overlay(tmp_path, "output:\n  pointers:\n    read_window: 12\n")
    assert config.output.pointers.read_window == 12


def test_shipped_rows_name_only_registered_actions() -> None:
    known = set(pointer_action_names())
    named = {name for row in shipped_pointer_rows().values() for name in row.together + row.then}
    assert named <= known


# ── loading a non-default table ────────────────────────────────────────────


def test_row_overlay_replaces_one_row_and_keeps_the_rest(tmp_path: Path) -> None:
    config = _overlay(
        tmp_path,
        "output:\n"
        "  pointers:\n"
        "    batch_threshold: 2\n"
        "    batch_max: 4\n"
        "    table:\n"
        "      overview_module:\n"
        "        together: [outline, callers]\n"
        "        then: [source]\n",
    )
    pointers = config.output.pointers
    assert pointers.batch_threshold == 2
    assert pointers.batch_max == 4
    assert pointers.table[ResponseKind.OVERVIEW_MODULE] == PointerTableRow(
        together=("outline", "callers"), then=("source",)
    )
    # Deep-merged: the rows the overlay did not name keep their shipped values.
    assert pointers.table[ResponseKind.DECISION] == shipped_pointer_rows()[ResponseKind.DECISION]


# ── malformed tables fail at startup, naming the offending row ─────────────


def test_unknown_response_kind_row_is_rejected_by_name(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as exc:
        _overlay(
            tmp_path,
            "output:\n  pointers:\n    table:\n      symbol_kard:\n        together: [source]\n",
        )
    message = str(exc.value)
    assert "symbol_kard" in message
    assert "is not a response kind" in message
    assert "search_hit_code" in message  # the expected shape is enumerated


def test_unknown_action_in_a_row_is_rejected_naming_row_and_group(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as exc:
        _overlay(
            tmp_path,
            "output:\n  pointers:\n    table:\n      decision:\n        then: [teleport]\n",
        )
    message = str(exc.value)
    assert "'decision'" in message
    assert "'then'" in message
    assert "teleport" in message
    assert "callers" in message  # the expected vocabulary is enumerated


def test_batch_threshold_above_max_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError) as exc:
        _overlay(
            tmp_path,
            "output:\n  pointers:\n    batch_threshold: 9\n    batch_max: 4\n",
        )
    assert "batch_threshold=9" in str(exc.value)


def test_typo_key_in_the_pointers_block_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _overlay(tmp_path, "output:\n  pointers: { bundles_enabld: true }\n")


def test_typo_key_in_a_row_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _overlay(
            tmp_path,
            "output:\n  pointers:\n    table:\n      decision:\n        togethur: [symbol]\n",
        )


# ── the table is the only source ───────────────────────────────────────────


def test_row_for_returns_the_shipped_row() -> None:
    assert PointerTableConfig().row_for(ResponseKind.OVERVIEW_MODULE) == PointerTableRow(
        together=("outline",)
    )


def test_row_for_an_unlisted_kind_is_empty_not_a_keyerror() -> None:
    """A partial table must never raise mid-response."""
    partial = PointerTableConfig(table={})
    assert partial.row_for(ResponseKind.DECISION) == PointerTableRow()


def test_the_removed_migration_gate_is_rejected_by_name(tmp_path: Path) -> None:
    """A deployment that still sets the staged rollout's flag is told what
    replaced it, not merely that the key is unknown."""
    with pytest.raises(ValidationError) as exc:
        _overlay(tmp_path, "output:\n  pointers:\n    bundles_enabled: false\n")
    message = str(exc.value)
    assert "bundles_enabled" in message
    assert "was removed" in message
    assert "output.pointers.table" in message


# ── the action vocabulary is extensible (issue #277 adds ``read``) ─────────


def test_action_registry_carries_the_grammar_coordinates() -> None:
    assert pointer_action("outline") == PointerAction("outline", "lookup-show", "tree")
    assert pointer_action("symbol") == PointerAction("symbol", "lookup")


def test_unknown_action_lookup_names_the_value_and_the_vocabulary() -> None:
    with pytest.raises(KeyError) as exc:
        pointer_action("teleport")
    assert "teleport" in str(exc.value)
    assert "outline" in str(exc.value)


def test_registering_a_duplicate_action_name_raises() -> None:
    with pytest.raises(ValueError, match="already registered"):
        register_pointer_action(PointerAction("symbol", "lookup"))


# ── the batch bounds ───────────────────────────────────────────────────────


def test_only_the_context_verb_carries_several_targets_at_once() -> None:
    """``get_context`` is the one tool in the frozen surface that takes a target
    list, so it is the one action a fan-out can consolidate into."""
    assert pointer_action("context").batch is True
    assert [name for name in pointer_action_names() if pointer_action(name).batch] == ["context"]


def test_a_fan_out_consolidates_at_the_threshold_and_not_below_it() -> None:
    pointers = PointerTableConfig()
    assert pointers.consolidates(pointers.batch_threshold) is True
    assert pointers.consolidates(pointers.batch_threshold - 1) is False


def test_a_batch_call_names_at_most_the_maximum() -> None:
    pointers = PointerTableConfig(batch_max=4)
    assert pointers.batch_targets(tuple(f"pkg.f{i}" for i in range(10))) == (
        "pkg.f0",
        "pkg.f1",
        "pkg.f2",
        "pkg.f3",
    )


def test_a_batch_call_under_the_maximum_names_every_target() -> None:
    assert PointerTableConfig().batch_targets(("pkg.a", "pkg.b")) == ("pkg.a", "pkg.b")
