"""Typed ``in:<project>`` / ``on:<branch>`` tokens inside a question (UI spec §6.10a).

Pure by contract — no streamlit, no langchain (a subprocess pin holds it). The page
hands the typed text in and gets back the cells to pin (a one-shot PIN), the text to
send, and — when a name is unknown — the refusal that stops the send.

Example:
    parsed = parse_scope_tokens(
        "why? in:backend", listing, caps, (), tokens_enabled=True, max_cells=4
    )
    parsed.cells == (ScopeCell("backend", "main"),) and parsed.stripped_text == "why?"
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import ScopeCell, listing_cell
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities

# The single source of both literals (R13); nothing else in the harness spells them.
# The S105 suppressions below: flake8-bandit reads "TOKEN" in the name as a secret.
# These are grammar prefixes a user types into a question, and a test pins both.
PROJECT_TOKEN_PREFIX = "in:"  # noqa: S105
BRANCH_TOKEN_PREFIX = "on:"  # noqa: S105
BRANCHES_NOT_CHOOSABLE = "Branches can't be chosen yet: this server indexes one branch per project."
_NOTHING_SENT = "Nothing was sent."
# Punctuation a person glues to the last word of a sentence ("… in:backend?"); it is
# never part of a project or branch name, so it never reaches the lookup (spec §6.10a).
_GLUED_PUNCTUATION = "?.,;:!)"
# Splitting on a CAPTURED whitespace run keeps the separators, so stripping a token
# can drop its own spacing and leave the question's newlines and runs intact.
_WHITESPACE_RUN = re.compile(r"(\s+)")


@dataclass(frozen=True, slots=True)
class ParsedScopeTokens:
    """``cells`` empty = no token in the text; ``refusal`` non-empty = do not send."""

    cells: tuple[ScopeCell, ...]
    stripped_text: str
    refusal: str = ""


def _token_name(word: str, prefix: str) -> str | None:
    """The name after ``prefix`` with glued punctuation removed, or None when ``word``
    is not that token (a bare prefix, or a prefix followed by punctuation only, is
    question text)."""
    if not word.startswith(prefix):
        return None
    return word[len(prefix) :].rstrip(_GLUED_PUNCTUATION) or None


def _is_token(word: str) -> bool:
    return any(
        _token_name(word, prefix) is not None
        for prefix in (PROJECT_TOKEN_PREFIX, BRANCH_TOKEN_PREFIX)
    )


def _dropped_indexes(parts: list[str]) -> set[int]:
    """Every token word, plus ONE whitespace run next to it: the run before the token
    when it is still there, else the one after. Only the token's OWN spacing collapses
    (spec §6.10a), so newlines and doubled spaces elsewhere reach the model unchanged."""
    dropped: set[int] = set()
    for index, part in enumerate(parts):
        if not _is_token(part):
            continue
        before = index - 1
        neighbour = before if before >= 0 and before not in dropped else index + 1
        dropped.update((index, neighbour))
    return dropped


def strip_scope_tokens(text: str) -> str:
    """The question without its tokens, each removed with the whitespace around it
    collapsed — every other byte survives, and a text that parses no token comes back
    as the SAME object (spec §6.10a, §8 token neutrality)."""
    parts = _WHITESPACE_RUN.split(text)
    dropped = _dropped_indexes(parts)
    if not dropped:
        return text
    return "".join(part for index, part in enumerate(parts) if index not in dropped)


def _lone_project(strip_projects: tuple[str, ...], listing: WorkspaceBranchListing) -> str:
    """The one project a bare ``on:`` may attach to: the strip's single target, else
    the workspace's single project, else ``""`` (refused)."""
    in_play = strip_projects or listing.project_names
    return in_play[0] if len(in_play) == 1 else ""


def _project_sentence(names: tuple[str, ...], label: str) -> str:
    """``Indexed: a, b`` / ``In play: a, b`` — or words when the workspace has nothing,
    since a bare ``Indexed: .`` reads as a rendering bug rather than an answer."""
    return f"{label}: {', '.join(names)}" if names else "No projects are indexed"


def _branch_sentence(names: tuple[str, ...], project: str) -> str:
    """``Indexed: main, develop`` — or words when the project has no pickable row (E8)."""
    return f"Indexed: {', '.join(names)}" if names else f"No branches are indexed for {project}"


def _unknown_project(name: str, listing: WorkspaceBranchListing) -> str:
    listed = _project_sentence(listing.project_names, "Indexed")
    return f"No project named {name!r}. {listed}. {_NOTHING_SENT}"


def _unknown_branch(branch: str, project: str, names: tuple[str, ...]) -> str:
    listed = _branch_sentence(names, project)
    return f"No branch named {branch!r} on {project}. {listed}. {_NOTHING_SENT}"


def _needs_project(branch: str, in_play: tuple[str, ...]) -> str:
    return (
        f"{BRANCH_TOKEN_PREFIX}{branch} needs a project: add {PROJECT_TOKEN_PREFIX}<project> "
        f"before it. {_project_sentence(in_play, 'In play')}. {_NOTHING_SENT}"
    )


def _on_before_in(branch: str, later_project: str) -> str:
    return (
        f"{BRANCH_TOKEN_PREFIX}{branch} must come after its {PROJECT_TOKEN_PREFIX}<project> "
        f"(found {PROJECT_TOKEN_PREFIX}{later_project} later in the question). {_NOTHING_SENT}"
    )


def _too_many(count: int, cap: int) -> str:
    return (
        f"That would be {count} searches; the limit is {cap} "
        f"(ask_your_docs.scope.max_cells). {_NOTHING_SENT}"
    )


