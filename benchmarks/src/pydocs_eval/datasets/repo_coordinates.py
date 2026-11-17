"""The ``repo`` + ``commit`` task metadata a before/after plan groups corpora by.

A task carrying both keys searches its own per-corpus workspace
(``campaign.before_after_corpora.plan_task_workspaces``); a task carrying
neither searches the shared ``--workspace``. The workspace slug needs ``repo``
spelled ``owner/name``, so a dataset that only holds a clone URL derives it here
— one spelling for every loader, never a per-dataset copy that drifts.

Example:
    >>> corpus_coordinates("https://github.com/psf/black.git", "a" * 40)["repo"]
    'psf/black'
"""

from __future__ import annotations

#: The metadata keys ``plan_task_workspaces`` reads (``before_after_corpora``).
REPO_METADATA_KEY = "repo"
COMMIT_METADATA_KEY = "commit"


def owner_name_of_repo_url(url: str) -> str:
    """``owner/name`` from a repository URL; ``.git`` and trailing slashes are dropped.

    The trailing slash goes before the ``.git`` suffix (the crosscommitvuln
    rule), so ``.../o/n.git/`` and ``.../o/n`` name the same repository.

    Raises:
        ValueError: ``url`` has no ``<owner>/<name>`` tail, naming the value.

    Example:
        >>> owner_name_of_repo_url("https://github.com/psf/black/")
        'psf/black'
    """
    parts = url.rstrip("/").removesuffix(".git").split("/")
    if len(parts) < 2 or not all(parts[-2:]):
        raise ValueError(f"repo url = {url!r}, expected .../<owner>/<name>")
    return f"{parts[-2]}/{parts[-1]}"


def corpus_coordinates(url: str, commit: str) -> dict[str, str]:
    """The ``repo`` + ``commit`` metadata naming the corpus checked out of ``url`` at ``commit``.

    Raises:
        ValueError: ``url`` has no ``<owner>/<name>`` tail.
    """
    return {REPO_METADATA_KEY: owner_name_of_repo_url(url), COMMIT_METADATA_KEY: commit}


__all__ = [
    "COMMIT_METADATA_KEY",
    "REPO_METADATA_KEY",
    "corpus_coordinates",
    "owner_name_of_repo_url",
]
