"""Every AppConfig overlay under ``benchmarks/configs/`` must parse and load.

One parametrized case per top-level ``configs/*.yaml`` pins the whole
directory: a rename, a YAML typo, or a schema drift in any overlay fails here
by name. Discovery is dynamic so newly added overlays are covered
automatically. The companion name test enforces the 2026-07-18 naming rule
(see the "Renamed configs" section of ``benchmarks/EXPERIMENTS.md``): overlay
names are dataset-free ``<method>[_<variant>][_<embedder>].yaml`` — the
runner's ``--dataset`` flag, not the filename, selects the dataset.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydocs_mcp.retrieval.config import AppConfig

_CONFIGS_DIR = Path(__file__).resolve().parents[2] / "configs"
_OVERLAYS = sorted(p.name for p in _CONFIGS_DIR.glob("*.yaml"))

# WHY a floor, not an exact count: adding an overlay must not fail this test,
# but an empty/near-empty glob (e.g. after a bad move of the configs dir)
# would make the parametrized loop vacuous — 40 pins the 44 known overlays.
_MIN_EXPECTED_OVERLAYS = 40

_FORBIDDEN_DATASET_PREFIXES = ("repoqa_", "swe_qa_pro_", "ds1000_")


def test_overlay_glob_is_nonvacuous() -> None:
    assert len(_OVERLAYS) >= _MIN_EXPECTED_OVERLAYS, (
        f"expected at least {_MIN_EXPECTED_OVERLAYS} overlays under "
        f"{_CONFIGS_DIR}, found {len(_OVERLAYS)}: {_OVERLAYS}"
    )


def test_overlay_names_are_dataset_free() -> None:
    """Overlay filenames never encode a dataset — the runner's ``--dataset``
    flag does. A prefixed name here re-introduces the false axis removed on
    2026-07-18 (byte-identical cross-dataset copies drifting independently).
    """
    prefixed = [n for n in _OVERLAYS if n.startswith(_FORBIDDEN_DATASET_PREFIXES)]
    assert not prefixed, (
        f"dataset-prefixed overlay names are forbidden: {prefixed}; "
        "name overlays <method>[_<variant>][_<embedder>].yaml and pair the "
        "dataset via --dataset (see benchmarks/EXPERIMENTS.md)"
    )


@pytest.mark.parametrize("overlay", _OVERLAYS)
def test_overlay_loads_via_appconfig(overlay: str) -> None:
    config = AppConfig.load(explicit_path=_CONFIGS_DIR / overlay)
    assert isinstance(config, AppConfig)
