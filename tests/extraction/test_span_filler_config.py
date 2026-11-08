"""The two ``extraction.chunking.python`` knobs and the one 400-line constant (issue #380).

``span_filler_max_lines`` defaults to the same number the source view caps its
window at. That literal used to live three times (the service default, the
``symbol_source.max_lines`` field, the ``mcp_inputs`` slot); it now lives once
in ``constants.SYMBOL_SOURCE_MAX_LINES`` and every site reads it.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from pydocs_mcp.application import mcp_inputs, symbol_source
from pydocs_mcp.constants import SYMBOL_SOURCE_MAX_LINES
from pydocs_mcp.extraction.config import ChunkingConfig, PythonChunkingConfig
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.models import SymbolSourceConfig


def test_the_python_block_defaults_to_filler_on_at_the_source_cap() -> None:
    cfg = ChunkingConfig().python
    assert cfg.span_filler is True
    assert cfg.span_filler_max_lines == SYMBOL_SOURCE_MAX_LINES


def test_the_cap_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        PythonChunkingConfig(span_filler_max_lines=0)


def test_an_unknown_python_key_is_refused() -> None:
    with pytest.raises(ValidationError):
        PythonChunkingConfig.model_validate({"span_filer": False})


def test_the_shipped_yaml_restates_the_defaults() -> None:
    assert AppConfig.load().extraction.chunking.python == PythonChunkingConfig()


def test_a_yaml_overlay_turns_the_filler_off(tmp_path) -> None:
    overlay = tmp_path / "off.yaml"
    overlay.write_text("extraction:\n  chunking:\n    python:\n      span_filler: false\n")
    assert AppConfig.load(explicit_path=overlay).extraction.chunking.python.span_filler is False


def test_every_source_cap_site_reads_the_one_constant() -> None:
    assert SYMBOL_SOURCE_MAX_LINES == 400
    assert symbol_source.SymbolSourceService.__dataclass_fields__["max_lines"].default == (
        SYMBOL_SOURCE_MAX_LINES
    )
    assert SymbolSourceConfig().max_lines == SYMBOL_SOURCE_MAX_LINES


def test_the_mcp_inputs_slot_starts_at_the_constant() -> None:
    """Read in a fresh interpreter: other tests install YAML values into the slot."""
    source = (
        "from pydocs_mcp.application import mcp_inputs;"
        "from pydocs_mcp.constants import SYMBOL_SOURCE_MAX_LINES as cap;"
        "print(mcp_inputs._SYMBOL_SOURCE_MAX_LINES == cap)"
    )
    result = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "True"
    assert hasattr(mcp_inputs, "_SYMBOL_SOURCE_MAX_LINES")
