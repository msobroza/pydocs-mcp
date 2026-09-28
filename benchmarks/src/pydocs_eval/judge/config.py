"""The answer judge's configuration: ``judge.yaml`` and ``reference_writer.yaml``.

``judge.yaml`` is typed by :class:`JudgeConfig`, one block per role: ``jev``
(the Jev judge and the code-first check), ``escalation`` (the in-band judge),
``alignment`` (the two blind labellers) and ``thresholds`` (Jev's review bands,
per Jev model, dataset and question). ``reference_writer.yaml`` is typed by
:class:`~pydocs_eval.judge.role_config.ReferenceWriterConfig`.

Every default lives once: as a ``Field`` default here or in ``role_config``,
or, for the citable extensions, in ``pydocs_eval.gold_extensions``. The shipped
YAMLs restate each one for the reader, and a test holds them together. Every
role's ``model`` is empty, so nothing is called until the deployment YAML
(``benchmarks/configs/judge_openrouter.yaml``, read by
:func:`load_judge_deployment`) pins it.

Example:
    >>> load_judge_config().jev.max_answer_chars
    12000
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from pydocs_eval.gold_extensions import GOLD_FILE_EXTENSIONS
from pydocs_eval.judge.role_config import (
    OPENROUTER_API_BASE,
    OPENROUTER_API_KEY_ENV,
    AlignmentConfig,
    EscalationConfig,
    ReferenceWriterConfig,
)
from pydocs_eval.judge.thresholds import ThresholdTable

_CONFIG_PACKAGE = "pydocs_eval.judge.configs"
_JUDGE_RESOURCE = "judge.yaml"
_REFERENCE_WRITER_RESOURCE = "reference_writer.yaml"

# An answer longer than this is flagged, never truncated and never sent to Jev
# as state: a truncated answer would be judged on text the agent did not end on.
_DEFAULT_MAX_ANSWER_CHARS = 12000
# Past this many gold sites or files, one answer's Jev request is split in parts.
_DEFAULT_MAX_GOLD_FILES_PER_REQUEST = 12
# A Jev call answers in 0.1-1 s; ten seconds is an outage, not a slow answer.
_DEFAULT_JEV_TIMEOUT_SECONDS = 10.0
_DEFAULT_JEV_RETRIES = 2

#: The extensions an answer can cite a file by: every file type gold may name,
#: since chat gold names README and config files.
DEFAULT_CITATION_EXTENSIONS: tuple[str, ...] = GOLD_FILE_EXTENSIONS


class JevConfig(BaseModel):
    """``judge.jev``: the Jev judge's pin and call settings, and what the code-first check reads.

    ``cache_dir`` empty means the derived default (``cache_root()/jev``).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = ""
    endpoint: str = OPENROUTER_API_BASE
    api_key_env: str = OPENROUTER_API_KEY_ENV
    timeout_seconds: float = Field(default=_DEFAULT_JEV_TIMEOUT_SECONDS, gt=0)
    retries: int = Field(default=_DEFAULT_JEV_RETRIES, ge=0)
    cache_dir: str = ""
    max_answer_chars: int = Field(default=_DEFAULT_MAX_ANSWER_CHARS, ge=1)
    max_gold_files_per_request: int = Field(default=_DEFAULT_MAX_GOLD_FILES_PER_REQUEST, ge=1)
    citation_extensions: tuple[str, ...] = Field(default=DEFAULT_CITATION_EXTENSIONS, min_length=1)

    @field_validator("citation_extensions")
    @classmethod
    def _dotted_extensions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        malformed = [
            extension
            for extension in value
            if not (extension.startswith(".") and extension[1:].isalnum())
        ]
        if malformed:
            raise ValueError(
                f"citation_extensions {malformed!r}, expected entries like '.py' or '.md'"
            )
        return value


class JudgeConfig(BaseModel):
    """The whole ``judge.yaml``; an unknown key is refused by name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    jev: JevConfig = Field(default_factory=JevConfig)
    escalation: EscalationConfig = Field(default_factory=EscalationConfig)
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
    thresholds: ThresholdTable = Field(default_factory=dict)


class JudgeDeployment(BaseModel):
    """One deployment's pins: ``judge`` (``judge.yaml``'s shape) and ``reference_writer``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    reference_writer: ReferenceWriterConfig = Field(default_factory=ReferenceWriterConfig)


@lru_cache(maxsize=1)
def load_judge_config() -> JudgeConfig:
    """The shipped ``judge.yaml``, validated and cached."""
    return JudgeConfig.model_validate(_shipped_yaml(_JUDGE_RESOURCE))


@lru_cache(maxsize=1)
def load_reference_writer_config() -> ReferenceWriterConfig:
    """The shipped ``reference_writer.yaml``, validated and cached."""
    return ReferenceWriterConfig.model_validate(_shipped_yaml(_REFERENCE_WRITER_RESOURCE))


def load_judge_deployment(path: Path) -> JudgeDeployment:
    """A deployment YAML: what it pins over every default; an unknown key is refused.

    Example:
        >>> load_judge_deployment(Path("configs/judge_openrouter.yaml")).judge.jev.model  # doctest: +SKIP
        'jev-1.13'
    """
    return JudgeDeployment.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def _shipped_yaml(resource: str) -> object:
    text = files(_CONFIG_PACKAGE).joinpath(resource).read_text(encoding="utf-8")
    return yaml.safe_load(text) or {}


__all__ = (
    "DEFAULT_CITATION_EXTENSIONS",
    "JevConfig",
    "JudgeConfig",
    "JudgeDeployment",
    "load_judge_config",
    "load_judge_deployment",
    "load_reference_writer_config",
)
