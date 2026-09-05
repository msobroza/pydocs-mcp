"""harness/core/skill_artifact_loader — the packaged shared-skill document.

The loader is the product-side firewall for the skill artifact (spec §4.2):
strict parse against the enumerated section set, unconditional presence of
every enumerated section (backbone, one harness-invariant task head per task
name, one harness task head per harness x task pair), per-section token caps. The packaged seed is the fallback ONLY
when no override was named at all — an explicitly named override that is
missing or invalid is a hard typed error, never a silent fallback (the
description-override precedent, ADR 0006 §4).
"""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

import pytest

from pydocs_mcp.application import description_source as ds
from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.harness.core import skill_artifact_loader as sal

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _valid_skill_sections() -> dict[str, str]:
    """Every skill-artifact section, valid under the loader firewall."""
    sections = {sal.BACKBONE_HEADER: "backbone policy text"}
    for key in sal.TASK_HEAD_SECTION_HEADERS:
        sections[key] = f"task head text for {key}"
    for key in sal.HARNESS_TASK_HEAD_SECTION_HEADERS:
        sections[key] = f"harness task head text for {key}"
    return sections


def _write_skill(tmp_path: Path, sections: dict[str, str]) -> Path:
    path = tmp_path / "override_skill.md"
    path.write_text(ds.render_sections(sections), encoding="utf-8")
    return path


# --- The enumerated section vocabulary -----------------------------------


def test_harness_task_head_section_header_formats_the_dotted_key() -> None:
    assert (
        sal.harness_task_head_section_header("ask_your_docs", "repo_qa")
        == "HARNESS_TASK_HEAD: ask_your_docs.repo_qa"
    )
    assert (
        sal.harness_task_head_section_header("external", "vuln")
        == "HARNESS_TASK_HEAD: external.vuln"
    )


def test_harness_task_head_section_header_rejects_unknown_pair() -> None:
    with pytest.raises(sal.SkillArtifactError) as excinfo:
        sal.harness_task_head_section_header("new_harness", "vuln")
    message = str(excinfo.value)
    assert "new_harness.vuln" in message
    assert "ask_your_docs" in message and "repo_qa" in message


def test_task_head_section_header_carries_no_harness_factor() -> None:
    # The TASK_HEAD tier is harness-INVARIANT: the key names the task alone, so
    # every harness running it reads and updates the SAME section.
    assert sal.task_head_section_header("repo_qa") == "TASK_HEAD: repo_qa"
    assert sal.task_head_section_header("vuln") == "TASK_HEAD: vuln"
    assert sal.task_head_section_header("bug_loc") == "TASK_HEAD: bug_loc"


def test_task_head_section_header_rejects_unknown_task_naming_the_set() -> None:
    with pytest.raises(sal.SkillArtifactError) as excinfo:
        sal.task_head_section_header("not_a_task")
    message = str(excinfo.value)
    assert "not_a_task" in message
    assert "repo_qa" in message and "vuln" in message and "bug_loc" in message


def test_task_names_feed_both_the_task_head_and_harness_task_head_tiers() -> None:
    # One enumeration, two tiers — a new task name widens both at once.
    assert sal.TASK_NAMES == ("repo_qa", "vuln", "bug_loc")
    assert sal.TASK_HEAD_SECTION_HEADERS == (
        "TASK_HEAD: repo_qa",
        "TASK_HEAD: vuln",
        "TASK_HEAD: bug_loc",
    )
    assert all(
        any(key.endswith(f".{task_name}") for key in sal.HARNESS_TASK_HEAD_SECTION_HEADERS)
        for task_name in sal.TASK_NAMES
    )


def test_the_ten_section_keys_in_canonical_order() -> None:
    # Task-head order follows TASK_NAMES; harness task heads are harness-major.
    # bug_loc (2026-07-28) is APPENDED to TASK_NAMES, so it lands last within
    # each tier and every pre-existing key keeps its position.
    assert sal.HARNESS_TASK_HEAD_SECTION_HEADERS == (
        "HARNESS_TASK_HEAD: ask_your_docs.repo_qa",
        "HARNESS_TASK_HEAD: ask_your_docs.vuln",
        "HARNESS_TASK_HEAD: ask_your_docs.bug_loc",
        "HARNESS_TASK_HEAD: external.repo_qa",
        "HARNESS_TASK_HEAD: external.vuln",
        "HARNESS_TASK_HEAD: external.bug_loc",
    )
    assert sal.SKILL_ARTIFACT_HEADERS == (
        "BACKBONE",
        "TASK_HEAD: repo_qa",
        "TASK_HEAD: vuln",
        "TASK_HEAD: bug_loc",
        "HARNESS_TASK_HEAD: ask_your_docs.repo_qa",
        "HARNESS_TASK_HEAD: ask_your_docs.vuln",
        "HARNESS_TASK_HEAD: ask_your_docs.bug_loc",
        "HARNESS_TASK_HEAD: external.repo_qa",
        "HARNESS_TASK_HEAD: external.vuln",
        "HARNESS_TASK_HEAD: external.bug_loc",
    )
    assert (
        sal.BACKBONE_HEADER,
        *sal.TASK_HEAD_SECTION_HEADERS,
        *sal.HARNESS_TASK_HEAD_SECTION_HEADERS,
    ) == sal.SKILL_ARTIFACT_HEADERS


