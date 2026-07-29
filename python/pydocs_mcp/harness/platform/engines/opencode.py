"""The opencode CLI adapted to the engine port — the second engine.

Every spelling below is taken from opencode's own documentation and, where the
docs are silent, from its implementation at release tag **v1.18.9**
(2026-07-28), with the config-stack, agent-selection and message-fold facts
re-read against upstream on 2026-07-29. Doc anchors are cited inline as
``docs/<page>``; source-derived facts are marked ``[src]`` with the file they
came from. Nothing here is invented: the CLI evolves, so a spelling that only
the tests know is a spelling nobody re-validates.

Headless shape (``docs/cli#run``): ``opencode run [message..]`` is the
documented non-interactive mode, and ``--format json`` ("raw JSON events")
makes stdout an NDJSON stream, one object per line, enveloped as
``{"type", "timestamp", "sessionID", ...data}`` ``[src cli/cmd/run.ts]``. The
event types this adapter folds are ``tool_use`` (a completed or errored tool
part), ``text`` (an assistant text part) and ``step_finish`` (which carries the
step's ``cost`` and ``tokens.cache.{read,write}``).

WHY THE ARGV STARTS WITH ``env``. ``run`` has flags for the model, the session
title, the output format, the agent and the working directory — and for NOTHING
else this port carries. There is no ``--mcp-config``, no ``--max-turns``, no
``--allowedTools`` and no system-prompt flag; MCP servers, permissions and step
caps are all CONFIG, delivered without writing into the shared workspace by the
``OPENCODE_CONFIG`` (path) and ``OPENCODE_CONFIG_CONTENT`` (inline JSON)
environment variables (``docs/config#precedence-order``, ``docs/cli`` env
table). yargs runs ``.strict()`` ``[src cli/index.ts]``, so inventing a flag
exits 1. POSIX ``env(1)`` is therefore the only shape that keeps every run
parameter IN the argv — which is what makes this adapter a pure function the
harness can pin, and what keeps the spawn seam free of an ``env=`` kwarg.

WHAT THIS ADAPTER PINS, so no run parameter is decided by the host or by the
corpus under test. All three sit inside the exact-argv pin, and each answers an
isolation ``claude_code`` gets from a flag; the citation for each lives next to
the code that emits it: ``--agent build`` binds the step cap to the agent that
actually runs (:meth:`OpencodeAdapter.build_command`),
``OPENCODE_DISABLE_PROJECT_CONFIG=1`` keeps the corpus out of the config stack
and out of the system prompt (:func:`_config_assignments`), and the prompt is
emitted as space-free tokens the CLI's own join reassembles exactly
(:func:`_prompt_tokens`).

HONEST DEGRADATIONS, each a capability ``claude_code`` has and opencode does
not expose:

1. **Guidance is a PROMPT PREFIX, not a system-prompt append.** opencode's only
   additive system-prompt affordance is the config's ``instructions`` list,
   which takes FILE PATHS (``docs/rules``); ``agent.<name>.prompt`` REPLACES the
   built-in provider prompt ``[src session/llm/request.ts]`` and is therefore
   not a guidance channel. ``build_command`` is pure, so it cannot write an
   instructions file. The channel is consequently ``prompt_prefix`` — the
   candidate text is prepended to the message, separated by a blank line. The
   composed harness's delivery map records the difference by construction
   (``<flag>.<slot>`` becomes ``prompt_prefix.skill_block``), so cross-engine
   arms differ in delivery MODE by recorded design rather than by accident.
2. **The session id becomes a session TITLE.** ``--session <id>`` resumes an
   EXISTING session and exits 1 with "Session not found" otherwise
   ``[src run.ts]``, so it cannot assign a caller-chosen id. ``--title`` is the
   documented string that names the session, so the trajectory id rides there:
   opencode's own records still name this trajectory, they just name it in the
   title rather than the id. The ADR 0009 correlation identity does not depend
   on this — it travels in the MCP config's ``environment`` block.
3. **``turn_budget_exhausted`` is always False — and nothing downstream
   recovers it.** The step cap is config (``agent.<name>.steps``,
   ``docs/agents#max-steps``); on reaching it opencode injects a "MAXIMUM STEPS
   REACHED" system message and the agent answers with a summary
   ``[src session/prompt.ts]``. The stream carries no stop-reason marker — no
   analogue of ``subtype: error_max_turns`` — so a capped run is
   INDISTINGUISHABLE from a completed one here, and there is no second line of
   defence: the campaign's turn guard is a PRE-LAUNCH lockfile-agreement
   assertion (``pydocs_eval/campaign/budget.py``) and the ``max_turns`` rubric
   gate is opt-in, registered by no shipped rubric. A capped opencode run is
   therefore SCORED AS FINISHED where the same truncation on ``claude_code``
   raises ``TurnBudgetExceededError``. Recorded, not guessed at: closing it
   needs an engine-neutral cap check in the composed harness (which holds both
   ``settings.max_agent_turns`` and ``result.turns``) — a measurement change to
   BOTH engines, not an adapter fix.

UNKNOWNS deferred to a real binary (opencode ships no sample of its JSON
output): whether ``tool_use`` can fire twice for one part (this adapter
de-duplicates by ``part.id``, which is harmless either way), whether a ``text``
event arrives for intermediate narration as well as the final answer (this
adapter keeps the LAST one, the closest analogue of a result envelope), and
whether ``state.output`` is truncated in the payload (not read here). The
env-gated smoke test in ``tests/harness/platform/engines/test_opencode.py`` is
the cheap early warning that the flag spellings still exist.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pydocs_mcp.harness.platform.engines.base import (
    CliAgentAdapter,
    CliRunRequest,
    CliRunResult,
    CliToolCall,
)
from pydocs_mcp.harness.platform.engines.registry import cli_agent_registry
from pydocs_mcp.harness.platform.serve_config import MCP_SERVER_NAME, serve_args

# Single source of truth for every spelling this engine uses. A CLI rename is a
# one-line edit here; the env-gated smoke test re-checks the real binary.
_CLI_FLAGS = {
    "format": "--format",
    "model": "--model",
    "title": "--title",
    "agent": "--agent",
}

# POSIX ``env(1)``: the documented way to set a child's environment from an
# argv, and the reason every run parameter stays inspectable in the argv.
_ENV_EXECUTABLE = "env"
_EXECUTABLE = "opencode"
_RUN_SUBCOMMAND = "run"

# ``docs/cli`` env table: a config file PATH and an inline config DOCUMENT.
# Both are merged into the config stack rather than replacing it.
_CONFIG_PATH_VAR = "OPENCODE_CONFIG"
_CONFIG_CONTENT_VAR = "OPENCODE_CONFIG_CONTENT"

# The corpus-isolation switch (see the module docstring): without it the repo
# under test contributes config, plugins and system-prompt text to the run.
_DISABLE_PROJECT_CONFIG_VAR = "OPENCODE_DISABLE_PROJECT_CONFIG"
_FLAG_TRUE = "1"

# ``--format json`` is required for the per-event tool / cost / token fold.
_OUTPUT_FORMAT = "json"

# End-of-flags marker: yargs runs with ``populate--``, so the prompt after it is
# never parsed as a flag even when it begins with a dash ``[src cli/index.ts]``.
_END_OF_FLAGS = "--"

# The separator ``run`` joins the positional message args with, and therefore
# the one this adapter splits on so the join round-trips ``[src cli/cmd/run.ts]``.
_ARG_SEPARATOR = " "

# The blank line between prepended guidance and the sample's own prompt.
_GUIDANCE_SEPARATOR = "\n\n"

# Config keys (``docs/config``, ``docs/permissions``, ``docs/agents#max-steps``).
_PERMISSION_KEY = "permission"
_AGENT_KEY = "agent"
_STEPS_KEY = "steps"
_MCP_KEY = "mcp"

# opencode's shipped default primary agent, and the one an unflagged
# ``opencode run`` uses — so it is the agent whose step cap binds this run.
_DEFAULT_AGENT = "build"

# Permission actions and the catch-all pattern. Rules are evaluated in the
# map's own key order with the LAST match winning (``docs/permissions``,
# ``[src permission/index.ts]``: ``Object.entries`` then ``findLast``), so the
# catch-all is written FIRST and the grants after it; a tool whose last match is
# a ``"*"`` deny is removed from the model's visible set entirely, which is what
# makes this an allowlist rather than a prompt-time request to behave. The
# ordering invariant holds for THIS document — see :func:`_permission_rules` for
# what a foreign config layer can still do to it.
_CATCH_ALL_PATTERN = "*"
_ALLOW = "allow"
_DENY = "deny"

# ``docs/mcp-servers``: a local server is ``type: "local"`` plus ONE ``command``
# array, and its environment block is called ``environment`` (not ``env``).
_MCP_CONFIG_FILENAME = "opencode.json"
_LOCAL_SERVER_TYPE = "local"
_SERVER_ENV_KEY = "environment"

# MCP tools are registered as ``<sanitized-server>_<sanitized-tool>``
# ``[src mcp/catalog.ts]``; ``<server>_*`` is a real wildcard in the permission
# map (``docs/mcp-servers``: "use ``mymcpservername_*``").
_MCP_NAME_SEPARATOR = "_"
_MCP_TOOL_WILDCARD = "*"
_SANITIZE_PATTERN = re.compile(r"[^a-zA-Z0-9_-]")

# The file-reading built-in whose argument key feeds the distinct-files metric.
# opencode spells the key in camelCase ``[src tool/read.ts]``.
_READ_TOOL = "read"
_READ_PATH_ARG = "filePath"

# NDJSON event types this fold consumes ``[src cli/cmd/run.ts]``. ``reasoning``
# is emitted only under ``--thinking`` (headless default false) and ``error``
# carries no field this contract records, so neither is read.
_TOOL_EVENT = "tool_use"
_TEXT_EVENT = "text"
_STEP_FINISH_EVENT = "step_finish"

# opencode's built-in tool ids (``docs/tools`` + the registry's own list). The
# set exists for ONE reason: ``server_tool_name`` must not mistake a built-in
# whose name contains an underscore for a ``<server>_<tool>`` MCP name.
_BUILT_IN_TOOLS = frozenset(
    {
        "bash",
        "read",
        "glob",
        "grep",
        "edit",
        "write",
        "task",
        "webfetch",
        "todowrite",
        "websearch",
        "skill",
        "apply_patch",
        "question",
        "lsp",
        "invalid",
        "list_mcp_resources",
        "list_mcp_resource_templates",
        "read_mcp_resource",
    }
)


@cli_agent_registry.register("opencode")
class OpencodeAdapter(CliAgentAdapter):
    """Argv builder + NDJSON transcript parser for the opencode CLI."""

    name = "opencode"
    # NOT a flag — the channel this engine degrades to; see degradation 1.
    guidance_flag = "prompt_prefix"
    file_tools = ("read", "grep", "glob", "bash")
    mcp_config_filename = _MCP_CONFIG_FILENAME

    def build_command(self, request: CliRunRequest) -> list[str]:
        """Assemble the headless ``env … opencode run`` argv for one run.

        Order is pinned: the ``env`` assignments (isolation first, then the
        config path, then the inline content — precedence is decided by
        opencode's config stack, not by argv order), then the executable and its
        subcommand, then the flag pairs, then the end-of-flags marker and the
        prompt's space-free tokens LAST.

        Example:
            >>> OpencodeAdapter().build_command(  # doctest: +SKIP
            ...     CliRunRequest(prompt="q?", cwd=Path("/repo"),
            ...                   model="anthropic/claude-sonnet-4-5",
            ...                   max_turns=40, allowed_tools=("read",))
            ... )
            ['env', 'OPENCODE_DISABLE_PROJECT_CONFIG=1', ..., 'opencode', 'run', ...]
        """
        cmd = [
            _ENV_EXECUTABLE,
            *_config_assignments(request),
            _EXECUTABLE,
            _RUN_SUBCOMMAND,
            _CLI_FLAGS["format"],
            _OUTPUT_FORMAT,
            _CLI_FLAGS["model"],
            request.model,
            # Pins the agent whose ``steps`` cap this run's config sets.
            # ``build`` is the effective agent ONLY while no layer sets
            # ``default_agent`` (the primary is ``cfg.default_agent ? x.name ===
            # cfg.default_agent : x.name === "build"`` ``[src agent/agent.ts]``),
            # and ``agent.steps ?? Infinity`` ``[src session/prompt.ts]`` leaves
            # a run bound to some other agent with no cap at all.
            _CLI_FLAGS["agent"],
            _DEFAULT_AGENT,
        ]
        if request.session_id:
            cmd += [_CLI_FLAGS["title"], request.session_id]
        cmd += [_END_OF_FLAGS, *_prompt_tokens(request)]
        return cmd

    def parse_transcript(self, stdout: str) -> CliRunResult:
        """Fold one run's ``--format json`` NDJSON stdout into the result.

        Total: unparseable lines are skipped and an answerless stream degrades
        to zero cost, zero turns and an empty answer, so a truncated capture
        still yields the partial facts the run already paid for.

        Example:
            >>> OpencodeAdapter().parse_transcript("not json\\n").turns
            0
        """
        fold = _Fold()
        for event in _iter_events(stdout):
            _fold_event(fold, event)
        return CliRunResult(
            answer=fold.answer,
            cost_usd=fold.cost_usd,
            turns=fold.turns,
            tool_calls=tuple(fold.tool_calls.values()),
            cache_read_tokens=fold.cache_read,
            cache_write_tokens=fold.cache_write,
            # No stop-reason marker exists in this stream — degradation 3.
            turn_budget_exhausted=False,
            files_read=frozenset(fold.files_read),
        )

    def mcp_tool_grant(
        self, server_name: str, tool_names: tuple[str, ...] | None
    ) -> tuple[str, ...]:
        """``<server>_<tool>`` per tool, or the server wildcard for ``None``.

        Example:
            >>> OpencodeAdapter().mcp_tool_grant("pydocs-mcp", ("get_why",))
            ('pydocs-mcp_get_why',)
        """
        prefix = _sanitize(server_name)
        if tool_names is None:
            return (f"{prefix}{_MCP_NAME_SEPARATOR}{_MCP_TOOL_WILDCARD}",)
        return tuple(f"{prefix}{_MCP_NAME_SEPARATOR}{_sanitize(tool)}" for tool in tool_names)

    def server_tool_name(self, tool_name: str) -> str:
        """Strip the ``<server>_`` namespace opencode stamps on MCP tools.

        Unlike Claude Code's reserved ``mcp__`` prefix, opencode's namespace is
        a bare underscore join, so the inverse is anchored on this engine's own
        built-in vocabulary first and only then splits at the first separator.
        Two residual ambiguities are recorded rather than hidden, and both are
        the same missing argument. (1) A server whose NAME contains an
        underscore would be over-stripped; the one server this platform attaches
        is ``pydocs-mcp``, so the join is exact today. (2) This engine's
        built-in ``grep`` / ``glob`` land on the SAME server-vocabulary name as
        the server's own ``grep`` / ``glob`` tools, so the composed harness's
        name-multiset join can credit a built-in shell grep to the server slice
        and then report the real MCP call as a CLIENT observation. The
        deterministic gates read the trace directly and are unaffected; the
        CLIENT tail is what misreports. Both need ``server_name`` threaded into
        this signature — a port change across every engine — so they are stated
        here rather than half-fixed with a heuristic.

        Example:
            >>> OpencodeAdapter().server_tool_name("pydocs-mcp_get_why")
            'get_why'
        """
        if tool_name in _BUILT_IN_TOOLS or _MCP_NAME_SEPARATOR not in tool_name:
            return tool_name
        return tool_name.split(_MCP_NAME_SEPARATOR, 1)[-1]

    def render_mcp_config(
        self,
        *,
        corpus_dir: Path,
        python: Path,
        env: Mapping[str, str],
        overlay: Path | None,
    ) -> str:
        """The ``mcp`` document ``OPENCODE_CONFIG`` points this run at.

        Three differences from the Claude Code schema, all of them load-bearing
        (``docs/mcp-servers``): the server map hangs off ``mcp`` rather than
        ``mcpServers``; the launch is ONE ``command`` array rather than a
        command/args pair; and the pass-through environment block — the ADR
        0009 correlation channel — is called ``environment``. Rendering the
        other engine's document here would start a server with no correlation
        identity, which surfaces as a traceless MCP-attached run.

        Example:
            >>> OpencodeAdapter().render_mcp_config(  # doctest: +SKIP
            ...     corpus_dir=Path("/corpus"), python=Path("/venv/bin/python"),
            ...     env={}, overlay=None
            ... )
            '{"mcp": {"pydocs-mcp": {"type": "local", "command": [...]}}}'
        """
        server: dict[str, object] = {
            "type": _LOCAL_SERVER_TYPE,
            "command": [str(python), *serve_args(corpus_dir, overlay)],
            "enabled": True,
        }
        if env:
            server[_SERVER_ENV_KEY] = dict(env)
        return json.dumps({_MCP_KEY: {MCP_SERVER_NAME: server}})


def _sanitize(value: str) -> str:
    """opencode's own MCP name sanitizer ``[src mcp/catalog.ts]``."""
    return _SANITIZE_PATTERN.sub("_", value)


def _prompt_tokens(request: CliRunRequest) -> list[str]:
    """The message as SPACE-FREE positional tokens, guidance prefixed.

    ``run`` re-quotes any message arg containing a space and then joins the args
    with one space ``[src cli/cmd/run.ts]``, so splitting on that same separator
    is the exact inverse: runs of spaces survive as empty tokens, embedded
    newlines never introduce one, and nothing reaches the model wrapped in
    literal quotes. Safe after ``--`` because ``populate--`` keeps those tokens
    out of the option parser entirely ``[src cli/index.ts]``.
    """
    message = request.prompt
    if request.system_prompt_suffix:
        message = f"{request.system_prompt_suffix}{_GUIDANCE_SEPARATOR}{request.prompt}"
    return message.split(_ARG_SEPARATOR)


def _config_assignments(request: CliRunRequest) -> list[str]:
    """The ``NAME=VALUE`` tokens ``env`` applies before exec'ing opencode.

    The isolation assignment leads, and it is the counterpart of
    ``claude_code``'s ``--strict-mcp-config``. Unset, opencode merges every
    ``opencode.json`` and ``.opencode`` directory walked up from the CWD — the
    indexed CORPUS — writing a ``.gitignore``, running an npm install and
    loading that directory's plugin JavaScript for each
    ``[src config/config.ts, config/paths.ts]``, and folds the corpus's
    ``AGENTS.md`` / ``CLAUDE.md`` into the system prompt under the same flag
    ``[src session/instruction.ts]``. All of it would vary the tool surface, the
    model and the prompt per corpus with nothing in the arm fingerprint. The
    flag is read as ``"1"`` / ``"true"`` ``[src core/flag/flag.ts]``; it does NOT
    suppress the operator's own global config (see :func:`_permission_rules`).
    """
    assignments = [f"{_DISABLE_PROJECT_CONFIG_VAR}={_FLAG_TRUE}"]
    if request.mcp_config is not None:
        assignments.append(f"{_CONFIG_PATH_VAR}={request.mcp_config}")
    assignments.append(f"{_CONFIG_CONTENT_VAR}={_run_config(request)}")
    return assignments


def _run_config(request: CliRunRequest) -> str:
    """The per-run inline config: this arm's tool allowlist and its step cap.

    Compact separators and insertion order (never ``sort_keys``) because the
    permission map's ORDER is semantic — the catch-all deny must precede the
    grants for "last match wins" to allow anything at all.
    """
    payload = {
        _PERMISSION_KEY: _permission_rules(request.allowed_tools),
        _AGENT_KEY: {_DEFAULT_AGENT: {_STEPS_KEY: request.max_turns}},
    }
    return json.dumps(payload, separators=(",", ":"))


def _permission_rules(allowed_tools: tuple[str, ...]) -> dict[str, str]:
    """Deny everything, then allow exactly the granted surface.

    An empty grant is the EXPLICIT tool-less surface (the blind-judge profile),
    and it renders as the bare catch-all deny — every tool hidden, not merely
    un-mentioned.

    RECORDED LIMITATION: the effective ruleset is the deep merge of every config
    layer, and a merge keeps a shared key at the TARGET's position
    ``[src config/config.ts]``. ``OPENCODE_DISABLE_PROJECT_CONFIG`` removes the
    corpus from that stack but not the operator's GLOBAL config, so a host-level
    ``{"permission": {"bash": "ask"}}`` would sort ``bash`` ahead of the
    catch-all and the ``"*": "deny"`` rule would then win for it — a grant
    silently revoked, with the arm fingerprint unchanged. Deployments that run
    this engine should keep the global opencode config free of ``permission``.
    """
    rules = {_CATCH_ALL_PATTERN: _DENY}
    for tool in allowed_tools:
        rules[tool] = _ALLOW
    return rules


@dataclass(slots=True)
class _Fold:
    """The single-pass accumulator over one transcript's events."""

    # Keyed by part id so a re-emitted part counts once; insertion order is the
    # emission order the contract's CLIENT observation point requires.
    tool_calls: dict[str, CliToolCall] = field(default_factory=dict)
    files_read: set[str] = field(default_factory=set)
    answer: str = ""
    cost_usd: float = 0.0
    turns: int = 0
    cache_read: int = 0
    cache_write: int = 0


def _iter_events(text: str) -> Iterator[object]:
    # Line-wise tolerant decode: skip blanks and any line json.loads cannot
    # parse. A truncated stream must yield partial facts, not raise.
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            yield json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            continue


def _fold_event(fold: _Fold, event: object) -> None:
    """Route one NDJSON event into the accumulator; ignore everything else."""
    if not isinstance(event, dict):
        return
    part = event.get("part")
    if not isinstance(part, dict):
        return
    kind = event.get("type")
    if kind == _TOOL_EVENT:
        _fold_tool_use(fold, part)
    elif kind == _TEXT_EVENT:
        # LAST text part wins: the closest analogue of a result envelope in a
        # stream that has none (see the deferred UNKNOWNS above).
        fold.answer = str(part.get("text", ""))
    elif kind == _STEP_FINISH_EVENT:
        _fold_step_finish(fold, part)


def _fold_tool_use(fold: _Fold, part: dict[str, object]) -> None:
    state = part.get("state")
    args = state.get("input") if isinstance(state, dict) else None
    call = CliToolCall(
        tool_name=str(part.get("tool", "")),
        args=args if isinstance(args, dict) else {},
    )
    part_id = part.get("id")
    key = str(part_id) if isinstance(part_id, str) and part_id else f"#{len(fold.tool_calls)}"
    fold.tool_calls[key] = call
    _record_read(call, fold.files_read)


def _record_read(call: CliToolCall, files_read: set[str]) -> None:
    # Only the built-in read counts. An MCP name always carries a ``<server>_``
    # prefix, so it can never collide with the separator-free ``read``.
    if call.tool_name != _READ_TOOL:
        return
    path = call.args.get(_READ_PATH_ARG)
    if isinstance(path, str) and path:
        files_read.add(path)


def _fold_step_finish(fold: _Fold, part: dict[str, object]) -> None:
    # One step == one turn: the stream reports no turn COUNT, and the step is
    # the unit ``agent.<name>.steps`` caps, so counting steps is the honest
    # translation rather than a guess at what the provider called a turn.
    fold.turns += 1
    fold.cost_usd += _number_or_zero(part.get("cost"))
    cache_read, cache_write = _cache_tokens(part)
    fold.cache_read += cache_read
    fold.cache_write += cache_write


def _cache_tokens(part: dict[str, object]) -> tuple[int, int]:
    """``tokens.cache.{read,write}`` off one ``step_finish`` part, else zeros."""
    tokens = part.get("tokens")
    cache = tokens.get("cache") if isinstance(tokens, dict) else None
    if not isinstance(cache, dict):
        return 0, 0
    return int(_number_or_zero(cache.get("read"))), int(_number_or_zero(cache.get("write")))


def _number_or_zero(value: object) -> float:
    """A numeric payload field, or 0.0 — a shape shift must never raise."""
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
