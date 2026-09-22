"""SubprocessGitRepository — the bounded ``git`` subprocess adapter (spec §6.2).

Every call is ``git -C <root> -c core.hooksPath=<null device> …`` with a
timeout, ``GIT_OPTIONAL_LOCKS=0`` (no ``index.lock`` writes from status-like
commands), ``GIT_TERMINAL_PROMPT=0`` (never block on a credential prompt), and
the inherited repository-redirecting variables dropped
(:func:`~pydocs_mcp.git.env.git_child_env`); no repository hook ever runs
(spec R8). Failures are translated to :class:`GitCommandError` at this boundary
(spec §6.14 item 7). Read-only except ``fetch`` and ``update_ref_if_unchanged``,
the two sanctioned writes of §6.8b. Tree, blob and grep reads address git
objects, never working-tree files. The patch-id reads chain two processes
through :func:`~pydocs_mcp.git.pipe.run_git_pipe` under the same timeout.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_mcp.git import landing_log
from pydocs_mcp.git.env import git_child_env, git_config_pins
from pydocs_mcp.git.errors import (
    GitCommandError,
    translate_git_start_failures,
    translate_git_timeout,
)
from pydocs_mcp.git.pipe import run_git_pipe
from pydocs_mcp.git.refs import HEADS_PREFIX
from pydocs_mcp.models import FileChangeKind, LandingStep

_DEFAULT_TIMEOUT_SECONDS = 30.0
# ls-remote is the remote lane's cheap change probe (spec §6.8b layer 3): it
# gets its own, shorter bound so a dead remote fails the probe fast. fetch
# keeps ``timeout_seconds`` — it transfers objects, and the spec bounds it so.
_DEFAULT_NETWORK_TIMEOUT_SECONDS = 10.0
_STDERR_TAIL_CHARS = 400
_HEADS_REFS = HEADS_PREFIX.removesuffix("/")
# Spec R8: no git subprocess runs a repository hook. The null device is a
# file, never a directory, so no ``<hooksPath>/<hook>`` exists on any
# platform; without this, fetch and update-ref fire ``reference-transaction``.
_NO_HOOKS = git_config_pins(f"core.hooksPath={os.devnull}")
# fetch otherwise starts ``git maintenance run --auto`` (``gc --auto`` before
# git 2.29), which can detach past the timeout and repack, prune or expire
# reflogs: more than the refs/remotes/* and objects §6.8b sanctions. Config
# keys rather than ``--no-auto-maintenance``, because an older git ignores an
# unknown key but rejects an unknown flag.
_NO_AUTO_MAINTENANCE = git_config_pins("maintenance.auto=false", "gc.auto=0")
# git grep reads the user's config. Pin the raw output shape the port
# documents: project-relative unquoted paths, no column, no color escapes, and
# basic regex unless the caller passes -E / -F / -P.
_GREP_OUTPUT_PINS = git_config_pins(
    "grep.fullName=false",
    "grep.column=false",
    "grep.patternType=basic",
    "core.quotePath=false",
)
# ls-tree modes that carry file content. 120000 (symlink) is a blob holding
# the link's target path; 160000 (gitlink) is a submodule commit.
_REGULAR_FILE_MODES = frozenset({"100644", "100755"})
# Exit 1 is git's "nothing found" answer for every query that allows it here:
# rev-parse --quiet, symbolic-ref --quiet, merge-base (no common ancestor),
# merge-base --is-ancestor (not an ancestor), grep (no match).
_EXIT_NOTHING_FOUND = frozenset({1})
# update-ref reports every refusal (stale old sha, held lock) as fatal 128.
_EXIT_FATAL = frozenset({128})
# 129 is git's usage error: an option this git version does not know.
_EXIT_USAGE = frozenset({129})
# Matching and context flags only: -O runs a pager program, --no-index /
# --untracked / --cached read the working tree or index, -f reads a file.
# (No -m<n>: git rejects it before 2.38.)
_SAFE_GREP_FLAG = re.compile(r"-[iwvFEPlLco]|-[ABC]\d+|--max-depth=\d+")
# A full SHA-1 or SHA-256 object id: the only request read_blobs sends cat-file.
_OBJECT_ID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# Output naming something git is asked about again (paths, refs) decodes with
# surrogateescape: a non-UTF-8 file name round-trips through argv
# (``os.fsencode``) and equals the str ``os.walk`` discovery gives it. Display
# text (grep lines, file contents) decodes with replacement.
_IDENTITY_DECODE = "surrogateescape"
_DISPLAY_DECODE = "replace"
# Porcelain v1 status codes → manifest change kind. Anything else (renames in
# the index, conflicts) reads as MODIFIED: the file's bytes must be re-hashed.
_STATUS_KINDS = {
    "??": FileChangeKind.ADDED,
    " D": FileChangeKind.DELETED,
    "D ": FileChangeKind.DELETED,
}


@dataclass(frozen=True, slots=True)
class SubprocessGitRepository:
    project_root: Path
    binary: str = "git"
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS
    network_timeout_seconds: float = _DEFAULT_NETWORK_TIMEOUT_SECONDS

    def current_branch(self) -> str | None:
        out = self._run(
            "symbolic-ref", "--quiet", "--short", "HEAD", allow_exit=_EXIT_NOTHING_FOUND
        )
        return out.strip() or None

    def head_sha(self, ref: str | None = None) -> str | None:
        revision = "HEAD" if ref is None else ref
        # ``^{commit}`` peels an annotated tag and rejects a tree or blob id.
        args = ("rev-parse", "--verify", "--quiet", f"{revision}^{{commit}}")
        self._refuse_option_like(args, revision)
        out = self._run(*args, allow_exit=_EXIT_NOTHING_FOUND)
        return out.strip() or None

    def index_manifest(self) -> tuple[tuple[str, str], ...]:
        # ``ls-files --stage``: "<mode> <blob> <stage>\t<path>" — git's own stat
        # cache answers without reading file bytes (spec §6.3 step 1).
        out = self._run("ls-files", "--stage", "-z")
        rows = []
        for entry in out.split("\0"):
            if not entry:
                continue
            meta, _, path = entry.partition("\t")
            rows.append((path, meta.split()[1]))
        return tuple(rows)

    def hash_objects(self, paths: Sequence[str]) -> tuple[tuple[str, str], ...]:
        if not paths:
            return ()
        out = self._run("hash-object", "--stdin-paths", stdin="\n".join(paths) + "\n")
        shas = out.split()
        if len(shas) != len(paths):
            raise GitCommandError(
                self._argv("hash-object", "--stdin-paths"),
                f"expected {len(paths)} blob ids, got {len(shas)}",
            )
        return tuple(zip(paths, shas, strict=True))

    def working_tree_changes(self) -> tuple[tuple[str, FileChangeKind], ...]:
        out = self._run("status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
        rows = []
        for entry in out.split("\0"):
            if len(entry) < 4:
                continue
            code, path = entry[:2], entry[3:]
            rows.append((path, _STATUS_KINDS.get(code, FileChangeKind.MODIFIED)))
        return tuple(rows)

    def list_worktrees(self) -> tuple[tuple[str, str | None], ...]:
        out = self._run("worktree", "list", "--porcelain")
        rows: list[tuple[str, str | None]] = []
        path: str | None = None
        for line in [*out.splitlines(), ""]:
            if line.startswith("worktree "):
                path = line[len("worktree ") :]
            elif line.startswith("branch ") and path is not None:
                rows.append((path, line.removeprefix("branch ").removeprefix(HEADS_PREFIX)))
                path = None
            elif line == "" and path is not None:
                rows.append((path, None))  # detached worktree
                path = None
        return tuple(rows)

    # ── P1 part one: branches, trees, blobs, remotes ──

    def symbolic_ref(self, name: str) -> str | None:
        args = ("symbolic-ref", "--quiet", name)
        self._refuse_option_like(args, name)
        out = self._run(*args, allow_exit=_EXIT_NOTHING_FOUND)
        return out.strip() or None

    def list_local_branches(self) -> tuple[tuple[str, str], ...]:
        # The full refname, not ``%(refname:short)``: the short form renders
        # ``heads/main`` as soon as a tag named ``main`` exists.
        out = self._run("for-each-ref", "--format=%(refname)\t%(objectname)", _HEADS_REFS)
        pairs = (_split_tab_pair(line) for line in out.splitlines() if line)
        return tuple((ref.removeprefix(HEADS_PREFIX), sha) for ref, sha in pairs)

    def ls_tree(self, ref: str) -> tuple[tuple[str, str, int], ...]:
        # "<mode> <type> <sha> <size>\t<path>"; ``-l`` gives the blob size so the
        # discovery size cap applies without reading a byte (spec §6.3 step 1).
        # Like ``ls-files``, the listing is scoped to and relative to ``-C <root>``.
        args = ("ls-tree", "-r", "-l", "-z", ref)
        self._refuse_option_like(args, ref)
        entries = (_parse_ls_tree_entry(entry) for entry in self._run(*args).split("\0") if entry)
        return tuple(entry for entry in entries if entry is not None)

    def merge_base(self, a: str, b: str) -> str | None:
        args = ("merge-base", a, b)
        self._refuse_option_like(args, a, b)
        out = self._run(*args, allow_exit=_EXIT_NOTHING_FOUND)
        return out.strip() or None

    def is_ancestor(self, a: str, b: str) -> bool:
        args = ("merge-base", "--is-ancestor", a, b)
        self._refuse_option_like(args, a, b)
        return self._completed(args, allow_exit=_EXIT_NOTHING_FOUND).returncode == 0

    def upstream_of(self, branch: str) -> str | None:
        full_ref = HEADS_PREFIX + branch
        # for-each-ref prefix-matches ``refs/heads/topic`` against
        # ``refs/heads/topic/one``; only the exact ref's row answers.
        out = self._run("for-each-ref", "--format=%(refname)\t%(upstream:short)", full_ref)
        upstreams = dict(_split_tab_pair(line) for line in out.splitlines() if line)
        return upstreams.get(full_ref) or None

    def ahead_behind(self, branch: str, upstream: str) -> tuple[int, int]:
        # ``branch`` is a local branch by contract: the full ref keeps a tag of
        # the same name from answering in its place.
        args = ("rev-list", "--left-right", "--count", f"{HEADS_PREFIX}{branch}...{upstream}")
        self._refuse_option_like(args, branch, upstream)
        ahead, behind = self._run(*args).split()
        return int(ahead), int(behind)

    def ls_remote_heads(self, remote: str) -> tuple[tuple[str, str], ...]:
        args = ("ls-remote", "--heads", remote)
        self._refuse_option_like(args, remote)
        out = self._run(*args, timeout=self.network_timeout_seconds)
        pairs = (_split_tab_pair(line) for line in out.splitlines() if line)
        return tuple(
            (ref.removeprefix(HEADS_PREFIX), sha)
            for sha, ref in pairs
            if ref.startswith(HEADS_PREFIX)
        )

    def fetch(self, remote: str, *, prune: bool = False) -> None:
        atomic = _fetch_args(remote, prune=prune, atomic=True)
        self._refuse_option_like(atomic, remote)
        if self._completed(atomic, allow_exit=_EXIT_USAGE).returncode == 0:
            return
        # ``--atomic`` (a partial failure updates no ref) needs git >= 2.31; an
        # older git rejects it as a usage error before any network I/O, and
        # §6.8b asks for it only "where git supports it".
        self._run(*_fetch_args(remote, prune=prune, atomic=False))

    def update_ref_if_unchanged(self, ref: str, new_sha: str, old_sha: str, message: str) -> bool:
        # ``update-ref <ref> <new> <old>`` is git's compare-and-swap; ``-m``
        # records why in the reflog, so the move is reversible (§6.8b layer 4).
        args = ("update-ref", "-m", message, ref, new_sha, old_sha)
        self._refuse_option_like(args, ref, new_sha, old_sha)
        proc = self._completed(args, allow_exit=_EXIT_FATAL)
        if proc.returncode == 0:
            return True
        if self.head_sha(ref) != old_sha:
            return False  # the ref moved under us: a lost race, not a failure
        reason = f"exit {proc.returncode}"
        raise GitCommandError(self._argv(*args), reason, _stderr_tail(proc.stderr))

    def grep(self, ref: str, pattern: str, flags: Sequence[str], paths: Sequence[str]) -> str:
        # ``-e`` keeps a pattern like ``-x`` a pattern; ``--`` keeps paths paths.
        query = ("-n", "-I", *flags, "-e", pattern, ref, "--", *paths)
        args = (*_GREP_OUTPUT_PINS, "grep", "--no-color", *query)
        self._refuse_option_like(args, ref)
        self._refuse_unsafe_grep_flags(args, flags)
        return self._run(*args, allow_exit=_EXIT_NOTHING_FOUND, decode_errors=_DISPLAY_DECODE)

    def show(self, ref: str, path: str) -> str:
        # ``cat-file blob`` gives the committed bytes untouched (no textconv, no
        # newline translation); ``./`` resolves ``path`` against ``-C <root>``
        # like every other project-relative path, not against the repo top.
        args = ("cat-file", "blob", f"{ref}:./{path}")
        self._refuse_option_like(args, ref)
        return self._run_bytes(*args).decode("utf-8", errors=_DISPLAY_DECODE)

    def read_blobs(self, entries: Sequence[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
        if not entries:
            return ()
        args = ("cat-file", "--batch")
        shas = [sha for sha, _ in entries]
        self._refuse_malformed_object_ids(args, shas)
        raw = self._run_bytes(*args, stdin="".join(f"{sha}\n" for sha in shas).encode("ascii"))
        texts = _split_batch_output(raw, self._argv(*args))
        if len(texts) != len(entries):
            reason = f"expected {len(entries)} objects, got {len(texts)}"
            raise GitCommandError(self._argv(*args), reason)
        return tuple(zip((path for _, path in entries), texts, strict=True))

    # ── P1 part two: patch ids, first-parent landings, upstream-gone, tags ──

    def patch_id(self, base_sha: str, ref: str) -> str:
        producer = landing_log.branch_diff_args(base_sha, ref)
        self._refuse_option_like(producer, base_sha, ref)
        rows = self._patch_id_rows(producer)
        return rows[0][0] if rows else ""  # an empty diff has no id

    def patch_ids_per_commit(self, base_sha: str, ref: str) -> tuple[tuple[str, str], ...]:
        producer = landing_log.per_commit_patch_log_args(base_sha, ref)
        self._refuse_option_like(producer, base_sha, ref)
        return tuple((sha, patch_id) for patch_id, sha in self._patch_id_rows(producer))

    def first_parent_landings(
        self, base_tip: str, *, max_count: int, stop_at: str | None = None
    ) -> tuple[LandingStep, ...]:
        # Two commands over one pinned range, joined by sha (spec §6.2): the
        # ids need the bare ``commit <sha>`` header, the metadata cannot share it.
        walk = self._first_parent_walk(base_tip, max_count, stop_at)
        rows = self._patch_id_rows(landing_log.landing_patch_log_args(walk))
        metadata_args = landing_log.landing_metadata_log_args(walk)
        metadata = self._run(*metadata_args, decode_errors=_DISPLAY_DECODE)
        return landing_log.parse_landing_steps(metadata, {sha: pid for pid, sha in rows})

    def upstream_gone(self, branch: str) -> bool:
        full_ref = HEADS_PREFIX + branch
        # The exact ref's row only, as in ``upstream_of`` (for-each-ref prefix-matches).
        out = self._run("for-each-ref", "--format=%(refname)\t%(upstream:track)", full_ref)
        tracks = dict(_split_tab_pair(line) for line in out.splitlines() if line)
        return tracks.get(full_ref) == landing_log.GONE_UPSTREAM_TRACK

    def tags_on_first_parent(
        self, base_tip: str, pattern: str, max_count: int
    ) -> tuple[tuple[str, str], ...]:
        # for-each-ref, not ``log %D``: decorations follow the user's
        # log.excludeDecoration, which can hide a tag; ``%(*objectname)`` names
        # an annotated tag's commit without parsing decoration text.
        walk = self._first_parent_walk(base_tip, max_count, None)
        first_parent_shas = self._run(*landing_log.first_parent_sha_log_args(walk)).split()
        tags = landing_log.parse_peeled_tags(
            self._run("for-each-ref", landing_log.PEELED_TAG_FORMAT, landing_log.TAGS_REFS)
        )
        return landing_log.select_tags_on_first_parent_shas(first_parent_shas, tags, pattern)

    def _first_parent_walk(
        self, base_tip: str, max_count: int, stop_at: str | None
    ) -> landing_log.FirstParentWalk:
        if max_count < 0:  # ``git log -n -1`` means "no limit": the ceiling would vanish
            reason = f"max_count must be >= 0, got {max_count}"
            raise GitCommandError(self._argv("log", "--first-parent"), reason)
        stop_sha = None if stop_at is None else self._commit_sha(stop_at)
        return landing_log.FirstParentWalk(self._commit_sha(base_tip), max_count, stop_sha)

    def _commit_sha(self, ref: str) -> str:
        sha = self.head_sha(ref)
        if sha is None:
            argv = self._argv("rev-parse", "--verify", f"{ref}^{{commit}}")
            raise GitCommandError(argv, f"unknown revision {ref!r}")
        return sha

    # ── the subprocess boundary ──

    def _argv(self, *args: str) -> tuple[str, ...]:
        return (self.binary, "-C", str(self.project_root), *_NO_HOOKS, *args)

    def _refuse_option_like(self, args: Sequence[str], *values: str) -> None:
        """Keep caller values out of git's option parser (``--upload-pack=<cmd>`` runs a program).

        ``args`` is the command that would have run, reported in the error.
        """
        for value in values:
            if value.startswith("-"):
                reason = f"refused option-like argument {value!r}"
                raise GitCommandError(self._argv(*args), reason)

    def _refuse_unsafe_grep_flags(self, args: Sequence[str], flags: Sequence[str]) -> None:
        for flag in flags:
            if not _SAFE_GREP_FLAG.fullmatch(flag):
                reason = f"refused grep flag {flag!r}: expected a matching or context flag"
                raise GitCommandError(self._argv(*args), reason)

    def _refuse_malformed_object_ids(self, args: Sequence[str], shas: Sequence[str]) -> None:
        for sha in shas:
            if not _OBJECT_ID.fullmatch(sha):
                reason = f"malformed object id {sha!r}: expected 40 or 64 lowercase hex digits"
                raise GitCommandError(self._argv(*args), reason)

    def _run(
        self,
        *args: str,
        stdin: str | None = None,
        allow_exit: frozenset[int] = frozenset(),
        timeout: float | None = None,
        decode_errors: str = _IDENTITY_DECODE,
    ) -> str:
        """Decoded stdout; paths and refs keep their identity unless told otherwise."""
        request = None if stdin is None else stdin.encode("utf-8", _IDENTITY_DECODE)
        proc = self._completed(args, stdin=request, allow_exit=allow_exit, timeout=timeout)
        return proc.stdout.decode("utf-8", decode_errors)

    def _run_bytes(self, *args: str, stdin: bytes | None = None) -> bytes:
        """Undecoded stdout, for readers that parse by byte count or decode leniently."""
        return self._completed(args, stdin=stdin).stdout

    def _completed(
        self,
        args: Sequence[str],
        *,
        stdin: bytes | None = None,
        allow_exit: frozenset[int] = frozenset(),
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """Spawn once and translate a disallowed exit status — the one exit check."""
        argv = self._argv(*args)
        limit = self.timeout_seconds if timeout is None else timeout
        proc = self._spawn(argv, stdin, limit)
        if proc.returncode not in allow_exit:
            _raise_on_failure(argv, proc.returncode, proc.stderr)
        return proc

    def _patch_id_rows(self, producer: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
        """``(patch_id, sha)`` rows of ``git <producer> | git patch-id --stable``, one timeout."""
        first = self._argv(*producer)
        second = self._argv(*landing_log.PATCH_ID_CONSUMER)
        outcome = run_git_pipe(first, second, timeout=self.timeout_seconds)
        # The consumer first: a producer killed by SIGPIPE after the consumer
        # quit is the echo of that failure, not its cause.
        _raise_on_failure(second, outcome.consumer_status, outcome.consumer_stderr)
        _raise_on_failure(first, outcome.producer_status, outcome.producer_stderr)
        return landing_log.parse_patch_id_rows(outcome.stdout.decode("ascii", _DISPLAY_DECODE))

    def _spawn(
        self, argv: tuple[str, ...], stdin: bytes | None, timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        """Run ``argv`` bounded; translate every start/timeout failure at this boundary."""
        env = git_child_env()  # strips GIT_DIR & co — see git/env.py
        # Bytes in and out: ``_run`` decodes per output kind. A locale-codec
        # text mode would raise UnicodeDecodeError past this boundary on the
        # first latin-1 line a grep prints.
        # S603: no shell, and every caller value is refused when option-like
        # (the ``_refuse_*`` guards) or sits behind ``-e`` / ``-m`` / ``--`` / stdin.
        with translate_git_start_failures(argv), translate_git_timeout(argv, timeout):
            return subprocess.run(  # noqa: S603 — see the comment above
                argv, input=stdin, capture_output=True, timeout=timeout, env=env, check=False
            )


def _fetch_args(remote: str, *, prune: bool, atomic: bool) -> tuple[str, ...]:
    """``fetch`` writing refs/remotes/* and objects only (§6.8b).

    No auto-maintenance (see ``_NO_AUTO_MAINTENANCE``) and no submodule
    recursion: submodules are never indexed (``ls_tree`` skips gitlinks), and
    each recursion is another repository written plus another network round
    trip inside the same timeout.
    """
    flags = (*(("--atomic",) if atomic else ()), *(("--prune",) if prune else ()))
    return (*_NO_AUTO_MAINTENANCE, "fetch", "--quiet", "--no-recurse-submodules", *flags, remote)


def _raise_on_failure(argv: tuple[str, ...], status: int, stderr: bytes) -> None:
    if status != 0:
        raise GitCommandError(argv, f"exit {status}", _stderr_tail(stderr))


def _stderr_tail(stderr: bytes) -> str:
    return stderr.decode("utf-8", errors=_DISPLAY_DECODE).strip()[-_STDERR_TAIL_CHARS:]


def _split_tab_pair(line: str) -> tuple[str, str]:
    left, _, right = line.partition("\t")
    return left, right


def _parse_ls_tree_entry(entry: str) -> tuple[str, str, int] | None:
    """One ``<mode> <type> <sha> <size>\\t<path>`` record; ``None`` unless a regular file."""
    meta, _, path = entry.partition("\t")
    mode, _kind, sha, size = meta.split()
    if mode not in _REGULAR_FILE_MODES:
        return None
    return path, sha, int(size)


def _split_batch_output(raw: bytes, argv: tuple[str, ...]) -> list[str]:
    """Parse ``cat-file --batch``: ``<sha> <type> <size>\\n<bytes>\\n`` per object."""
    texts: list[str] = []
    pos = 0
    while pos < len(raw):
        start, size = _batch_header(raw, pos, argv)
        end = start + size
        if raw[end : end + 1] != b"\n":  # git appends one newline after each object
            raise GitCommandError(argv, "truncated cat-file --batch output")
        texts.append(raw[start:end].decode("utf-8", errors=_DISPLAY_DECODE))
        pos = end + 1
    return texts


def _batch_header(raw: bytes, pos: int, argv: tuple[str, ...]) -> tuple[int, int]:
    """``(content_start, size)`` from the ``<sha> <type> <size>`` header at ``pos``."""
    header_end = raw.find(b"\n", pos)
    if header_end < 0:
        raise GitCommandError(argv, "truncated cat-file --batch output")
    fields = raw[pos:header_end].decode("ascii", errors=_DISPLAY_DECODE).split(" ")
    if len(fields) != 3 or not fields[2].isdigit():  # "<name> missing" / "<name> ambiguous"
        raise GitCommandError(argv, f"unreadable object {' '.join(fields)!r}")
    return header_end + 1, int(fields[2])
