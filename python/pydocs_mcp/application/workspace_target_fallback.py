"""Multi-project pass 2 — the workspace-wide target fallback (spec 2026-09-10 §2.5).

``MultiProjectLookup._resolve_by_recency`` runs pass 1 (the exact path in
every loaded project, most recently indexed first). Only when every project
misses does it call :func:`resolve_workspace_target_fallback`, which asks each
project's ``target_resolver`` once and rewrites only when the WHOLE workspace
proves one answer (``decide_workspace_rewrite``, spec K3). Otherwise it raises
today's all-projects miss, suffixed with the merged ``name (project p)``
candidates when ``target_resolution.miss_candidates`` is on.

Kept out of ``multi_project_search`` so that module stays under the 500-line
file budget; it imports ``ProjectServices`` for typing only (no runtime cycle).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, TypeVar

from pydocs_mcp.application.formatting import pointer_token
from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.target_resolution import (
    ResolutionEntry,
    TargetResolution,
    TargetRewrite,
    decide_workspace_rewrite,
    log_target_fallback_resolved,
    render_workspace_miss_message,
)

if TYPE_CHECKING:
    from pydocs_mcp.application.multi_project_search import ProjectServices
    from pydocs_mcp.retrieval.config import TargetResolutionConfig

_T = TypeVar("_T")


def workspace_miss_base(target: str) -> str:
    """Today's all-projects miss message (spec §D1) — candidates append to it.

    >>> workspace_miss_base("pkg.Cls")
    "'pkg.Cls' not found in any loaded project. [[next:search:Cls]]"
    """
    return (
        f"'{target}' not found in any loaded project. "
        f"{pointer_token('search', target.rsplit('.', 1)[-1])}"
    )


def _workspace_miss_message(
    target: str,
    resolutions: Sequence[tuple[ProjectServices, TargetResolution]],
    rules: TargetResolutionConfig,
) -> str:
    base = workspace_miss_base(target)
    if not rules.miss_candidates:
        return base  # AC12: the flag silences the merged sentence too
    per_project = [(svc.project.name, res) for svc, res in resolutions]
    return render_workspace_miss_message(base, per_project, rules.max_candidates)


async def resolve_workspace_target_fallback(
    ordered: Sequence[ProjectServices],
    run_rewrite: Callable[[ProjectServices, TargetRewrite], Awaitable[_T]],
    *,
    target: str,
    entry: ResolutionEntry,
    rules: TargetResolutionConfig,
) -> _T:
    """Pass 2 over ``ordered`` (recency order): one proven rewrite, else raise.

    >>> await resolve_workspace_target_fallback(  # doctest: +SKIP
    ...     ordered, lambda svc, rw: svc.lookup.lookup_rewritten(p, rw),
    ...     target="Cls", entry="lookup", rules=TargetResolutionConfig())
    """
    resolutions = [
        (svc, await svc.lookup.target_resolver.resolve(target, entry=entry)) for svc in ordered
    ]
    decided = decide_workspace_rewrite(resolutions)
    if decided is None:
        raise NotFoundError(_workspace_miss_message(target, resolutions, rules))
    svc, rewrite = decided
    try:
        value = await run_rewrite(svc, rewrite)
    except NotFoundError as exc:
        # Retry miss: today's message and no candidates — never advertise the
        # canonical that just failed, and never return a wrong body.
        raise NotFoundError(workspace_miss_base(target)) from exc
    log_target_fallback_resolved(
        entry=entry, rewrite=rewrite, target=target, project=svc.project.name
    )
    return value
