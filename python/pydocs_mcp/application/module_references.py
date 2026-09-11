"""A module target's reference graph — seeds, importer union, impact merge.

``get_references`` on a MODULE (``get_references(target="pkg.mod", …)``) answers
the IMPORT graph, because that is the only graph a module root participates in:
every IMPORTS edge starts at a module id, ``from M import X`` lands on the
child, and GOVERNS edges mostly target module ids.

- ``callers`` = who imports the module: edges into the module id, plus IMPORTS
  edges into its direct class/function children.
- ``impact`` = blast radius: transitive callers of the module AND its members,
  with the module's own internals removed.

The asymmetry is deliberate: ``callers`` answers "who imports this module",
``impact`` answers "what breaks if I change it".

Everything here is pure or takes its collaborator as a parameter, so
:mod:`pydocs_mcp.application.lookup_service` grows by only the dispatch.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.application.truncation import TruncationEntry, get_active_ledger
from pydocs_mcp.extraction.reference_kind import ReferenceKind

if TYPE_CHECKING:
    from pydocs_mcp.application.protocols import CrossNavigator, ReferenceNavigator
    from pydocs_mcp.application.reference_service import CrossReferenceRow, ImpactNode
    from pydocs_mcp.extraction.model import DocumentNode
    from pydocs_mcp.storage.node_reference import NodeReference

log = logging.getLogger(__name__)

# Direct children worth seeding: the kinds a sibling module can import by name.
# ``import_block`` / ``code_example`` children are scaffolding, never targets.
_SEED_KINDS: frozenset[str] = frozenset({"class", "function"})

# YAML anchor quoted in the truncation entry — the knob that widens the fan-out.
_SEED_CAP_KEY = "reference_graph.impact.max_module_seeds"

INHERITS_NEEDS_CLASS = (
    "direction 'inherits' applies only to class targets; {target!r} is a {kind}. "
    "Directions that accept it: callers, callees, impact, governed_by."
)
CONTEXT_NEEDS_SYMBOL = (
    "show 'context' needs a symbol target; {target!r} is a module — "
    'use get_symbol(target={target!r}, depth="tree") for its outline.'
)
PACKAGE_NEEDS_MODULE = (
    "direction {show!r} needs a module or symbol target; {package!r} is an indexed "
    "package with no top-level module — name a module, e.g. '{package}.<module>'"
)

# Module-target ``show`` values that cannot mean anything, with their wording.
_REJECTED_MODULE_SHOWS: dict[str, str] = {
    "inherits": INHERITS_NEEDS_CLASS,
    "context": CONTEXT_NEEDS_SYMBOL,
}


@dataclass(frozen=True, slots=True)
class ModuleSeeds:
    """The node ids one module target searches, plus what the cap left out.

    ``member_total`` is the UNCAPPED count of importable members, so the
    truncation entry can say how much of the module went unsearched.
    """

    ids: tuple[str, ...]
    member_total: int

    @property
    def unsearched_members(self) -> int:
        """Members the cap excluded — the first id is the module root itself."""
        return self.member_total - (len(self.ids) - 1)


def module_seed_ids(root: DocumentNode, cap: int) -> ModuleSeeds:
    """The module root plus its direct class/function children, in source order.

    Example::

        seeds = module_seed_ids(tree, cap=32)
        rows = await module_importer_rows(ref_svc, package, seeds.ids)
    """
    members = tuple(c.node_id for c in root.children if str(c.kind) in _SEED_KINDS)
    return ModuleSeeds(ids=(root.node_id, *members)[:cap], member_total=len(members))


def module_internal_qnames(root: DocumentNode) -> frozenset[str]:
    """Every qualified name inside the module tree, including the root itself.

    This is the exclusion set for ``impact``: a module's own members calling
    each other is not blast radius, it is the module.
    """
    names: list[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        names.append(node.qualified_name)
        stack.extend(node.children)
    return frozenset(names)


def reject_module_show(target: str, show: str) -> None:
    """Raise for the two directions a module target cannot answer (AC1.4)."""
    template = _REJECTED_MODULE_SHOWS.get(show)
    if template is None:
        return
    raise InvalidArgumentError(template.format(target=target, kind="module"))


async def module_importer_rows(
    ref_svc: ReferenceNavigator,
    package: str,
    seeds: Sequence[str],
) -> tuple[NodeReference | CrossReferenceRow, ...]:
    """Edges into the module id, then IMPORTS edges into its members (AC1.2).

    CALLS edges into a member are that member's own answer — asking for the
    module's callers must not drag every call site of every function with it.
    """
    if not seeds:
        return ()
    seen: set[tuple[str, str | None, str]] = set()
    rows = list(_unseen_rows(await ref_svc.callers(package, seeds[0]), seen=seen))
    for member in seeds[1:]:
        incoming = _imports_edges(await ref_svc.callers(package, member))
        rows.extend(_unseen_rows(incoming, seen=seen))
    return tuple(rows)


def _imports_edges(
    rows: Sequence[NodeReference | CrossReferenceRow],
) -> tuple[NodeReference | CrossReferenceRow, ...]:
    """``from M import X`` only — a CALLS edge into X is X's own answer."""
    return tuple(row for row in rows if row.kind == ReferenceKind.IMPORTS)


