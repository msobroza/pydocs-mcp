"""Which packages a decision read covers (#346).

Under ``decision_capture.include_deps`` a dependency's decisions persist under
its own package; these helpers keep them answering only when a request names
them. :data:`PROJECT_DECISION_CORPUS` is the corpus of ``get_why(query)`` and of
every decision search that asks for no dependency;
:func:`decision_pre_filter_for_packages` pushes a corpus into the retrieval
pre-filter; :func:`records_for_governs_edges_to_qname` resolves a target's
GOVERNS edges in the package that mined each decision;
:func:`bundle_holds_dependency_decisions` tells the composition root whether a
loaded bundle's ordinary searches must leave dependency decisions out.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

from pydocs_mcp.extraction.decisions.engine import decision_key
from pydocs_mcp.models import PROJECT_PACKAGE_NAME, ChunkFilterField, ChunkOrigin
from pydocs_mcp.storage.decision_record import DecisionRecord

if TYPE_CHECKING:
    from pydocs_mcp.storage.protocols import UnitOfWork

# The corpus of get_why(query) and of every decision search that asks for no
# dependency (#346): dependency decisions answer only when a request names them.
PROJECT_DECISION_CORPUS = (PROJECT_PACKAGE_NAME,)

# ``{decision_key: record}`` per package. A decision key is a normalized title,
# not package-qualified, so the package is what keeps a dependency's decision
# apart from a same-titled project one.
RecordsByKeyPerPackage = dict[str, Mapping[str, DecisionRecord]]


async def bundle_holds_dependency_decisions(uow_factory: Callable[[], UnitOfWork]) -> bool:
    """Whether the bundle behind ``uow_factory`` holds any dependency's decisions.

    WHY the bundle and not the config (#346): a bundle is often indexed under
    one config and served under another (a read-only ``--workspace`` / ``--db``
    load, a GPU-index / CPU-serve split), so only what it holds says whether its
    ordinary searches can meet a dependency decision. Read once per loaded
    bundle, never per query.
    """
    async with uow_factory() as uow:
        holds = await uow.decisions.has_dependency_records()
    return holds


def decision_pre_filter_for_packages(packages: tuple[str, ...]) -> dict[str, object]:
    """The decision-chunk pre-filter: the origin plus the corpus's packages.

    ``package`` rather than ``scope``: the dense branch drops ``scope`` from the
    tree it searches, while a ``package`` ``eq`` / ``in`` reaches both branches.
    """
    wanted: object = packages[0] if len(packages) == 1 else {"in": list(packages)}
    return {
        ChunkFilterField.ORIGIN.value: ChunkOrigin.DECISION_RECORD.value,
        ChunkFilterField.PACKAGE.value: wanted,
    }


async def records_for_governs_edges_to_qname(
    uow: UnitOfWork,
    qname: str,
    records_by_key_per_package: RecordsByKeyPerPackage,
    branch: str | None = None,
) -> list[DecisionRecord]:
    """The records whose GOVERNS edge resolves to ``qname``, each looked up in
    the package that mined it. ``records_by_key_per_package`` is the caller's
    per-call cache: each package's records load once, on first use. Edges and
    records are ``branch``'s (#313; ``None`` — the served default)."""
    found: list[DecisionRecord] = []
    for package, key in await uow.references.find_governing(qname, branch=branch):
        if package not in records_by_key_per_package:
            records = await uow.decisions.list_for_package(package, branch=branch)
            records_by_key_per_package[package] = {decision_key(r.title): r for r in records}
        record = records_by_key_per_package[package].get(key)
        if record is not None:
            found.append(record)
    return found
