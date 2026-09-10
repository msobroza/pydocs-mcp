"""The CLI-agent ENGINE port: one request in, one argv out; one transcript in,
one result out.

Ontology (spec amendment 2026-07-28): a CLI coding agent — ``claude``,
``opencode``, ``codex`` — is an ENGINE, not a harness. It owns its own
VOCABULARY and nothing else: how one run's parameters become an argv, how that
run's stdout becomes the facts a trajectory needs, and how it spells tool names
(its built-ins, and the tools an attached MCP server provides). Everything
else — which corpus, which trace, which guidance sections, how the result
becomes a :class:`~pydocs_mcp.harness.core.run_contract.Trajectory` — belongs
to the COMPOSED harness that HAS-A adapter (``harness/external/``). That split
is what makes a new engine cost one subclass and one registry line.

WHY the tool-name vocabulary is engine-owned rather than harness-owned: the
built-in tool set (``Read``/``Bash``/…) and the MCP naming convention
(``mcp__<server>__<tool>``) are both spellings of ONE binary. A harness that
hard-coded them would hand a second engine the first engine's names — a grant
the CLI resolves to nothing, and a trace join that never matches.

The two value objects below carry exactly what a real builder consumes and a
real parser produces; nothing is declared "for later". ``cwd`` is deliberately
part of the REQUEST but never part of the argv — it is the child process's
working directory, applied at spawn time by the harness.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class CliToolCall:
    """One tool invocation as the engine's transcript reports it.

    ``args`` stays the raw argument mapping (not a digest) because the harness
    needs it twice: to digest for the contract's ``ToolCallRecord`` and to
    match CLIENT-observed calls against the server trace by name.
    """

    tool_name: str
    args: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class CliRunRequest:
    """Everything one CLI run's argv is a function of.

    ``allowed_tools`` is PRE-RESOLVED by the harness — the empty tuple is the
    tool-less grant (an explicit empty surface), not "unset". Keeping the
    profile logic out of the engine is what lets a second engine be an argv
    spelling and nothing more.
    """

    prompt: str
    cwd: Path
    model: str
    max_turns: int
    allowed_tools: tuple[str, ...]
    mcp_config: Path | None = None
    system_prompt_suffix: str = ""
    session_id: str | None = None


@dataclass(frozen=True, slots=True)
class CliRunResult:
    """The scoring-relevant view of one CLI run's transcript.

    ``tool_calls`` is ORDERED as the transcript emitted them (the CLIENT
    observation point); ``turn_budget_exhausted`` is the engine-neutral answer
    to "did this run stop because it ran out of turns", which the harness
    translates into the contract's typed
    :class:`~pydocs_mcp.harness.core.run_contract.TurnBudgetExceededError`.
    The two cache-token sums are the engine's own accounting, carried for the
    efficiency reports that consume them.
    """

    answer: str
    cost_usd: float
    turns: int
    tool_calls: tuple[CliToolCall, ...] = ()
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    turn_budget_exhausted: bool = False
    files_read: frozenset[str] = field(default_factory=frozenset)


class CliAgentAdapter(ABC):
    """One CLI coding agent, adapted to the engine port.

    Subclasses declare three class-level values — the registry ``name``, the
    ``guidance_flag`` this engine carries candidate text on, and its built-in
    ``file_tools`` — then implement the four methods. All four are pure: no
    I/O, no spawn — the harness owns the process, so an adapter is testable
    from a fixture transcript alone.

    ``guidance_flag`` is the ENGINE half of the delivery-map value; the SLOT
    half (which of a candidate's channels lands there) is the composed
    harness's, which is why the harness composes ``<flag>.<slot>`` rather than
    reading a whole channel off the engine.
    """

    name: ClassVar[str]
    guidance_flag: ClassVar[str]
    #: This engine's built-in (non-MCP) tools, in grant order.
    file_tools: ClassVar[tuple[str, ...]]

    @abstractmethod
    def build_command(self, request: CliRunRequest) -> list[str]:
        """The full argv for one run, in this engine's own flag spelling."""

    @abstractmethod
    def parse_transcript(self, stdout: str) -> CliRunResult:
        """Fold one run's stdout into the engine-neutral result.

        Total by contract: a truncated or answerless transcript degrades to
        zeros and empty text rather than raising, so a partially captured run
        is still scoreable as the failure it was.
        """

    @abstractmethod
    def mcp_tool_grant(
        self, server_name: str, tool_names: tuple[str, ...] | None
    ) -> tuple[str, ...]:
        """This engine's grant spelling for tools of the MCP server ``server_name``.

        ``tool_names`` is the SERVER's own vocabulary (the frozen nine);
        ``None`` means the whole server, which an engine with a wildcard
        spelling may return as a single entry.
        """

    @abstractmethod
    def server_tool_name(self, tool_name: str) -> str:
        """The name the MCP SERVER records for a transcript-reported call.

        The inverse of :meth:`mcp_tool_grant`'s per-tool spelling, and the join
        key between the two observation points: the CLI reports its own
        namespaced name while the server records its bare registration name, so
        a raw-name join would emit every MCP call twice. A non-MCP (built-in)
        name is returned unchanged.
        """
