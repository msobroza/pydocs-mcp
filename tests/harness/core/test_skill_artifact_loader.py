"""harness/core/skill_artifact_loader — the packaged shared-skill document.

The loader is the product-side firewall for the skill artifact (spec §4.2):
strict parse against the enumerated section set, unconditional presence of
all five sections, per-section token caps. The packaged seed is the
fallback ONLY when no override was named at all — an explicitly named
override that is missing or invalid is a hard typed error, never a silent
fallback (the description-override precedent, ADR 0006 §4).
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
    """All five skill-artifact sections, valid under the loader firewall."""
    sections = {sal.ADAPTER_HEADER: "adapter policy text"}
    for key in sal.HEAD_SECTION_HEADERS:
        sections[key] = f"head text for {key}"
    return sections


def _write_skill(tmp_path: Path, sections: dict[str, str]) -> Path:
    path = tmp_path / "override_skill.md"
    path.write_text(ds.render_sections(sections), encoding="utf-8")
    return path


# --- The enumerated section vocabulary -----------------------------------


def test_head_section_header_formats_the_dotted_key() -> None:
    assert sal.head_section_header("ask_your_docs", "sweqapro") == "HEAD: ask_your_docs.sweqapro"
    assert sal.head_section_header("external", "ccv") == "HEAD: external.ccv"


def test_head_section_header_rejects_unknown_pair() -> None:
    with pytest.raises(sal.SkillArtifactError) as excinfo:
        sal.head_section_header("new_harness", "ccv")
    message = str(excinfo.value)
    assert "new_harness.ccv" in message
    assert "ask_your_docs" in message and "sweqapro" in message


def test_the_four_head_keys_in_harness_major_order() -> None:
    assert sal.HEAD_SECTION_HEADERS == (
        "HEAD: ask_your_docs.sweqapro",
        "HEAD: ask_your_docs.ccv",
        "HEAD: external.sweqapro",
        "HEAD: external.ccv",
    )
    assert (sal.ADAPTER_HEADER, *sal.HEAD_SECTION_HEADERS) == sal.SKILL_ARTIFACT_HEADERS


def test_every_skill_header_is_legal_in_the_shared_grammar() -> None:
    # The header-widening protocol's step (1): each key must parse as a
    # section under the one closed regex in description_source.
    text = ds.render_sections({key: "body" for key in sal.SKILL_ARTIFACT_HEADERS})
    assert tuple(ds.parse_sections(text)) == sal.SKILL_ARTIFACT_HEADERS


# --- Packaged seed -------------------------------------------------------


def test_packaged_seed_loads_with_all_five_sections() -> None:
    artifact = sal.load_packaged_skill()
    assert artifact.adapter.strip()
    for harness in sal.HEAD_HARNESSES:
        for task_type in sal.HEAD_TASK_TYPES:
            assert artifact.head(harness, task_type).strip()


def test_adapter_charter_names_the_routing_boundary() -> None:
    # Spec §3: the adapter teaches "when search_codebase vs grep" — the two
    # route endpoints must appear in the shared policy text by name.
    adapter = sal.load_packaged_skill().adapter
    assert "search_codebase" in adapter and "grep" in adapter


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
    assert artifact.adapter == "adapter policy text"
    assert artifact.head("external", "sweqapro") == "head text for HEAD: external.sweqapro"


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
    path.write_bytes(b"=== ADAPTER ===\n\xff\xfe not utf8\n")
    with pytest.raises(sal.SkillArtifactError) as excinfo:
        sal.load_skill_artifact(path)
    assert str(path) in str(excinfo.value)


def test_override_with_unknown_header_raises_collision_naming_source(tmp_path: Path) -> None:
    sections = _valid_skill_sections()
    sections["HEAD: new_harness.ccv"] = "not an enumerated head"
    path = _write_skill(tmp_path, sections)
    with pytest.raises(ds.HeaderCollisionError) as excinfo:
        sal.load_skill_artifact(path)
    assert "HEAD: new_harness.ccv" in str(excinfo.value)
    assert any(str(path) in note for note in excinfo.value.__notes__)


def test_override_rejects_product_document_keys(tmp_path: Path) -> None:
    # Firewalling is symmetric: SERVER_INSTRUCTIONS belongs to the product
    # document, not the skill artifact — parseable but rejected here.
    sections = _valid_skill_sections()
    sections["SERVER_INSTRUCTIONS"] = "belongs to descriptions.md"
    with pytest.raises(ds.HeaderCollisionError):
        sal.load_skill_artifact(_write_skill(tmp_path, sections))


def test_override_missing_head_section_raises_missing_section(tmp_path: Path) -> None:
    sections = _valid_skill_sections()
    del sections["HEAD: external.ccv"]
    with pytest.raises(ds.MissingSectionError) as excinfo:
        sal.load_skill_artifact(_write_skill(tmp_path, sections))
    assert excinfo.value.missing == ("HEAD: external.ccv",)


def test_adapter_token_cap_enforced(tmp_path: Path) -> None:
    sections = _valid_skill_sections()
    overflow = (sal.ADAPTER_TOKEN_BUDGET + 1) * ds.CHARS_PER_TOKEN
    sections[sal.ADAPTER_HEADER] = "x" * overflow
    with pytest.raises(ds.TokenBudgetExceededError) as excinfo:
        sal.load_skill_artifact(_write_skill(tmp_path, sections))
    assert excinfo.value.section == sal.ADAPTER_HEADER
    assert excinfo.value.budget == sal.ADAPTER_TOKEN_BUDGET


def test_head_token_cap_enforced(tmp_path: Path) -> None:
    sections = _valid_skill_sections()
    overflow = (sal.PER_HEAD_TOKEN_BUDGET + 1) * ds.CHARS_PER_TOKEN
    sections["HEAD: ask_your_docs.ccv"] = "x" * overflow
    with pytest.raises(ds.TokenBudgetExceededError) as excinfo:
        sal.load_skill_artifact(_write_skill(tmp_path, sections))
    assert excinfo.value.section == "HEAD: ask_your_docs.ccv"
    assert excinfo.value.budget == sal.PER_HEAD_TOKEN_BUDGET


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
