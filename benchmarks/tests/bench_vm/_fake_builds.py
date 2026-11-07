"""Image builds in tests: what a build the fake received carried, and the digest a push gets."""

from __future__ import annotations

import hashlib
import tarfile
from dataclasses import dataclass

from pydocs_eval.bench_vm.image_build_request import COMMIT_ARCHIVE_NAME, ImageBuildRequest


def fake_built_digest(image: str, *, builder: str = "this launch") -> str:
    """The digest the fake registry gives ``image`` when ``builder`` pushes it."""
    return "sha256:" + hashlib.sha256(f"{builder}:{image}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class FakeBuild:
    """One build the fake received, with its source read while it still existed.

    ``archive_commit`` is the commit id ``git archive`` embeds in the tar's pax
    header (what ``git get-tar-commit-id`` prints); ``config_text`` is the
    Cloud Build config the build was submitted with.
    """

    request: ImageBuildRequest
    source_files: tuple[str, ...]
    archive_commit: str | None
    archive_members: frozenset[str]
    config_text: str


def read_build_source(request: ImageBuildRequest) -> FakeBuild:
    source_files = tuple(sorted(path.name for path in request.source_dir.iterdir()))
    with tarfile.open(request.source_dir / COMMIT_ARCHIVE_NAME) as archive:
        commit = archive.pax_headers.get("comment")
        members = frozenset(member.name for member in archive.getmembers() if member.isfile())
    return FakeBuild(
        request=request,
        source_files=source_files,
        archive_commit=commit,
        archive_members=members,
        config_text=request.config.read_text(encoding="utf-8"),
    )