def _unseen_rows(
    candidates: Sequence[NodeReference | CrossReferenceRow],
    *,
    seen: set[tuple[str, str | None, str]],
) -> tuple[NodeReference | CrossReferenceRow, ...]:
    """Rows whose ``(from, to, kind)`` identity is new. Mutates ``seen``."""
    kept: list[NodeReference | CrossReferenceRow] = []
    for row in candidates:
        key = (row.from_node_id, row.to_node_id, str(row.kind))
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return tuple(kept)


async def module_impact_rows(
    navigator: CrossNavigator,
    ref_svc: ReferenceNavigator,
    package: str,
    module: str,
    seeds: Sequence[str],
    internal: frozenset[str],
    *,
    max_depth: int,
    limit: int,
) -> tuple[ImpactNode, ...]:
    """One impact walk per seed, merged into the exact global top-``limit``.

    Each seed is asked for ``limit + len(internal)`` rows so that no external
    row which belongs in the merged top-``limit`` can be crowded out of its own
    seed's slice by the internals we are about to drop (§1 proof).
    """
    per_seed = [
        await navigator.impact(
            ref_svc, package, seed, max_depth=max_depth, limit=limit + len(internal)
        )
        for seed in seeds
    ]
    return merge_impact(per_seed, internal, module, limit)


def merge_impact(
    per_seed: Sequence[tuple[ImpactNode, ...]],
    internal: frozenset[str],
    module: str,
    limit: int,
) -> tuple[ImpactNode, ...]:
    """Min-hop merge of per-seed walks, module internals dropped, re-ranked.

    Identity is ``(project, qualified_name)`` — the same identity the cross-repo
    navigator uses, so a same-named symbol in another bundle stays distinct.
    """
    nearest_by_identity: dict[tuple[str, str], ImpactNode] = {}
    for row in (row for rows in per_seed for row in rows):
        if _is_module_internal(row.qualified_name, internal, module):
            continue
        key = (row.project, row.qualified_name)
        if key not in nearest_by_identity or row.hop < nearest_by_identity[key].hop:
            nearest_by_identity[key] = row
    ranked = sorted(
        nearest_by_identity.values(),
        key=lambda n: (n.hop, -n.pagerank, -n.in_degree, n.qualified_name),
    )
    return tuple(ranked[:limit])


def _is_module_internal(qname: str, internal: frozenset[str], module: str) -> bool:
    """The tree's own names, plus anything under the module's dotted prefix.

    The prefix test catches names the tree does not carry as nodes (nested
    classes, comprehension scopes) but that still belong to this module.
    """
    return qname in internal or qname.startswith(f"{module}.")


def record_seed_cap(module: str, seeds: ModuleSeeds) -> None:
    """Register the un-searched members as an elision, and log it once (AC1.9).

    ``meta.truncated`` going true is correct here: a configured limit cut the
    answer, exactly like a row limit does.
    """
    if seeds.unsearched_members <= 0:
        return
    log.warning(
        json.dumps(
            {
                "event": "module_target_seed_cap",
                "module": module,
                "seeds": len(seeds.ids),
                "members": seeds.member_total,
            }
        )
    )
    ledger = get_active_ledger()
    if ledger is None:
        return
    ledger.record(
        TruncationEntry(
            description=(
                f"{seeds.unsearched_members} of {seeds.member_total} module members "
                f"not searched — raise {_SEED_CAP_KEY}"
            ),
            recovery="",
        )
    )


def log_module_target(module: str, direction: str, seeds: int, rows: int) -> None:
    """One structured line per module-target call — the fan-out is observable."""
    log.info(
        json.dumps(
            {
                "event": "module_target_references",
                "module": module,
                "direction": direction,
                "seeds": seeds,
                "rows": rows,
            }
        )
    )
