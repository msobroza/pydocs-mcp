"""Where decision-capture work runs — predicates the content hash shares.

The package hash must fold a setting exactly where that setting can change
what a package extracts: fold it anywhere else and packages re-extract for
nothing; miss a place and the new output is discarded as a cache hit on every
pass (issues #263, #347). A predicate here states one such "where" once, so the
salt that folds a setting reads the same answer the stages act on.

A leaf on purpose: it imports only config and ``TargetKind``, so
``stages/content_hash.py`` can use it without pulling the decision stages in.
"""

from __future__ import annotations

from pydocs_mcp.extraction.pipeline.ingestion import TargetKind
from pydocs_mcp.retrieval.config import DecisionCaptureConfig


def decision_mining_applies(config: DecisionCaptureConfig, target_kind: TargetKind) -> bool:
    """True when ``capture_decisions`` mines a target of ``target_kind``.

    The project is mined whenever ``decision_capture.enabled`` holds; a
    dependency only when ``include_deps`` holds too (issue #346).
    ``CaptureDecisionsPipeline.run`` gates on this, and the content hash folds
    the decision settings into a dependency's hash exactly where it holds. One
    predicate for both, because if the two drifted apart the #263 loop would
    return (mining with no fold), or every dependency would re-extract for
    nothing (a fold with no mining).

    Example: ``decision_mining_applies(DecisionCaptureConfig(),
    TargetKind.DEPENDENCY)`` is False — ``include_deps`` is off by default.
    """
    return config.enabled and (target_kind is TargetKind.PROJECT or config.include_deps)


def llm_structuring_applies(config: DecisionCaptureConfig, target_kind: TargetKind) -> bool:
    """True when the structuring LLM runs for a target of ``target_kind``.

    Only a mined PROJECT is ever structured: ``_maybe_build_llm_client`` builds
    a client only when ``decision_capture.enabled`` and
    ``llm_structuring.enabled`` both hold, and ``StructureDecisionsStage.run``
    skips every target this predicate rejects. So a dependency mined under
    ``include_deps`` is never structured (issue #346): an LLM error would fail
    that dependency on every pass, and the cost would scale with the number of
    dependencies. tests/extraction/test_decision_capture_gates.py pins this
    predicate against what the stages actually do.

    Example: ``llm_structuring_applies(DecisionCaptureConfig(),
    TargetKind.PROJECT)`` is False — structuring is off by default.
    """
    if target_kind is not TargetKind.PROJECT:
        return False
    return config.enabled and config.llm_structuring.enabled
