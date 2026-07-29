"""harness/builtin/external/binding — settings, the delivery map, and the factory.

The delivery map's digest is a PERSISTED, money-costing key (it folds into
every external arm hash), so it is pinned to its golden bytes here.
"""

from __future__ import annotations

import tomllib
from importlib import resources
from pathlib import Path

import pytest

from pydocs_mcp.harness.platform.contract import HarnessRunner
from pydocs_mcp.harness.builtin.external import binding
from pydocs_mcp.harness.platform.composed_harness import CliAgentHarness

_REPO_ROOT = Path(__file__).resolve().parents[4]


def _settings(tmp_path: Path, **overrides: object) -> dict[str, object]:
    settings: dict[str, object] = {
        "workspace": str(tmp_path / "ws"),
        "model": "a-model",
        "trace_root": str(tmp_path / "traces"),
    }
    settings.update(overrides)
    return settings


# --- Settings ---------------------------------------------------------------


def test_the_factory_returns_the_port(tmp_path: Path) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    assert isinstance(runner, CliAgentHarness)
    assert isinstance(runner, HarnessRunner)
    assert runner.harness_name == "external"


def test_a_typo_in_settings_fails_at_construction(tmp_path: Path) -> None:
    with pytest.raises(Exception, match="mystery_knob"):
        binding.make_harness_runner({**_settings(tmp_path), "mystery_knob": True})


