"""Command lines and output parsers for the landing and patch-id reads (spec §6.2, §6.8a).

A patch id is a hash of diff TEXT, so two ids compare only when both diffs
were rendered the same way. Squash detection compares a branch's id computed
today with a landing's id cached in ``landing_patch_ids`` months ago, under
whatever git config the user had then. Every patch-id producer therefore pins
the options that change the hashed text (``--no-renames -U3`` as the spec
names, plus prefixes, hunk merging, the diff algorithm, color and external
diff drivers), and the pipe's child drops the environment that reshapes diff
text (``GIT_DIFF_OPTS``, :func:`~pydocs_mcp.git.env.patch_text_child_env`), so
neither the user's config, the launching shell, nor a config change between
the two reads can make equal changes hash differently.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

from pydocs_mcp.git.env import git_config_pins
from pydocs_mcp.models import LandingStep

# Config that reshapes the diff text: noprefix / mnemonicPrefix / srcPrefix /
# dstPrefix rewrite the ---/+++ lines patch-id hashes (the last two exist since
# git 2.45; an older git ignores an unknown key, as for _NO_AUTO_MAINTENANCE),
# interHunkContext merges hunks, the algorithm and heuristic move hunk
# boundaries, relative trims paths, showRoot drops the root commit's diff,
# showSignature runs gpg and prints into the stream.
_PATCH_TEXT_PINS = git_config_pins(
    "diff.noprefix=false",
    "diff.mnemonicPrefix=false",
    "diff.srcPrefix=a/",
    "diff.dstPrefix=b/",
    "diff.relative=false",
    "diff.algorithm=myers",
    "diff.indentHeuristic=true",
    "diff.interHunkContext=0",
    "diff.suppressBlankEmpty=false",
    "diff.submodule=short",
    "core.quotePath=true",
    "log.showRoot=true",
    "log.showSignature=false",
)
# ``color.ui=always`` would bury the diff in escapes patch-id cannot parse (an
# empty id); an external driver or textconv would hash another program's text.
_PATCH_TEXT_FLAGS = ("--no-color", "--no-ext-diff", "--no-textconv", "--no-renames", "-U3")
PATCH_ID_CONSUMER = ("patch-id", "--stable")
# patch-id splits commits on a bare ``commit <sha>`` line only; anything else on
# that line would enter the hash.
_PATCH_ID_COMMIT_HEADER = "--format=commit %H"
# NUL-terminated (``-z``) so no character a subject may hold can split a record.
_LANDING_METADATA_FORMAT = "--format=%H %P%x09%ct%x09%s"
_LOG_PINS = git_config_pins("log.showSignature=false")
TAGS_REFS = "refs/tags"
_TAGS_PREFIX = TAGS_REFS + "/"
# The tag's own object and, for an annotated tag, the object it points at.
PEELED_TAG_FORMAT = "--format=%(refname)%09%(objectname)%09%(*objectname)"
# ``%(upstream:track)`` renders exactly this when the configured upstream ref
# is missing (after a prune fetch); ``git branch -vv`` prints another shape.
GONE_UPSTREAM_TRACK = "[gone]"


@dataclass(frozen=True, slots=True)
class FirstParentWalk:
    """A first-parent range pinned to commit shas: ``stop_sha..tip_sha``, newest ``max_count``.

    Pinned so every command of one read walks the same steps even if a branch
    name moves between them.
    """

    tip_sha: str
    max_count: int
    stop_sha: str | None = None

    def log_args(self) -> tuple[str, ...]:
        target = self.tip_sha if self.stop_sha is None else f"{self.stop_sha}..{self.tip_sha}"
        return ("--first-parent", "-n", str(self.max_count), target, "--")


def branch_diff_args(base_sha: str, ref: str) -> tuple[str, ...]:
    """``git diff base ref`` shaped for ``patch-id`` (the whole-range squash id)."""
    return (*_PATCH_TEXT_PINS, "diff", *_PATCH_TEXT_FLAGS, base_sha, ref, "--")


def per_commit_patch_log_args(base_sha: str, ref: str) -> tuple[str, ...]:
    """``git log -p base..ref`` oldest first, one diff per non-merge commit."""
    return (
        *_PATCH_TEXT_PINS,
        "log",
        "-p",
        "--reverse",
        "--no-merges",
        *_PATCH_TEXT_FLAGS,
        _PATCH_ID_COMMIT_HEADER,
        f"{base_sha}..{ref}",
        "--",
    )


def landing_patch_log_args(walk: FirstParentWalk) -> tuple[str, ...]:
    """``git log -p --first-parent``: each step's ``c^1..c`` diff, merges included.

    Merges only on git >= 2.29, where ``--first-parent`` implies the
    first-parent merge diff; an older git prints none, so its merge steps
    carry ``patch_id == ""``. ``-m`` is not passed: its format follows the
    user's ``log.diffMerges``.
    """
    return (
        *_PATCH_TEXT_PINS,
        "log",
        "-p",
        *_PATCH_TEXT_FLAGS,
        _PATCH_ID_COMMIT_HEADER,
        *walk.log_args(),
    )


def landing_metadata_log_args(walk: FirstParentWalk) -> tuple[str, ...]:
    return (*_LOG_PINS, "log", "-z", _LANDING_METADATA_FORMAT, *walk.log_args())


def first_parent_sha_log_args(walk: FirstParentWalk) -> tuple[str, ...]:
    return (*_LOG_PINS, "log", "--format=%H", *walk.log_args())


def parse_patch_id_rows(output: str) -> tuple[tuple[str, str], ...]:
    """``(patch_id, commit_sha)`` per ``git patch-id`` line, in stream order."""
    return tuple(_split_patch_id_line(line) for line in output.split("\n") if line.strip())


def _split_patch_id_line(line: str) -> tuple[str, str]:
    patch_id, commit_sha = line.split()
    return patch_id, commit_sha


def parse_landing_steps(metadata: str, patch_ids: Mapping[str, str]) -> tuple[LandingStep, ...]:
    """Join ``-z`` metadata records with ``{sha: patch_id}``; an empty diff has no id (``""``)."""
    return tuple(_landing_step(record, patch_ids) for record in metadata.split("\0") if record)


def _landing_step(record: str, patch_ids: Mapping[str, str]) -> LandingStep:
    shas, landed_at, subject = record.split("\t", 2)
    sha, *parents = shas.split()
    return LandingStep(
        sha=sha,
        parent_shas=tuple(parents),
        landed_at=float(landed_at),
        subject=subject,
        patch_id=patch_ids.get(sha, ""),
    )


def parse_peeled_tags(output: str) -> tuple[tuple[str, str], ...]:
    """``(tag, sha)`` per ``for-each-ref`` row, an annotated tag peeled to its target."""
    return tuple(_peeled_tag(line) for line in output.split("\n") if line)


def _peeled_tag(line: str) -> tuple[str, str]:
    refname, own_object, peeled = line.split("\t")
    return refname.removeprefix(_TAGS_PREFIX), peeled or own_object


def select_tags_on_first_parent_shas(
    first_parent_shas: Sequence[str], tags: Iterable[tuple[str, str]], pattern: str
) -> tuple[tuple[str, str], ...]:
    """``(tag, sha)`` newest step first for tags matching ``pattern`` on ``first_parent_shas``.

    ``fnmatchcase``: the same answer on every platform (``fnmatch`` folds case
    on Windows). Several tags on one step come in name order.
    """
    by_sha: dict[str, list[str]] = {}
    for tag, sha in tags:
        if fnmatchcase(tag, pattern):
            by_sha.setdefault(sha, []).append(tag)
    return tuple((tag, sha) for sha in first_parent_shas for tag in sorted(by_sha.get(sha, ())))


__all__ = (
    "GONE_UPSTREAM_TRACK",
    "PATCH_ID_CONSUMER",
    "PEELED_TAG_FORMAT",
    "TAGS_REFS",
    "FirstParentWalk",
    "branch_diff_args",
    "first_parent_sha_log_args",
    "landing_metadata_log_args",
    "landing_patch_log_args",
    "parse_landing_steps",
    "parse_patch_id_rows",
    "parse_peeled_tags",
    "per_commit_patch_log_args",
    "select_tags_on_first_parent_shas",
)
