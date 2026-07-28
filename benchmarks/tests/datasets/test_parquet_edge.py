"""The retrieval track's pinned-parquet release edge stays import-cheap.

The whole point of function-local heavy imports is that the ``datasets``
package — which the registry populates eagerly on first lookup — never drags
``pyarrow`` or ``huggingface_hub`` into a process that only reads fixtures.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import pydocs_eval
from pydocs_eval.datasets._parquet import (
    _INSTALL_HINT,
    ParquetPin,
    ParquetPinMismatchError,
    _raise_missing_parquet_extra,
    assert_pin_row_count,
    default_parquet_cache_dir,
)

_SRC_ROOT = str(Path(pydocs_eval.__file__).resolve().parents[1])


def _pin(*, files: tuple[str, ...] = ("data/x.parquet",), expected_rows: int = 50) -> ParquetPin:
    return ParquetPin(
        dataset_id="org/name", revision="a" * 40, files=files, expected_rows=expected_rows
    )


def test_a_pin_serializes_for_a_campaign_lockfile() -> None:
    assert _pin().to_dict() == {
        "dataset_id": "org/name",
        "revision": "a" * 40,
        "files": ["data/x.parquet"],
        "expected_rows": 50,
    }


def test_a_release_holding_the_recorded_row_count_passes_through() -> None:
    rows = [{"a": i} for i in range(50)]
    assert assert_pin_row_count(rows, _pin()) is rows


def test_a_short_release_names_both_counts_and_the_revision() -> None:
    # The concrete drift this guards: a multi-config dataset ships IDENTICALLY
    # NAMED shards under every config directory (lca-bug-localization's py/,
    # java/ and kt/), so a one-character edit to ``files`` swaps the corpus to
    # a language the indexer cannot read, with no other signal.
    pin = _pin(files=("py/test-00000-of-00001.parquet",))
    with pytest.raises(ParquetPinMismatchError) as excinfo:
        assert_pin_row_count([{"a": 1}], pin)
    message = str(excinfo.value)
    assert "1 row" in message
    assert "expected 50" in message
    assert pin.revision in message


def test_a_missing_extra_names_the_pip_command_rather_than_the_module() -> None:
    # Raised from inside an asyncio.to_thread worker, where a bare
    # ModuleNotFoundError names neither the extra nor why it exists.
    with pytest.raises(RuntimeError) as excinfo:
        _raise_missing_parquet_extra(ModuleNotFoundError("No module named 'pyarrow'"))
    assert 'pip install "pydocs-mcp-eval[datasets-parquet]"' in str(excinfo.value)
    assert 'pip install "pydocs-mcp-eval[datasets-parquet]"' in _INSTALL_HINT


def test_the_cache_root_is_absolute_and_user_scoped() -> None:
    root = default_parquet_cache_dir()
    assert root.is_absolute()
    assert "~" not in str(root)


def test_importing_the_datasets_package_pulls_in_no_parquet_engine() -> None:
    # A fresh interpreter, so no other test's imports can mask a module-level
    # heavy import that crept back in.
    code = (
        "import sys; import pydocs_eval.datasets;"
        "print(sorted(m for m in sys.modules if m in {'pyarrow', 'huggingface_hub'}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": _SRC_ROOT, "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"