@pytest.mark.parametrize("retired", ["sweqapro", "ccv"])
def test_retired_task_names_are_promoted_then_rejected_not_smuggled_as_content(
    tmp_path: Path, retired: str
) -> None:
    # The 2026-07-28 consolidation RETIRED the ``sweqapro`` and ``ccv`` task
    # names. Retiring narrows THIS artifact's allowed set, never the grammar:
    # a document still carrying ``=== TASK_HEAD: sweqapro ===`` parses that
    # line as a SECTION (so it can never ride silently as body text) and is
    # then rejected here — the promoted-then-firewall-rejected behavior the
    # shape-vs-enumeration split exists for.
    header = f"TASK_HEAD: {retired}"
    text = ds.render_sections({**_valid_skill_sections(), header: "an old framing"})
    assert header in ds.parse_sections(text)  # grammar-legal, artifact-illegal
    with pytest.raises(ds.HeaderCollisionError) as excinfo:
        sal.parse_skill_artifact(text, origin="candidate")
    assert header in str(excinfo.value)


@pytest.mark.parametrize("retired", ["sweqapro", "ccv"])
def test_retired_task_names_are_refused_by_the_section_key_builders(retired: str) -> None:
    # The error must NAME the surviving set, so a stale config is told what to
    # write instead of only that it was wrong.
    with pytest.raises(sal.SkillArtifactError) as excinfo:
        sal.task_head_section_header(retired)
    assert retired in str(excinfo.value) and "['repo_qa', 'vuln', 'bug_loc']" in str(excinfo.value)
    with pytest.raises(sal.SkillArtifactError):
        sal.harness_task_head_section_header("ask_your_docs", retired)


def test_every_skill_header_is_legal_in_the_shared_grammar() -> None:
    # The header-widening protocol's step (1): each key must parse as a
    # section under the one closed regex in description_source.
    text = ds.render_sections({key: "body" for key in sal.SKILL_ARTIFACT_HEADERS})
    assert tuple(ds.parse_sections(text)) == sal.SKILL_ARTIFACT_HEADERS


# --- Packaged seed -------------------------------------------------------


def test_packaged_seed_loads_with_every_enumerated_section() -> None:
    artifact = sal.load_packaged_skill()
    assert artifact.backbone.strip()
    for task_name in sal.TASK_NAMES:
        assert artifact.task_head(task_name).strip()
    for harness in sal.HARNESS_NAMES:
        for task_name in sal.TASK_NAMES:
            assert artifact.harness_task_head(harness, task_name).strip()


def test_backbone_charter_names_the_routing_boundary() -> None:
    # Spec §3: the backbone teaches "when search_codebase vs grep" — the two
    # route endpoints must appear in the shared policy text by name.
    backbone = sal.load_packaged_skill().backbone
    assert "search_codebase" in backbone and "grep" in backbone


def test_seed_task_head_sections_carry_no_harness_local_facts() -> None:
    # The tier's whole point: task guidance holds only what is true for
    # EVERY harness, so the harness identifier and the harness-local facts
    # the harness task heads own (catalog pre-injection, orientation policy)
    # stay out.
    artifact = sal.load_packaged_skill()
    for task_name in sal.TASK_NAMES:
        text = artifact.task_head(task_name)
        assert "ask_your_docs" not in text
        assert "catalog" not in text and "pre-injected" not in text


