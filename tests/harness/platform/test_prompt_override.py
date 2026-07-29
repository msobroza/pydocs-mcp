"""harness/platform/prompt_override — override type + the one assembly function.

Pure text composition: no agent framework involved, core-only.
"""

from __future__ import annotations

from pydocs_mcp.harness.platform.prompt_override import PromptOverrides, assemble_system_prompt


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


def test_all_optional_components_default_to_byte_identity() -> None:
    # The control-arm proof (run-contract design §9 stage 2): every optional
    # component off == the pre-existing bytes, exactly.
    baseline = assemble_system_prompt("SYS", "CAT")
    assert assemble_system_prompt("SYS", "CAT", None, None) == baseline


def test_skill_block_appends_after_the_session_pack() -> None:
    assembled = assemble_system_prompt("SYS", "CAT", "PACK", "SKILL-GUIDANCE")
    assert assembled.endswith("\nPACK\nSKILL-GUIDANCE")


def test_skill_block_composes_without_a_session_pack() -> None:
    assembled = assemble_system_prompt("SYS", "CAT", skill_block="SKILL-GUIDANCE")
    assert assembled == "SYS\nIndexed projects and packages:\nCAT\nSKILL-GUIDANCE"
