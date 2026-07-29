"""The tier partition + fold a COMPOSED harness delivers guidance through.

A candidate is a mapping of named sections (run-contract design §4). A harness
that carries guidance as ONE text block — a system-prompt suffix, an appended
instruction file — answers the same three questions about that mapping: which
sections does it deliver, which does it recognize-but-not-deliver, and which
are an error. This module is that answer, parameterized on ``harness_name``:

- **Delivered** — the three tiers, in fold order: the shared ``BACKBONE``, the
  harness-invariant ``TASK_HEAD: <task_name>``, and this harness's own
  ``HARNESS_TASK_HEAD: <harness_name>.<task_name>``.
- **Recognized, undelivered** — the prompt artifacts other harnesses deliver
  through their own seams (:data:`OTHER_HARNESS_PROMPT_SECTION_KEYS`) and every
  OTHER task head or harness task head. They are the same candidate's slices
  for other arms, so they travel with the document and drop here silently.
- **Anything else** — :class:`~pydocs_mcp.harness.platform.contract.UndeliverableGuidanceError`.
  Text the optimizer paid to train must reach a model or fail loudly. An EMPTY
  ``task_name`` puts the two task-scoped tiers in THIS bucket rather than the
  previous one: with no framing named nothing selects them, and neither is
  another arm's slice (``TASK_HEAD`` is harness-invariant and
  ``HARNESS_TASK_HEAD: <harness_name>.*`` is this harness's own), so dropping
  them would be exactly the silent loss contract rule 2 forbids.

WHY the partition is PATTERN-based rather than reading the enumerated
``skill_artifact`` headers: this logic's other implementation is the
eval suite's base-install twin (``pydocs_eval.agent_track._guidance``), which
sits on ADR 0009's no-``pydocs_mcp`` floor and therefore CANNOT enumerate.
Keeping the shapes identical is what lets a parity test pin the two folds
against each other byte-for-byte; enumerating here would silently fork them.
It also means an unknown task name selects no section instead of raising deep
inside a paid run — the arms platform's load firewall checks a task name
against the product enumeration before any spend.

Section BODIES are expected in the ``parse_sections`` normal form (one
trailing newline already trimmed) — the form the cross-harness byte parity is
stated over, since the ask harness reads its own block through render+parse.

The ask harness does NOT use this module: it delivers a whole candidate
DOCUMENT to its own assembly site and folds there. That is recorded rather
than refactored — a shared abstraction one of two callers does not need is a
worse fit than two honest shapes with a parity test between them.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydocs_mcp.harness.platform.contract import UndeliverableGuidanceError

# The section-key grammar, spelled once on this side of the parity.
BACKBONE_SECTION_KEY = "BACKBONE"
TASK_HEAD_KEY_PREFIX = "TASK_HEAD: "
HARNESS_TASK_HEAD_KEY_PREFIX = "HARNESS_TASK_HEAD: "

# Prompt artifacts delivered through ANOTHER harness's seam (the ask harness's
# prompt-override channel). A composed harness owns its engine's system prompt,
# so these ride along in the document and land nowhere.
OTHER_HARNESS_PROMPT_SECTION_KEYS = ("SYSTEM_PROMPT", "REWRITE_PROMPT")

# Single ``\n`` between tiers — byte-identical to the ask harness's
# ``_resolved_skill_block``, so one candidate reads the same in both harnesses
# (pinned by a parity test in tests/harness/platform/test_guidance_fold.py).
TIER_SEPARATOR = "\n"


def deliverable_section_keys(*, harness_name: str, task_name: str) -> tuple[str, ...]:
    """The section keys ``harness_name`` delivers for ``task_name``, in fold order.

    An EMPTY ``task_name`` degenerates to the backbone alone — the ask
    harness's ``task_name is None`` branch: with no task named, nothing selects
    a task head. Handing :func:`fold_guidance_sections` task-scoped sections in
    that state is a configuration error, not a drop (see its ``Raises``).

    Example:
        >>> deliverable_section_keys(harness_name="external", task_name="vuln")
        ('BACKBONE', 'TASK_HEAD: vuln', 'HARNESS_TASK_HEAD: external.vuln')
    """
    if not task_name:
        return (BACKBONE_SECTION_KEY,)
    return (
        BACKBONE_SECTION_KEY,
        f"{TASK_HEAD_KEY_PREFIX}{task_name}",
        f"{HARNESS_TASK_HEAD_KEY_PREFIX}{harness_name}.{task_name}",
    )


def fold_guidance_sections(
    sections: Mapping[str, str], *, harness_name: str, task_name: str
) -> str:
    """Fold ``sections`` into one guidance block for ``harness_name``.

    Returns the deliverable sections present in ``sections``, joined in tier
    order; an empty mapping (or one carrying only other arms' slices) folds to
    ``""``, which a caller renders as NO channel at all — the byte-identical
    no-guidance path.

    Raises:
        UndeliverableGuidanceError: a section key that is neither deliverable
            here nor a recognized slice of another arm — including a
            ``TASK_HEAD: *`` or ``HARNESS_TASK_HEAD: <harness_name>.*`` handed
            in with an EMPTY ``task_name``, which names no framing to select
            them.

    Example:
        >>> fold_guidance_sections(
        ...     {"BACKBONE": "search first"}, harness_name="external", task_name="vuln"
        ... )
        'search first'
    """
    deliverable = deliverable_section_keys(harness_name=harness_name, task_name=task_name)
    unknown = tuple(
        key
        for key in sections
        if key not in deliverable
        and not _is_another_arms_slice(key, harness_name=harness_name, task_name=task_name)
    )
    if unknown:
        raise UndeliverableGuidanceError(sections=unknown, deliverable=deliverable)
    return TIER_SEPARATOR.join(sections[key] for key in deliverable if key in sections)


def _is_another_arms_slice(key: str, *, harness_name: str, task_name: str) -> bool:
    """True for a section that belongs to ANOTHER arm's slice of the candidate.

    The skill document travels whole (its grammar requires the full section
    set), so every task head and harness task head that is not this arm's —
    plus the other harnesses' prompt artifacts — is expected, not an error.

    With NO framing named the "not this arm's" judgement is unavailable for the
    two tiers this harness owns, so both become undeliverable text rather than
    somebody else's slice. Another harness's head still rides along.
    """
    if key in OTHER_HARNESS_PROMPT_SECTION_KEYS:
        return True
    if not (key.startswith(TASK_HEAD_KEY_PREFIX) or key.startswith(HARNESS_TASK_HEAD_KEY_PREFIX)):
        return False
    if task_name:
        return True
    return key.startswith(HARNESS_TASK_HEAD_KEY_PREFIX) and not key.startswith(
        f"{HARNESS_TASK_HEAD_KEY_PREFIX}{harness_name}."
    )
