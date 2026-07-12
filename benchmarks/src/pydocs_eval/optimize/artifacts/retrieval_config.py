"""The ``retrieval_config`` structured YAML artifact (spec §3.2.3).

``render()`` is the literal bytes of a retrieval pipeline overlay YAML — an
``AppConfig`` overlay, the sanctioned YAML tuning surface. The seed is a
run-config-named existing file from ``benchmarks/configs/pipelines/``. This
artifact claims the config-injection slice the ``retrieval`` fitness
scaffolding deferred: the fitness writes the candidate's render to a temp
overlay and sweeps it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

# Module-level ``pydocs_mcp`` boundary: the known-sections firewall reads the
# product AppConfig model fields (never a hard-coded list) — a base install
# without the [retrieval] extra gets the actionable install hint.
try:
    from pydocs_mcp.retrieval.config.app_config import AppConfig
except ImportError as exc:
    raise_missing_retrieval_extra(exc)

from pydocs_eval.optimize.registries import artifact_registry


@artifact_registry.register("retrieval_config")
@dataclass(frozen=True, slots=True)
class RetrievalConfigArtifact:
    """A candidate retrieval overlay YAML (spec §3.2.3)."""

    name: str = "retrieval_config"
    seed_path: Path | None = None
    content: str | None = None

    def render(self) -> str:
        """The candidate overlay bytes, or the seed file's bytes when unseeded."""
        if self.content is not None:
            return self.content
        if self.seed_path is not None:
            return self.seed_path.read_text(encoding="utf-8")
        return ""

    def with_content(self, content: str) -> RetrievalConfigArtifact:
        """Return a copy carrying ``content`` as the candidate overlay."""
        return replace(self, content=content)

    def validate(self) -> tuple[str, ...]:
        """Return constraint violations; empty tuple == valid (never raises).

        The overlay must parse as a YAML mapping whose top-level keys are a
        subset of ``AppConfig``'s known sections — imported from the product
        model, so a product config rename breaks loudly here, never mid-sweep.
        """
        text = self.render()
        if not text.strip():
            return ("overlay is empty — seed a pipelines YAML or set content",)
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            return (f"overlay is not parseable YAML: {exc}",)
        if not isinstance(parsed, dict):
            return (f"overlay must be a YAML mapping, got {type(parsed).__name__}",)
        known = set(AppConfig.model_fields)
        return tuple(
            f"unknown top-level key {key!r}; AppConfig sections are {sorted(known)}"
            for key in parsed
            if key not in known
        )

    def landing_note(self) -> str:
        """Explain how a human lands a proposal from this artifact."""
        return (
            "Copy the winning overlay into benchmarks/configs/pipelines/ (or "
            "the deployment's serve --config) — retrieval tuning lands as "
            "YAML, never as product code."
        )

    @property
    def fingerprint(self) -> str:
        """SHA-256 hex digest of the rendered overlay (64 chars)."""
        return hashlib.sha256(self.render().encode()).hexdigest()

    def retrieval_overlay(self) -> str:
        """The candidate overlay bytes — this artifact's render IS the overlay."""
        return self.render()
