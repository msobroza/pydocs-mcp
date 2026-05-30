"""Runner + system wiring for the ``IndexesDependencies`` opt-in toggle.

The runner sets ``system.index_dependencies`` once per sweep from
``corpus_dir is not None``: reference-project datasets (DS-1000, ``--corpus-dir``
set) index the corpus's declared deps; per-task repo datasets (RepoQA,
``corpus_dir is None``) index repo-source-only. ``PydocsMcpSystem`` opts into the
``IndexesDependencies`` Protocol; systems that don't are an isinstance-gated
no-op.

Hermetic: ``_maybe_set_index_dependencies`` is a pure isinstance dispatch and
``PydocsMcpSystem`` construction defers every ``pydocs_mcp`` import, so this
test needs no ``pydocs_mcp`` install.
"""

from __future__ import annotations

from benchmarks.eval.runner import _maybe_set_index_dependencies
from benchmarks.eval.systems.base_system import IndexesDependencies
from benchmarks.eval.systems.pydocs import PydocsMcpSystem


def test_pydocs_system_opts_into_protocol_and_defaults_true() -> None:
    system = PydocsMcpSystem()
    assert isinstance(system, IndexesDependencies)
    assert system.index_dependencies is True


def test_maybe_set_toggles_index_dependencies_false() -> None:
    system = PydocsMcpSystem()
    _maybe_set_index_dependencies(system, False)
    assert system.index_dependencies is False


def test_maybe_set_toggles_index_dependencies_true() -> None:
    system = PydocsMcpSystem()
    system.index_dependencies = False
    _maybe_set_index_dependencies(system, True)
    assert system.index_dependencies is True


def test_maybe_set_is_noop_for_non_opt_in_system() -> None:
    """A plain object without the attr is a strict isinstance-gated no-op."""

    class PlainSystem:
        name = "plain"

    system = PlainSystem()
    # Must not crash and must not graft the attribute on.
    _maybe_set_index_dependencies(system, False)
    assert not isinstance(system, IndexesDependencies)
    assert not hasattr(system, "index_dependencies")
