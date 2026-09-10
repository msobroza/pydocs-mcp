"""Miss-path target resolution for get_symbol / get_context / get_references.

Runs only after the exact target raised ``NotFoundError`` (spec 2026-09-10
§2.1 P1), so every call that resolves today keeps its path. Three rules, each
behind its own YAML flag (``target_resolution.*``, ADR 0007 R7 per-rule
ablation):

- Rule 1 ``source_root_strip`` — ``src.pkg.mod.Cls`` → ``pkg.mod.Cls`` when the
  hit chunk's ``source_path`` proves ``src`` is the one directory the ``.py``
  chunker dropped from the module id.
- Rule 2 ``unique_bare_name`` — a bare ``Cls`` → its canonical name when exactly
  one eligible project symbol has that case-sensitive leaf, over a complete scan.
- Rule 3 ``miss_candidates`` — otherwise, the closest indexed names, appended
  to the unchanged miss message (errors carry no envelope, so text is the only
  channel).

Never guesses (P3): no rewrite from a truncated scan or an ambiguous name, and
every emitted name passes ``is_symbol_target`` (P4). A resolved fallback logs
one ``target_fallback_resolved`` JSON line on this module's own logger — NOT
``suggestion_fired``, which would trip the trace merge cross-check (OD-1 (a)).
"""

from __future__ import annotations

import difflib
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, NoReturn, TypeVar

from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.mcp_inputs import is_symbol_target
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ChunkSymbolName

if TYPE_CHECKING:
    from pydocs_mcp.application.protocols import TargetResolver
    from pydocs_mcp.retrieval.config.models import TargetResolutionConfig
    from pydocs_mcp.storage.protocols import UnitOfWork

FallbackRule = Literal["source_root_strip", "unique_bare_name"]
ResolutionEntry = Literal["lookup", "context", "source"]

# Safety bound, not a quality knob (so not YAML): caps miss-path memory and
# latency on huge indexes. A truncated scan never resolves and emits no
# candidates — it degrades to today's message rather than guess from a prefix.
_SYMBOL_NAME_SCAN_LIMIT = 50_000
# Chunker pseudo-node holding a module's import block — not an addressable symbol.
_IMPORTS_PSEUDO_LEAF = "__imports__"
# v1 resolves Python code symbols only (spec §10 R3): bare ``md`` must never
# land on AGENTS.md, and chunk ``origin`` cannot tell markdown apart.
_RESOLVABLE_SOURCE_SUFFIX = ".py"
_PACKAGE_INIT_STEM = "__init__"

_T = TypeVar("_T")
_K = TypeVar("_K")

_target_resolution_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TargetRewrite:
    """One proven target rewrite; the retry is pinned to ``__project__`` (P2)."""

    rule: FallbackRule
    canonical: str  # e.g. "needle.scoring.strategies.MaxSimScorer"
    module: str  # e.g. "needle.scoring.strategies"
    symbol_path: tuple[str, ...]  # () for a module-only rewrite


@dataclass(frozen=True, slots=True)
class TargetResolution:
    """What the resolver learned about one missed target."""

    rewrite: TargetRewrite | None = None
    candidates: tuple[str, ...] = ()  # ranked, capped at max_candidates
    candidate_total: int = 0  # before the cap → "(+N more)"
    exact_leaf_count: int = 0  # bare targets: eligible exact-leaf matches
    ambiguous: bool = False
    scan_truncated: bool = False


# ── pure predicates ───────────────────────────────────────────────────────


def _leaf_of(qualified_name: str) -> str:
    return qualified_name.rsplit(".", 1)[-1]


def is_stripped_source_root(segment: str, source_path: str | None, module: str) -> bool:
    """True iff ``segment`` is exactly the one directory the .py chunker dropped from ``module``.

    >>> is_stripped_source_root("src", "src/needle/scoring/strategies.py", "needle.scoring.strategies")
    True
    >>> is_stripped_source_root("src", "src/needle/scoring/__init__.py", "needle.scoring")
    True
    """
    path = (source_path or "").replace("\\", "/")
    if not path.endswith(_RESOLVABLE_SOURCE_SUFFIX):
        return False
    stem_parts = tuple(p for p in path[: -len(_RESOLVABLE_SOURCE_SUFFIX)].split("/") if p)
    if stem_parts and stem_parts[-1] == _PACKAGE_INIT_STEM:
        stem_parts = stem_parts[:-1]
    return stem_parts == (segment, *module.split("."))


