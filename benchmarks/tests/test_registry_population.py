"""Registry self-population pins — importing a registry alone yields its names.

The regression guard for the "empty registry" trap (a smoke run once showed
``trackers: []`` because ``pydocs_eval.trackers`` was never imported). Each pin
runs the import in a FRESH subprocess — no other test's import side effects can
mask a broken populate callback — reads ``<registry>.names()``, and asserts the
full expected name set. If a registry stops self-populating, its pin flips to an
empty (or short) list and fails loudly.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import pydocs_eval

# The src root to hand the subprocess on PYTHONPATH — derived from the imported
# package so the pin works regardless of how pytest itself was invoked.
_SRC_ROOT = str(Path(pydocs_eval.__file__).resolve().parents[1])

# The optimize registries import ``pydocs_mcp`` (the [retrieval] extra) at
# populate time; skip their pins when the extra is absent, mirroring the
# ``pytest.importorskip`` guard the rest of the optimize suite uses.
_HAS_RETRIEVAL = (
    subprocess.run(
        [sys.executable, "-c", "import pydocs_mcp.application.description_source"],
        capture_output=True,
        env={**os.environ, "PYTHONPATH": _SRC_ROOT},
    ).returncode
    == 0
)

_GLOBAL_PINS = [
    (
        "pydocs_eval.registries",
        "dataset_registry",
        ["ds1000", "repoqa", "repoqa-structural", "swe-qa", "swe-qa-pro"],
    ),
    (
        "pydocs_eval.registries",
        "metric_registry",
        [
            "coverage",
            "library_resolution@1",
            "mrr",
            "ndcg@k",
            "pass@1-needle",
            "precision@1",
            "recall@k",
        ],
    ),
    ("pydocs_eval.registries", "tracker_registry", ["jsonl", "mlflow"]),
    (
        "pydocs_eval.registries",
        "system_registry",
        [
            "context7",
            "neuledge",
            "pydocs-mcp",
            "pydocs-mcp-composite",
            "pydocs-mcp-tree-only",
            "pydocs-mcp-tree-parallel",
            "pydocs-oracle",
        ],
    ),
]

_OPTIMIZE_PINS = [
    (
        "pydocs_eval.optimize.registries",
        "artifact_registry",
        ["ask_architecture", "ask_prompt", "retrieval_config", "tool_docs", "usage_skill"],
    ),
    (
        "pydocs_eval.optimize.registries",
        "fitness_registry",
        ["ask_rubric", "paired_agent", "retrieval"],
    ),
    (
        "pydocs_eval.optimize.registries",
        "optimizer_registry",
        ["config_search", "critique_refine", "gepa", "skillopt"],
    ),
]


def _names_in_fresh_process(module: str, attr: str) -> list[str]:
    """Import ``module``, read ``attr.names()`` in a fresh interpreter, return them."""
    code = f"from {module} import {attr}; print(' '.join(sorted({attr}.names())))"
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _SRC_ROOT},
    )
    assert result.returncode == 0, f"import failed:\n{result.stderr}"
    return result.stdout.split()


@pytest.mark.parametrize("module, attr, expected", _GLOBAL_PINS)
def test_global_registry_self_populates(module: str, attr: str, expected: list[str]) -> None:
    assert _names_in_fresh_process(module, attr) == sorted(expected)


@pytest.mark.skipif(not _HAS_RETRIEVAL, reason="optimize registries need the [retrieval] extra")
@pytest.mark.parametrize("module, attr, expected", _OPTIMIZE_PINS)
def test_optimize_registry_self_populates(module: str, attr: str, expected: list[str]) -> None:
    # optimizer_registry pins config_search + gepa specifically: both were absent
    # from a bare ``import optimizers`` before the populate callback landed
    # (config_search was never imported by the package __init__; gepa lives in
    # the sibling gepa_harness package).
    assert _names_in_fresh_process(module, attr) == sorted(expected)
