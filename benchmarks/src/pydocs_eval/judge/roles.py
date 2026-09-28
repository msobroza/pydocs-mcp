"""The judge roles as a deployment pins them: one builder per role, and the family rule.

Each builder is the only way its role reaches a client, so an empty ``model``
refuses there, naming the YAML key to set — before a request is built, let
alone paid for. :func:`role_family_violations` keeps the model families apart
the way the owner set them (turn-efficiency decision log, Rounds 5d and 5f):
Qwen is the agent under test, Anthropic writes the references, OpenAI and
TypeSafe judge. No judging role shares the chat model's family, the two
labellers are of two families, Jev shares none with a labeller, and the writer
and its fallback share none with the escalation judge or the chat model. The
escalation judge and a labeller may both be OpenAI (Round 5d names OpenAI as
the family of both); the alignment label needs the two labellers to agree.

Example:
    >>> ChatRole("judge.escalation.model", ChatRoleConfig(model="openai/gpt-6-luna")).model
    'openai/gpt-6-luna'
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_eval.judge.config import JudgeConfig, JudgeDeployment
from pydocs_eval.judge.role_config import ChatRoleConfig, ReferenceWriterConfig, pinned_model

_JEV_MODEL_KEY = "judge.jev.model"
_ESCALATION_MODEL_KEY = "judge.escalation.model"
_WRITER_MODEL_KEY = "reference_writer.model"
_WRITER_FALLBACK_MODEL_KEY = "reference_writer.fallback_model"
# OpenRouter serves a bare System One id (jev-1.13) under this namespace.
_SYSTEM_ONE_FAMILY = "typesafe"


def _labeller_model_key(index: int) -> str:
    return f"judge.alignment.labellers[{index}].model"


@dataclass(frozen=True, slots=True)
class ChatRole:
    """One chat-completion role, ready to call: its pinned model's key and its settings.

    Raises:
        JudgeConfigError: ``config.model`` is empty, named by ``model_key``.
    """

    model_key: str
    config: ChatRoleConfig

    def __post_init__(self) -> None:
        pinned_model(self.model_key, self.config.model)

    @property
    def model(self) -> str:
        return self.config.model


def jev_model(judge: JudgeConfig) -> str:
    """The Jev model ``judge`` pins, refused by its key when empty."""
    return pinned_model(_JEV_MODEL_KEY, judge.jev.model)


def escalation_role(judge: JudgeConfig) -> ChatRole:
    """The in-band escalation judge ``judge`` pins."""
    return ChatRole(_ESCALATION_MODEL_KEY, judge.escalation)


def labeller_roles(judge: JudgeConfig) -> tuple[ChatRole, ChatRole]:
    """The two blind alignment labellers ``judge`` pins, in their YAML order."""
    first, second = judge.alignment.labellers
    return ChatRole(_labeller_model_key(0), first), ChatRole(_labeller_model_key(1), second)


def reference_writer_role(writer: ReferenceWriterConfig) -> ChatRole:
    """The reference-answer writer ``writer`` pins."""
    return ChatRole(_WRITER_MODEL_KEY, _chat_settings_of(writer, writer.model))


def reference_writer_fallback_role(writer: ReferenceWriterConfig) -> ChatRole:
    """The writer's fallback: ``fallback_model`` under the writer block's own settings."""
    return ChatRole(_WRITER_FALLBACK_MODEL_KEY, _chat_settings_of(writer, writer.fallback_model))


def _chat_settings_of(writer: ReferenceWriterConfig, model: str) -> ChatRoleConfig:
    settings = writer.model_dump(exclude={"fallback_model"})
    return ChatRoleConfig.model_validate({**settings, "model": model})


def model_family(model: str) -> str:
    """The family a model id belongs to: its vendor prefix, ``typesafe`` for a bare System One id.

    Example:
        >>> model_family("anthropic/claude-opus-5.5:batch"), model_family("jev-1.13")
        ('anthropic', 'typesafe')
    """
    vendor, slash, _ = model.partition("/")
    return vendor if slash else _SYSTEM_ONE_FAMILY


@dataclass(frozen=True, slots=True)
class _Pin:
    """A pinned model and the name the family rule reports it by."""

    name: str
    model: str

    @property
    def family(self) -> str:
        return model_family(self.model)


def role_family_violations(deployment: JudgeDeployment, *, chat_model: str) -> tuple[str, ...]:
    """Every family rule ``deployment`` breaks against ``chat_model``, one sentence each.

    An unpinned role is not checked: it cannot run.
    """
    judge, writer = deployment.judge, deployment.reference_writer
    jev = _Pin(_JEV_MODEL_KEY, judge.jev.model)
    escalation = _Pin(_ESCALATION_MODEL_KEY, judge.escalation.model)
    first, second = (
        _Pin(_labeller_model_key(i), block.model)
        for i, block in enumerate(judge.alignment.labellers)
    )
    writers = (
        _Pin(_WRITER_MODEL_KEY, writer.model),
        _Pin(_WRITER_FALLBACK_MODEL_KEY, writer.fallback_model),
    )
    chat = _Pin("the chat model", chat_model)
    pairs = [
        (first, second),
        *((judging, chat) for judging in (jev, escalation, first, second)),
        (jev, first),
        (jev, second),
        *((pin, other) for pin in writers for other in (escalation, chat)),
    ]
    return tuple(_violation(pin, other) for pin, other in pairs if _shares_family(pin, other))


def _shares_family(pin: _Pin, other: _Pin) -> bool:
    return bool(pin.model and other.model) and pin.family == other.family


def _violation(pin: _Pin, other: _Pin) -> str:
    return f"{pin.name}={pin.model} and {other.name}={other.model} share the {pin.family} family"


__all__ = (
    "ChatRole",
    "escalation_role",
    "jev_model",
    "labeller_roles",
    "model_family",
    "reference_writer_fallback_role",
    "reference_writer_role",
    "role_family_violations",
)