def is_resolvable_symbol_name(row: ChunkSymbolName, entry: ResolutionEntry) -> bool:
    """The one eligibility predicate behind Rules 2 and 3 (spec §2.4, P4).

    get_context rejects module-only targets, so they never count for it.

    >>> is_resolvable_symbol_name(ChunkSymbolName("p.m.C", "p.m", "p/m.py"), "lookup")
    True
    """
    if not is_symbol_target(row.qualified_name):
        return False
    if not (row.source_path or "").endswith(_RESOLVABLE_SOURCE_SUFFIX):
        return False
    if _leaf_of(row.qualified_name) == _IMPORTS_PSEUDO_LEAF:
        return False
    return entry != "context" or row.qualified_name != row.module


# ── storage reads (inside the resolver's single UoW) ──────────────────────


async def _scan_symbol_names(
    uow: UnitOfWork, package: str
) -> tuple[tuple[ChunkSymbolName, ...], bool]:
    """``(rows, truncated)`` — one ``limit + 1`` read detects truncation."""
    rows = await uow.chunks.list_symbol_names(package, limit=_SYMBOL_NAME_SCAN_LIMIT + 1)
    return rows, len(rows) > _SYMBOL_NAME_SCAN_LIMIT


async def _candidate_pool_package(uow: UnitOfWork, first_segment: str) -> str:
    """The indexed dependency named by ``first_segment``, else ``__project__``."""
    if await uow.packages.get(first_segment) is not None:
        return first_segment
    return PROJECT_PACKAGE_NAME


async def _source_root_rewrite(
    uow: UnitOfWork, parts: tuple[str, ...], entry: ResolutionEntry
) -> TargetRewrite | None:
    """Rule 1 — strip ``parts[0]`` when the longest indexed module prefix proves it."""
    if await uow.packages.get(parts[0]) is not None:
        return None  # an indexed dependency of that name shadows the rule
    for end in range(len(parts), 1, -1):
        module = ".".join(parts[1:end])
        hits = await uow.chunks.list(
            filter={"package": PROJECT_PACKAGE_NAME, "module": module}, limit=1
        )
        if hits:
            return _strip_rewrite(parts, module, hits[0].metadata.get("source_path"), entry)
    return None


def _strip_rewrite(
    parts: tuple[str, ...], module: str, source_path: str | None, entry: ResolutionEntry
) -> TargetRewrite | None:
    if not is_stripped_source_root(parts[0], source_path, module):
        return None
    symbol_path = parts[1 + len(module.split(".")) :]
    if entry == "context" and not symbol_path:
        return None  # get_context rejects module-only targets
    return TargetRewrite("source_root_strip", ".".join(parts[1:]), module, symbol_path)


# ── Rule 2 / Rule 3 over an eligible projection ───────────────────────────


def _exact_leaf_rows(
    rows: Sequence[ChunkSymbolName], leaf: str, entry: ResolutionEntry
) -> list[ChunkSymbolName]:
    """Eligible rows whose leaf equals ``leaf`` (case-sensitive), one per (name, module)."""
    by_key = {
        (r.qualified_name, r.module): r
        for r in reversed(rows)
        if _leaf_of(r.qualified_name) == leaf and is_resolvable_symbol_name(r, entry)
    }
    return [by_key[key] for key in sorted(by_key)]


def _bare_name_rewrite(row: ChunkSymbolName) -> TargetRewrite | None:
    if row.qualified_name == row.module:
        return TargetRewrite("unique_bare_name", row.qualified_name, row.module, ())
    if not row.qualified_name.startswith(row.module + "."):
        return None  # name/module disagree — cannot pin the retry, so never guess
    symbol_path = tuple(row.qualified_name[len(row.module) + 1 :].split("."))
    return TargetRewrite("unique_bare_name", row.qualified_name, row.module, symbol_path)


