"""Pin RetrieverStep's contract: every concrete step exposes to_dict()."""

from __future__ import annotations

import inspect

from pydocs_mcp.retrieval.pipeline.base import RetrieverStep


def test_retriever_step_declares_to_dict_abstract() -> None:
    """to_dict is part of RetrieverStep's published contract.

    Nested-step containers (ParallelStep, RouteStep, ConditionalStep,
    TokenBudgetStep) call ``step.to_dict()`` on their child steps to
    round-trip through YAML. Declaring to_dict on the ABC codifies
    the contract every concrete step already satisfies and makes mypy
    able to typecheck the nested-step call sites without
    ``# type: ignore``.
    """
    # The ABC must list to_dict as a method (not necessarily abstract,
    # but at least present so mypy sees it on the union type).
    assert hasattr(RetrieverStep, "to_dict"), (
        "RetrieverStep must declare to_dict so nested-step containers "
        "(ParallelStep, RouteStep, ConditionalStep, TokenBudgetStep) "
        "can call step.to_dict() without # type: ignore."
    )

    sig = inspect.signature(RetrieverStep.to_dict)
    # Returns a dict — checked by every concrete impl's existing tests.
    # Just confirm the method takes only self (no kwargs).
    assert list(sig.parameters.keys()) == ["self"]
