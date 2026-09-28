"""Every judge role is named by its YAML key, and none runs until the deployment pins its model."""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pydocs_eval.judge.config import (
    JudgeConfig,
    JudgeDeployment,
    load_judge_config,
    load_judge_deployment,
    load_reference_writer_config,
)
from pydocs_eval.judge.jev_requests import JudgedDataset
from pydocs_eval.judge.judge_errors import JudgeConfigError
from pydocs_eval.judge.model_ids import model_family
from pydocs_eval.judge.role_config import (
    AlignmentConfig,
    ChatRoleConfig,
    ReasoningEffort,
    ReferenceWriterConfig,
)
from pydocs_eval.registries import dataset_registry

from ._judge_fakes import BENCHMARKS_ROOT, DEPLOYMENT_YAML
from pydocs_eval.judge.roles import (
    ChatRole,
    escalation_role,
    jev_model,
    labeller_roles,
    reference_writer_fallback_role,
    reference_writer_role,
    role_family_violations,
)


def _pinned_labellers(first: str, second: str) -> JudgeConfig:
    labellers = (ChatRoleConfig(model=first), ChatRoleConfig(model=second))
    return JudgeConfig(alignment=AlignmentConfig(labellers=labellers))


@pytest.mark.parametrize(
    ("verb", "key"),
    [
        (lambda: jev_model(JudgeConfig()), "judge.jev.model"),
        (lambda: escalation_role(JudgeConfig()), "judge.escalation.model"),
        (lambda: labeller_roles(JudgeConfig()), "judge.alignment.labellers[0].model"),
        (
            lambda: labeller_roles(_pinned_labellers("openai/gpt-6-astra:batch", "")),
            "judge.alignment.labellers[1].model",
        ),
        (lambda: reference_writer_role(ReferenceWriterConfig()), "reference_writer.model"),
        (
            lambda: reference_writer_fallback_role(ReferenceWriterConfig(model="a/b:batch")),
            "reference_writer.fallback_model",
        ),
    ],
)
def test_a_role_with_no_pinned_model_refuses_by_its_key(
    verb: Callable[[], object], key: str
) -> None:
    with pytest.raises(JudgeConfigError) as refused:
        verb()

    assert key in str(refused.value)
    assert "benchmarks/configs/judge_openrouter.yaml" in str(refused.value)


def test_the_shipped_defaults_pin_no_model() -> None:
    judge = load_judge_config()
    writer = load_reference_writer_config()

    models = [judge.jev.model, judge.escalation.model, writer.model, writer.fallback_model]
    models += [labeller.model for labeller in judge.alignment.labellers]
    assert models == [""] * 6


def test_the_shipped_yamls_restate_every_code_default() -> None:
    """The YAMLs are what a reader edits, so their hand-written copies may not drift."""
    assert load_judge_config() == JudgeConfig()
    assert load_reference_writer_config() == ReferenceWriterConfig()


def test_the_role_defaults_are_the_specs() -> None:
    judge, writer = JudgeConfig(), ReferenceWriterConfig()

    assert (judge.jev.timeout_seconds, judge.jev.retries) == (10.0, 2)
    assert judge.jev.max_gold_files_per_request == 12
    assert judge.escalation.reasoning_effort is ReasoningEffort.XHIGH
    assert (judge.escalation.timeout_seconds, judge.escalation.retries) == (120.0, 2)
    for block in (*judge.alignment.labellers, writer):
        assert block.reasoning_effort is ReasoningEffort.HIGH
        assert (block.timeout_seconds, block.retries) == (600.0, 2)
    every_block = (judge.jev, judge.escalation, *judge.alignment.labellers, writer)
    assert {block.api_key_env for block in every_block} == {"OPENROUTER_API_KEY"}
    assert {block.endpoint for block in every_block} == {"https://openrouter.ai/api/v1"}
    assert judge.thresholds == {}


def test_the_deployment_yaml_pins_each_role_to_the_owners_model() -> None:
    deployment = load_judge_deployment(DEPLOYMENT_YAML)

    assert jev_model(deployment.judge) == "jev-1.13"
    assert escalation_role(deployment.judge).model == "openai/gpt-6-luna"
    assert [role.model for role in labeller_roles(deployment.judge)] == [
        "openai/gpt-6-astra:batch",
        "anthropic/claude-opus-5.5:batch",
    ]
    assert reference_writer_role(deployment.reference_writer).model == (
        "anthropic/claude-opus-5.5:batch"
    )
    assert reference_writer_fallback_role(deployment.reference_writer).model == (
        "anthropic/claude-sonnet-5:batch"
    )


def test_a_role_keeps_its_block_settings_under_its_pinned_model() -> None:
    writer = ReferenceWriterConfig(model="anthropic/a:batch", fallback_model="anthropic/b:batch")

    fallback = reference_writer_fallback_role(writer)

    assert fallback == ChatRole(
        model_key="reference_writer.fallback_model",
        config=ChatRoleConfig(model="anthropic/b:batch"),
    )