def _shared_trailing_segments(name: str, target_parts: tuple[str, ...]) -> int:
    shared = 0
    for mine, theirs in zip(reversed(name.split(".")), reversed(target_parts), strict=False):
        if mine != theirs:
            break
        shared += 1
    return shared


def _similar_leaf_names(leaf: str, names: Sequence[str], cutoff: float) -> list[str]:
    """Tier 2 — casefolded difflib ratio over the whole projection (no prefix probe)."""
    wanted = leaf.casefold()
    ratio_by_leaf = {
        name_leaf: difflib.SequenceMatcher(None, wanted, name_leaf.casefold()).ratio()
        for name_leaf in {_leaf_of(n) for n in names}
    }
    kept = [n for n in names if ratio_by_leaf[_leaf_of(n)] >= cutoff]
    return sorted(kept, key=lambda n: (-ratio_by_leaf[_leaf_of(n)], n))


def rank_target_candidates(
    target_parts: tuple[str, ...], names: Sequence[str], cutoff: float
) -> list[str]:
    """Rule 3 ranking: exact leaves by shared trailing segments, else similar leaves.

    >>> rank_target_candidates(("q", "Cls"), ["a.Cls", "q.Cls"], 0.75)
    ['q.Cls', 'a.Cls']
    """
    tier1 = [n for n in names if _leaf_of(n) == target_parts[-1]]
    if tier1:
        return sorted(tier1, key=lambda n: (-_shared_trailing_segments(n, target_parts), n))
    return _similar_leaf_names(target_parts[-1], names, cutoff)


@dataclass(frozen=True, slots=True)
class ProjectTargetResolver:
    """Rules 1-3 over one read UoW; each rule gated by its own YAML flag.

    >>> await resolver.resolve("src.pkg.mod.Cls", entry="lookup")  # doctest: +SKIP
    TargetResolution(rewrite=TargetRewrite(rule='source_root_strip', ...))
    """

    uow_factory: Callable[[], UnitOfWork]
    rules: TargetResolutionConfig

    async def resolve(self, target: str, /, *, entry: ResolutionEntry) -> TargetResolution:
        if not is_symbol_target(target):
            return TargetResolution()
        parts = tuple(target.split("."))
        # Bound inside, returned after: the UoW's __aexit__ may swallow, so a
        # return inside the block reads as a missing return to mypy.
        async with self.uow_factory() as uow:
            if len(parts) >= 2:
                resolution = await self._resolve_dotted(uow, parts, entry)
            else:
                resolution = await self._resolve_bare(uow, target, entry)
        return resolution

    async def _resolve_dotted(
        self, uow: UnitOfWork, parts: tuple[str, ...], entry: ResolutionEntry
    ) -> TargetResolution:
        if self.rules.source_root_strip:
            rewrite = await _source_root_rewrite(uow, parts, entry)
            if rewrite is not None:
                return TargetResolution(rewrite=rewrite)
        return await self._miss_candidates(uow, parts, entry, project_rows=None)

    async def _resolve_bare(
        self, uow: UnitOfWork, name: str, entry: ResolutionEntry
    ) -> TargetResolution:
        if not (self.rules.unique_bare_name or self.rules.miss_candidates):
            return TargetResolution()
        rows, truncated = await _scan_symbol_names(uow, PROJECT_PACKAGE_NAME)
        if truncated:
            return TargetResolution(scan_truncated=True)
        exact = _exact_leaf_rows(rows, name, entry)
        rewrite = _bare_name_rewrite(exact[0]) if len(exact) == 1 else None
        if rewrite is not None and self.rules.unique_bare_name:
            return TargetResolution(rewrite=rewrite, exact_leaf_count=1)
        found = await self._miss_candidates(uow, (name,), entry, project_rows=rows)
        return replace(found, exact_leaf_count=len(exact), ambiguous=len(exact) >= 2)

    async def _miss_candidates(
        self,
        uow: UnitOfWork,
        parts: tuple[str, ...],
        entry: ResolutionEntry,
        *,
        project_rows: tuple[ChunkSymbolName, ...] | None,
    ) -> TargetResolution:
        """Rule 3 — reuses the bare scan when the pool is ``__project__``."""
        if not self.rules.miss_candidates:
            return TargetResolution()
        package = await _candidate_pool_package(uow, parts[0])
        if package == PROJECT_PACKAGE_NAME and project_rows is not None:
            rows, truncated = project_rows, False
        else:
            rows, truncated = await _scan_symbol_names(uow, package)
        if truncated:
            return TargetResolution(scan_truncated=True)
        return self._ranked_resolution(parts, rows, entry)

    def _ranked_resolution(
        self, parts: tuple[str, ...], rows: Sequence[ChunkSymbolName], entry: ResolutionEntry
    ) -> TargetResolution:
        names = sorted({r.qualified_name for r in rows if is_resolvable_symbol_name(r, entry)})
        ranked = rank_target_candidates(parts, names, self.rules.candidate_similarity_cutoff)
        shown = tuple(ranked[: self.rules.max_candidates])
        return TargetResolution(candidates=shown, candidate_total=len(ranked))


