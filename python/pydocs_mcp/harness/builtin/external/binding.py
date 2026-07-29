"""The external CLI harness's ``HarnessRunner`` binding (run-contract §9 stage 2).

The ONLY module that knows this harness's concrete settings type: a generic
caller holds a dotted path to :func:`make_harness_runner`, passes a plain
settings mapping, and gets back the port. No optional extra gates it — the
engine is driven with stdlib ``subprocess``, so the composed harness ships in
the product wheel and costs nothing until an arm runs it.

Delivery map (constraint C2 / design §4): this harness delivers the three
guidance tiers as ONE folded block on the engine's guidance channel. The keys
are PATTERNS (``<task_name>`` is a literal placeholder — one key per TIER, not
one per task), because the partition itself is pattern-based: see
``harness/platform/guidance_fold.py`` for why that shape is load-bearing rather
than a convenience. Which task's heads actually fold is arm state (the sample's
``task_name``), and the map's digest folds into the arm cell fingerprint —
delivery mode is a first-order variable.

Engine vs harness: the delivery map's value composes the engine's
``guidance_flag`` with THIS harness's candidate slot, so
:func:`delivery_map_digest` resolves the default engine. That is not an
identity hole — an arm's ``settings.engine`` folds into its arm hash directly,
so two engines are two arms whatever the digest says. It IS a recorded
record-fidelity gap: the bridge that stamps a digest onto an arm's ledger row
calls this with no argument, so a non-default-engine arm's row names the
DEFAULT engine's map. Threading the arm's settings through arm identity is a
measurement change of its own; until then the gap is stated here and in the
run-contract design amendment's structural-gaps list rather than papered over.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from pydocs_mcp.application.description_source import FROZEN_TOOL_NAMES
from pydocs_mcp.harness.platform.composed_harness import CliAgentHarness
from pydocs_mcp.harness.platform.engines.registry import cli_agent_registry
from pydocs_mcp.harness.platform.guidance_fold import (
    OTHER_HARNESS_PROMPT_SECTION_KEYS,
    deliverable_section_keys,
)
from pydocs_mcp.harness.platform.skill_artifact import HARNESS_NAMES, SkillArtifactError

# This harness's name in the ``HARNESS_TASK_HEAD: <harness>.<task>`` tier — the
# product's ``HARNESS_NAMES`` entry for the external CLI track. Every engine
# running under this harness shares these sections.
EXTERNAL_HARNESS_NAME = "external"

if EXTERNAL_HARNESS_NAME not in HARNESS_NAMES:  # pragma: no cover — import-time invariant
    # Tied to the enumeration rather than merely commented as matching it: the
    # fold's "another arm's slice" rule classifies an unrecognized
    # ``HARNESS_TASK_HEAD: <name>.<task>`` as somebody else's and DROPS it, so a
    # drifted name would silently run every rollout without its harness tier
    # instead of raising. Loud at import beats silent at scoring time.
    raise SkillArtifactError(
        f"external harness name {EXTERNAL_HARNESS_NAME!r} is not in the product's "
        f"harness enumeration {list(HARNESS_NAMES)} — its HARNESS_TASK_HEAD tier "
        "would be delivered nowhere"
    )

# The candidate SLOT this harness carries guidance in. The delivery map's value
# is ``<engine flag>.<this slot>``: the flag is the engine's, the slot is the
# harness's, so two composed harnesses sharing one engine still publish
# different maps.
_GUIDANCE_SLOT = "skill_block"

# The engine an arm gets when it names none. Single source: the settings field
# default and the delivery-map resolution both read it.
DEFAULT_CLI_ENGINE = "claude_code"

# The literal placeholder standing in for a task name in the map's keys.
_TASK_NAME_PLACEHOLDER = "<task_name>"

# The turn cap an arm gets when it names none — the established CLI-arm cap
# (the in-process harness's 12 is a different agent with a different loop).
_DEFAULT_MAX_AGENT_TURNS = 40

# The per-run wall bound the harness itself enforces. The campaign wrapper adds
# its own outer bound; this inner one is what actually kills the process group.
_DEFAULT_TASK_TIMEOUT_SECONDS = 900.0


class ExternalRunnerSettings(BaseModel):
    """This harness's private settings — validated HERE, nowhere upstream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    workspace: str
    model: str
    trace_root: str
    engine: str = DEFAULT_CLI_ENGINE
    mcp: bool = True
    pydocs_config: str | None = None
    # Accepted and INERT: the arms platform stamps the bound rubric section's
    # graph name onto every arm's settings, and this harness has no agent graph
    # to route. Rejecting it (extra="forbid") would fail every external arm on
    # its first rollout; silently renaming it would hide the run's real shape.
    architecture: str | None = None
    # The explicit MCP tool surface, REPLACING the profile grant when set — the
    # drop-one arm shape. The arms platform sets it from the cell's own
    # ``tool_names``, which is what arm identity folds, so the vocabulary is
    # the SERVER's (the frozen nine); the engine adapter spells it.
    tool_names: tuple[str, ...] | None = None
    max_agent_turns: int = _DEFAULT_MAX_AGENT_TURNS
    task_timeout_seconds: float = _DEFAULT_TASK_TIMEOUT_SECONDS

    @field_validator("tool_names")
    @classmethod
    def _check_frozen_nine(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        """Reject a surface outside the frozen nine, before any spend.

        The engine namespaces whatever it is handed, so an already-namespaced
        or misspelled name would silently become a grant the CLI resolves to
        nothing — an arm that runs tool-less while its ledger row says
        drop-one. Fails loud instead, the ``_select_bound_tools`` precedent.
        """
        if value is None:
            return None
        unknown = tuple(name for name in value if name not in FROZEN_TOOL_NAMES)
        if unknown:
            raise ValueError(
                f"invalid tool_names {list(unknown)}: an arm may only narrow within the "
                f"frozen nine {list(FROZEN_TOOL_NAMES)}, spelled as the SERVER names them "
                "(the engine adds its own namespace)"
            )
        return value

    @model_validator(mode="after")
    def _check_surface_has_a_server(self) -> ExternalRunnerSettings:
        """A narrowed MCP surface needs an MCP server to narrow.

        ``tool_names`` names server tools only, so pairing it with ``mcp:
        false`` would grant tools nothing provides — a silently tool-less arm.
        """
        if self.tool_names is not None and not self.mcp:
            raise ValueError(
                f"invalid settings: tool_names {list(self.tool_names)} narrows the MCP "
                "surface, but mcp is false — a bare arm has no server tools to narrow"
            )
        return self


def delivery_map(engine: str = DEFAULT_CLI_ENGINE) -> Mapping[str, str]:
    """This harness's section→channel map for ``engine``.

    Built FROM :func:`~pydocs_mcp.harness.platform.guidance_fold.deliverable_section_keys`
    rather than re-spelling its tiers: the map is the hashed statement of what
    this harness delivers, so narrowing or widening the fold must move the
    digest by construction — a hand-kept duplicate would leave the digest still
    while the delivered text changed.

    Example:
        >>> delivery_map()["BACKBONE"]
        'append_system_prompt.skill_block'
    """
    return _delivery_map_for_channel(guidance_channel(engine))


def guidance_channel(engine: str) -> str:
    """This harness's ``<flag>.<slot>`` channel for ``engine``.

    Example:
        >>> guidance_channel("claude_code")
        'append_system_prompt.skill_block'
    """
    return f"{cli_agent_registry.get(engine).guidance_flag}.{_GUIDANCE_SLOT}"


def _delivery_map_for_channel(channel: str) -> Mapping[str, str]:
    """The tier keys routed to one channel — the map's shape, engine-free."""
    return MappingProxyType(
        dict.fromkeys(
            deliverable_section_keys(
                harness_name=EXTERNAL_HARNESS_NAME, task_name=_TASK_NAME_PLACEHOLDER
            ),
            channel,
        )
    )


def delivery_map_digest(engine: str = DEFAULT_CLI_ENGINE) -> str:
    """SHA-256 of the canonical delivery map — folded into the arm fingerprint
    so a delivery change is a recorded configuration change."""
    return _digest_of(delivery_map(engine))


def _digest_of(delivered: Mapping[str, str]) -> str:
    """The canonical digest payload, shared with the in-process harness's
    spelling so the two are read the same way (they never mix inside one hash).

    ``recognized_undelivered`` names the concrete sections this harness
    recognizes and drops. The tier-pattern drops (another task's head, another
    harness's head) are structural rather than enumerable, and are stated in
    ``guidance_fold``'s docstring rather than spelled as invented placeholders.
    """
    payload = json.dumps(
        {
            "delivered": dict(delivered),
            "recognized_undelivered": list(OTHER_HARNESS_PROMPT_SECTION_KEYS),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_harness_runner(settings: Mapping[str, object]) -> CliAgentHarness:
    """Build this harness's ``HarnessRunner`` from a plain settings mapping.

    The generic composition root resolves this function by dotted path and
    never sees the concrete settings type — validation (``extra="forbid"``, so
    a typo fails loud) and the engine lookup (an unregistered name names the
    supported set) both happen here, before any spend.
    """
    validated = ExternalRunnerSettings.model_validate(dict(settings))
    return CliAgentHarness(
        harness_name=EXTERNAL_HARNESS_NAME,
        adapter=cli_agent_registry.build(validated.engine),
        settings=validated,
    )
