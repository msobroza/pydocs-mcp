"""The external track's guidance partition + fold (run-contract design §4).

Two halves. The base-install half exercises ``_guidance`` alone — pattern-key
partition, fold order, and the loud failure on an undeliverable section — with
no ``pydocs_mcp`` import at all. The parity half (``importorskip``-gated,
because the product is not installed in a base-only environment) pins this
harness's fold and error message against the ask harness's, so the SAME
candidate text delivered through two harnesses reads identically.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.agent_track._guidance import (
    ExternalUndeliverableGuidanceError,
    deliverable_section_keys,
    fold_guidance,
)

_TASK = "vuln"
_BACKBONE = "search the index before the filesystem"
_TASK_HEAD = "name the vulnerable symbol"
_EXTERNAL_HEAD = "the CLI arm has Bash; prefer get_references"


def _sections(**overrides: str) -> dict[str, str]:
    """A candidate slice carrying this harness's three deliverable sections."""
    sections = {
        "BACKBONE": _BACKBONE,
        f"TASK_HEAD: {_TASK}": _TASK_HEAD,
        f"HARNESS_TASK_HEAD: external.{_TASK}": _EXTERNAL_HEAD,
    }
    sections.update(overrides)
    return sections


# --------------------------------------------------------------------------- #
# Partition
# --------------------------------------------------------------------------- #
def test_the_deliverable_keys_are_the_three_pattern_slots() -> None:
    assert deliverable_section_keys(_TASK) == (
        "BACKBONE",
        "TASK_HEAD: vuln",
        "HARNESS_TASK_HEAD: external.vuln",
    )


def test_no_task_name_degenerates_to_the_backbone_alone() -> None:
    # Mirrors the ask harness's ``task_name is None`` branch: with no task
    # named, nothing selects a task head, so only the backbone is deliverable.
    assert deliverable_section_keys("") == ("BACKBONE",)


def test_the_fold_joins_the_deliverable_sections_in_tier_order() -> None:
    assert fold_guidance(_sections(), task_name=_TASK) == (
        f"{_BACKBONE}\n{_TASK_HEAD}\n{_EXTERNAL_HEAD}"
    )


def test_the_fold_skips_absent_sections() -> None:
    sections = {"BACKBONE": _BACKBONE, f"HARNESS_TASK_HEAD: external.{_TASK}": _EXTERNAL_HEAD}
    assert fold_guidance(sections, task_name=_TASK) == f"{_BACKBONE}\n{_EXTERNAL_HEAD}"


def test_empty_guidance_folds_to_the_empty_string() -> None:
    # The load-bearing no-guidance case: an empty fold must produce NO CLI flag
    # downstream, so the bare argv stays byte-identical.
    assert fold_guidance({}, task_name=_TASK) == ""


def test_ask_only_prompt_sections_are_recognized_and_dropped() -> None:
    folded = fold_guidance(
        _sections(SYSTEM_PROMPT="you are...", REWRITE_PROMPT="rewrite..."), task_name=_TASK
    )
    assert folded == f"{_BACKBONE}\n{_TASK_HEAD}\n{_EXTERNAL_HEAD}"


def test_other_tasks_and_other_harnesses_heads_are_recognized_and_dropped() -> None:
    folded = fold_guidance(
        _sections(
            **{
                "TASK_HEAD: repo_qa": "other framing",
                "HARNESS_TASK_HEAD: ask_your_docs.vuln": "other harness",
                "HARNESS_TASK_HEAD: external.repo_qa": "other framing, this harness",
            }
        ),
        task_name=_TASK,
    )
    assert folded == f"{_BACKBONE}\n{_TASK_HEAD}\n{_EXTERNAL_HEAD}"


def test_task_scoped_sections_without_a_task_name_raise() -> None:
    # An unnamed framing cannot SELECT a task head, and the two task-scoped
    # tiers are this harness's own (``TASK_HEAD`` is harness-invariant,
    # ``external.*`` is ours by name) — so dropping them would be exactly the
    # silent loss contract rule 2 forbids. The likeliest misconfiguration (an
    # unset ``AgentTrackConfig.task_name`` on a guidance-carrying pass) fails
    # loudly instead of degrading a paid run to backbone-only.
    with pytest.raises(ExternalUndeliverableGuidanceError) as excinfo:
        fold_guidance(_sections(), task_name="")
    assert excinfo.value.sections == (f"TASK_HEAD: {_TASK}", f"HARNESS_TASK_HEAD: external.{_TASK}")
    assert excinfo.value.deliverable == ("BACKBONE",)


def test_without_a_task_name_the_backbone_alone_still_folds() -> None:
    # The genuine no-framing slice — a candidate carrying only the shared tier,
    # plus other harnesses' heads, which are never ours under any framing.
    sections = {"BACKBONE": _BACKBONE, "HARNESS_TASK_HEAD: ask_your_docs.vuln": "other harness"}
    assert fold_guidance(sections, task_name="") == _BACKBONE