@dataclass(frozen=True, slots=True)
class NullTargetResolver:
    """Null-object resolver: every flag off, or direct/test construction."""

    async def resolve(self, target: str, /, *, entry: ResolutionEntry) -> TargetResolution:
        return TargetResolution()


# ── rendering (spec §3) ───────────────────────────────────────────────────


def _append_sentence(message: str, sentence: str) -> str:
    joiner = " " if message.endswith((".", "]]")) else ". "
    return f"{message}{joiner}{sentence}"


def _more_suffix(total: int, shown: int) -> str:
    return f" (+{total - shown} more)" if total > shown else ""


def _candidate_sentence(names: Sequence[str], total: int, *, ambiguous: bool, scope: str) -> str:
    listed = f"{', '.join(names)}{_more_suffix(total, len(names))}"
    if ambiguous:
        leaf = _leaf_of(names[0].split(" ", 1)[0])
        return f"Ambiguous name '{leaf}' matches {total} indexed symbols{scope}: {listed}."
    return f"Closest indexed names: {listed}."


def render_miss_message(message: str, res: TargetResolution) -> str:
    """``message`` plus the candidate sentence; ``message`` itself when there are none.

    >>> res = TargetResolution(candidates=("a.x",), candidate_total=1)
    >>> render_miss_message("package 'x' not indexed", res)
    "package 'x' not indexed. Closest indexed names: a.x."
    """
    if not res.candidates:
        return message
    total = max(res.candidate_total, len(res.candidates))
    sentence = _candidate_sentence(res.candidates, total, ambiguous=res.ambiguous, scope="")
    return _append_sentence(message, sentence)


def _workspace_match_count(res: TargetResolution) -> int:
    """Exact matches one project contributes: its bare-leaf count, or 1 for a rewrite."""
    return max(res.exact_leaf_count, 1 if res.rewrite is not None else 0)


def _project_entries(project: str, res: TargetResolution) -> tuple[tuple[str, ...], int]:
    names = (res.rewrite.canonical,) if res.rewrite is not None else res.candidates
    total = 1 if res.rewrite is not None else res.candidate_total
    return tuple(f"{name} (project {project})" for name in names), total


def render_workspace_miss_message(
    message: str, per_project: Sequence[tuple[str, TargetResolution]], max_candidates: int
) -> str:
    """Multi-project variant: ``name (project p)`` entries, recency order, deduplicated, capped.

    A workspace-wide exact-match total of 2+ uses the ambiguity sentence and
    lists only the projects holding exact matches.
    """
    ambiguous = sum(_workspace_match_count(res) for _, res in per_project) >= 2
    kept = [(p, r) for p, r in per_project if not ambiguous or _workspace_match_count(r)]
    tagged = [_project_entries(p, r) for p, r in kept]
    entries = list(dict.fromkeys(e for names, _ in tagged for e in names))
    if not entries:
        return message
    total = max(sum(t for _, t in tagged), len(entries))
    sentence = _candidate_sentence(
        entries[:max_candidates], total, ambiguous=ambiguous, scope=" across projects"
    )
    return _append_sentence(message, sentence)


