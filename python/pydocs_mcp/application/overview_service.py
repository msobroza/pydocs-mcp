"""OverviewService — the §D17 structural orientation card (blocks 1, 3-7).

uow_factory service (CLAUDE.md contract). Every block reads data the index
already holds; centrality ranking uses node_scores.pagerank and degrades to
the degree_by_package in-degree proxy — the SAME rule §D6/§D11 use, one
degradation strategy across features. Blocks 2/8/9 (LLM summary, decisions,
git activity) land with the decision layer.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from statistics import median

from pydocs_mcp.application.overview_aggregates import ActivitySummary, OverviewAggregates
from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ModuleMember, Package
from pydocs_mcp.storage.node_score import CommunityCohesion, NodeScore
from pydocs_mcp.storage.protocols import UnitOfWork

_DEFAULT_MAX_MODULES = 20
_DEFAULT_MAX_COMMUNITIES = 10
_DEFAULT_MAX_ROOTS = 5
_DEFAULT_MAX_DEPENDENCIES = 10
_TEST_PATH_MARKERS = ("test", "conftest")
_DUNDER_MAIN_SUFFIX = ".__main__"


@dataclass(frozen=True, slots=True)
class ModuleEntry:
    qualified_name: str
    first_doc_line: str
    rank_score: float


@dataclass(frozen=True, slots=True)
class EntryPoint:
    name: str
    kind: str  # script | module | root


@dataclass(frozen=True, slots=True)
class CommunityEntry:
    label: str
    size: int
    cohesion: float
    top_member: str


@dataclass(frozen=True, slots=True)
class OverviewCard:
    package: str
    package_count: int
    module_count: int
    symbol_count: int
    doc_coverage: float  # 0..1, members with docstrings
    modules: tuple[ModuleEntry, ...]
    entry_points: tuple[EntryPoint, ...]
    communities: tuple[CommunityEntry, ...]  # empty + hint when node_scores off
    dependency_profile: tuple[tuple[str, int], ...]
    node_scores_available: bool
    # Blocks 2/8/9 aggregates (§D17). New fields MUST default so the existing
    # construction sites (fakes / golden fixtures) that predate them still build.
    activity: ActivitySummary | None = None


@dataclass(frozen=True, slots=True)
class OverviewService:
    uow_factory: Callable[[], UnitOfWork]
    scripts: dict[str, str]  # [project.scripts], parsed at composition
    max_modules: int = _DEFAULT_MAX_MODULES
    max_communities: int = _DEFAULT_MAX_COMMUNITIES
    # Index-time aggregates (block 9 git activity, later block 2 LLM summary)
    # read via an injected closure — the freshness-probe pattern (sync sqlite3
    # reader built in ``storage.factories``, threaded off the event loop). ``None``
    # when the deployment doesn't persist them → the block is simply omitted.
    aggregates_reader: Callable[[], OverviewAggregates] | None = None

    async def build(self, package: str = "") -> OverviewCard:
        target = package or PROJECT_PACKAGE_NAME
        async with self.uow_factory() as uow:
            packages = await uow.packages.list()
            trees = await uow.trees.load_all_in_package(target)
            members = await uow.module_members.list(filter={"package": target})
            scores = await uow.node_scores.for_package(target)
            degrees = await uow.references.degree_by_package(target)
            imports = await uow.references.imports_grouped_by_target(target)
            cohesion = await uow.node_scores.community_cohesion(target) if scores else {}
        aggregates = self._read_aggregates()
        return self._assemble(
            target, packages, trees, members, scores, degrees, imports, cohesion, aggregates
        )

    def _read_aggregates(self) -> OverviewAggregates:
        """Load the persisted aggregates via the injected reader (empty without one)."""
        if self.aggregates_reader is None:
            return OverviewAggregates()
        return self.aggregates_reader()

    def _assemble(
        self,
        target: str,
        packages: Sequence[Package],
        trees: Mapping[str, DocumentNode],
        members: Sequence[ModuleMember],
        scores: Sequence[NodeScore],
        degrees: Mapping[str, tuple[int, int]],
        imports: Mapping[str, int],
        cohesion: Mapping[int, CommunityCohesion],
        aggregates: OverviewAggregates,
    ) -> OverviewCard:
        pagerank = {s.qualified_name: s.pagerank for s in scores}
        modules = _rank_modules(trees, pagerank, degrees, bool(scores), self.max_modules)
        communities = _build_communities(scores, cohesion, self.max_communities) if scores else ()
        own_tops = _own_top_levels(trees)
        return OverviewCard(
            package=target,
            package_count=len(packages),
            module_count=len(trees),
            symbol_count=len(members),
            doc_coverage=_doc_coverage(members),
            modules=modules,
            entry_points=_entry_points(self.scripts, trees, degrees),
            communities=communities,
            dependency_profile=_dependency_profile(imports, own_tops),
            node_scores_available=bool(scores),
            activity=aggregates.activity,
        )


def _first_doc_line(node: DocumentNode) -> str:
    """First non-empty line of the module node's prose text (empty if none)."""
    for line in node.text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _rank_modules(
    trees: Mapping[str, DocumentNode],
    pagerank: Mapping[str, float],
    degrees: Mapping[str, tuple[int, int]],
    has_scores: bool,
    cap: int,
) -> tuple[ModuleEntry, ...]:
    """Module map: candidates = tree module names ranked by pagerank when
    scores exist, else by the degree in-degree proxy; tie-break by name."""
    entries: list[ModuleEntry] = []
    for qname, node in trees.items():
        score = pagerank.get(qname, 0.0) if has_scores else float(degrees.get(qname, (0, 0))[0])
        entries.append(ModuleEntry(qname, _first_doc_line(node), score))
    entries.sort(key=lambda e: (-e.rank_score, e.qualified_name))
    return tuple(entries[:cap])


