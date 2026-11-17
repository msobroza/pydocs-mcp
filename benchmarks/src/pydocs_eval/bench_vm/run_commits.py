"""Resolve and freeze a run's commits, and check what the run reads exists at them — with real git.

All of it runs on the laptop, offline: refs resolve against the local
repository and reachability is checked against the remote-tracking branches it
already has (``git fetch`` first if they are stale). A commit no
remote-tracking branch contains is refused, because the VM's image is built
from what the remote holds. A before/after baseline or candidate must be on a
remote-tracking branch of the product repository itself (a remote whose URL
normalises to ``PRODUCT_REPO_URL``), because that is what the VM clones to
check them out; a fork's branch is not enough. Files are read at the commit,
never from the working tree, so an uncommitted edit can never reach a VM.

A ref naming a local branch must also be in step with its remote-tracking
counterpart (its upstream while that exists, else every remote's branch of the
same name), at one commit: a first launch freezes the commit for every attempt,
so a local ``main`` behind ``origin/main`` would freeze an old one for good.
Behind, ahead or diverged, it is refused; a SHA, a tag or a remote-tracking
name (``origin/main``) is taken as written, and a relaunch never resolves the
refs again.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.bench_vm.commit_layout import (
    HOST_SHUTDOWN_SCRIPT,
    HOST_STARTUP_SCRIPT,
    recipe_paths,
)
from pydocs_eval.bench_vm.launch_refusal import LaunchRefused
from pydocs_eval.bench_vm.product_repo import (
    PRODUCT_REPO_URL,
    is_product_repo_url,
    normalised_repo_url,
)
from pydocs_eval.bench_vm.run_kinds import RunKind
from pydocs_eval.bench_vm.run_spec import RunSpec
from pydocs_eval.bench_vm.vm_request import ImageFlavour

_GIT_TIMEOUT_S = 60.0
# A whole-tree archive of the repository takes longer than a single lookup.
_GIT_ARCHIVE_TIMEOUT_S = 300.0
_BLOB = "blob"
_TREE = "tree"
_LOCAL_BRANCH_PREFIX = "refs/heads/"
_REMOTE_TRACKING_PREFIX = "refs/remotes/"


@dataclass(frozen=True, slots=True)
class RemoteTrackingBranch:
    """A remote-tracking branch, by full name (``refs/remotes/origin/main``), and its commit."""

    name: str
    commit: str

    @property
    def short_name(self) -> str:
        """``origin/main``: the name a run spec can write as a ref."""
        return self.name.removeprefix(_REMOTE_TRACKING_PREFIX)


@dataclass(frozen=True, slots=True)
class GitCheckout:
    """A local git repository the launcher reads, read-only."""

    root: Path

    def resolve(self, ref: str) -> str | None:
        """The commit ``ref`` names, ``None`` when it names none."""
        if ref.startswith("-"):
            return None
        completed = self._git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    def local_branch_named_by(self, ref: str) -> str | None:
        """The local branch ``ref`` resolves through, ``None`` when it names no local branch.

        ``main``, ``heads/main`` and a ``HEAD`` attached to ``main`` all name
        ``main``. A SHA, a tag, a remote-tracking branch (``origin/main``), an
        expression (``main~1``) and an ambiguous name name none.
        """
        if ref.startswith("-"):
            return None
        completed = self._git("rev-parse", "--symbolic-full-name", ref)
        name = completed.stdout.strip()
        if completed.returncode != 0 or not name.startswith(_LOCAL_BRANCH_PREFIX):
            return None
        return name.removeprefix(_LOCAL_BRANCH_PREFIX)

    def remote_tracking_counterparts(self, branch: str) -> tuple[RemoteTrackingBranch, ...]:
        """The remote-tracking branches local ``branch`` pairs with.

        Its configured upstream while that remote-tracking branch exists;
        otherwise (no upstream, a local one, or one pruned since, as after the
        remote renamed the branch) ``refs/remotes/<remote>/<branch>`` of every
        remote that has one. Never contacts a remote: these are the refs the
        last ``git fetch`` left.
        """
        upstream = self._existing_remote_tracking((self._upstream_of(branch),))
        if upstream:
            return upstream
        names = (f"{_REMOTE_TRACKING_PREFIX}{remote}/{branch}" for remote in self._remote_names())
        return self._existing_remote_tracking(tuple(names))

    def _upstream_of(self, branch: str) -> str:
        """``branch``'s configured upstream, by full name; empty when it has none."""
        listed = self._git(
            "for-each-ref", "--format=%(upstream)", f"{_LOCAL_BRANCH_PREFIX}{branch}"
        )
        return listed.stdout.strip()

    def _existing_remote_tracking(self, names: tuple[str, ...]) -> tuple[RemoteTrackingBranch, ...]:
        """Those of ``names`` that are remote-tracking branches this repository holds."""
        wanted = tuple(name for name in names if name.startswith(_REMOTE_TRACKING_PREFIX))
        if not wanted:
            return ()
        listed = self._git("for-each-ref", "--format=%(refname) %(objectname)", *wanted)
        found = (line.partition(" ") for line in listed.stdout.splitlines())
        # A pattern also matches refs below it (`origin/main/x`): keep exact names only.
        return tuple(
            RemoteTrackingBranch(name=name, commit=commit)
            for name, _, commit in found
            if name in wanted
        )

    def _remote_names(self) -> list[str]:
        return self._git("remote").stdout.split()

    def commits_ahead_behind(self, local: str, remote: str) -> tuple[int, int]:
        """Commits ``local`` has that ``remote`` lacks, and commits ``remote`` has that ``local`` lacks."""
        completed = self._git("rev-list", "--left-right", "--count", f"{local}...{remote}")
        counts = completed.stdout.split()
        if completed.returncode != 0 or len(counts) != 2:
            raise RuntimeError(
                f"git rev-list --left-right --count {local}...{remote}: got exit "
                f"{completed.returncode} ({completed.stderr.strip()}), expected two counts"
            )
        return int(counts[0]), int(counts[1])

    def remote_tracking_refs_containing(self, commit: str) -> tuple[str, ...]:
        """Full names (``refs/remotes/<remote>/<branch>``) of the remote-tracking branches holding ``commit``."""
        completed = self._git(
            "for-each-ref", "--contains", commit, "--format=%(refname)", _REMOTE_TRACKING_PREFIX
        )
        return tuple(line for line in completed.stdout.splitlines() if line.strip())

    def remote_fetch_urls(self) -> dict[str, str]:
        """Each remote's fetch URL, by remote name, as git resolves it.

        That is the remote's first URL after any ``url.<base>.insteadOf``
        rewrite (``git ls-remote --get-url`` never contacts the remote): the
        repository its remote-tracking branches are fetched from.
        """
        return {
            name: self._git("ls-remote", "--get-url", name).stdout.strip()
            for name in self._remote_names()
        }

    def object_type_at(self, commit: str, path: str) -> str | None:
        """``blob`` or ``tree`` for ``path`` at ``commit``; ``None`` when it is absent."""
        completed = self._git("cat-file", "-t", f"{commit}:{path}")
        return completed.stdout.strip() if completed.returncode == 0 else None

    def read_file_at(self, commit: str, path: str) -> str:
        completed = self._git("cat-file", "blob", f"{commit}:{path}")
        if completed.returncode != 0:
            raise LaunchRefused(
                f"{path}: got nothing at commit {commit}, expected a file the commit carries"
            )
        return completed.stdout

    def archive_to(self, commit: str, output: Path) -> None:
        """Write ``git archive --format=tar`` of ``commit`` (it embeds the commit id) to ``output``."""
        completed = self._git(
            "archive",
            "--format=tar",
            f"--output={output}",
            commit,
            timeout_s=_GIT_ARCHIVE_TIMEOUT_S,
        )
        if completed.returncode != 0:
            raise LaunchRefused(
                f"git archive of commit {commit}: got exit {completed.returncode} "
                f"({completed.stderr.strip()}), expected a commit the local repository holds"
            )

    def _git(
        self, *args: str, timeout_s: float = _GIT_TIMEOUT_S
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # fixed git argv, never a shell
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )


