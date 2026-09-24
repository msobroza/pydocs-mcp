"""The blob-keyed extraction cache's contents and key (spec §6.1, §6.3 step 2).

A cache row holds everything computable from one file's bytes: chunk spans
(P0), the document tree, the module members, and the UNRESOLVED reference
sweep with the two resolver inputs captured beside it. A branch pass copies
these under its own branch key on a hit and parses nothing; resolution reruns
per branch (step 5), which is why ``to_node_id`` is never cached.

A row is keyed by ``(blob_sha, path, extraction key)``; the key is
:func:`file_extraction_cache_key` — the pipeline hash plus the per-file
extraction identity (#261, #309) — so a row can only be reused by a pass that
would have extracted the same file the same way.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePath
from types import MappingProxyType
from typing import Any, TypeVar

from pydocs_mcp.extraction.config import ChunkingConfig
from pydocs_mcp.extraction.model import DocumentNode
from pydocs_mcp.extraction.pipeline.stages.content_hash import file_extraction_identity
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.models import ModuleMember, ModuleMemberFilterField
from pydocs_mcp.retrieval.config import ReferenceCaptureConfig
from pydocs_mcp.storage.branch_records import BranchFile, ChunkMembership, FileExtraction
from pydocs_mcp.storage.node_reference import NodeReference
from pydocs_mcp.storage.protocols import UnitOfWork
from pydocs_mcp.storage.sqlite.document_tree_store import tree_from_json, tree_to_json
from pydocs_mcp.storage.sqlite.row_mappers import (
    parameters_from_json_rows,
    parameters_to_json_rows,
)

_BRANCH_KEY = ModuleMemberFilterField.BRANCH.value
_MODULE_KEY = ModuleMemberFilterField.MODULE.value
_PARAMETERS_KEY = "parameters"
# The owner "path" of a module id no single file owns: two files share it
# (package-rooting collisions, owner decision OD-A) or no tree roots it.
_NO_OWNER = ""
# Edge kinds the PASS derives across files, never from one blob's bytes, so
# never cached (spec §6.1: the cache holds file-derived artifacts only; #309
# review). SIMILAR is the kNN over every chunk the pass embedded, and it
# arrives resolved: the codec would drop its to_node_id and the resolver passes
# it through, so a hit would restore it dangling. GOVERNS is projected from the
# pass's mined decisions. A pass consuming a hit re-derives both.
_PASS_DERIVED_KINDS = frozenset({ReferenceKind.SIMILAR, ReferenceKind.GOVERNS})
# Between a key's two halves: the pipeline hash, then the per-file identity.
_IDENTITY_SEPARATOR = "|x:"
_Item = TypeVar("_Item")


def file_extraction_cache_key(
    pipeline_hash: str, *, chunking: ChunkingConfig, reference_capture: ReferenceCaptureConfig
) -> str:
    """The ``file_extractions`` key of a pass: ``"<pipeline hash>|x:<identity>"``.

    WHY a composite (#261, #309): the pipeline hash alone let a row outlive a
    grammar appearing or disappearing, a chunker change, a module-id rule bump
    or a capture change — each reshapes the cached tree / members / sweep, and
    each already moves the package hash. Stored in the existing
    ``pipeline_hash`` column (no schema bump; v19 stays P2's), the readable
    prefix keeping the pipeline half greppable.

    Example: ``file_extraction_cache_key("ab12", chunking=..., reference_capture=...)``
    returns ``'ab12|x:'`` followed by 16 lowercase hex characters.
    """
    identity = file_extraction_identity(chunking=chunking, reference_capture=reference_capture)
    return f"{pipeline_hash}{_IDENTITY_SEPARATOR}{identity}"


def require_extraction_cache_key(key: str) -> str:
    """``key``, when it has the shape :func:`file_extraction_cache_key` makes.

    WHY loud (#309 review): every cache seam takes its key from the caller, and
    a wrong one fails silently. Under ``""`` nothing is written or hit; under
    P0's bare pipeline hash nothing is hit either, and the GC would sweep every
    live row as superseded.

    Example: ``require_extraction_cache_key("ab12")`` raises ``ValueError``.
    """
    pipeline_hash, separator, identity = key.partition(_IDENTITY_SEPARATOR)
    if not (pipeline_hash and separator and identity):
        raise ValueError(
            f"invalid extraction cache key: got {key!r}, expected "
            f"'<pipeline hash>{_IDENTITY_SEPARATOR}<identity>' from file_extraction_cache_key"
        )
    return key


@dataclass(frozen=True, slots=True)
class ReferenceSweep:
    """One file's unresolved references plus the two resolver inputs captured with them.

    Read-only by type: :data:`EMPTY_SWEEP` is one instance shared by every row
    that carries no sweep, so a consumer merging tables must copy them.
    """

    references: tuple[NodeReference, ...]
    aliases: Mapping[str, Mapping[str, str]]
    class_attribute_types: Mapping[str, Mapping[str, str]]


EMPTY_SWEEP = ReferenceSweep((), MappingProxyType({}), MappingProxyType({}))


@dataclass(frozen=True, slots=True)
class FileArtifacts:
    tree: DocumentNode | None
    members: tuple[ModuleMember, ...]
    sweep: ReferenceSweep


@dataclass(frozen=True, slots=True)
class CacheSplit:
    hits: tuple[tuple[BranchFile, FileExtraction], ...]
    misses: tuple[BranchFile, ...]


@dataclass(frozen=True, slots=True)
class CachedFile:
    memberships: tuple[ChunkMembership, ...]
    artifacts: FileArtifacts


# ── codecs ────────────────────────────────────────────────────────────────


def _member_row(member: ModuleMember) -> dict[str, Any]:
    # The branch is the ROW's key, never the file's: stamped back on read.
    row = {k: v for k, v in member.metadata.items() if k != _BRANCH_KEY}
    if _PARAMETERS_KEY in row:
        # The row mapper's own codec: ``Parameter`` objects (inspect mode)
        # cannot go through json.dumps as they are.
        row[_PARAMETERS_KEY] = parameters_to_json_rows(row[_PARAMETERS_KEY])
    return row


def _member_from_row(row: dict[str, Any], branch: str) -> ModuleMember:
    if _PARAMETERS_KEY in row:
        row[_PARAMETERS_KEY] = parameters_from_json_rows(row[_PARAMETERS_KEY])
    return ModuleMember(metadata={**row, _BRANCH_KEY: branch})


def members_to_json(members: Sequence[ModuleMember]) -> str:
    return json.dumps([_member_row(m) for m in members], separators=(",", ":"), sort_keys=True)


def members_from_json(text: str, *, branch: str) -> tuple[ModuleMember, ...]:
    return tuple(_member_from_row(row, branch) for row in json.loads(text))


def _reference_row(ref: NodeReference) -> dict[str, str]:
    return {
        "from_package": ref.from_package,
        "from_node_id": ref.from_node_id,
        "to_name": ref.to_name,
        "kind": ref.kind.value,
    }


def sweep_to_json(
    refs: Sequence[NodeReference],
    aliases: Mapping[str, Mapping[str, str]],
    class_attribute_types: Mapping[str, Mapping[str, str]],
) -> str:
    # Unresolved on purpose: resolution is tree-derived and reruns per branch.
    payload = {
        "refs": [_reference_row(r) for r in refs],
        "aliases": {k: dict(v) for k, v in aliases.items()},
        "class_attribute_types": {k: dict(v) for k, v in class_attribute_types.items()},
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def sweep_from_json(text: str) -> ReferenceSweep:
    payload = json.loads(text)
    refs = tuple(
        NodeReference(
            row["from_package"],
            row["from_node_id"],
            row["to_name"],
            None,
            ReferenceKind(row["kind"]),
        )
        for row in payload["refs"]
    )
    return ReferenceSweep(refs, payload["aliases"], payload["class_attribute_types"])


def artifacts_json(artifacts: FileArtifacts) -> tuple[str | None, str, str]:
    """``(tree_json, members_json, references_json)`` for one cache row."""
    tree = tree_to_json(artifacts.tree) if artifacts.tree is not None else None
    sweep = artifacts.sweep
    return (
        tree,
        members_to_json(artifacts.members),
        sweep_to_json(sweep.references, sweep.aliases, sweep.class_attribute_types),
    )


# ── grouping one extraction per file ───────────────────────────────────────


def posix_source_path(path: str) -> str:
    """A chunker-written source path with POSIX separators; ``""`` stays ``""``.

    The chunkers' ``_relpath`` writes the PLATFORM separator (backslashes on
    Windows) while manifest paths are always POSIX, so a join by string
    equality would silently miss there. Every such join (the cache's file
    grouping, the membership and cache rows' spans) normalizes through here.
    """
    return PurePath(path).as_posix() if path else ""


def _module_owners(trees: Sequence[DocumentNode]) -> dict[str, str]:
    """Module id → the path of the one file whose tree roots it, else ``_NO_OWNER``."""
    owners: dict[str, str] = {}
    for tree in trees:
        claimed = tree.qualified_name in owners
        owners[tree.qualified_name] = _NO_OWNER if claimed else posix_source_path(tree.source_path)
    return owners


def _owner_of(node_id: str, owners: Mapping[str, str]) -> str:
    """The file owning ``node_id``: the owner of its LONGEST module-id prefix.

    Stops at the longest prefix even when that module has no single owner, so
    an edge of a colliding module never falls through to a shorter one.
    """
    candidate = node_id
    while candidate:
        if candidate in owners:
            return owners[candidate]
        candidate = candidate.rpartition(".")[0]
    return _NO_OWNER


def _group(items: Iterable[_Item], owner: Callable[[_Item], str]) -> dict[str, list[_Item]]:
    grouped: dict[str, list[_Item]] = defaultdict(list)
    for item in items:
        path = owner(item)
        if path:
            grouped[path].append(item)
    return grouped


def _group_tables(
    tables: Mapping[str, Mapping[str, str]], owners: Mapping[str, str]
) -> dict[str, dict[str, dict[str, str]]]:
    """Alias / class-attribute tables split per file by the module their key names."""
    by_path: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for key, table in tables.items():
        path = _owner_of(key, owners)
        if path:
            by_path[path][key] = dict(table)
    return by_path


def file_artifacts(
    trees: Sequence[DocumentNode],
    members: Sequence[ModuleMember],
    references: Sequence[NodeReference],
    *,
    aliases: Mapping[str, Mapping[str, str]],
    class_attribute_types: Mapping[str, Mapping[str, str]],
    relative_paths: Sequence[str],
) -> dict[str, FileArtifacts]:
    """Group one extraction's outputs by project-relative file.

    A tree by its root ``source_path``; members by the module their metadata
    names (exact — a member of a file that produced no tree has no owner);
    references and table entries by their longest module-id prefix. A file
    whose module id another file shares gets no artifacts: its members and
    edges cannot be told apart, so it stays a miss rather than a wrong hit.
    Edges of a pass-derived kind (SIMILAR, GOVERNS) are never cached.
    """
    owners = _module_owners(trees)
    wanted = set(relative_paths)
    tree_by_path = {
        posix_source_path(t.source_path): t for t in trees if owners[t.qualified_name] in wanted
    }
    member_groups = _group(members, lambda m: owners.get(m.metadata.get(_MODULE_KEY, ""), ""))
    file_derived = (r for r in references if r.kind not in _PASS_DERIVED_KINDS)
    ref_groups = _group(file_derived, lambda r: _owner_of(r.from_node_id, owners))
    alias_groups = _group_tables(aliases, owners)
    type_groups = _group_tables(class_attribute_types, owners)
    return {
        path: FileArtifacts(
            tree,
            tuple(member_groups.get(path, ())),
            ReferenceSweep(
                tuple(ref_groups.get(path, ())),
                alias_groups.get(path, {}),
                type_groups.get(path, {}),
            ),
        )
        for path, tree in tree_by_path.items()
    }


# ── the split and the hit ─────────────────────────────────────────────────


async def split_cache_hits(
    uow: UnitOfWork, files: Sequence[BranchFile], extraction_cache_key: str
) -> CacheSplit:
    """Hits carry a full row (tree included) under ``extraction_cache_key``;
    blob-less files, rows under any other key and spans-only rows (P0's shape)
    are misses. A key :func:`file_extraction_cache_key` did not make raises."""
    require_extraction_cache_key(extraction_cache_key)
    hits: list[tuple[BranchFile, FileExtraction]] = []
    misses: list[BranchFile] = []
    for file in files:
        row = await _cached_row(uow, file, extraction_cache_key)
        if row is not None and row.tree_json is not None:
            hits.append((file, row))
        else:
            misses.append(file)
    return CacheSplit(tuple(hits), tuple(misses))


async def _cached_row(uow: UnitOfWork, file: BranchFile, key: str) -> FileExtraction | None:
    if not file.blob_sha:
        return None
    return await uow.file_extractions.get(file.blob_sha, file.path, key)


def cached_file(row: FileExtraction, *, branch: str) -> CachedFile:
    spans = json.loads(row.chunk_spans)
    memberships = tuple(
        ChunkMembership(branch, int(cid), row.path, start, end) for cid, start, end in spans
    )
    artifacts = FileArtifacts(
        tree=tree_from_json(row.tree_json) if row.tree_json else None,
        members=members_from_json(row.members_json, branch=branch) if row.members_json else (),
        sweep=sweep_from_json(row.references_json) if row.references_json else EMPTY_SWEEP,
    )
    return CachedFile(memberships, artifacts)


__all__ = (
    "EMPTY_SWEEP",
    "CacheSplit",
    "CachedFile",
    "FileArtifacts",
    "ReferenceSweep",
    "artifacts_json",
    "cached_file",
    "file_artifacts",
    "file_extraction_cache_key",
    "members_from_json",
    "members_to_json",
    "posix_source_path",
    "require_extraction_cache_key",
    "split_cache_hits",
    "sweep_from_json",
    "sweep_to_json",
)