def _doc_coverage(members: Sequence[ModuleMember]) -> float:
    """Fraction of members carrying a non-empty ``docstring`` metadata value."""
    documented = sum(1 for m in members if str(m.metadata.get("docstring", "") or "").strip())
    return documented / max(1, len(members))


def _has_test_marker(qname: str) -> bool:
    """True if any dotted segment of ``qname`` contains a test-path marker."""
    return any(marker in seg for seg in qname.split(".") for marker in _TEST_PATH_MARKERS)


def _root_entry_points(
    trees: Mapping[str, DocumentNode],
    degrees: Mapping[str, tuple[int, int]],
) -> list[str]:
    """Qnames with zero in-degree and out-degree strictly above the median
    out-degree of the card's module candidates (capped in the caller)."""
    if not degrees:
        return []
    candidate_out = [degrees.get(q, (0, 0))[1] for q in trees]
    threshold = median(candidate_out) if candidate_out else 0.0
    roots = [q for q, (in_deg, out_deg) in degrees.items() if in_deg == 0 and out_deg > threshold]
    roots.sort(key=lambda q: (-degrees[q][1], q))
    return roots


# Kind precedence for entry-point dedup: a more specific/declared kind beats an
# inferred one. A [project.scripts] name is declared; a *.__main__ module is a
# structural convention; a zero-in/high-out graph root is purely inferred — so
# the same name surfacing under several kinds collapses to the most specific.
_ENTRY_KIND_PRECEDENCE = {"script": 0, "module": 1, "root": 2}


def _entry_points(
    scripts: Mapping[str, str],
    trees: Mapping[str, DocumentNode],
    degrees: Mapping[str, tuple[int, int]],
) -> tuple[EntryPoint, ...]:
    """Union of ``[project.scripts]``, ``*.__main__`` modules, and graph roots.

    Any qname carrying a test-path marker segment is dropped — a test harness
    entry point is noise on the orientation card. A name emitted under several
    kinds (e.g. a ``*.__main__`` module that is also a graph root) collapses to
    its most specific kind via ``_ENTRY_KIND_PRECEDENCE``.
    """
    entries: list[EntryPoint] = [EntryPoint(name, "script") for name in scripts]
    entries.extend(
        EntryPoint(qname, "module") for qname in trees if qname.endswith(_DUNDER_MAIN_SUFFIX)
    )
    entries.extend(
        EntryPoint(qname, "root")
        for qname in _root_entry_points(trees, degrees)[:_DEFAULT_MAX_ROOTS]
    )
    kept = [e for e in entries if not _has_test_marker(e.name)]
    return _dedup_by_kind_precedence(kept)


