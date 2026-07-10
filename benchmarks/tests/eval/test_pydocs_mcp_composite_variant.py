"""Pin the ``pydocs-mcp-composite`` system variant.

WHY a registered variant (not a runner kwarg): the runner builds systems
via ``system_registry.build(name)`` with NO kwargs, so the only way to
reach the comparison run with ``composite_mode=True`` is a registered
subclass that defaults the flag on. This variant's ``search()`` prefers
the budgeted 1-item composite (``state.result``) so pydocs emits one blob
like Context7/Neuledge for a fair cross-system recall@1.
"""

from __future__ import annotations

# WHY: importing the package fires the ``@system_registry.register``
# decorators so the variant is present in ``names()``.
import pydocs_eval.systems
from pydocs_eval.serialization import system_registry
from pydocs_eval.systems.pydocs import (
    PydocsMcpCompositeSystem,
    PydocsMcpSystem,
)


def test_composite_variant_is_registered() -> None:
    assert "pydocs-mcp-composite" in system_registry.names()


def test_composite_variant_builds_with_composite_mode_on() -> None:
    system = system_registry.build("pydocs-mcp-composite")
    assert system.composite_mode is True
    assert system.name == "pydocs-mcp-composite"


def test_composite_variant_subclasses_pydocs_mcp() -> None:
    """The variant reuses the parent's index/search/teardown wholesale —
    only the ``composite_mode`` + ``name`` defaults differ. Subclassing (not
    copy-paste) keeps the adapter logic in one place.
    """
    assert issubclass(PydocsMcpCompositeSystem, PydocsMcpSystem)


def test_base_pydocs_mcp_defaults_composite_off() -> None:
    """REGRESSION guard: the plain ``pydocs-mcp`` system must keep
    ``composite_mode=False`` (the recall@k-friendly N-item behavior RepoQA
    and the pydocs-only DS-1000 run rely on). The variant must not leak its
    override onto the parent default.
    """
    base = system_registry.build("pydocs-mcp")
    assert base.composite_mode is False
