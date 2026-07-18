"""Entry-point plumbing for the description-source override (ADR 0006 §2-§4).

Resolves WHICH description document this process serves — precedence
``--descriptions`` CLI flag > ``PYDOCS_SERVE__DESCRIPTIONS_PATH`` env var >
user-YAML ``serve.descriptions_path`` > packaged default — and applies the
winner via :func:`~pydocs_mcp.application.description_source.apply_source`.
Kept separate from ``description_source`` (grammar / validation / hash) so
each module keeps one reason to change: that one evolves with the document
format, this one with the entry-point surface.

Universal strictness (ADR 0006 §4): an explicitly named source that is
missing or invalid is a hard error — fallback to the packaged document
exists only when NO override was supplied at all, so a named optimization
candidate can never silently degrade to the defaults. Errors carry the
winning source's origin because the env var silently outranks the YAML key
(pydantic-settings source order) and a failure must be diagnosable without
knowing that ordering.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydocs_mcp.application.description_source import (
    DescriptionSourceError,
    apply_source,
    current_artifact_hash,
)

# WHY hardcoded: ``main()`` must apply the env override BEFORE any config
# parse (the argparse tree renders the description bundle, so CLI help
# parity — R2 — needs the rebinding first), which rules out deriving the
# name from a loaded AppConfig. Kept in lockstep with AppConfig's
# ``env_prefix="PYDOCS_"`` + ``env_nested_delimiter="__"`` and the
# ``ServeConfig.descriptions_path`` field; the env-outranks-YAML test in
# tests/test_serve_descriptions_override.py pins that pydantic-settings
# actually routes this exact name.
DESCRIPTIONS_PATH_ENV_VAR = "PYDOCS_SERVE__DESCRIPTIONS_PATH"

# Startup-log source labels (single source of truth — ``server.py`` compares
# against these when deciding what the pinned log line reports).
PACKAGED_SOURCE = "packaged"
PRE_APPLIED_SOURCE = "pre-applied"

_PRECEDENCE = "precedence: CLI flag > env > user YAML > packaged"


class EmptyDescriptionsEnvError(DescriptionSourceError):
    """The descriptions env var is SET but EMPTY — refuse to guess intent.

    Because the env layer outranks the user YAML in pydantic-settings source
    order, an empty value would silently merge ``''`` over a YAML-configured
    ``serve.descriptions_path`` and degrade the run to the packaged fallback
    — exactly the silent downgrade universal strictness forbids.
    """

    def __init__(self) -> None:
        super().__init__(
            f"{DESCRIPTIONS_PATH_ENV_VAR} is set but empty — it would silently "
            "suppress any YAML-configured serve.descriptions_path (the env "
            "layer outranks user YAML); unset it or provide a path"
        )


def resolve_descriptions_override(
    *, cli_path: Path | None, configured_path: str | None
) -> tuple[Path, str] | None:
    """Pick the winning explicit description source, or ``None`` for packaged.

    ``configured_path`` is AppConfig's merged ``serve.descriptions_path``, in
    which the env var already outranks the user YAML (pydantic-settings
    source order) — the env probe here only recovers WHICH of the two
    supplied the value, so hard errors can name the winning source. An empty
    YAML string counts as unset; a SET-but-EMPTY env var raises
    :class:`EmptyDescriptionsEnvError` (it silently clobbers the YAML key in
    the merge, so treating it as unset would swallow a configured override).
    The CLI flag outranks the env var, failure path included — a flag-named
    source wins before the env value is even inspected.

    Example:
        >>> resolve_descriptions_override(cli_path=None, configured_path=None) is None
        True
    """
    if cli_path is not None:
        return cli_path.expanduser(), "--descriptions flag"
    env_value = os.environ.get(DESCRIPTIONS_PATH_ENV_VAR)
    if env_value == "":
        raise EmptyDescriptionsEnvError()
    if not configured_path:
        return None
    if env_value:
        return Path(configured_path).expanduser(), f"env {DESCRIPTIONS_PATH_ENV_VAR}"
    return Path(configured_path).expanduser(), "user YAML serve.descriptions_path"


def apply_descriptions_override(
    *, cli_path: Path | None, configured_path: str | None
) -> tuple[str, str]:
    """Resolve + apply the winning source; return ``(artifact_hash, source)``.

    ``source`` is the label the startup log line reports: ``"packaged"``
    when no override was named, otherwise the winning path. With an
    override, :func:`apply_source` validates BEFORE rebinding and any
    failure is re-raised with the winning source's origin attached (ADR
    0006 §4 — never a silent fallback). Without one, the live attributes
    are left untouched and only fingerprinted.

    Example:
        >>> apply_descriptions_override(cli_path=None, configured_path=None)  # doctest: +SKIP
        ('4f9a1c0d8be2...', 'packaged')
    """
    override = resolve_descriptions_override(cli_path=cli_path, configured_path=configured_path)
    if override is None:
        return current_artifact_hash(), PACKAGED_SOURCE
    path, origin = override
    try:
        artifact_hash = apply_source(path)
    except DescriptionSourceError as exc:
        exc.add_note(f"selected by {origin} ({_PRECEDENCE}); explicit sources never fall back")
        raise
    return artifact_hash, str(path)
