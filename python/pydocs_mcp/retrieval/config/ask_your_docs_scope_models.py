"""The ``ask_your_docs.scope`` block and its closed vocabularies.

UI spec 2026-09-04-ask-your-docs-branch-scope-ui-design §6.2, §7. Split out of
``ask_your_docs_models.py`` to keep that module inside its line budget; the old
path re-exports every name here, so importers keep one import site.

The harness package is mypy-excluded, so these enums live here (a checked
module) and ``harness/ask_your_docs/question_scope.py`` re-exports them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The YAML spelling of "no project default" (ask_your_docs.scope.project).
ANY_PROJECT = "any"
# Single source of the fan-out cap default (the YAML restates it for readers).
_DEFAULT_SCOPE_MAX_CELLS = 4


class ScopeSlice(StrEnum):
    """Which part of a branch a search covers (UI spec 2026-09-04 §6.2).

    The server spells the last two ``changed`` / ``diff`` (multi-branch spec
    §6.5); whole branch sends no ``scope`` value at all.
    """

    WHOLE_BRANCH = "whole_branch"
    CHANGED_FILES = "changed_files"
    DIFF_HUNKS = "diff_hunks"


class ScopeCode(StrEnum):
    """Today's own-code vs dependency filter; OWN is the server's ``project``."""

    ALL = "all"
    OWN = "own"
    DEPS = "deps"


class ScopeBranchDefault(StrEnum):
    """Symbolic branch defaults — split from a free branch name so a branch
    literally named ``base`` stays addressable through ``branch_name``."""

    BASE = "base"
    CHECKED_OUT = "checked_out"


class ScopeDefaultsConfig(BaseModel):
    """Soft scope defaults for the chat and graph pages (UI spec 2026-09-04 §7).

    The "Where to search" strip and picker override these for one session
    only; they fill what the model leaves unspecified and never overwrite a
    model-passed argument. ``branch_name`` is checked against the workspace's
    branch listing at resolution time, not here — the config is
    workspace-agnostic.
    """

    model_config = ConfigDict(extra="forbid")

    project: str = Field(default=ANY_PROJECT)
    branch_default: ScopeBranchDefault = Field(default=ScopeBranchDefault.BASE)
    branch_name: str = Field(default="")
    slice: ScopeSlice = Field(default=ScopeSlice.WHOLE_BRANCH)
    code: ScopeCode = Field(default=ScopeCode.ALL)
    package: str = Field(default="")
    max_cells: int = Field(default=_DEFAULT_SCOPE_MAX_CELLS, ge=1, le=16)
    # D14 typed tokens: "in:<project>" / "on:<branch>" inside the question, one-shot;
    # false leaves the text untouched and parses nothing (UI spec §6.10a).
    tokens_enabled: bool = Field(default=True)
    # The footer's teaching hint ("add in:<name> to search there too", §6.8); it only
    # ever renders when tokens_enabled is also true.
    footer_hint: bool = Field(default=True)

    @model_validator(mode="after")
    def _slice_excludes_dependencies_only(self) -> ScopeDefaultsConfig:
        # E11: the server's deps slice and its changed/diff slices are disjoint.
        if self.slice is not ScopeSlice.WHOLE_BRANCH and self.code is ScopeCode.DEPS:
            raise ValueError(
                f"ask_your_docs.scope: slice={self.slice.value!r} cannot combine with "
                f"code={self.code.value!r}; expected code 'all' or 'own' with a slice"
            )
        return self


__all__ = (
    "ANY_PROJECT",
    "ScopeBranchDefault",
    "ScopeCode",
    "ScopeDefaultsConfig",
    "ScopeSlice",
)
