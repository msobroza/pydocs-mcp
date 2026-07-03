"""EmbedPolicy — which chunks get dense vectors, decided per package tier.

Embedding is the dominant indexing cost: big dependencies (torch, sklearn) carry
tens of thousands of code chunks, and embedding them all takes ~an hour on CPU.
The policy keeps everything INDEXED (FTS/BM25, trees, members are unaffected)
but embeds selectively, by a per-package **tier**:

- ``full`` — every chunk is embedded. Applies to the project itself and to any
  dependency promoted via ``embedding.full_index_dependencies`` (exact names or
  fnmatch globs; CLI ``--full-dep``), or globally via
  ``embedding.dependency_policy: full``.
- ``doc_pages`` (default for dependencies) — only documentation chunks are
  embedded: the per-module docstring pages (``dependency_module_doc``),
  markdown sections (``.md`` files anywhere, docs directories, READMEs), and
  notebook markdown.
- ``none`` — no dependency chunk is embedded (``dependency_policy: none``);
  dependencies are BM25-only.

The tier is also folded into each chunk's content_hash (see
``AssignChunkContentHashStage``), so promoting/demoting one dependency
invalidates ONLY that package's chunk hashes — the diff-merge then re-embeds
(or drops vectors for) exactly that package, nothing else.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from pydocs_mcp.extraction.pipeline.ingestion import TargetKind
from pydocs_mcp.models import ChunkOrigin

# Chunk origins that count as documentation — embedded under the doc_pages tier.
_DOC_ORIGINS = frozenset(
    {
        ChunkOrigin.DEPENDENCY_MODULE_DOC.value,
        ChunkOrigin.DEPENDENCY_README.value,
        ChunkOrigin.DEPENDENCY_DOC_FILE.value,
        ChunkOrigin.MARKDOWN_SECTION.value,
        ChunkOrigin.NOTEBOOK_MARKDOWN_CELL.value,
    }
)

_VALID_POLICIES = ("doc_pages", "full", "none")
_DEFAULT_DEPENDENCY_POLICY = "doc_pages"  # single source for the field + from_config fallback


def _normalize(name: str) -> str:
    """PyPI-style normalization that PRESERVES fnmatch wildcards.

    ``deps.normalize_package_name`` splits on specifier characters, which would
    eat ``*``; patterns only need the case/dash folding part.
    """
    return name.strip().lower().replace("-", "_")


@dataclass(frozen=True, slots=True)
class EmbedPolicy:
    """Value object shared by the hash + embed stages (one policy, two readers)."""

    dependency_policy: str = _DEFAULT_DEPENDENCY_POLICY
    full_index_dependencies: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.dependency_policy not in _VALID_POLICIES:
            raise ValueError(
                f"dependency_policy must be one of {_VALID_POLICIES}, "
                f"got {self.dependency_policy!r}",
            )

    @classmethod
    def from_config(cls, embedding_cfg: object | None) -> EmbedPolicy:
        """Build from ``AppConfig.embedding`` (or defaults when absent)."""
        if embedding_cfg is None:
            return cls()
        return cls(
            dependency_policy=getattr(
                embedding_cfg, "dependency_policy", _DEFAULT_DEPENDENCY_POLICY
            ),
            full_index_dependencies=tuple(
                _normalize(n) for n in getattr(embedding_cfg, "full_index_dependencies", ()) or ()
            ),
        )

    def is_full_indexed(self, package_name: str) -> bool:
        """True when ``package_name`` is promoted to the project-grade tier."""
        norm = _normalize(package_name)
        return any(fnmatch.fnmatchcase(norm, pat) for pat in self.full_index_dependencies)

    def tier(self, target_kind: TargetKind, package_name: str) -> str:
        """The package's embed tier: ``full`` | ``doc_pages`` | ``none``."""
        if target_kind is TargetKind.PROJECT:
            return "full"
        if self.dependency_policy == "full" or self.is_full_indexed(package_name):
            return "full"
        return self.dependency_policy  # doc_pages | none

    @staticmethod
    def should_embed(origin: str | None, tier: str) -> bool:
        """Whether a chunk with ``origin`` gets a vector under ``tier``."""
        if tier == "full":
            return True
        if tier == "none":
            return False
        return origin in _DOC_ORIGINS


__all__ = ("EmbedPolicy",)