# ── fallback orchestration ────────────────────────────────────────────────


def log_target_fallback_resolved(
    *, entry: ResolutionEntry, rewrite: TargetRewrite, target: str, project: str | None = None
) -> None:
    """One structured line per resolved fallback — key order fixed (spec §4)."""
    event: dict[str, str] = {
        "event": "target_fallback_resolved",
        "entry": entry,
        "rule": rewrite.rule,
        "target": target,
        "resolved": rewrite.canonical,
    }
    if project is not None:
        event["project"] = project
    _target_resolution_log.info(json.dumps(event))


def _raise_miss(original: NotFoundError, res: TargetResolution) -> NoReturn:
    """No candidates → the ORIGINAL exception object (byte- and type-identical)."""
    if not res.candidates:
        raise original
    raise NotFoundError(render_miss_message(str(original), res)) from original


def _without_canonical(res: TargetResolution, canonical: str) -> TargetResolution:
    kept = tuple(c for c in res.candidates if c != canonical)
    total = res.candidate_total - (len(res.candidates) - len(kept))
    return TargetResolution(
        candidates=kept, candidate_total=total, ambiguous=res.ambiguous and total >= 2
    )


async def with_target_fallback(
    target: str,
    *,
    entry: ResolutionEntry,
    resolver: TargetResolver,
    run_exact: Callable[[], Awaitable[_T]],
    run_rewrite: Callable[[TargetRewrite], Awaitable[_T]],
    project: str | None = None,
) -> _T:
    """``run_exact()``; on ``NotFoundError`` only, consult ``resolver`` once.

    A rewrite runs ``run_rewrite(rewrite)`` at most once; success logs
    ``target_fallback_resolved`` and returns its value. A retry miss raises
    the original message plus the canonical's candidates minus the canonical,
    chained ``from`` the original. Any other exception (e.g.
    ``ServiceUnavailableError``) propagates untouched.

    >>> await with_target_fallback("Cls", entry="lookup", resolver=r,  # doctest: +SKIP
    ...     run_exact=lambda: svc.lookup_exact(p), run_rewrite=lambda rw: svc.lookup_rewritten(p, rw))
    """
    try:
        return await run_exact()
    except NotFoundError as exc:
        original = exc
    resolution = await resolver.resolve(target, entry=entry)
    if resolution.rewrite is None:
        _raise_miss(original, resolution)
    rewrite = resolution.rewrite
    try:
        value = await run_rewrite(rewrite)
    except NotFoundError:
        retry_candidates = await resolver.resolve(rewrite.canonical, entry=entry)
    else:
        log_target_fallback_resolved(entry=entry, rewrite=rewrite, target=target, project=project)
        return value
    _raise_miss(original, _without_canonical(retry_candidates, rewrite.canonical))


def decide_workspace_rewrite(
    resolutions: Sequence[tuple[_K, TargetResolution]],
) -> tuple[_K, TargetRewrite] | None:
    """The one project whose rewrite may run in multi-project pass 2, else None.

    Generic over the project key so this module never imports
    ``multi_project_search`` (no cycle). Resolves only when exactly one
    project rewrites, no scan was truncated, and — for a bare-name rewrite —
    the workspace holds exactly one exact-leaf match.

    >>> decide_workspace_rewrite([("a", TargetResolution())]) is None
    True
    """
    if any(res.scan_truncated for _, res in resolutions):
        return None
    rewrites = [(key, res.rewrite) for key, res in resolutions if res.rewrite is not None]
    if len(rewrites) != 1:
        return None
    key, rewrite = rewrites[0]
    workspace_leaf_matches = sum(res.exact_leaf_count for _, res in resolutions)
    if rewrite.rule == "unique_bare_name" and workspace_leaf_matches != 1:
        return None  # unique in one project but not across the workspace (K3)
    return key, rewrite
