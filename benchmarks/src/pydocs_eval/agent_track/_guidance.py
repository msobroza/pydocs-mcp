"""This harness's guidance partition + fold (run-contract design §4).

The external CLI track's answer to the same question every harness answers:
given the optimizer's section mapping, which sections does THIS harness
deliver, which does it recognize-but-not-deliver, and which are an error?

- **Delivered** — the three tiers this arm reads: the shared ``BACKBONE``,
  the harness-invariant ``TASK_HEAD: <task_name>``, and this harness's own
  ``HARNESS_TASK_HEAD: external.<task_name>``. They fold, in that order, into
  the ``--append-system-prompt`` block (``_command.build_claude_command``).
- **Recognized, undelivered** — the ask harness's prompt artifacts
  (``SYSTEM_PROMPT`` / ``REWRITE_PROMPT``) and every OTHER task head or
  harness task head. They are the same candidate's slices for other arms, so
  they travel with the document and are dropped here without complaint.
- **Anything else** — :class:`ExternalUndeliverableGuidanceError`. Text the
  optimizer paid to train must reach a model or fail loudly. An EMPTY
  ``task_name`` puts the two task-scoped tiers in this bucket rather than the
  previous one: with no framing named, nothing selects them, and they are not
  another arm's slice (``TASK_HEAD`` is harness-invariant and
  ``HARNESS_TASK_HEAD: external.*`` is this harness's own), so dropping them
  would be exactly the silent loss rule 2 forbids.

Section BODIES are expected in the ``parse_sections`` normal form — what
``pydocs_mcp.application.description_source.parse_sections`` returns, one
trailing newline already trimmed. That is the form the cross-harness byte
parity is stated over (the ask harness reads its own block through
render+parse); a caller that folds raw, un-round-tripped bodies gets its own
bytes back, which is honest but not comparable.

BASE install by contract (ADR 0009's 2026-07-27 floor): stdlib only, no
``pydocs_mcp`` import — ``pydocs-eval-agent-track`` is a base console script.
That is WHY the partition is PATTERN-based rather than a copy of the
product's enumerated ``SKILL_ARTIFACT_HEADERS``: this module cannot read the
enumeration, and a hand-written copy would be a second spelling that every
task-name widening event must hand-edit in lockstep. It is also why
:class:`ExternalUndeliverableGuidanceError` is a format-coupled TWIN of the
contract's ``UndeliverableGuidanceError`` (same message shape, pinned by a
parity test) instead of the type itself.
"""

from __future__ import annotations

from collections.abc import Mapping

# The section-key grammar, spelled once. These four literals are the pattern
# half of the format coupling the base-install floor forces; the product's
# ``skill_artifact_loader`` owns the enumerated half.
BACKBONE_SECTION_KEY = "BACKBONE"
TASK_HEAD_KEY_PREFIX = "TASK_HEAD: "
HARNESS_TASK_HEAD_KEY_PREFIX = "HARNESS_TASK_HEAD: "

# This harness's name in the ``HARNESS_TASK_HEAD: <harness>.<task>`` tier —
# the product's ``HARNESS_NAMES`` entry for the external CLI track.
EXTERNAL_HARNESS_NAME = "external"

# Recognized-but-undelivered prompt artifacts: the ask harness's prompt
# override seam has no counterpart here (the CLI owns its own system prompt),
# so these ride along in the document and land nowhere.
_ASK_ONLY_PROMPT_SECTION_KEYS = ("SYSTEM_PROMPT", "REWRITE_PROMPT")

# The harness-task-head tier that is OURS — the one prefix no other arm can
# ever deliver, which is why an unnamed framing cannot silently drop it.
_EXTERNAL_HARNESS_TASK_HEAD_PREFIX = f"{HARNESS_TASK_HEAD_KEY_PREFIX}{EXTERNAL_HARNESS_NAME}."

# Single ``\n`` between tiers — byte-identical to the ask harness's
# ``_resolved_skill_block`` for bodies in the ``parse_sections`` normal form
# (see the module docstring), so one candidate reads the same in both harnesses
# (pinned by ``test_guidance.py``'s parity tests).
_TIER_SEPARATOR = "\n"


