"""Pins + lockfile metadata (ADR 0013 deliverable 1)."""

from __future__ import annotations

from pydocs_eval.datasets_swe import pins


def test_live_and_pro_revisions_are_the_pinned_shas():
    assert pins.LIVE_REVISION == "a637bd46829f3132e12938c8a0ca93173a977b8e"
    assert pins.PRO_REVISION == "7ab5114912baf22bb098818e604c02fe7ad2c11f"


def test_pin_metadata_carries_both_pins_and_dedupe_rule():
    meta = pins.pin_metadata()
    assert meta["dev_val"]["dataset_id"] == pins.LIVE_DATASET_ID
    assert meta["dev_val"]["revision"] == pins.LIVE_REVISION
    assert meta["frozen_test"]["dataset_id"] == pins.PRO_DATASET_ID
    assert meta["frozen_test"]["python_instances"] == 266
    assert meta["dedupe"]["instance_id"] == "conan-io__conan-18153"
    assert meta["dedupe"]["raw_rows"] == 1888
    assert meta["dedupe"]["working_rows"] == 1887


def test_pro_python_repos_are_the_three_measured_repos():
    assert pins.PRO_PYTHON_REPOS == (
        "ansible/ansible",
        "internetarchive/openlibrary",
        "qutebrowser/qutebrowser",
    )


def test_dataset_pin_to_dict_is_lockfile_serializable():
    d = pins.LIVE_PIN.to_dict()
    assert d["revision"] == pins.LIVE_REVISION
    assert d["parquet_files"] == list(pins.LIVE_PARQUET_FILES)
