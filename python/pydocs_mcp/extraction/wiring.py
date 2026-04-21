"""Load and build :class:`IngestionPipeline` from YAML (spec §7.3).

Single YAML file → single pipeline instance. The shipped
``presets/ingestion.yaml`` is the default; users override via
``extraction.ingestion.pipeline_path`` in their config YAML. Path
candidates resolve through the SAME sub-PR #2 allowlist that retrieval's
``pipeline_path`` uses (AC #33, spec §5.9): shipped ``presets/`` directory
or the user's config directory — symlinks resolve BEFORE the check, so a
symlink planted inside ``presets/`` pointing at ``/etc/shadow`` is
rejected.

Side-effect import of ``extraction.stages`` populates :data:`stage_registry`
via the six ``@stage_registry.register(...)`` decorators; without that
import :func:`load_ingestion_pipeline` would raise ``KeyError`` on the
first stage lookup.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

# Side-effect import — registers the 6 ingestion stages via decorators.
from pydocs_mcp.extraction import stages as _stages  # noqa: F401
from pydocs_mcp.extraction.pipeline import IngestionPipeline
from pydocs_mcp.extraction.serialization import stage_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.config import AppConfig


_DEFAULT_INGESTION_PRESET = Path(__file__).resolve().parent.parent / "presets" / "ingestion.yaml"


def load_ingestion_pipeline(
    path: Path, cfg: "AppConfig",
) -> IngestionPipeline:
    """Load and build an :class:`IngestionPipeline` from a YAML file.

    The path is resolved + allowlisted through retrieval's
    ``_resolve_pipeline_path`` so ingestion and retrieval share a single
    security-critical path-check implementation (AC #33).
    """
    resolved = _resolve_ingestion_pipeline_path(path, cfg)
    data = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "stages" not in data:
        raise ValueError(f"invalid ingestion pipeline YAML: {resolved!s}")
    # Reuse retrieval's BuildContext — extraction stages only read
    # ``context.app_config`` inside ``from_dict``; the other BuildContext
    # fields stay unused here but must be constructed to satisfy the
    # dataclass's required ``connection_provider`` field. A ``None`` stand-in
    # is acceptable because no extraction stage dereferences it.
    from pydocs_mcp.retrieval.serialization import BuildContext
    context = BuildContext(connection_provider=None, app_config=cfg)  # type: ignore[arg-type]
    pipeline_stages = tuple(stage_registry.build(s, context) for s in data["stages"])
    return IngestionPipeline(stages=pipeline_stages)


def build_ingestion_pipeline(cfg: "AppConfig") -> IngestionPipeline:
    """Build the :class:`IngestionPipeline` for this :class:`AppConfig`.

    Uses ``cfg.extraction.ingestion.pipeline_path`` if set (allowlist
    enforced); otherwise falls back to the bundled
    ``presets/ingestion.yaml``. The bundled default stays inside
    ``_shipped_presets_dir`` and always passes the allowlist.
    """
    override = cfg.extraction.ingestion.pipeline_path
    path = override if override is not None else _DEFAULT_INGESTION_PRESET
    return load_ingestion_pipeline(Path(path), cfg)


def _resolve_ingestion_pipeline_path(
    path: Path, cfg: "AppConfig",
) -> Path:
    """Resolve ``path`` through retrieval's shared pipeline_path allowlist.

    Keeping this in a private helper (instead of calling
    ``_resolve_pipeline_path`` directly at the top level) isolates the
    tightly-coupled import from the public API — if the retrieval helper
    moves or renames, only this one function needs updating.
    """
    # Deferred import — retrieval.config pulls in pydantic-settings + formatters
    # + retrievers via its own side-effect imports; deferring keeps ``extraction``
    # importable from contexts that don't need retrieval's registry fully warmed.
    from pydocs_mcp.retrieval.config import _resolve_pipeline_path
    user_config_path = cfg._user_config_path() if hasattr(cfg, "_user_config_path") else None
    return _resolve_pipeline_path(path, user_config_path)


__all__ = ("build_ingestion_pipeline", "load_ingestion_pipeline")
