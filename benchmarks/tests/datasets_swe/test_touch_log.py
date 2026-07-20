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


# --- Phase 4 pin: authorized rollouts admitted, unauthorized ones fail (ADR 0020) ---


def _authorized_rollout(config, justification="owner-authorized seed+one sweep"):
    return touch_log.rollout_entry(config, justification=justification, instances_touched=266)


def test_authorized_rollout_is_admitted():
    cfg = {"campaign_id": "frozen-seed"}
    entry = _authorized_rollout(cfg)
    authorized = frozenset({touch_log.config_hash(cfg)})
    assert touch_log.unauthorized_rollouts([entry], authorized) == ()


def test_rollout_under_unknown_config_is_flagged():
    entry = _authorized_rollout({"campaign_id": "rogue"})
    known = frozenset({touch_log.config_hash({"campaign_id": "frozen-seed"})})
    assert touch_log.unauthorized_rollouts([entry], known) == (entry,)


def test_rollout_without_justification_is_flagged():
    cfg = {"campaign_id": "frozen-seed"}
    entry = _authorized_rollout(cfg, justification="   ")
    authorized = frozenset({touch_log.config_hash(cfg)})
    assert touch_log.unauthorized_rollouts([entry], authorized) == (entry,)


def test_read_only_entries_are_never_flagged():
    entry = touch_log.read_only_entry({"a": 1}, justification="manifest")
    assert touch_log.unauthorized_rollouts([entry], frozenset()) == ()