@dataclass(frozen=True, slots=True)
class FrozenCommits:
    """The commits a first launch resolved; every later attempt reuses them."""

    run: str
    baseline: str | None = None
    candidate: str | None = None


@dataclass(frozen=True, slots=True)
class HostScripts:
    """The run commit's startup and shutdown scripts, as the VM metadata carries them."""

    startup: str
    shutdown: str


def freeze_commits(git: GitCheckout, spec: RunSpec) -> FrozenCommits:
    """Resolve ``git_ref`` (and the before/after refs); each must be on a remote-tracking branch.

    A ref naming a local branch must be in step with its remote-tracking
    branch. A before/after baseline and candidate must be on one of the
    product repository's: the VM clones it to check them out.
    """
    product_urls = git.remote_fetch_urls() if spec.kind is RunKind.BEFORE_AFTER else None
    return FrozenCommits(
        run=_pushed_commit(git, "git_ref", spec.git_ref),
        baseline=_optional_product_commit(git, "baseline_ref", spec.baseline_ref, product_urls),
        candidate=_optional_product_commit(git, "candidate_ref", spec.candidate_ref, product_urls),
    )


def _optional_product_commit(
    git: GitCheckout, field: str, ref: str | None, remote_urls: Mapping[str, str] | None
) -> str | None:
    """``ref``'s commit; on a product remote's branch when ``remote_urls`` is given, else on any."""
    if ref is None:
        return None
    if remote_urls is None:
        return _pushed_commit(git, field, ref)
    commit = _commit_to_freeze(git, field, ref)
    _require_on_product_remote(git, remote_urls, f"{field}: got {ref!r} at commit {commit}", commit)
    return commit


