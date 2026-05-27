"""ReferenceCaptureStage — captures cross-node references from ``.py`` files.

Re-parses each ``.py`` file in ``state.file_contents`` (cheap —
``ast.parse`` is ~ms per file) and runs ``capture_imports`` /
``capture_calls`` / ``capture_inherits`` from
:mod:`pydocs_mcp.extraction.strategies.references`. Stores the
unresolved tuple on ``state.references``, the per-module alias table on
``state.reference_aliases``, and the per-class ``self.X`` attribute-type
table on ``state.class_attribute_types``. The resolver pass runs later
inside ``IndexingService.reindex_package`` (where it has access to the
cross-package qname universe via ``uow.trees``).

Per-file isolation: a ``SyntaxError`` or other ``Exception`` on one
file logs and continues — same contract as
:class:`~pydocs_mcp.extraction.pipeline.stages.chunking.ChunkingStage`
(AC #27). The dedicated stage (rather than rewiring ``ChunkingStage`` to
thread ``ref_collector`` everywhere) keeps capture single-purpose and
the cost is one extra ``ast.parse`` per file — bounded and only over
``.py`` files.

The capture configuration (``enabled`` + ``kinds`` filter) lives as a
module-level singleton updated by ``configure_from_app_config`` at
server / CLI startup. A module-level constant is the right shape here
because the stage's ``run`` is otherwise stateless and the config is
process-global, not per-pipeline-invocation.
"""
from __future__ import annotations

import ast
import asyncio
import logging
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydocs_mcp.extraction.pipeline.ingestion import (
    IngestionState,
    ReferenceBundle,
)
from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.retrieval.config import ReferenceCaptureConfig

log = logging.getLogger("pydocs-mcp")


# Module-level capture config — installed by ``configure_from_app_config`` at
# server / CLI startup. Default keeps the pre-#5c behavior (all three AST
# kinds enabled) so unit tests and any caller that constructs the stage
# without going through the YAML path get the safe baseline.
_CAPTURE_CONFIG: ReferenceCaptureConfig = ReferenceCaptureConfig()


def _get_capture_config() -> ReferenceCaptureConfig:
    """Return the active reference-capture config (module-level singleton)."""
    return _CAPTURE_CONFIG


def _set_capture_config(cfg: ReferenceCaptureConfig) -> None:
    """Install a new reference-capture config — called by
    ``configure_from_app_config(cfg)`` at server / CLI startup."""
    global _CAPTURE_CONFIG
    _CAPTURE_CONFIG = cfg


