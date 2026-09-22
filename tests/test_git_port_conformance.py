"""Every GitRepository method exists on all three conformers (spec §6.2).

``runtime_checkable`` isinstance only checks attribute presence, and only for
the conformer a test happens to build; this pins the whole surface at once so a
port method added to the Protocol without its subprocess, Null and fake
implementation fails here rather than in a later task's integration test. The
fake's own semantics (what the application-layer tests rely on) are pinned
below.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import pytest

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.git.null_repository import NullGitRepository
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from tests._fakes import FakeGitRepository

_P1_PART_ONE = {
    "symbolic_ref",
    "list_local_branches",
    "ls_tree",
    "merge_base",
    "is_ancestor",
    "upstream_of",
    "ahead_behind",
    "ls_remote_heads",
    "fetch",
    "update_ref_if_unchanged",
    "grep",
    "show",
    "read_blobs",
}


def _parameter_shape(function: Callable[..., object]) -> list[tuple[str, object, object]]:
    """Name, kind (positional / keyword-only) and default of every parameter."""
    parameters = inspect.signature(function).parameters.values()
    return [(p.name, p.kind, p.default) for p in parameters]


def _port_methods() -> set[str]:
    return {
        name
        for name, member in vars(GitRepository).items()
        if not name.startswith("_") and inspect.isfunction(member)
    }


def test_the_protocol_carries_the_p1_part_one_methods() -> None:
    assert _P1_PART_ONE.issubset(_port_methods())
    assert list(inspect.signature(GitRepository.head_sha).parameters) == ["self", "ref"]


@pytest.mark.parametrize(
    "conformer", [SubprocessGitRepository, NullGitRepository, FakeGitRepository]
)
def test_every_port_method_is_implemented_with_the_same_parameters(conformer: type) -> None:
    for name in sorted(_port_methods()):
        implemented = getattr(conformer, name, None)
        assert implemented is not None, f"{conformer.__name__} lacks {name}"
        expected = _parameter_shape(getattr(GitRepository, name))
        assert _parameter_shape(implemented) == expected, name


def _fake() -> FakeGitRepository:
    return FakeGitRepository(
        head="h" * 40,
        refs={"refs/heads/main": "m" * 40, "refs/heads/x": "x" * 40, "origin/main": "o" * 40},
        trees={"main": (("a.py", "b1", 6),)},
        blobs={"b1": "a = 1\n"},
    )


def test_fake_resolves_refs_like_the_adapter() -> None:
    git = _fake()
    assert git.head_sha() == "h" * 40
    assert git.head_sha("main") == "m" * 40
    assert git.head_sha("origin/main") == "o" * 40
    assert git.head_sha("gone") is None
    assert dict(git.list_local_branches()) == {"main": "m" * 40, "x": "x" * 40}


def test_fake_reads_trees_and_blobs_and_raises_on_a_missing_path() -> None:
    git = _fake()
    assert git.ls_tree("main") == (("a.py", "b1", 6),)
    assert git.show("main", "a.py") == "a = 1\n"
    assert git.read_blobs([("b1", "a.py")]) == (("a.py", "a = 1\n"),)
    with pytest.raises(GitCommandError):
        git.show("main", "missing.py")


def test_fake_raises_git_command_error_for_an_unknown_blob_like_the_adapter() -> None:
    git = _fake()
    git.trees["main"] = (("a.py", "b1", 6), ("gone.py", "no-such-blob", 3))
    with pytest.raises(GitCommandError, match="missing"):
        git.read_blobs([("no-such-blob", "gone.py")])
    with pytest.raises(GitCommandError, match="missing"):
        git.show("main", "gone.py")


def test_fake_compare_and_swap_records_only_successful_updates() -> None:
    git = _fake()
    assert git.update_ref_if_unchanged("refs/heads/x", "n" * 40, "stale", "ff") is False
    assert git.update_ref_if_unchanged("refs/heads/x", "n" * 40, "x" * 40, "ff") is True
    assert git.head_sha("x") == "n" * 40
    assert git.updated_refs == [("refs/heads/x", "n" * 40, "x" * 40, "ff")]


def test_fake_records_fetches_and_fails_every_call_when_asked() -> None:
    git = _fake()
    git.fetch("origin", prune=True)
    assert git.fetch_calls == [("origin", True)]
    git.fail = True
    with pytest.raises(GitCommandError):
        git.ls_tree("main")
    with pytest.raises(GitCommandError):
        git.update_ref_if_unchanged("refs/heads/x", "n" * 40, "x" * 40, "ff")
