"""The ``ask_architecture`` structured discrete artifact (spec §3.2.2).

A canonical-YAML document selecting one value per named dimension of the
ask-architecture search space: which registry entry answers, whether the
rewrite interceptor runs, whether the scope pin stays, which retrieval
overlay serves, and the agent-turn cap. ``render()`` emits sorted-key YAML so
fingerprints are stable across key-order permutations; ``enumerate_space``
yields the cross-product for the grid/random/halving optimizer.
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, replace
from pathlib import Path

import yaml

from pydocs_eval.optimize._agent_track_binding import _DEFAULT_MAX_TURNS
from pydocs_eval.optimize.ask_binding import (
    _DEFAULT_ASK_ARCHITECTURE,
    ask_architecture_registry,
)
from pydocs_eval.optimize.registries import artifact_registry

# WHY: only behaviors the product can already express may be searched; the
# turn ceiling mirrors the agent-track default (one bump site).
_MAX_ASK_TURNS = _DEFAULT_MAX_TURNS
_DEFAULT_MAX_AGENT_TURNS = 12

# WHY a repo-relative default: the shipped run configs name pipeline STEMS
# under this directory; __main__ wiring passes an absolute dir at run time.
_DEFAULT_PIPELINES_DIR = Path("benchmarks/configs/pipelines")

# The searchable dimensions IN CANONICAL ORDER — enumerate_space iterates
# this tuple (never the caller's dict order) so cell order is deterministic.
_DIMENSION_FIELDS = (
    "architecture",
    "rewrite_enabled",
    "scope_pin",
    "retrieval_config",
    "max_agent_turns",
)


@artifact_registry.register("ask_architecture")
@dataclass(frozen=True, slots=True)
class AskArchitectureArtifact:
    """One cell of the ask-architecture search space (spec §3.2.2)."""

    architecture: str = _DEFAULT_ASK_ARCHITECTURE
    rewrite_enabled: bool = True
    scope_pin: bool = True
    retrieval_config: str = ""
    max_agent_turns: int = _DEFAULT_MAX_AGENT_TURNS
    pipelines_dir: Path = _DEFAULT_PIPELINES_DIR
    name: str = "ask_architecture"
    content: str | None = None

    def render(self) -> str:
        """Canonical sorted-key YAML of the cell (or the raw candidate text)."""
        if self.content is not None:
            return self.content
        cell = {field: getattr(self, field) for field in _DIMENSION_FIELDS}
        return yaml.safe_dump(cell, sort_keys=True)

    def with_content(self, content: str) -> AskArchitectureArtifact:
        """Return a copy carrying ``content`` as the candidate document."""
        return replace(self, content=content)

    def validate(self) -> tuple[str, ...]:
        """Return constraint violations; empty tuple == valid (never raises).

        Every dimension present, no unknown keys, ``architecture`` registered,
        ``retrieval_config`` resolving to an existing pipelines YAML
        (fail-loud — ``AppConfig.load`` silently ignores missing overlays, so
        the artifact must not), and turns within ``1..{_MAX_ASK_TURNS}``.
        """
        parsed = _parse_mapping(self.render())
        if parsed is None:
            return ("candidate is not a YAML mapping",)
        return (
            *_key_violations(parsed),
            *_value_violations(parsed, pipelines_dir=self.pipelines_dir),
        )

    def landing_note(self) -> str:
        """Explain how a human lands a proposal from this artifact."""
        return (
            "Pin the winning cell in the deployment: architecture + images "
            "settings in the ask_your_docs YAML block, the retrieval overlay "
            "as the serve --config, and the turn cap in the harness run "
            "config. Nothing lands in product code."
        )

    @property
    def fingerprint(self) -> str:
        """SHA-256 hex digest of the canonical render (64 chars)."""
        return hashlib.sha256(self.render().encode()).hexdigest()

    @classmethod
    def enumerate_space(
        cls,
        dims: Mapping[str, Sequence[object]],
        *,
        pipelines_dir: Path = _DEFAULT_PIPELINES_DIR,
    ) -> tuple[AskArchitectureArtifact, ...]:
        """The full cross-product of ``dims``, deterministically ordered (AC-5).

        ``dims`` maps dimension name → candidate values (from run config,
        never from code). Iteration follows the canonical field order, so the
        caller's key order never changes cell order or fingerprints.

        Raises:
            KeyError: a dimension name outside the searchable field set.
        """
        unknown = sorted(set(dims) - set(_DIMENSION_FIELDS))
        if unknown:
            raise KeyError(f"unknown dimension(s) {unknown}; have {list(_DIMENSION_FIELDS)}")
        # WHY fields(): slots=True replaces class attributes with slot
        # descriptors, so field defaults are only reachable via the dataclass
        # field metadata.
        defaults = {f.name: f.default for f in fields(cls)}
        axes = [
            tuple(dims.get(field_name, (defaults[field_name],))) for field_name in _DIMENSION_FIELDS
        ]
        return tuple(
            cls(**dict(zip(_DIMENSION_FIELDS, cell, strict=True)), pipelines_dir=pipelines_dir)  # type: ignore[arg-type]
            for cell in itertools.product(*axes)
        )


def _parse_mapping(text: str) -> dict[str, object] | None:
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _key_violations(parsed: Mapping[str, object]) -> tuple[str, ...]:
    violations = [f"missing dimension {key!r}" for key in _DIMENSION_FIELDS if key not in parsed]
    violations += [
        f"unknown key {key!r}; have {list(_DIMENSION_FIELDS)}"
        for key in parsed
        if key not in _DIMENSION_FIELDS
    ]
    return tuple(violations)


def _value_violations(parsed: Mapping[str, object], *, pipelines_dir: Path) -> tuple[str, ...]:
    violations: list[str] = []
    architecture = parsed.get("architecture")
    if architecture is not None and architecture not in ask_architecture_registry.names():
        violations.append(
            f"unknown architecture {architecture!r}; have {list(ask_architecture_registry.names())}"
        )
    stem = parsed.get("retrieval_config")
    if isinstance(stem, str) and stem and not (pipelines_dir / f"{stem}.yaml").exists():
        violations.append(
            f"retrieval_config {stem!r} does not resolve to {pipelines_dir}/{stem}.yaml"
        )
    turns = parsed.get("max_agent_turns")
    if isinstance(turns, int) and not 1 <= turns <= _MAX_ASK_TURNS:
        violations.append(f"max_agent_turns {turns} outside 1..{_MAX_ASK_TURNS}")
    return tuple(violations)