class ExternalUndeliverableGuidanceError(ValueError):
    """A guidance section this harness cannot deliver (contract rule 2).

    The format-coupled twin of the product contract's
    ``UndeliverableGuidanceError``: same message shape, same
    ``ValueError`` lineage, no product import (base-install floor).
    """

    def __init__(self, *, sections: tuple[str, ...], deliverable: tuple[str, ...]) -> None:
        self.sections = sections
        self.deliverable = deliverable
        super().__init__(
            f"guidance section(s) {list(sections)} are not deliverable by this "
            f"harness — its delivery map covers {list(deliverable)}; text is "
            "never dropped silently"
        )


def deliverable_section_keys(task_name: str) -> tuple[str, ...]:
    """The section keys this harness delivers for ``task_name``, in fold order.

    An EMPTY ``task_name`` degenerates to the backbone alone — the ask
    harness's ``task_name is None`` branch: with no task named, nothing
    selects a task head. Handing :func:`fold_guidance` task-scoped sections in
    that state is a configuration error, not a drop (see its ``Raises``).

    Example:
        >>> deliverable_section_keys("vuln")
        ('BACKBONE', 'TASK_HEAD: vuln', 'HARNESS_TASK_HEAD: external.vuln')
    """
    if not task_name:
        return (BACKBONE_SECTION_KEY,)
    return (
        BACKBONE_SECTION_KEY,
        f"{TASK_HEAD_KEY_PREFIX}{task_name}",
        f"{HARNESS_TASK_HEAD_KEY_PREFIX}{EXTERNAL_HARNESS_NAME}.{task_name}",
    )


def fold_guidance(sections: Mapping[str, str], *, task_name: str) -> str:
    """Fold ``sections`` into this harness's system-prompt suffix.

    Returns the deliverable sections present in ``sections``, joined in tier
    order; an empty mapping (or one carrying only other arms' slices) folds to
    ``""``, which the command builder renders as NO flag at all — the
    byte-identical no-guidance argv.

    Raises:
        ExternalUndeliverableGuidanceError: a section key that is neither
            deliverable here nor a recognized slice of another arm — including
            a ``TASK_HEAD: *`` or ``HARNESS_TASK_HEAD: external.*`` handed in
            with an EMPTY ``task_name``, which names no framing to select them.

    Example:
        >>> fold_guidance({"BACKBONE": "search first"}, task_name="vuln")
        'search first'
    """
    deliverable = deliverable_section_keys(task_name)
    unknown = tuple(
        key
        for key in sections
        if key not in deliverable and not _is_recognized_elsewhere(key, task_name=task_name)
    )
    if unknown:
        raise ExternalUndeliverableGuidanceError(sections=unknown, deliverable=deliverable)
    return _TIER_SEPARATOR.join(sections[key] for key in deliverable if key in sections)


def _is_recognized_elsewhere(key: str, *, task_name: str) -> bool:
    """True for a section that belongs to ANOTHER arm's slice of the candidate.

    The skill document travels whole (its grammar requires the full section
    set), so every task head and harness task head that is not this arm's —
    plus the ask harness's prompt artifacts — is expected, not an error.

    With NO framing named the "not this arm's" judgement is unavailable for the
    two tiers this harness owns: ``TASK_HEAD: *`` is harness-invariant (ours
    under whichever framing the arm names) and ``HARNESS_TASK_HEAD: external.*``
    is ours by construction, so both become undeliverable text rather than
    somebody else's slice. Another harness's head still rides along.
    """
    if key in _ASK_ONLY_PROMPT_SECTION_KEYS:
        return True
    if not (key.startswith(TASK_HEAD_KEY_PREFIX) or key.startswith(HARNESS_TASK_HEAD_KEY_PREFIX)):
        return False
    if task_name:
        return True
    return key.startswith(HARNESS_TASK_HEAD_KEY_PREFIX) and not key.startswith(
        _EXTERNAL_HARNESS_TASK_HEAD_PREFIX
    )