def test_an_unknown_key_in_the_deployment_is_refused_by_name(tmp_path: Path) -> None:
    misspelt = tmp_path / "judge.yaml"
    misspelt.write_text("judge:\n  escalation:\n    modle: openai/gpt-6-luna\n", encoding="utf-8")

    with pytest.raises(ValidationError, match="modle"):
        load_judge_deployment(misspelt)


def test_an_empty_deployment_file_is_every_default(tmp_path: Path) -> None:
    empty = tmp_path / "judge.yaml"
    empty.write_text("", encoding="utf-8")

    assert load_judge_deployment(empty) == JudgeDeployment()


_CHAT_SERVING_YAML = BENCHMARKS_ROOT / "configs" / "ask_openrouter_example_needle_chat.yaml"


def _chat_model() -> str:
    """The agent under test, as the chat serving config pins it."""
    serving = yaml.safe_load(_CHAT_SERVING_YAML.read_text(encoding="utf-8"))
    return serving["ask_your_docs"]["llm"]["model"]


def test_a_model_family_is_its_vendor_prefix() -> None:
    assert model_family("openai/gpt-6-luna") == "openai"
    assert model_family("anthropic/claude-opus-5.5:batch") == "anthropic"
    assert model_family("qwen/qwen3.8-27b") == "qwen"
    # OpenRouter serves a bare System One id under the typesafe/ namespace.
    assert model_family("jev-1.13") == "typesafe"


def test_the_deployment_keeps_every_family_apart() -> None:
    deployment = load_judge_deployment(DEPLOYMENT_YAML)

    assert _chat_model() == "qwen/qwen3.8-27b"
    assert role_family_violations(deployment, chat_model=_chat_model()) == ()


def _deployment(**pins: str) -> JudgeDeployment:
    """The owner's deployment with some pins replaced."""
    owner = load_judge_deployment(DEPLOYMENT_YAML)
    judge, writer = owner.judge, owner.reference_writer
    labellers = (
        ChatRoleConfig(model=pins.get("labeller_0", judge.alignment.labellers[0].model)),
        ChatRoleConfig(model=pins.get("labeller_1", judge.alignment.labellers[1].model)),
    )
    return JudgeDeployment(
        judge=JudgeConfig(
            jev=judge.jev.model_copy(update={"model": pins.get("jev", judge.jev.model)}),
            escalation=judge.escalation.model_copy(
                update={"model": pins.get("escalation", judge.escalation.model)}
            ),
            alignment=AlignmentConfig(labellers=labellers),
        ),
        reference_writer=writer.model_copy(
            update={
                "model": pins.get("writer", writer.model),
                "fallback_model": pins.get("fallback", writer.fallback_model),
            }
        ),
    )


@pytest.mark.parametrize(
    ("pins", "broken"),
    [
        ({"labeller_1": "openai/gpt-6-sol:batch"}, "judge.alignment.labellers[1].model"),
        ({"escalation": "qwen/qwen3-judge"}, "judge.escalation.model"),
        ({"labeller_0": "qwen/qwen3.8-27b:batch"}, "judge.alignment.labellers[0].model"),
        ({"jev": "qwen/jev-clone"}, "judge.jev.model"),
        ({"labeller_1": "typesafe/jev-labeller"}, "judge.jev.model"),
        ({"writer": "openai/gpt-6-sol:batch"}, "reference_writer.model"),
        ({"fallback": "qwen/qwen3-writer:batch"}, "reference_writer.fallback_model"),
    ],
)
def test_a_role_sharing_a_family_it_must_not_is_named(pins: dict[str, str], broken: str) -> None:
    violations = role_family_violations(_deployment(**pins), chat_model="qwen/qwen3.8-27b")

    assert any(broken in violation for violation in violations), violations


def test_the_escalation_judge_may_share_the_openai_family_with_a_labeller() -> None:
    """The owner's Round 5d: OpenAI judges — Astra labels, Luna escalates — so Luna and Astra share it."""
    deployment = load_judge_deployment(DEPLOYMENT_YAML)

    assert model_family(deployment.judge.escalation.model) == "openai"
    assert model_family(deployment.judge.alignment.labellers[0].model) == "openai"
    assert role_family_violations(deployment, chat_model="qwen/qwen3.8-27b") == ()


def test_the_eval_suite_depends_on_no_typesafe_sdk() -> None:
    project = tomllib.loads((BENCHMARKS_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    requirements = list(project["dependencies"])
    for extra in project.get("optional-dependencies", {}).values():
        requirements += extra

    assert requirements
    assert [each for each in requirements if "typesafe" in each.lower()] == []


@pytest.mark.parametrize("dataset", list(JudgedDataset))
def test_every_judged_dataset_is_a_registered_dataset(dataset: JudgedDataset) -> None:
    """The judge keeps its own copy of the names (its import floor); this test ties the two."""
    assert dataset.value in dataset_registry.names()