def _commit_to_freeze(git: GitCheckout, field: str, ref: str) -> str:
    """``ref``'s commit; a ref naming a local branch must be in step with its remote-tracking one."""
    commit = git.resolve(ref)
    if commit is None:
        raise LaunchRefused(
            f"{field}: got {ref!r}, expected a commit SHA, tag or branch that resolves in {git.root}"
        )
    _require_branch_in_step(git, field, ref, commit)
    return commit


@dataclass(frozen=True, slots=True)
class _LocalBranch:
    """A local branch (``main``) and the commit it is at."""

    name: str
    commit: str


def _require_branch_in_step(git: GitCheckout, field: str, ref: str, commit: str) -> None:
    """Refuse a ref naming a local branch whose remote-tracking counterpart is elsewhere (#434).

    The launch never fetches, so a local ``main`` left behind ``origin/main``
    would freeze an old commit into the run record, for every attempt.
    """
    branch = git.local_branch_named_by(ref)
    if branch is None:
        return
    counterparts = git.remote_tracking_counterparts(branch)
    out_of_step = [counterpart for counterpart in counterparts if counterpart.commit != commit]
    if out_of_step:
        local = _LocalBranch(name=branch, commit=commit)
        got = f"{field}: got {ref!r}"
        raise _out_of_step_refusal(git, got, local, out_of_step[0], several=len(counterparts) > 1)


def _out_of_step_refusal(
    git: GitCheckout,
    got: str,
    local: _LocalBranch,
    counterpart: RemoteTrackingBranch,
    *,
    several: bool,
) -> LaunchRefused:
    """The refusal: both commits, how they stand, the fix; how to pick one of ``several``."""
    ahead, behind = git.commits_ahead_behind(local.commit, counterpart.commit)
    short = counterpart.short_name
    relation, fix = _out_of_step_wording(local.name, short, ahead, behind)
    pick = _pick_an_upstream_hint(local.name) if several else ""
    return LaunchRefused(
        f"{got}, local branch {local.name} at commit {local.commit}, {relation} its "
        f"remote-tracking branch {short} at commit {counterpart.commit}; expected the two at "
        f"one commit: {fix}, or write {short} or a commit SHA instead{pick}"
    )


def _out_of_step_wording(branch: str, counterpart: str, ahead: int, behind: int) -> tuple[str, str]:
    """How local ``branch`` stands against ``counterpart``, and how to bring it in step."""
    if not ahead:
        return (
            f"{_commit_count_text(behind)} behind",
            f"fast-forward {branch} to {counterpart} (`git pull --ff-only` with it checked out)",
        )
    if not behind:
        return f"{_commit_count_text(ahead)} ahead of", f"push {branch} to {counterpart}"
    # No pull-then-push advice: after a squash merge it would push a merge commit.
    return (
        f"diverged ({_commit_count_text(ahead)} ahead, {behind} behind) from",
        f"bring {branch} in step with {counterpart}",
    )


def _pick_an_upstream_hint(branch: str) -> str:
    """Several remotes hold a ``branch`` and none is its upstream: say how to name one."""
    return (
        f"; {branch} has no upstream, so every remote's {branch} must agree: "
        f"`git branch --set-upstream-to=<remote>/{branch} {branch}` names the one it follows"
    )


def _commit_count_text(count: int) -> str:
    return f"{count} commit" if count == 1 else f"{count} commits"


def _pushed_commit(git: GitCheckout, field: str, ref: str) -> str:
    commit = _commit_to_freeze(git, field, ref)
    if not git.remote_tracking_refs_containing(commit):
        raise LaunchRefused(
            f"{field}: got {ref!r} at commit {commit}, expected a commit reachable from a "
            "remote-tracking branch; push it (or `git fetch`) first"
        )
    return commit


