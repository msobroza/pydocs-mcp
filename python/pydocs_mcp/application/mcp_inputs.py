"""Pydantic input models for MCP tools (sub-PR #6 §4.3).

Enforces format via regex + protocol-safety caps. Limits are permissive:
query up to 30k chars, limit up to 1000 — covers runaway clients without
rejecting legit edge cases.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Format validators — reject malformed input at the boundary.
_PACKAGE_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9._-]*[a-zA-Z0-9])?|__project__)$"
)  # alphanumeric start AND end (rejects trailing dot/dash); dots/dashes/underscores allowed in middle
_TARGET_RE = re.compile(
    r"^(?:[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)?$"
)  # empty or dotted-identifier chain; rejects foo..bar, foo., leading digit


class SearchInput(BaseModel):
    """Input for the ``search`` MCP tool (spec §4.1)."""

    query: str = Field(min_length=1, max_length=30000)
    kind: Literal["docs", "api", "any"] = "any"
    package: str = ""
    scope: Literal["project", "deps", "all"] = "all"
    limit: int = Field(default=10, ge=1, le=1000)

    @field_validator("package")
    @classmethod
    def _check_package(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError(
                "package must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ or be '__project__'"
            )
        return v


class LookupInput(BaseModel):
    """Input for the ``lookup`` MCP tool (spec §4.1)."""

    target: str = ""
    show: Literal["default", "tree", "callers", "callees", "inherits"] = "default"

    @field_validator("target")
    @classmethod
    def _check_target(cls, v: str) -> str:
        if v and not _TARGET_RE.match(v):
            raise ValueError(
                "target must be a dotted identifier like 'pkg.mod.Class.method' or empty"
            )
        return v
