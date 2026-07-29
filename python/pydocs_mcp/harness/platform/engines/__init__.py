"""CLI coding agents as ENGINES — harness-agnostic, one adapter per binary.

An engine is not a harness (spec amendment 2026-07-28). A harness is the named
thing in the guidance vocabulary — it owns a delivery map, minted
``HARNESS_TASK_HEAD: <harness>.<task>`` sections, and ``HarnessRunner``
conformance. An engine owns only an argv spelling and a transcript shape.
Several engines therefore run UNDER one composed harness
(``harness/builtin/external/``), sharing that harness's guidance sections while
staying a separately RECORDED identity dimension (an arm's ``settings.engine`` folds
into its arm hash).

Registered engines: ``claude_code`` (Claude Code, the default) and ``opencode``
(landed 2026-07-29 — the first engine whose guidance channel is NOT a
system-prompt flag, which is what made the delivery map's engine half observable
for the first time).

Cost of widening, by construction:

- **new engine** — one :class:`~.base.CliAgentAdapter` subclass + one registry
  line; it inherits the composed harness's sections, delivery map and contract
  conformance, and must pass the adapter conformance battery. The subclass
  states this engine's whole vocabulary: its flag spellings, its transcript
  shape, its tool names, and its MCP config schema.
- **new composed harness** — a new folder, a ``harness_name``, and minted skill
  sections for it (the rehearsed widening event).
- **new self-contained harness** — the ``HarnessRunner`` Protocol plus the
  shared contract suite, and nothing from this package.

Registry population is by side-effect import below, the architecture-registry
pattern; the adapters are pure (stdlib only), so importing this package costs
nothing but its own bytes.
"""

from pydocs_mcp.harness.platform.engines.base import (
    CliAgentAdapter,
    CliRunRequest,
    CliRunResult,
    CliToolCall,
)
from pydocs_mcp.harness.platform.engines.registry import CliAgentRegistry, cli_agent_registry

# Side-effect import populates the registry (one line per engine).
from pydocs_mcp.harness.platform.engines import claude_code  # noqa: F401  isort:skip
from pydocs_mcp.harness.platform.engines import opencode  # noqa: F401  isort:skip

__all__ = [
    "CliAgentAdapter",
    "CliAgentRegistry",
    "CliRunRequest",
    "CliRunResult",
    "CliToolCall",
    "cli_agent_registry",
]