@stage_registry.register("reference_capture")
@dataclass(frozen=True, slots=True)
class ReferenceCaptureStage:
    name: str = "reference_capture"

    async def run(self, state: IngestionState) -> IngestionState:
        cfg = _get_capture_config()
        if not cfg.enabled:
            # Short-circuit — capture disabled by YAML. Reset both the new
            # ReferenceBundle and the legacy flat fields to their
            # defaults so a re-run from a state with prior captures
            # doesn't keep stale values.
            # I7 commit 2 — write to both ReferenceBundle and legacy flat.
            return replace(
                state,
                refs=ReferenceBundle(),
                references=(),
                reference_aliases={},
                class_attribute_types={},
            )
        allowed = set(cfg.kinds)
        refs, aliases, attr_types = await asyncio.to_thread(
            self._capture_all, state, allowed,
        )
        refs_tuple = tuple(refs)
        # I7 commit 2 — write to ReferenceBundle AND mirror to legacy flat.
        new_refs_bundle = ReferenceBundle(
            references=refs_tuple,
            reference_aliases=aliases,
            class_attribute_types=attr_types,
        )
        return replace(
            state,
            refs=new_refs_bundle,
            references=refs_tuple,
            reference_aliases=aliases,
            class_attribute_types=attr_types,
        )

    def _capture_all(
        self, state: IngestionState, allowed: set[str],
    ) -> tuple[list[Any], dict[str, dict[str, str]], dict[str, dict[str, str]]]:
        # Deferred imports — strategies pull in ast + reference value objects
        # which are otherwise irrelevant at stage-registry construction time.
        from pydocs_mcp.extraction.strategies.chunkers import _module_from_path
        from pydocs_mcp.extraction.strategies.references import (
            ReferenceCollector,
            capture_calls,
            capture_imports,
            capture_inherits,
            capture_mentions,
            capture_self_attribute_types,
        )
        collector = ReferenceCollector()
        # I7 commit 2 — read file_contents/package_name/root from the
        # FileBundle when populated, fall back to legacy flat fields.
        file_contents = (
            state.files.file_contents if state.files.file_contents
            else state.file_contents
        )
        package_name = state.files.package_name or state.package_name
        root = state.files.root if state.files.root != Path(".") else state.root
        for path, source in file_contents:
            if not source:
                continue
            # Python branch — AST capture for calls/imports/inherits.
            if path.endswith(".py"):
                try:
                    tree = ast.parse(source)
                except SyntaxError as exc:
                    # Per-file containment — same contract as ChunkingStage (AC #27).
                    log.warning(
                        "reference_capture: ast.parse failed on %s: %s", path, exc,
                    )
                    continue
                try:
                    module_qname = _module_from_path(path, root)
                    # capture_imports always runs — it populates collector.aliases,
                    # which the resolver consumes regardless of whether IMPORTS
                    # rows survive the kinds filter below. We drop the IMPORTS
                    # *rows* after capture if "imports" isn't in allowed, but
                    # the alias table is the source of truth for the resolver
                    # and must be preserved (spec §5.3 / Task 3 of sub-PR #5c).
                    capture_imports(
                        tree.body,
                        from_package=package_name,
                        module_qname=module_qname,
                        collector=collector,
                    )
                    if "calls" in allowed or "inherits" in allowed:
                        for stmt in tree.body:
                            if (
                                isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and "calls" in allowed
                            ):
                                capture_calls(
                                    stmt.body,
                                    from_package=package_name,
                                    from_node_id=f"{module_qname}.{stmt.name}",
                                    collector=collector,
                                )
                            elif isinstance(stmt, ast.ClassDef):
                                class_qname = f"{module_qname}.{stmt.name}"
                                if "inherits" in allowed:
                                    capture_inherits(
                                        list(stmt.bases),
                                        from_package=package_name,
                                        class_qname=class_qname,
                                        collector=collector,
                                    )
                                if "calls" in allowed:
                                    # self.X.Y inference: learn attribute types
                                    # from this class FIRST, then walk every
                                    # method body for calls. The capture helper
                                    # re-iterates ``cls.body`` and ``init.body``
                                    # internally, but that's a few extra dozen
                                    # iterations per class — negligible
                                    # alongside the per-method ast.walk that
                                    # capture_calls runs.
                                    collector.record_class_attrs(
                                        class_qname,
                                        capture_self_attribute_types(stmt),
                                    )
                                    for m in stmt.body:
                                        if isinstance(
                                            m, (ast.FunctionDef, ast.AsyncFunctionDef),
                                        ):
                                            capture_calls(
                                                m.body,
                                                from_package=package_name,
                                                from_node_id=f"{class_qname}.{m.name}",
                                                collector=collector,
                                            )
                except Exception as exc:  # noqa: BLE001 -- per-file containment
                    log.warning("reference_capture failed on %s: %s", path, exc)
                continue
            # Markdown branch — regex-fuzzy MENTIONS for backtick-quoted
            # dotted names. Gated on "mentions" in allowed because the
            # shipped default omits MENTIONS (lower-precision than AST
            # capture, opt-in per spec §5.3).
            if path.endswith(".md") and "mentions" in allowed:
                try:
                    from_node_id = _module_from_path(path, root)
                    capture_mentions(
                        source,
                        from_package=package_name,
                        from_node_id=from_node_id,
                        collector=collector,
                    )
                except Exception as exc:  # noqa: BLE001 -- per-file containment
                    log.warning(
                        "reference_capture (markdown) failed on %s: %s", path, exc,
                    )
                continue
        # Filter IMPORTS rows out of collector.refs if "imports" isn't allowed.
        # The alias table (collector.aliases) is untouched — the resolver
        # consumes it independently of whether IMPORTS edges land in the DB.
        if "imports" not in allowed:
            refs = [r for r in collector.refs if r.kind is not ReferenceKind.IMPORTS]
        else:
            refs = collector.refs
        return refs, collector.aliases, collector.class_attribute_types

    @classmethod
    def from_dict(cls, data: dict, context: Any) -> "ReferenceCaptureStage":
        return cls()

    def to_dict(self) -> dict:
        return {"type": "reference_capture"}


__all__ = (
    "ReferenceCaptureStage",
    "_get_capture_config",
    "_set_capture_config",
)