def test_seed_sections_fit_their_caps() -> None:
    text = (
        resources.files("pydocs_mcp.harness.core.skills")
        .joinpath("search_guidance_seed.md")
        .read_text(encoding="utf-8")
    )
    sections = ds.parse_sections(text, allowed=sal.SKILL_ARTIFACT_HEADERS)
    assert len(sections[sal.BACKBONE_HEADER]) // ds.CHARS_PER_TOKEN <= sal.BACKBONE_TOKEN_BUDGET
    for key in sal.TASK_HEAD_SECTION_HEADERS:
        assert len(sections[key]) // ds.CHARS_PER_TOKEN <= sal.PER_TASK_HEAD_TOKEN_BUDGET
    for key in sal.HARNESS_TASK_HEAD_SECTION_HEADERS:
        assert len(sections[key]) // ds.CHARS_PER_TOKEN <= sal.PER_HARNESS_TASK_HEAD_TOKEN_BUDGET


def test_seed_is_canonical_byte_surface() -> None:
    text = (
        resources.files("pydocs_mcp.harness.core.skills")
        .joinpath("search_guidance_seed.md")
        .read_text(encoding="utf-8")
    )
    assert ds.normalize(text) == text


def test_missing_override_falls_back_to_packaged_seed() -> None:
    assert sal.load_skill_artifact(None) == sal.load_packaged_skill()


# --- Override path -------------------------------------------------------


def test_override_document_wins_when_named(tmp_path: Path) -> None:
    sections = _valid_skill_sections()
    artifact = sal.load_skill_artifact(_write_skill(tmp_path, sections))
    assert artifact.backbone == "backbone policy text"
    assert artifact.task_head("vuln") == "task head text for TASK_HEAD: vuln"
    assert (
        artifact.harness_task_head("external", "repo_qa")
        == "harness task head text for HARNESS_TASK_HEAD: external.repo_qa"
    )


def test_task_head_accessor_rejects_an_unknown_task_name(tmp_path: Path) -> None:
    artifact = sal.load_skill_artifact(_write_skill(tmp_path, _valid_skill_sections()))
    with pytest.raises(sal.SkillArtifactError):
        artifact.task_head("not_a_task")


def test_explicit_override_missing_file_is_a_hard_error(tmp_path: Path) -> None:
    missing = tmp_path / "nope_skill.md"
    with pytest.raises(sal.SkillArtifactError) as excinfo:
        sal.load_skill_artifact(missing)
    message = str(excinfo.value)
    assert str(missing) in message
    assert "packaged seed" in message


def test_non_utf8_override_lands_in_the_typed_family(tmp_path: Path) -> None:
    # A mojibake candidate file must raise SkillArtifactError, not a bare
    # UnicodeDecodeError — "one except DescriptionSourceError" is the
    # loader's whole catching contract.
    path = tmp_path / "mojibake_skill.md"
    path.write_bytes(b"=== BACKBONE ===\n\xff\xfe not utf8\n")
    with pytest.raises(sal.SkillArtifactError) as excinfo:
        sal.load_skill_artifact(path)
    assert str(path) in str(excinfo.value)


def test_override_with_unknown_header_raises_collision_naming_source(tmp_path: Path) -> None:
    sections = _valid_skill_sections()
    sections["HARNESS_TASK_HEAD: new_harness.vuln"] = "not an enumerated head"
    path = _write_skill(tmp_path, sections)
    with pytest.raises(ds.HeaderCollisionError) as excinfo:
        sal.load_skill_artifact(path)
    assert "HARNESS_TASK_HEAD: new_harness.vuln" in str(excinfo.value)
    assert any(str(path) in note for note in excinfo.value.__notes__)


def test_override_rejects_product_document_keys(tmp_path: Path) -> None:
    # Firewalling is symmetric: SERVER_INSTRUCTIONS belongs to the product
    # document, not the skill artifact — parseable but rejected here.
    sections = _valid_skill_sections()
    sections["SERVER_INSTRUCTIONS"] = "belongs to descriptions.md"
    with pytest.raises(ds.HeaderCollisionError):
        sal.load_skill_artifact(_write_skill(tmp_path, sections))


def test_override_missing_harness_task_head_section_raises_missing_section(tmp_path: Path) -> None:
    sections = _valid_skill_sections()
    del sections["HARNESS_TASK_HEAD: external.vuln"]
    with pytest.raises(ds.MissingSectionError) as excinfo:
        sal.load_skill_artifact(_write_skill(tmp_path, sections))
    assert excinfo.value.missing == ("HARNESS_TASK_HEAD: external.vuln",)


def test_override_missing_task_head_section_raises_missing_section(tmp_path: Path) -> None:
    # Every section is required unconditionally — the TASK_HEAD tier is
    # not optional just because the harness task heads are present.
    sections = _valid_skill_sections()
    del sections["TASK_HEAD: vuln"]
    with pytest.raises(ds.MissingSectionError) as excinfo:
        sal.load_skill_artifact(_write_skill(tmp_path, sections))
    assert excinfo.value.missing == ("TASK_HEAD: vuln",)


