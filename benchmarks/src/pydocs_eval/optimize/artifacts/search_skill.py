"""The ``search_skill`` optimizable artifact (run-contract design §4, stage 4).

Exposes the PRODUCT's packaged skill artifact — the harness platform's
"weights file" — as an optimizable family: one delimited document in the
skill grammar (``BACKBONE`` plus one ``TASK_HEAD: <task_name>`` section per
enumerated task name and one ``HARNESS_TASK_HEAD: <harness>.<task_name>``
section per harness x task pair).

**No second grammar lives here.** ``validate()`` calls the product's public
``parse_skill_artifact(text, *, origin)`` and reports whatever it raises, so
"firewall-accepts ⇒ product-accepts" holds BY IDENTITY rather than by a
mutated-document parity battery (ADR 0019 §Amendment 2026-07-27). The header
set, the header order, and the three token budgets are the loader's —
re-encoding any of them benchmarks-side is exactly the drift the delegation
deletes, and ``tests/optimize/candidates/test_firewall_parity.py`` pins their
absence.

Every ``pydocs_mcp`` import is DEFERRED (function-local, behind the
``[retrieval]`` guard), the ``ask_binding`` shape rather than the other
artifacts' module-level one: registering this family costs no product import,
so a proposal is parsed, validated, normalized, and hashed before it may cost
a single rollout (ADR 0019 §Decision 5's zero-cost rule).

The seed is the product's own packaged ``search_guidance_seed.md``, read
through the loader — there is deliberately NO committed benchmarks-side copy
to drift from it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from types import ModuleType

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra
from pydocs_eval.optimize.registries import artifact_registry

# Where a landed proposal applies. The seed is replaced by a trained revision
# only through the reviewed promotion path (platform spec §4.4 / §8.2), never
# by an optimizer writing the file.
_PRODUCT_SEED_PATH = "python/pydocs_mcp/harness/assets/skills/search_guidance_seed.md"

# Named in every product failure note so a rejected proposal's violations say
# where the text came from (the loader's ``origin`` contract).
_CANDIDATE_ORIGIN = "search_skill candidate (optimizer proposal, pre-rollout firewall)"


def _skill_loader() -> ModuleType:
    """The product skill-artifact loader, imported behind the extras guard.

    DEFERRED on purpose (the ``ask_binding._run_contract`` shape): importing
    it at module scope would drag ``pydocs_mcp`` into the artifact REGISTRY
    population path for a family that may never be built.
    """
    try:
        import pydocs_mcp.harness.platform.skill_artifact as loader
    except ImportError as exc:
        raise_missing_retrieval_extra(exc)
    return loader


def _packaged_seed_document() -> str:
    """Re-render the packaged product seed as one delimited document.

    Reads through ``load_packaged_skill`` (not the seed path) so the section
    KEYS and their canonical order come from the loader's own
    ``SKILL_ARTIFACT_HEADERS``. The packaged seed is already the canonical
    surface, so this round-trip is byte-identical to the shipped file — pinned
    by ``test_search_skill_artifact.py``.
    """
    from pydocs_eval.optimize.artifacts._delimited import render_delimited

    loader = _skill_loader()
    skill = loader.load_packaged_skill()
    by_key = {
        loader.BACKBONE_HEADER: skill.backbone,
        **skill.task_heads,
        **skill.harness_task_heads,
    }
    return render_delimited({key: by_key[key] for key in loader.SKILL_ARTIFACT_HEADERS})


@artifact_registry.register("search_skill")
@dataclass(frozen=True, slots=True)
class SearchSkillArtifact:
    """A candidate skill artifact — backbone + task heads + harness task heads."""

    name: str = "search_skill"
    content: str | None = None

    def render(self) -> str:
        """Return the candidate text, or the packaged product seed when unseeded."""
        if self.content is not None:
            return self.content
        return _packaged_seed_document()

    def with_content(self, content: str) -> SearchSkillArtifact:
        """Return a copy carrying ``content`` as the candidate skill document."""
        return replace(self, content=content)

    def validate(self) -> tuple[str, ...]:
        """Return the PRODUCT loader's violations; empty tuple == valid.

        One-line delegation (the ``tool_docs`` → firewall shape): the whole
        verdict — the enumerated allowed set, every section present, and the
        per-tier token caps — is ``parse_skill_artifact``'s. Never raises; the
        product's typed grammar errors are caught into the violations tuple so
        an invalid proposal is ledgered and dropped without paying a rollout.
        """
        loader = _skill_loader()
        try:
            loader.parse_skill_artifact(self.render(), origin=_CANDIDATE_ORIGIN)
        except loader.DescriptionSourceError as exc:
            return (str(exc),)
        return ()

    def landing_note(self) -> str:
        """Explain how a human lands a proposal from this artifact."""
        return (
            f"Apply the diff to {_PRODUCT_SEED_PATH} by hand — the packaged seed "
            "is replaced by a trained revision only through the reviewed "
            "promotion path, never by the optimizer writing the file — then "
            "rerun tests/harness/platform/test_skill_artifact.py to confirm "
            "the edited seed still parses, stays inside its caps, and remains "
            "the canonical surface."
        )

    @property
    def fingerprint(self) -> str:
        """SHA-256 hex digest of the rendered skill document (64 chars)."""
        return hashlib.sha256(self.render().encode()).hexdigest()
