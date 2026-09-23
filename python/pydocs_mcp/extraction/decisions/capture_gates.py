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


def llm_structuring_applies(config: DecisionCaptureConfig, target_kind: TargetKind) -> bool:
    """True when the structuring LLM runs for a target of ``target_kind``.

    Structuring sits behind two gates today, and this is their conjunction:
    ``CaptureDecisionsPipeline.run`` runs its sub-stages only for a PROJECT
    target with ``decision_capture.enabled``, and ``_maybe_build_llm_client``
    builds a client only when ``llm_structuring.enabled`` holds too. The
    stages keep their own checks; tests/extraction/test_decision_capture_gates.py
    pins this predicate against what they actually do.

    Example: ``llm_structuring_applies(DecisionCaptureConfig(),
    TargetKind.PROJECT)`` is False — structuring is off by default.
    """
    if target_kind is not TargetKind.PROJECT:
        return False
    return config.enabled and config.llm_structuring.enabled
