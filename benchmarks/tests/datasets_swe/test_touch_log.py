"""R3 append-only frozen-test touch log (ADR 0013 deliverable 5)."""

from __future__ import annotations

import pytest

from pydocs_eval.datasets_swe import touch_log


def test_append_then_read_roundtrips(tmp_path):
    log = tmp_path / "pro-touch-log.jsonl"
    entry = touch_log.read_only_entry({"pin": "abc"}, justification="manifest read")
    touch_log.append_entry(log, entry)
    entries = touch_log.read_entries(log)
    assert len(entries) == 1
    assert entries[0].access_type == touch_log.READ_ONLY_MANIFEST
    assert entries[0].instances_touched == 0


def test_append_is_additive_not_overwriting(tmp_path):
    log = tmp_path / "pro-touch-log.jsonl"
    touch_log.append_entry(log, touch_log.read_only_entry({"a": 1}, justification="first"))
    touch_log.append_entry(log, touch_log.read_only_entry({"a": 2}, justification="second"))
    assert len(touch_log.read_entries(log)) == 2


def test_invalid_access_type_raises():
    with pytest.raises(ValueError, match="invalid access_type"):
        touch_log.TouchLogEntry(
            timestamp="t",
            config_hash="h",
            access_type="bogus",
            justification="x",
            instances_touched=0,
        )


def test_config_hash_is_stable_and_order_independent():
    a = touch_log.config_hash({"x": 1, "y": 2})
    b = touch_log.config_hash({"y": 2, "x": 1})
    assert a == b


def test_read_missing_log_is_empty(tmp_path):
    assert touch_log.read_entries(tmp_path / "absent.jsonl") == []