def _first_project_token(words: list[str]) -> str:
    """The first ``in:`` name among ``words``, or ``""``."""
    return next((n for n in (_token_name(w, PROJECT_TOKEN_PREFIX) for w in words) if n), "")


def _open_project(
    targets: dict[str, list[str]], name: str, listing: WorkspaceBranchListing
) -> tuple[str, str]:
    """Start (or resume) ``name``'s target; ``("", refusal)`` when it is not indexed.

    A bundle stem is accepted for the same reason §6.3 accepts it, and the listing
    normalizes it — it owns the stem -> project map (§6.10a) — so no stem ever reaches
    a cell, a chip, a caption or the footer's ``head_sha``.
    """
    project = listing.project_for(name)
    if not project:
        return "", _unknown_project(name, listing)
    targets.setdefault(project, [])
    return project, ""


def _bare_branch_owner(
    branch: str,
    later_words: list[str],
    strip_projects: tuple[str, ...],
    listing: WorkspaceBranchListing,
) -> tuple[str, str]:
    """(owner, refusal) for an ``on:`` with no ``in:`` before it: a later ``in:`` is an
    ordering mistake and is named (P24); else the lone project in play; else refused."""
    later = _first_project_token(later_words)
    if later:
        return "", _on_before_in(branch, later)
    owner = _lone_project(strip_projects, listing)
    if not owner:
        return "", _needs_project(branch, strip_projects or listing.project_names)
    return owner, ""


def _attach_branch(
    targets: dict[str, list[str]],
    branch: str,
    owner: str,
    listing: WorkspaceBranchListing,
) -> str:
    """Attach ``branch`` to ``owner``; the refusal text when it is not pickable there."""
    names = tuple(r.name for r in listing.pickable(owner))
    if branch not in names:
        return _unknown_branch(branch, owner, names)
    targets.setdefault(owner, []).append(branch)
    return ""


def _branch_token(
    targets: dict[str, list[str]],
    word: str,
    current: str,
    later_words: list[str],
    strip_projects: tuple[str, ...],
    listing: WorkspaceBranchListing,
) -> str:
    """Attach one ``on:`` token to the nearest preceding ``in:``, or the first refusal."""
    branch = _token_name(word, BRANCH_TOKEN_PREFIX) or ""
    owner, refusal = (
        (current, "")
        if current
        else _bare_branch_owner(branch, later_words, strip_projects, listing)
    )
    return refusal or _attach_branch(targets, branch, owner, listing)


def _walk_tokens(
    words: list[str],
    listing: WorkspaceBranchListing,
    strip_projects: tuple[str, ...],
) -> tuple[dict[str, list[str]], str]:
    """project -> branches in token order, or the first refusal met."""
    targets: dict[str, list[str]] = {}
    current = ""
    for position, word in enumerate(words):
        project_name = _token_name(word, PROJECT_TOKEN_PREFIX)
        if project_name is not None:
            current, refusal = _open_project(targets, project_name, listing)
        else:
            later = words[position + 1 :]
            refusal = _branch_token(targets, word, current, later, strip_projects, listing)
        if refusal:
            return {}, refusal
    return targets, ""


def _cells_of(
    targets: dict[str, list[str]], listing: WorkspaceBranchListing
) -> tuple[ScopeCell, ...]:
    """One cell per attached ``on:`` branch, or the project's stamped row when none was
    named — on U0r always, since every ``on:`` is refused (§6.4a, §6.10a)."""
    # Grouped by project, not by strict first occurrence: ``targets`` is keyed by
    # project, so `in:a on:x in:b on:y on:z` fans out a, a…, b, b… — the order the
    # per-cell "## <project>" answer labels read best in (§6.4 rule 3).
    cells = [
        listing_cell(listing, project, branch)
        for project, branches in targets.items()
        for branch in (branches or [""])
    ]
    return tuple(dict.fromkeys(cells))


def parse_scope_tokens(
    text: str,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    strip_projects: tuple[str, ...],
    *,
    tokens_enabled: bool,
    max_cells: int,
) -> ParsedScopeTokens:
    """The grammar and refusals of UI spec §6.10a (plus P24: glued punctuation is not
    part of a name, and a bare ``on:`` ahead of an ``in:`` is refused by naming the
    order). The two flags are parameters so this module reads no config; every ``on:``
    is refused before ``branch_selector`` is advertised, before any name is checked."""
    if not tokens_enabled:
        return ParsedScopeTokens((), text)
    words = [word for word in text.split() if _is_token(word)]
    if not words:
        return ParsedScopeTokens((), text)
    names_a_branch = any(_token_name(w, BRANCH_TOKEN_PREFIX) is not None for w in words)
    if names_a_branch and not capabilities.branch_selector:
        return ParsedScopeTokens((), text, BRANCHES_NOT_CHOOSABLE)
    targets, refusal = _walk_tokens(words, listing, strip_projects)
    if refusal:
        return ParsedScopeTokens((), text, refusal)
    cells = _cells_of(targets, listing)
    if len(cells) > max_cells:
        return ParsedScopeTokens((), text, _too_many(len(cells), max_cells))
    return ParsedScopeTokens(cells, strip_scope_tokens(text))


__all__ = (
    "BRANCHES_NOT_CHOOSABLE",
    "BRANCH_TOKEN_PREFIX",
    "PROJECT_TOKEN_PREFIX",
    "ParsedScopeTokens",
    "parse_scope_tokens",
    "strip_scope_tokens",
)
