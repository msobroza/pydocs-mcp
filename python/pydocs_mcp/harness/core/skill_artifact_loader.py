"""Loader + product-side firewall for the packaged shared-skill artifact.

The skill artifact is the harness platform's "weights file" (spec §4.2 in
docs/superpowers/specs/2026-07-26-retriever-centric-harness-platform-design.md):
one delimited document carrying the shared ``ADAPTER`` section (transferable
search policy) and four enumerated ``HEAD: <harness>.<task_type>`` sections
(per-harness, per-task-type conventions, spec §5.2). The grammar is
``application/description_source.py``'s — its regex carries the HEAD *shape*;
THIS module's ``SKILL_ARTIFACT_HEADERS`` is the enumerated allowed set, so an
unknown head parses and is rejected here (the header-widening protocol's
per-artifact firewall — the ``ask_prompt`` ``_SECTION_ORDER`` precedent).

Failure semantics (spec §4.2; ADR 0006 §4 restated at
``application/description_override.py``): the packaged seed is the fallback
ONLY when no override is named at all; an explicitly named override that is
missing or invalid is a hard typed error — never a silent fallback. The seed
ships as package data and is replaced by a trained revision only per spec
§8.2 decision 3 (a reviewed commit through the §4.4 promotion path).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from pydocs_mcp.application.description_source import (
    CHARS_PER_TOKEN,
    DescriptionSourceError,
    MissingSectionError,
    TokenBudgetExceededError,
    parse_sections,
)

_SEED_PACKAGE = "pydocs_mcp.harness.core.skills"
_SEED_FILENAME = "search_guidance_seed.md"

ADAPTER_HEADER = "ADAPTER"

# v1 head keys are a FIXED enumerated set (spec §5.2): {harness} × {task
# type}. A new harness or task type is a deliberate widening event — extend
# these tuples (and, if the dotted shape changes, ``_HEADER_RE``) — never
# config drift.
HEAD_HARNESSES = ("ask_your_docs", "external")
HEAD_TASK_TYPES = ("sweqapro", "ccv")

# Section caps in the description_source / usage_skill style (spec §5.3
# item 2: "stop the optimizer inflating the searchable region"). The adapter
# carries policy, not a tool catalogue, so it is capped below usage_skill's
# 1,500; heads carry task-local conventions and stay small by design.
ADAPTER_TOKEN_BUDGET = 1000
PER_HEAD_TOKEN_BUDGET = 300


class SkillArtifactError(DescriptionSourceError):
    """Loader-level skill-artifact failure (unreadable override, unknown head).

    Grammar and validation failures reuse the ``description_source`` family,
    so one ``except DescriptionSourceError`` catches every skill-artifact
    failure; the ``PydocsMCPError`` + ``ValueError`` lineage comes with it.
    """


def head_section_header(harness: str, task_type: str) -> str:
    """Return the section key for one head (``"HEAD: ask_your_docs.ccv"``)."""
    if harness not in HEAD_HARNESSES or task_type not in HEAD_TASK_TYPES:
        requested = f"{harness}.{task_type}"
        raise SkillArtifactError(
            f"unknown head {requested!r} — v1 heads are the fixed set "
            f"{list(HEAD_HARNESSES)} × {list(HEAD_TASK_TYPES)} (spec §5.2; "
            "widening it is a deliberate event, not config drift)"
        )
    return f"HEAD: {harness}.{task_type}"


# Harness-major order, matching the spec §5.2 set notation
# {ask_your_docs, external} × {sweqapro, ccv}.
HEAD_SECTION_HEADERS: tuple[str, ...] = tuple(
    head_section_header(harness, task_type)
    for harness in HEAD_HARNESSES
    for task_type in HEAD_TASK_TYPES
)

# The skill artifact's allowed set — all five sections are REQUIRED
# unconditionally (the CANONICAL_HEADERS precedent: a fixed section set
# keeps validation unconditional).
SKILL_ARTIFACT_HEADERS: tuple[str, ...] = (ADAPTER_HEADER, *HEAD_SECTION_HEADERS)


@dataclass(frozen=True, slots=True)
class SkillArtifact:
    """Adapter + head views over one validated skill document.

    Example: ``load_skill_artifact().head("ask_your_docs", "ccv")``.
    """

    adapter: str
    heads: Mapping[str, str]

    def head(self, harness: str, task_type: str) -> str:
        """The head section for one (harness, task type) arm."""
        return self.heads[head_section_header(harness, task_type)]


def load_packaged_skill() -> SkillArtifact:
    """Parse + validate the packaged seed — the always-present "weights file".

    A failure here is a packaging bug: raise loud rather than serve a
    partial artifact (the ``load_packaged`` precedent).

    Example:
        >>> load_packaged_skill().adapter  # doctest: +SKIP
    """
    text = resources.files(_SEED_PACKAGE).joinpath(_SEED_FILENAME).read_text("utf-8")
    origin = f"packaged {_SEED_FILENAME} (a failure here is a packaging bug)"
    return _parse_and_validate_skill(text, origin=origin)


def load_skill_artifact(override: Path | None = None) -> SkillArtifact:
    """Load the skill artifact: the named override, or the packaged seed.

    ``None`` — the shipped default — serves the packaged seed. An explicitly
    named override that is missing or invalid is a hard error; fallback to
    the seed exists only when NO override was supplied at all.

    Example:
        >>> load_skill_artifact(Path("candidate_skill.md"))  # doctest: +SKIP
    """
    if override is None:
        return load_packaged_skill()
    try:
        text = override.read_text(encoding="utf-8")
    # UnicodeDecodeError included: a mojibake candidate file must land in the
    # same typed family as an unreadable one, or "one except
    # DescriptionSourceError" (the class docstring's contract) is false for
    # exactly the case SkillArtifactError exists to cover.
    except (OSError, UnicodeDecodeError) as exc:
        raise SkillArtifactError(
            f"skill artifact {str(override)!r} could not be read: {exc} — "
            "explicit overrides never fall back to the packaged seed; omit "
            "the override to serve the seed"
        ) from exc
    origin = f"{override} (fix the document, or omit the override to serve the packaged seed)"
    return _parse_and_validate_skill(text, origin=origin)


def parse_skill_artifact(text: str, *, origin: str) -> SkillArtifact:
    """Parse + validate in-memory skill-document text (the PUBLIC entrypoint).

    The cross-package seam the benchmarks ``search_skill`` family delegates
    to (run-contract design §4/§9 stage 4): one validator on the product
    side means "firewall-accepts ⇒ product-accepts" holds by identity.
    ``origin`` names the text's source in every failure note.

    Example:
        >>> parse_skill_artifact(candidate_text, origin="candidate")  # doctest: +SKIP
    """
    return _parse_and_validate_skill(text, origin=origin)


def _parse_and_validate_skill(text: str, *, origin: str) -> SkillArtifact:
    try:
        sections = parse_sections(text, allowed=SKILL_ARTIFACT_HEADERS)
        _check_skill_presence(sections)
        _check_skill_caps(sections)
    except DescriptionSourceError as exc:
        exc.add_note(f"skill artifact: {origin}")
        raise
    heads = {key: sections[key] for key in HEAD_SECTION_HEADERS}
    return SkillArtifact(adapter=sections[ADAPTER_HEADER], heads=heads)


def _check_skill_presence(sections: Mapping[str, str]) -> None:
    missing = tuple(key for key in SKILL_ARTIFACT_HEADERS if key not in sections)
    if missing:
        raise MissingSectionError(missing=missing, expected=SKILL_ARTIFACT_HEADERS)


def _check_skill_caps(sections: Mapping[str, str]) -> None:
    # Ceiling only, deliberately no floor: an empty section is structurally
    # valid (a trained head may legitimately converge to empty), so the
    # acceptance statistics — not this firewall — judge degenerate candidates.
    budgets = {ADAPTER_HEADER: ADAPTER_TOKEN_BUDGET}
    budgets |= dict.fromkeys(HEAD_SECTION_HEADERS, PER_HEAD_TOKEN_BUDGET)
    for key, budget in budgets.items():
        tokens = len(sections[key]) // CHARS_PER_TOKEN
        if tokens > budget:
            raise TokenBudgetExceededError(section=key, tokens=tokens, budget=budget)