def test_an_unknown_section_raises_naming_the_offending_keys() -> None:
    with pytest.raises(ExternalUndeliverableGuidanceError) as excinfo:
        fold_guidance(_sections(TOOL_DOCS="not a skill section"), task_name=_TASK)
    error = excinfo.value
    assert error.sections == ("TOOL_DOCS",)
    assert error.deliverable == deliverable_section_keys(_TASK)
    assert "TOOL_DOCS" in str(error) and "HARNESS_TASK_HEAD: external.vuln" in str(error)


def test_the_undeliverable_error_is_a_value_error() -> None:
    # Format-coupled twin of the contract type: callers that already catch
    # ``ValueError`` (the contract error's other base) keep working.
    assert issubclass(ExternalUndeliverableGuidanceError, ValueError)


# --------------------------------------------------------------------------- #
# Cross-harness parity (product-coupled; skipped on a base-only install)
# --------------------------------------------------------------------------- #
def _write_skill_artifact(tmp_path: Path, *, shared_head: str, backbone: str = _BACKBONE) -> Path:
    """A full 10-section artifact whose two harness heads carry ONE body.

    Identical bodies are what make the parity assertion meaningful: if the two
    harnesses fold the same tiers in the same order with the same separator,
    the delivered text is byte-identical.
    """
    from pydocs_mcp.application.description_source import render_sections
    from pydocs_mcp.harness.platform.skill_artifact import SKILL_ARTIFACT_HEADERS

    bodies = {}
    for key in SKILL_ARTIFACT_HEADERS:
        if key == "BACKBONE":
            bodies[key] = backbone
        elif key == f"TASK_HEAD: {_TASK}":
            bodies[key] = _TASK_HEAD
        elif key.startswith("HARNESS_TASK_HEAD: ") and key.endswith(f".{_TASK}"):
            bodies[key] = shared_head
        else:
            bodies[key] = f"unused body for {key}"
    path = tmp_path / "candidate_skill.md"
    path.write_text(render_sections(bodies), encoding="utf-8")
    return path


def test_the_fold_is_byte_identical_to_the_ask_harnesss_skill_block(tmp_path: Path) -> None:
    pytest.importorskip("pydocs_mcp.harness.builtin.ask_your_docs.agent")
    from pydocs_mcp.harness.builtin.ask_your_docs.agent import _resolved_skill_block

    path = _write_skill_artifact(tmp_path, shared_head=_EXTERNAL_HEAD)
    ask_block = _resolved_skill_block(path, _TASK)
    assert fold_guidance(_sections(), task_name=_TASK) == ask_block


def test_the_no_task_fold_matches_the_ask_harnesss_backbone_only_block(tmp_path: Path) -> None:
    pytest.importorskip("pydocs_mcp.harness.builtin.ask_your_docs.agent")
    from pydocs_mcp.harness.builtin.ask_your_docs.agent import _resolved_skill_block

    path = _write_skill_artifact(tmp_path, shared_head=_EXTERNAL_HEAD)
    # Backbone-only on BOTH sides: the ask harness's ``task_name is None``
    # branch returns the backbone, and so does the fold when handed the slice
    # an unnamed framing can actually deliver.
    assert fold_guidance({"BACKBONE": _BACKBONE}, task_name="") == _resolved_skill_block(path, None)


def test_the_fold_matches_the_ask_block_for_a_newline_terminated_body(tmp_path: Path) -> None:
    # The parity claim is stated over bodies in the ``parse_sections`` normal
    # form (module docstring): the ask harness reads its block through
    # render+parse, which trims one trailing newline, so a body that ends in a
    # newline only reads identically when the fold is handed the PARSED value.
    pytest.importorskip("pydocs_mcp.harness.builtin.ask_your_docs.agent")
    from pydocs_mcp.application.description_source import parse_sections
    from pydocs_mcp.harness.builtin.ask_your_docs.agent import _resolved_skill_block
    from pydocs_mcp.harness.platform.skill_artifact import SKILL_ARTIFACT_HEADERS

    path = _write_skill_artifact(tmp_path, shared_head=_EXTERNAL_HEAD, backbone=f"{_BACKBONE}\n")
    parsed = parse_sections(path.read_text(encoding="utf-8"), allowed=SKILL_ARTIFACT_HEADERS)
    ours = fold_guidance(
        {key: parsed[key] for key in deliverable_section_keys(_TASK)}, task_name=_TASK
    )
    assert ours == _resolved_skill_block(path, _TASK)


def test_the_undeliverable_message_shape_matches_the_contract_error() -> None:
    run_contract = pytest.importorskip("pydocs_mcp.harness.platform.contract")

    sections = ("TOOL_DOCS",)
    deliverable = deliverable_section_keys(_TASK)
    theirs = run_contract.UndeliverableGuidanceError(sections=sections, deliverable=deliverable)
    ours = ExternalUndeliverableGuidanceError(sections=sections, deliverable=deliverable)
    assert str(ours) == str(theirs)
