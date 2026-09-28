"""The answer judge's configuration: ``judge.yaml`` typed by :class:`JudgeConfig`.

Every default lives once, as a ``Field`` default here; the shipped
``configs/judge.yaml`` restates each one for the reader. The Jev block holds
only what the code-first check reads today; the judge clients add their own
blocks and model pins beside it.

Example:
    >>> load_judge_config().jev.max_answer_chars
    12000
"""

from __future__ import annotations

from functools import lru_cache
from importlib.resources import files

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

_CONFIG_PACKAGE = "pydocs_eval.judge.configs"
_CONFIG_RESOURCE = "judge.yaml"

# An answer longer than this is flagged, never truncated and never sent to Jev
# as state: a truncated answer would be judged on text the agent did not end on.
_DEFAULT_MAX_ANSWER_CHARS = 12000

#: The extensions an answer can cite a file by: ``.py`` plus the text and config
#: set the product indexes by default, since chat gold names README and config
#: files.
DEFAULT_CITATION_EXTENSIONS: tuple[str, ...] = (
    ".py",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".cfg",
    ".ini",
    ".rst",
    ".txt",
    ".json",
)


class JevConfig(BaseModel):
    """``judge.jev``: what the Jev tier and the code-first check read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_answer_chars: int = Field(default=_DEFAULT_MAX_ANSWER_CHARS, ge=1)
    citation_extensions: tuple[str, ...] = Field(default=DEFAULT_CITATION_EXTENSIONS, min_length=1)

    @field_validator("citation_extensions")
    @classmethod
    def _dotted_extensions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        bad = [ext for ext in value if not (ext.startswith(".") and ext[1:].isalnum())]
        if bad:
            raise ValueError(f"citation_extensions {bad!r}, expected entries like '.py' or '.md'")
        return value


class JudgeConfig(BaseModel):
    """The whole ``judge.yaml``; an unknown key is refused by name."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    jev: JevConfig = Field(default_factory=JevConfig)


@lru_cache(maxsize=1)
def load_judge_config() -> JudgeConfig:
    """The shipped ``judge.yaml``, validated and cached."""
    text = files(_CONFIG_PACKAGE).joinpath(_CONFIG_RESOURCE).read_text(encoding="utf-8")
    return JudgeConfig.model_validate(yaml.safe_load(text) or {})


__all__ = ("DEFAULT_CITATION_EXTENSIONS", "JevConfig", "JudgeConfig", "load_judge_config")