def test_an_unknown_engine_fails_at_construction_naming_the_supported_set(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Unknown CLI agent engine: 'nosuch'"):
        binding.make_harness_runner(_settings(tmp_path, engine="nosuch"))


def test_the_default_engine_is_the_claude_code_adapter(tmp_path: Path) -> None:
    runner = binding.make_harness_runner(_settings(tmp_path))
    assert runner.adapter.name == binding.DEFAULT_CLI_ENGINE == "claude_code"


def test_the_arms_platform_injected_architecture_key_is_accepted_and_inert(
    tmp_path: Path,
) -> None:
    # The arms platform stamps the bound rubric section's graph name onto EVERY
    # arm's settings. This harness has no graph to route, so the key must be
    # accepted (extra="forbid" would fail every external arm's first rollout)
    # and change nothing.
    plain = binding.make_harness_runner(_settings(tmp_path))
    stamped = binding.make_harness_runner(_settings(tmp_path, architecture="text_react"))
    assert stamped.settings.architecture == "text_react"
    assert stamped.settings.model_dump(exclude={"architecture"}) == plain.settings.model_dump(
        exclude={"architecture"}
    )


def test_the_harness_name_is_an_entry_of_the_products_enumeration() -> None:
    # The fold classifies an unrecognized ``HARNESS_TASK_HEAD: <name>.<task>``
    # as ANOTHER arm's slice and drops it silently, so a name that drifted out
    # of HARNESS_NAMES would run every rollout without its harness tier while
    # still scoring. Tied, not merely commented as matching.
    from pydocs_mcp.harness.platform.skill_artifact import HARNESS_NAMES

    assert binding.EXTERNAL_HARNESS_NAME in HARNESS_NAMES


def test_a_tool_surface_outside_the_frozen_nine_fails_at_construction(tmp_path: Path) -> None:
    # A namespaced name is the likely mistake: the engine namespaces what it is
    # handed, so ``mcp__pydocs-mcp__mcp__pydocs-mcp__search_codebase`` would be
    # a grant the CLI resolves to nothing — a tool-less arm recorded as
    # drop-one. Loud before any spend instead.
    with pytest.raises(ValueError, match="frozen nine"):
        binding.make_harness_runner(
            _settings(tmp_path, tool_names=["mcp__pydocs-mcp__search_codebase"])
        )


def test_a_narrowed_surface_without_a_server_fails_at_construction(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="bare arm has no server tools"):
        binding.make_harness_runner(_settings(tmp_path, mcp=False, tool_names=["search_codebase"]))


def test_the_turn_cap_setting_is_spelled_as_the_arms_platform_reads_it(
    tmp_path: Path,
) -> None:
    # ``max_agent_turns`` is the key the arms platform's timeout sentinel reads
    # (``turns = cap + 1`` must fail the max_turns gate); spelling it
    # ``max_turns`` here would silently decouple the two.
    runner = binding.make_harness_runner(_settings(tmp_path, max_agent_turns=7))
    assert runner.settings.max_agent_turns == 7
    assert "max_agent_turns" in binding.ExternalRunnerSettings.model_fields


# --- Delivery map -----------------------------------------------------------


def test_the_delivery_map_is_one_pattern_key_per_tier() -> None:
    assert dict(binding.delivery_map()) == {
        "BACKBONE": "append_system_prompt.skill_block",
        "TASK_HEAD: <task_name>": "append_system_prompt.skill_block",
        "HARNESS_TASK_HEAD: external.<task_name>": "append_system_prompt.skill_block",
    }


def test_the_delivery_map_is_exactly_what_the_fold_delivers() -> None:
    # The map is the HASHED statement of what this harness delivers, so it must
    # not be a hand-kept duplicate of the fold's tier set: narrowing or widening
    # the fold has to move the digest by construction.
    from pydocs_mcp.harness.platform.guidance_fold import deliverable_section_keys

    assert tuple(binding.delivery_map()) == deliverable_section_keys(
        harness_name="external", task_name="<task_name>"
    )


def test_the_delivery_map_digest_is_stable_and_pinned() -> None:
    # A PERSISTED, money-costing key: it folds into every external arm hash, so
    # ledger rows carry it and a resume matches it exactly. Moving this literal
    # orphans those rows and forces a re-spend — it changes only as a
    # deliberate, reviewed measurement bump. NEW at this commit: no campaign
    # has been recorded under it, so committing it costs nothing.
    assert (
        binding.delivery_map_digest()
        == "0c29b7dea1138909066eaeb36610df9c9356322522aa25194db7c448c28cc45a"
    )
    assert binding.delivery_map_digest() == binding.delivery_map_digest()


def test_the_channel_composes_the_engines_flag_with_this_harnesss_slot() -> None:
    # The flag half is the ENGINE's (a second engine spells it differently);
    # the slot half is the HARNESS's, so a second composed harness reusing this
    # engine publishes its own map rather than inheriting this one's.
    from pydocs_mcp.harness.platform.engines.claude_code import ClaudeCodeAdapter

    channel = binding.guidance_channel(binding.DEFAULT_CLI_ENGINE)
    assert channel.split(".")[0] == ClaudeCodeAdapter.guidance_flag
    assert channel == "append_system_prompt.skill_block"


def test_a_second_engine_publishes_its_own_channel_and_digest() -> None:
    # The first REAL cross-engine delivery-mode difference (2026-07-29): the
    # opencode CLI has no system-prompt flag, so its channel is a documented
    # prompt PREFIX. Two engines under one harness therefore deliver the same
    # sections by different mechanisms — recorded by design, not by accident —
    # and the map's digest separates them with no harness-side change at all.
    from pydocs_mcp.harness.platform.engines.opencode import OpencodeAdapter

    assert binding.guidance_channel("opencode") == "prompt_prefix.skill_block"
    assert binding.guidance_channel("opencode").split(".")[0] == OpencodeAdapter.guidance_flag
    assert dict(binding.delivery_map("opencode")) == {
        "BACKBONE": "prompt_prefix.skill_block",
        "TASK_HEAD: <task_name>": "prompt_prefix.skill_block",
        "HARNESS_TASK_HEAD: external.<task_name>": "prompt_prefix.skill_block",
    }
    # NEW at this commit and unspent, exactly as the default engine's was.
    assert (
        binding.delivery_map_digest("opencode")
        == "338723af003b92fdc045330251c940a4cc4fc6e6c1fdf89da96ef4731341e864"
    )


def test_the_default_engines_digest_did_not_move_when_a_second_engine_landed() -> None:
    # The bridge that stamps a digest onto a ledger row calls the no-argument
    # form (the recorded record-fidelity gap in this module's docstring), so a
    # moved default would orphan every recorded external row. It did not move.
    assert binding.delivery_map_digest() != binding.delivery_map_digest("opencode")
    assert binding.delivery_map_digest() == binding.delivery_map_digest("claude_code")


def test_a_different_channel_moves_the_digest() -> None:
    # The engine's channel is the map's VALUE, so an engine that carries
    # guidance somewhere else is a different delivery and must not resume the
    # first engine's rows. (The engine ALSO folds into the arm hash directly
    # through ``settings``; this pins the map half of that separation.)
    elsewhere = binding._digest_of(
        binding._delivery_map_for_channel("instructions_file.skill_block")
    )
    assert elsewhere != binding.delivery_map_digest()


# --- Packaging --------------------------------------------------------------


def test_the_harness_skill_home_ships_via_an_explicit_maturin_include() -> None:
    # Wheels ship what pyproject names: a data file with no include line is
    # present in the repo and absent from every installed copy.
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = pyproject["tool"]["maturin"]["include"]
    assert "python/pydocs_mcp/harness/builtin/external/skills/**/*.md" in include


def test_the_freeze_home_states_its_governance_rule() -> None:
    readme = resources.files("pydocs_mcp.harness.builtin.external.skills").joinpath(
        "freeze/README.md"
    )
    text = readme.read_text("utf-8")
    # The two rules that decide what may land here: optimizable sections stay
    # cross-harness, and a local frozen asset arrives WITH its manifest.
    assert "harness/assets/skills" in text
    assert "manifest" in text


def test_the_composed_harness_needs_no_optional_extra() -> None:
    # Stdlib subprocess is the whole runtime, so nothing here may reach for the
    # extras guard — an external arm must be runnable on a bare product install.
    source = (_REPO_ROOT / "python/pydocs_mcp/harness/platform/composed_harness.py").read_text(
        encoding="utf-8"
    )
    assert "extra_guard" not in source
