"""harness/core/prompt_override — override type + the one assembly function.

Pure text composition: no agent framework involved, core-only.
"""

from __future__ import annotations

from pydocs_mcp.harness.core.prompt_override import PromptOverrides, assemble_system_prompt


def test_overrides_default_to_shipped_templates() -> None:
    overrides = PromptOverrides()
    assert overrides.system_prompt is None and overrides.rewrite_prompt is None


def test_assembly_without_session_context_is_byte_stable() -> None:
    assembled = assemble_system_prompt("SYS", "CATALOG-BLOCK")
    assert assembled == "SYS\nIndexed projects and packages:\nCATALOG-BLOCK"


def test_assembly_appends_session_context_after_catalog() -> None:
    assembled = assemble_system_prompt("SYS", "CAT", session_start_context="PACK")
    assert assembled.endswith("\nPACK")
    assert "Indexed projects and packages:" in assembled
