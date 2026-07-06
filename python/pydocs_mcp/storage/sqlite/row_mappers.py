"""Row ↔ domain-model mappers for the chunks / module_members / packages tables.

Kept in one module (not one per repository): the mappers are the
deserialization CONTRACT with the SQLite schema, and drift surfaces
uniformly when they live side by side. ``storage/factories.py`` reuses
``row_to_chunk`` for the vector-hit hydrator for the same reason.
"""

from __future__ import annotations

import json

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
    Parameter,
)


# ── Chunk ↔ row ──────────────────────────────────────────────────────────
def _chunk_to_row(c: Chunk) -> dict[str, object]:
    md = c.metadata
    return {
        "id": c.id,
        "package": md.get(ChunkFilterField.PACKAGE.value, ""),
        "module": md.get(ChunkFilterField.MODULE.value, ""),
        "title": md.get(ChunkFilterField.TITLE.value, ""),
        "text": c.text,
        "origin": md.get(ChunkFilterField.ORIGIN.value, ""),
        "content_hash": c.content_hash,
        # Plain metadata key (no ChunkFilterField member, like "kind"); persisted
        # as its own column (schema v7) so it survives the round-trip — the tree
        # reasoning step joins LLM-picked nodes on it.
        "qualified_name": md.get("qualified_name", ""),
    }


def row_to_chunk(row) -> Chunk:
    """Convert a ``sqlite3.Row`` (or dict) to a ``Chunk`` domain model.

    Accesses each column directly: a ``KeyError`` from a missing column is
    the correct signal that the schema has drifted (repositories always
    ``SELECT *`` or explicit columns matching the schema), and silently
    returning ``None`` would mask the drift.
    """
    metadata: dict[str, object] = {}
    for key in (
        ChunkFilterField.PACKAGE.value,
        ChunkFilterField.MODULE.value,
        ChunkFilterField.TITLE.value,
        ChunkFilterField.ORIGIN.value,
    ):
        value = row[key]
        if value:
            metadata[key] = value
    # qualified_name is a plain metadata key (not a ChunkFilterField). Persisted
    # as its own column (schema v7) so it survives the round-trip — the tree
    # reasoning step joins LLM-picked nodes on it. Direct index: the column is
    # guaranteed by the migration, matching the row["content_hash"] convention.
    qname = row["qualified_name"]
    if qname:
        metadata["qualified_name"] = qname
    # Defensive against NULL: legacy rows (pre-content_hash wiring) carry
    # NULL in this column. Empty-string preserves the existing __post_init__
    # auto-compute path (which fires when content_hash is falsy).
    hash_value = row["content_hash"]
    return Chunk(
        text=row["text"] or "",
        id=row["id"],
        metadata=metadata,
        content_hash=hash_value if hash_value is not None else "",
    )


# ── ModuleMember ↔ row ───────────────────────────────────────────────────
def _module_member_to_row(m: ModuleMember) -> dict[str, object]:
    md = m.metadata
    params = md.get("parameters", ())
    params_json = json.dumps(
        [
            {"name": p.name, "annotation": p.annotation, "default": p.default}
            if isinstance(p, Parameter)
            else p
            for p in params
        ]
    )
    return {
        "id": m.id,
        "package": md.get(ModuleMemberFilterField.PACKAGE.value, ""),
        "module": md.get(ModuleMemberFilterField.MODULE.value, ""),
        "name": md.get(ModuleMemberFilterField.NAME.value, ""),
        "kind": md.get(ModuleMemberFilterField.KIND.value, ""),
        "signature": md.get("signature", ""),
        "return_annotation": md.get("return_annotation", ""),
        "parameters": params_json,
        "docstring": md.get("docstring", ""),
    }


def _row_to_module_member(row) -> ModuleMember:
    """Convert a ``sqlite3.Row`` (or dict) to a ``ModuleMember`` domain model."""
    raw_params = json.loads(row["parameters"] or "[]")
    params = tuple(
        Parameter(
            name=p["name"],
            annotation=p.get("annotation", ""),
            default=p.get("default", ""),
        )
        for p in raw_params
    )
    metadata = {
        ModuleMemberFilterField.PACKAGE.value: row["package"] or "",
        ModuleMemberFilterField.MODULE.value: row["module"] or "",
        ModuleMemberFilterField.NAME.value: row["name"] or "",
        ModuleMemberFilterField.KIND.value: row["kind"] or "",
        "signature": row["signature"] or "",
        "return_annotation": row["return_annotation"] or "",
        "parameters": params,
        "docstring": row["docstring"] or "",
    }
    return ModuleMember(id=row["id"], metadata=metadata)


# ── Package ↔ row ────────────────────────────────────────────────────────
def _package_to_row(pkg: Package) -> dict[str, object]:
    return {
        "name": pkg.name,
        "version": pkg.version,
        "summary": pkg.summary,
        "homepage": pkg.homepage,
        "dependencies": json.dumps(list(pkg.dependencies)),
        "content_hash": pkg.content_hash,
        "origin": pkg.origin.value,
        # ``embedding_model`` round-trips so the startup staleness check
        # (IndexingService.invalidate_stale_embeddings) can detect a YAML
        # model rename and trigger re-embed of the affected packages.
        "embedding_model": pkg.embedding_model,
    }


def _row_to_package(row) -> Package:
    """Convert a ``sqlite3.Row`` (or dict) to a ``Package`` domain model."""
    # ``embedding_model`` column was added in schema v5 — older rows /
    # legacy callers may not surface it via ``sqlite3.Row`` key access,
    # so default to None when absent. ``or None`` keeps "" out of the
    # stale check (an empty string is not a model name).
    try:
        embedding_model = row["embedding_model"]
    except (IndexError, KeyError):
        embedding_model = None
    return Package(
        name=row["name"] or "",
        version=row["version"] or "",
        summary=row["summary"] or "",
        homepage=row["homepage"] or "",
        dependencies=tuple(json.loads(row["dependencies"] or "[]")),
        content_hash=row["content_hash"] or "",
        origin=PackageOrigin(row["origin"] or PackageOrigin.DEPENDENCY.value),
        embedding_model=embedding_model or None,
    )