def _dedup_by_kind_precedence(entries: Sequence[EntryPoint]) -> tuple[EntryPoint, ...]:
    """Keep one EntryPoint per name — the one whose kind ranks most specific."""
    best: dict[str, EntryPoint] = {}
    for entry in entries:
        current = best.get(entry.name)
        if current is None or _entry_rank(entry) < _entry_rank(current):
            best[entry.name] = entry
    return tuple(best.values())


def _entry_rank(entry: EntryPoint) -> int:
    return _ENTRY_KIND_PRECEDENCE[entry.kind]


def _community_label(members: Sequence[str], top_member: str) -> str:
    """Longest shared dotted prefix of the community's member qnames.

    Falls back to the top member's module (its qname minus the final segment)
    when the members share no common dotted prefix.
    """
    segments = [m.split(".") for m in members]
    shared: list[str] = []
    # strict=False: community members legitimately differ in segment depth
    # (``proj.core`` vs ``proj.core.helpers``); zip stops at the shortest.
    for parts in zip(*segments, strict=False):
        if len(set(parts)) == 1:
            shared.append(parts[0])
        else:
            break
    if shared:
        return ".".join(shared)
    return top_member.rsplit(".", 1)[0] if "." in top_member else top_member


def _build_communities(
    scores: Sequence[NodeScore],
    cohesion: Mapping[int, CommunityCohesion],
    cap: int,
) -> tuple[CommunityEntry, ...]:
    """Group scores by community (skip -1); label by shared prefix, size by
    group, top member by pagerank, cohesion = intra / (intra + cross)."""
    groups: dict[int, list[NodeScore]] = {}
    for s in scores:
        if s.community == -1:
            continue
        groups.setdefault(s.community, []).append(s)
    entries: list[CommunityEntry] = []
    for community, rows in groups.items():
        top = max(rows, key=lambda r: r.pagerank)
        member_names = [r.qualified_name for r in rows]
        entries.append(
            CommunityEntry(
                label=_community_label(member_names, top.qualified_name),
                size=len(rows),
                cohesion=_cohesion_ratio(cohesion.get(community)),
                top_member=top.qualified_name,
            )
        )
    entries.sort(key=lambda e: (-e.size, e.label))
    return tuple(entries[:cap])


def _cohesion_ratio(cohesion: CommunityCohesion | None) -> float:
    """intra / max(1, intra + cross) — 0.0 when the community has no edges."""
    if cohesion is None:
        return 0.0
    return cohesion.intra_edges / max(1, cohesion.intra_edges + cohesion.cross_edges)


def _own_top_levels(trees: Mapping[str, DocumentNode]) -> frozenset[str]:
    """Distinct first dotted segments of the project's module qnames.

    The storage-layer ``top == package`` self-import guard is dead for the
    primary target: ``package`` is the ``__project__`` sentinel while the
    project's own import targets carry the REAL top-level name (``proj``), so
    self-imports slip through. Deriving the real top-levels here lets the
    consumer strip them from the dependency profile.
    """
    return frozenset(q.split(".", 1)[0] for q in trees if q)


def _dependency_profile(
    imports: Mapping[str, int],
    own_tops: frozenset[str],
) -> tuple[tuple[str, int], ...]:
    """External import counts sorted by count desc (name tie-break), top 10.

    Drops any target whose top-level segment is one of the project's own
    module top-levels — those are self-imports the storage guard missed.
    """
    external = {name: count for name, count in imports.items() if name not in own_tops}
    ordered = sorted(external.items(), key=lambda kv: (-kv[1], kv[0]))
    return tuple(ordered[:_DEFAULT_MAX_DEPENDENCIES])