def _require_on_product_remote(
    git: GitCheckout, remote_urls: Mapping[str, str], got: str, commit: str
) -> None:
    """Refuse ``commit`` unless a remote-tracking branch of a product remote holds it (#424, #430).

    The VM clones ``PRODUCT_REPO_URL``, so a commit only a fork holds would
    pass every other laptop check and fail on the VM after the spend.
    """
    product = {name for name, url in remote_urls.items() if is_product_repo_url(url)}
    holders = {
        _owning_remote(ref, remote_urls) for ref in git.remote_tracking_refs_containing(commit)
    }
    if product & holders:
        return
    # The normalised URL drops any credential a remote URL embeds.
    checked = ", ".join(
        f"{name} ({normalised_repo_url(url)})" for name, url in sorted(remote_urls.items())
    )
    raise LaunchRefused(
        f"{got}, expected a commit reachable from a remote-tracking branch of "
        f"{PRODUCT_REPO_URL}, the repository the before/after VM clones; remotes checked: "
        f"{checked or 'none'}; push the commit there (or `git fetch` that remote) first"
    )


def _owning_remote(ref: str, remote_names: Iterable[str]) -> str | None:
    """The remote a ``refs/remotes/...`` ref belongs to: the longest name that prefixes it.

    Remote names may hold slashes, so ``refs/remotes/origin/fork/x`` is
    ``origin/fork``'s when that remote exists, never ``origin``'s.
    """
    owners = [name for name in remote_names if ref.startswith(f"refs/remotes/{name}/")]
    return max(owners, key=len, default=None)


def require_recorded_commits(git: GitCheckout, commits: FrozenCommits, kind: RunKind) -> None:
    """A relaunch reuses the recorded commits; each must exist in the local repository.

    Reachability was checked when the first launch froze them, and a moved
    branch does not change what they are, so only their presence is checked;
    a before/after baseline and candidate must still be on a product remote,
    which also catches a record frozen before that rule existed.
    """
    for field, commit in (
        ("commit", commits.run),
        ("baseline_commit", commits.baseline),
        ("candidate_commit", commits.candidate),
    ):
        if commit is not None and git.resolve(commit) != commit:
            raise LaunchRefused(
                f"run record {field}: got {commit}, expected a commit the local repository "
                f"{git.root} holds; `git fetch` it first"
            )
    if kind is RunKind.BEFORE_AFTER:
        _require_recorded_on_product_remote(git, commits)


def _require_recorded_on_product_remote(git: GitCheckout, commits: FrozenCommits) -> None:
    remote_urls = git.remote_fetch_urls()
    for field, commit in (
        ("baseline_commit", commits.baseline),
        ("candidate_commit", commits.candidate),
    ):
        if commit is not None:
            _require_on_product_remote(
                git, remote_urls, f"run record {field}: got {commit}", commit
            )


def check_run_inputs_at_commit(git: GitCheckout, spec: RunSpec, commit: str) -> None:
    """Every config must be a file and ``corpus_dir`` a directory at the run commit."""
    for config in spec.configs:
        _require_path(git, commit, "configs", config, _BLOB, "a file")
    if spec.corpus_dir is not None:
        _require_path(git, commit, "corpus_dir", spec.corpus_dir, _TREE, "a directory")


def _require_path(
    git: GitCheckout, commit: str, field: str, path: str, object_type: str, shape: str
) -> None:
    found = git.object_type_at(commit, path)
    if found != object_type:
        what = "nothing" if found is None else f"a {found}"
        raise LaunchRefused(
            f"{field}: got {path!r}, expected {shape} at commit {commit}, found {what} there"
        )


def check_recipe_at_commit(git: GitCheckout, flavour: ImageFlavour, commit: str) -> None:
    """The run commit must carry the bench VM image recipe for ``flavour``."""
    missing = [path for path in recipe_paths(flavour) if git.object_type_at(commit, path) != _BLOB]
    if missing:
        raise LaunchRefused(
            f"git_ref: commit {commit} has no bench VM recipe (missing {', '.join(missing)}), "
            "expected a commit that carries the bench VM tooling; to measure an older product "
            "commit, use kind before_after from a current commit"
        )


def read_host_scripts(git: GitCheckout, commit: str) -> HostScripts:
    """The startup and shutdown scripts at the run commit, never the working tree's."""
    return HostScripts(
        startup=git.read_file_at(commit, HOST_STARTUP_SCRIPT),
        shutdown=git.read_file_at(commit, HOST_SHUTDOWN_SCRIPT),
    )
