"""``reference_graph.impact.max_module_seeds`` — the module-target fan-out cap.

The cap bounds how many seeds a module target searches (spec §1, AC1.9). It is
a YAML tunable, never an MCP parameter, so this file pins the model default
against the shipped YAML, the declared bounds, and an overlay override.
"""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig, ImpactConfig, _DEFAULT_MAX_MODULE_SEEDS
from pydocs_mcp.retrieval.config.app_config import _shipped_default_config_path


@pytest.fixture(autouse=True)
def _clean_config_env(monkeypatch, tmp_path):
    monkeypatch.delenv("PYDOCS_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)  # no ./pydocs-mcp.yaml
    yield


def test_default_comes_from_the_single_source_constant() -> None:
    assert ImpactConfig().max_module_seeds == _DEFAULT_MAX_MODULE_SEEDS
    assert AppConfig.load().reference_graph.impact.max_module_seeds == _DEFAULT_MAX_MODULE_SEEDS


def test_shipped_yaml_states_the_same_default() -> None:
    """YAML/model parity — the user-visible knob must not drift from the field."""
    shipped = yaml.safe_load(_shipped_default_config_path().read_text())
    assert shipped["reference_graph"]["impact"]["max_module_seeds"] == _DEFAULT_MAX_MODULE_SEEDS


@pytest.mark.parametrize("value", [0, 257])
def test_out_of_range_values_are_rejected(value: int) -> None:
    with pytest.raises(ValidationError):
        ImpactConfig(max_module_seeds=value)


@pytest.mark.parametrize("value", [1, 256])
def test_the_bounds_themselves_are_accepted(value: int) -> None:
    assert ImpactConfig(max_module_seeds=value).max_module_seeds == value


def test_user_yaml_overrides_the_cap(tmp_path) -> None:
    user_file = tmp_path / "pydocs-mcp.yaml"
    user_file.write_text("reference_graph:\n  impact:\n    max_module_seeds: 8\n")
    config = AppConfig.load(explicit_path=user_file)
    assert config.reference_graph.impact.max_module_seeds == 8
    # An overlay of one key leaves its siblings on the shipped defaults.
    assert config.reference_graph.impact.max_depth == ImpactConfig().max_depth
