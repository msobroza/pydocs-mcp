"""Loader + product-side firewall for the packaged shared-skill artifact.

The skill artifact is the harness platform's "weights file" (spec §4.2 in
docs/superpowers/specs/2026-07-26-retriever-centric-harness-platform-design.md):
one delimited document in three tiers — the shared ``BACKBONE`` section
(transferable search policy), three enumerated ``TASK_HEAD: <task_name>``
sections (harness-INVARIANT task guidance: every harness running that task
reads and updates the same section), and six enumerated
``HARNESS_TASK_HEAD: <harness>.<task_name>`` sections (per-harness,
per-task conventions, spec §5.2). The grammar is
``application/description_source.py``'s — its regex carries the TASK_HEAD /
HARNESS_TASK_HEAD *shapes*; THIS module's ``SKILL_ARTIFACT_HEADERS`` is the
enumerated allowed set, so an unknown task head or harness task head parses
and is rejected here (the header-widening protocol's per-artifact firewall
— the ``ask_prompt`` ``_SECTION_ORDER`` precedent).

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

BACKBONE_HEADER = "BACKBONE"

# v1 task names and harness names are FIXED enumerated sets (spec §5.2):
# ``TASK_NAMES`` feeds BOTH tiers — the harness-invariant ``TASK_HEAD:``
# sections and the ``{harness} × {task}`` harness-task-head keys. A new
# harness or task name is a deliberate widening event — extend these tuples
# (and, if a header shape changes, ``_HEADER_RE``) — never config drift.
# ``repo_qa`` (2026-07-27) is the FIRST second framing: a QA framing minted
# over records that already carry another framing's rows, which is why the
# three-part ``<dataset>/<task_name>/<record_id>`` id spelling activates in
# the same event (run-contract spec §5).
HARNESS_NAMES = ("ask_your_docs", "external")
TASK_NAMES = ("sweqapro", "ccv", "repo_qa")

# Section caps in the description_source / usage_skill style (spec §5.3
# item 2: "stop the optimizer inflating the searchable region"). The backbone
# carries policy, not a tool catalogue, so it is capped below usage_skill's
# 1,500; task-head and harness-task-head sections carry task-local
# conventions and stay small by design.
BACKBONE_TOKEN_BUDGET = 1000
PER_TASK_HEAD_TOKEN_BUDGET = 300
PER_HARNESS_TASK_HEAD_TOKEN_BUDGET = 300


class SkillArtifactError(DescriptionSourceError):
    """Loader-level skill-artifact failure (unreadable override, unknown key).

    Grammar and validation failures reuse the ``description_source`` family,
    so one ``except DescriptionSourceError`` catches every skill-artifact
    failure; the ``PydocsMCPError`` + ``ValueError`` lineage comes with it.
    """


def task_head_section_header(task_name: str) -> str:
    """Return the section key for one task head (``"TASK_HEAD: ccv"``).

    Harness-INVARIANT by construction: the key carries no harness factor, so
    every harness running ``task_name`` reads and updates the same section.
    """
    if task_name not in TASK_NAMES:
        raise SkillArtifactError(
            f"unknown task {task_name!r} — v1 task names are the fixed set "
            f"{list(TASK_NAMES)} (spec §5.2; widening it is a deliberate "
            "event, not config drift)"
        )
    return f"TASK_HEAD: {task_name}"


def harness_task_head_section_header(harness: str, task_name: str) -> str:
    """Return one harness-task-head key (``"HARNESS_TASK_HEAD: ask_your_docs.ccv"``)."""
    if harness not in HARNESS_NAMES or task_name not in TASK_NAMES:
        requested = f"{harness}.{task_name}"
        raise SkillArtifactError(
            f"unknown harness task head {requested!r} — v1 harness task heads "
            f"are the fixed set {list(HARNESS_NAMES)} × {list(TASK_NAMES)} "
            "(spec §5.2; widening it is a deliberate event, not config drift)"
        )
    return f"HARNESS_TASK_HEAD: {harness}.{task_name}"


# Task-head order matches TASK_NAMES; harness task heads are harness-major,
# matching the spec §5.2 set notation {ask_your_docs, external} × TASK_NAMES.
TASK_HEAD_SECTION_HEADERS: tuple[str, ...] = tuple(
    task_head_section_header(task_name) for task_name in TASK_NAMES
)
HARNESS_TASK_HEAD_SECTION_HEADERS: tuple[str, ...] = tuple(
    harness_task_head_section_header(harness, task_name)
    for harness in HARNESS_NAMES
    for task_name in TASK_NAMES
)

# The skill artifact's allowed set — all ten sections are REQUIRED
# unconditionally (the CANONICAL_HEADERS precedent: a fixed section set
# keeps validation unconditional). The count is DERIVED
# (1 + len(TASK_NAMES) + len(HARNESS_NAMES) * len(TASK_NAMES)), so a
# widening event edits the two tuples above and nothing here.
SKILL_ARTIFACT_HEADERS: tuple[str, ...] = (
    BACKBONE_HEADER,
    *TASK_HEAD_SECTION_HEADERS,
    *HARNESS_TASK_HEAD_SECTION_HEADERS,
)


@dataclass(frozen=True, slots=True)
class SkillArtifact:
    """Backbone + task-head + harness-task-head views over one skill document.

    Example: ``load_skill_artifact().harness_task_head("ask_your_docs", "ccv")``.
    """

    backbone: str
    task_heads: Mapping[str, str]
    harness_task_heads: Mapping[str, str]

    def task_head(self, task_name: str) -> str:
        """The harness-invariant task-head section for one task name."""
        return self.task_heads[task_head_section_header(task_name)]

    def harness_task_head(self, harness: str, task_name: str) -> str:
        """The harness-task-head section for one (harness, task) arm."""
        return self.harness_task_heads[harness_task_head_section_header(harness, task_name)]


def load_packaged_skill() -> SkillArtifact:
    """Parse + validate the packaged seed — the always-present "weights file".

    A failure here is a packaging bug: raise loud rather than serve a
    partial artifact (the ``load_packaged`` precedent).

    Example:
        >>> load_packaged_skill().backbone  # doctest: +SKIP
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
    return SkillArtifact(
        backbone=sections[BACKBONE_HEADER],
        task_heads={key: sections[key] for key in TASK_HEAD_SECTION_HEADERS},
        harness_task_heads={key: sections[key] for key in HARNESS_TASK_HEAD_SECTION_HEADERS},
    )


def _check_skill_presence(sections: Mapping[str, str]) -> None:
    missing = tuple(key for key in SKILL_ARTIFACT_HEADERS if key not in sections)
    if missing:
        raise MissingSectionError(missing=missing, expected=SKILL_ARTIFACT_HEADERS)


def _check_skill_caps(sections: Mapping[str, str]) -> None:
    # Ceiling only, deliberately no floor: an empty section is structurally
    # valid (a trained head may legitimately converge to empty), so the
    # acceptance statistics — not this firewall — judge degenerate candidates.
    budgets = {BACKBONE_HEADER: BACKBONE_TOKEN_BUDGET}
    budgets |= dict.fromkeys(TASK_HEAD_SECTION_HEADERS, PER_TASK_HEAD_TOKEN_BUDGET)
    budgets |= dict.fromkeys(HARNESS_TASK_HEAD_SECTION_HEADERS, PER_HARNESS_TASK_HEAD_TOKEN_BUDGET)
    for key, budget in budgets.items():
        tokens = len(sections[key]) // CHARS_PER_TOKEN
        if tokens > budget:
            raise TokenBudgetExceededError(section=key, tokens=tokens, budget=budget)