def test_override_with_unknown_task_head_header_raises_collision(tmp_path: Path) -> None:
    # The regex carries the TASK_HEAD *shape*; the enumerated two live here —
    # so an unknown task is promoted to a section and rejected by the allowed set.
    sections = _valid_skill_sections()
    sections["TASK_HEAD: new_task"] = "not an enumerated task"
    with pytest.raises(ds.HeaderCollisionError) as excinfo:
        sal.load_skill_artifact(_write_skill(tmp_path, sections))
    assert "TASK_HEAD: new_task" in str(excinfo.value)


def test_backbone_token_cap_enforced(tmp_path: Path) -> None:
    sections = _valid_skill_sections()
    overflow = (sal.BACKBONE_TOKEN_BUDGET + 1) * ds.CHARS_PER_TOKEN
    sections[sal.BACKBONE_HEADER] = "x" * overflow
    with pytest.raises(ds.TokenBudgetExceededError) as excinfo:
        sal.load_skill_artifact(_write_skill(tmp_path, sections))
    assert excinfo.value.section == sal.BACKBONE_HEADER
    assert excinfo.value.budget == sal.BACKBONE_TOKEN_BUDGET


def test_task_head_token_cap_enforced(tmp_path: Path) -> None:
    sections = _valid_skill_sections()
    overflow = (sal.PER_TASK_HEAD_TOKEN_BUDGET + 1) * ds.CHARS_PER_TOKEN
    sections["TASK_HEAD: repo_qa"] = "x" * overflow
    with pytest.raises(ds.TokenBudgetExceededError) as excinfo:
        sal.load_skill_artifact(_write_skill(tmp_path, sections))
    assert excinfo.value.section == "TASK_HEAD: repo_qa"
    assert excinfo.value.budget == sal.PER_TASK_HEAD_TOKEN_BUDGET


def test_harness_task_head_token_cap_enforced(tmp_path: Path) -> None:
    sections = _valid_skill_sections()
    overflow = (sal.PER_HARNESS_TASK_HEAD_TOKEN_BUDGET + 1) * ds.CHARS_PER_TOKEN
    sections["HARNESS_TASK_HEAD: ask_your_docs.vuln"] = "x" * overflow
    with pytest.raises(ds.TokenBudgetExceededError) as excinfo:
        sal.load_skill_artifact(_write_skill(tmp_path, sections))
    assert excinfo.value.section == "HARNESS_TASK_HEAD: ask_your_docs.vuln"
    assert excinfo.value.budget == sal.PER_HARNESS_TASK_HEAD_TOKEN_BUDGET


# --- Typed error family --------------------------------------------------


def test_skill_artifact_error_joins_the_description_source_family() -> None:
    # One catch handle for every skill-artifact failure: grammar errors are
    # DescriptionSourceError already, so the loader's own error subclasses it.
    assert issubclass(sal.SkillArtifactError, ds.DescriptionSourceError)
    assert issubclass(sal.SkillArtifactError, PydocsMCPError)
    assert issubclass(sal.SkillArtifactError, ValueError)


# --- Packaging pins (the trap-T4 class: wheels ship what pyproject names) --


def test_seed_ships_via_an_explicit_maturin_include() -> None:
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = pyproject["tool"]["maturin"]["include"]
    assert "python/pydocs_mcp/harness/core/prompts/*.j2" in include  # the precedent
    assert "python/pydocs_mcp/harness/core/skills/*.md" in include


def test_seed_visible_via_importlib_resources() -> None:
    resource = resources.files("pydocs_mcp.harness.core.skills").joinpath("search_guidance_seed.md")
    assert resource.is_file()


def test_parse_skill_artifact_is_the_public_in_memory_entrypoint(tmp_path: Path) -> None:
    # The cross-package seam the search_skill family will delegate to
    # (run-contract design §4/§9 stage 4) — no private import, no temp file.
    text = ds.render_sections(_valid_skill_sections())
    artifact = sal.parse_skill_artifact(text, origin="candidate")
    assert artifact.backbone == "backbone policy text"
    with pytest.raises(ds.HeaderCollisionError) as excinfo:
        sal.parse_skill_artifact(
            text + "=== SERVER_INSTRUCTIONS ===\nsmuggled\n", origin="candidate"
        )
    assert any("candidate" in note for note in excinfo.value.__notes__)
