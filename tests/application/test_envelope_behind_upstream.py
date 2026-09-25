"""The behind-upstream hint in ``meta.suggestion`` (spec §6.8b layer 1, #318).

It rides the existing field only — no new meta key — on the three tools that
declare it, only with ``git.remote.behind_hint`` on, only when the resolved
branch is behind, and only when no other suggestion fired: the tool's own and
the checkout one both win. Without it every answer is byte-identical.
"""

from __future__ import annotations

import logging
from dataclasses import replace

import pytest

from pydocs_mcp.application.branch_resolution import BranchSelectorKind
from pydocs_mcp.application.envelope import ResponseEnvelope, _assemble_meta
from pydocs_mcp.application.suggestions import BEHIND_UPSTREAM_RULE
from pydocs_mcp.application.tool_response import SUGGESTION_TOOLS
from pydocs_mcp.application.upstream_status import CheckoutPlace, UpstreamStatus

from ._router_fakes import CountingProbe
from .test_envelope_branch_meta import (
    _META_KEYS,
    _SUGGESTION,
    _fired_rules,
    _info,
    _resolved,
)

NOW = 10_000.0
BEHIND = UpstreamStatus("main", "origin/main", 1, 3, NOW - 2 * 3600, CheckoutPlace.THIS_WORKTREE)
_HINT = "[suggestion: branch 'main' is behind origin/main by 3 (last fetch 2h ago); run: git pull]"


def _behind(
    status: UpstreamStatus | None = BEHIND,
    suggestion: str | None = None,
    kind: BranchSelectorKind = BranchSelectorKind.DEFAULT,
):
    resolved = _resolved("main", kind, suggestion=suggestion)
    return replace(resolved, upstream=status)


def _meta(tool: str, branch, *, hint: bool = True, extras: dict | None = None) -> dict:
    return _assemble_meta(
        tool=tool,
        project="p",
        info=_info(stale=False),
        truncated=False,
        extras=extras or {},
        branch=branch,
        behind_upstream_hint=hint,
        now=NOW,
    )


@pytest.mark.parametrize("tool", sorted(SUGGESTION_TOOLS))
def test_a_branch_behind_its_upstream_is_hinted_on_the_suggestion_tools(
    tool: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        meta = _meta(tool, _behind())
    assert meta["suggestion"] == _HINT
    assert _fired_rules(caplog) == [BEHIND_UPSTREAM_RULE]


def test_a_branch_checked_out_nowhere_is_never_told_to_pull() -> None:
    """#318 review: ``branch="main"`` while ``feature/x`` is checked out —
    ``git pull`` would sync ``feature/x``; the hint fast-forwards ``main``."""
    nowhere = replace(BEHIND, checked_out=CheckoutPlace.NOWHERE)
    by_name = _behind(nowhere, kind=BranchSelectorKind.NAME)
    assert _meta("grep", by_name)["suggestion"] == (
        "[suggestion: branch 'main' is behind origin/main by 3 (last fetch 2h ago); "
        "run: git fetch . origin/main:main]"
    )


def test_tools_without_the_field_never_carry_the_hint(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        meta = _meta("get_symbol", _behind())
    assert set(meta) == _META_KEYS and _fired_rules(caplog) == []


@pytest.mark.parametrize(
    "branch",
    [
        _behind(None),  # no upstream, or the lane published nothing (offline, no refresh loop)
        _behind(replace(BEHIND, behind=0)),  # ahead only: nothing to pull
    ],
)
def test_no_behind_status_leaves_meta_exactly_as_before(branch) -> None:
    assert _meta("search_codebase", branch) == _meta(
        "search_codebase", replace(branch, upstream=None)
    )
    assert "suggestion" not in _meta("search_codebase", branch)


def test_the_hint_is_off_with_the_behind_hint_switch(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        meta = _meta("grep", _behind(), hint=False)
    assert "suggestion" not in meta and _fired_rules(caplog) == []


def test_any_other_suggestion_wins_over_the_hint(caplog: pytest.LogCaptureFixture) -> None:
    own = {"suggestion": "[suggestion: zero hits — orient with get_overview()]"}
    with caplog.at_level(logging.DEBUG):
        by_tool = _meta("search_codebase", _behind(), extras=own)
        by_checkout = _meta("search_codebase", _behind(suggestion=_SUGGESTION))
    assert (by_tool["suggestion"], by_checkout["suggestion"]) == (own["suggestion"], _SUGGESTION)
    assert BEHIND_UPSTREAM_RULE not in _fired_rules(caplog)


async def _body() -> str:
    return "body"


async def test_the_envelope_reads_its_switch_and_clock_and_changes_meta_only() -> None:
    probe = CountingProbe(_info(stale=False))
    quiet = ResponseEnvelope(probe=probe, surface="mcp", pointers_enabled=False)
    hinting = ResponseEnvelope(
        probe=probe,
        surface="mcp",
        pointers_enabled=False,
        behind_upstream_hint=True,
        clock=lambda: NOW,
    )
    plain = await quiet.wrap("grep", "p", _body, branch=_behind())
    hinted = await hinting.wrap("grep", "p", _body, branch=_behind())
    assert "suggestion" not in plain.meta  # the default envelope never hints
    assert (hinted.text, hinted.items) == (plain.text, plain.items)
    assert hinted.meta == {**plain.meta, "suggestion": _HINT}
