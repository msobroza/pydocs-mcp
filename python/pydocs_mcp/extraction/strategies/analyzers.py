"""LanguageAnalyzer seam — extension-keyed reference-capture registry (ADR 0004).

Freezes the per-language code-structure seam so adding a language is
additive registration, not another hardcoded branch in
``ReferenceCaptureStage._capture_all``. Modeled on ``chunker_registry``
(:mod:`pydocs_mcp.extraction.serialization`): a plain extension-keyed
dict — there is no YAML dict to decode, so the ``ComponentRegistry``
machinery would be dead weight. Keys include the leading dot and are
lowercase (``".py"``, ``".md"``).

Each analyzer also declares its :class:`LanguageCapabilities` — the
frozen contract vocabulary of docs/tool-contracts.md §5.1::

    {outline, definitions, references} × {semantic | syntactic | unavailable}

``PYTHON_CAPABILITIES`` is the single source for the ``references``
flag surfaced as ``get_references`` ``meta.resolution``. A future
semantic backend (jedi, YAML opt-in) flips only the declared value —
the tool contract is invariant under the swap (ADR 0004).
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import (
    TYPE_CHECKING,
    ClassVar,
    Literal,
    Protocol,
    TypedDict,
    runtime_checkable,
)

if TYPE_CHECKING:
    from pathlib import Path

    from pydocs_mcp.extraction.strategies.references import ReferenceCollector


class LanguageCapabilities(TypedDict):
    """Frozen per-language capability declaration (contract §5.1)."""

    outline: Literal["available", "unavailable"]
    definitions: Literal["available", "unavailable"]
    references: Literal["semantic", "syntactic", "unavailable"]


@runtime_checkable
class LanguageAnalyzer(Protocol):
    """Reference-capture backend for one file extension.

    ``capture`` parses ``source`` and emits unresolved
    :class:`~pydocs_mcp.storage.node_reference.NodeReference` candidates
    (plus alias / attribute-type tables) into ``collector``. Per-file
    error containment is the CALLER's job (``ReferenceCaptureStage``
    logs and continues) — analyzers may raise freely.
    """

    capabilities: ClassVar[LanguageCapabilities]

    def capture(
        self,
        source: str,
        *,
        path: str,
        root: Path,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None: ...


PYTHON_CAPABILITIES: LanguageCapabilities = {
    "outline": "available",  # persisted document trees ARE the outline, with spans
    "definitions": "available",
    "references": "syntactic",  # name/alias-matched graph, not scope-resolved
}

MARKDOWN_CAPABILITIES: LanguageCapabilities = {
    "outline": "available",  # heading trees
    "definitions": "unavailable",
    "references": "syntactic",  # regex-fuzzy backtick MENTIONS, opt-in
}


analyzer_registry: dict[str, LanguageAnalyzer] = {}
"""Extension → analyzer INSTANCE (analyzers are stateless singletons).

Unlike ``chunker_registry`` (which stores classes and constructs per
config), analyzers take no config, so the decorator registers one
shared instance at import time.
"""


def register_analyzer(ext: str) -> Callable[[type], type]:
    """Decorator: registers a :class:`LanguageAnalyzer` class by extension.

    Usage::

        @register_analyzer(".py")
        class PythonAstAnalyzer:
            capabilities = PYTHON_CAPABILITIES
            def capture(self, source, *, path, root, from_package, allowed, collector): ...

    Returns the class unchanged. Duplicate registration raises
    :class:`ValueError` — extension conflicts are a wiring bug we want
    surfaced at import time, not at first index run.
    """

    def deco(cls: type) -> type:
        if ext in analyzer_registry:
            raise ValueError(f"analyzer for {ext!r} already registered")
        analyzer_registry[ext] = cls()
        return cls

    return deco


def language_capabilities(ext: str) -> LanguageCapabilities | None:
    """Declared capability matrix for ``ext``, or ``None`` if unregistered."""
    analyzer = analyzer_registry.get(ext)
    return analyzer.capabilities if analyzer is not None else None


@register_analyzer(".py")
class PythonAstAnalyzer:
    """CPython-ast syntactic backend — wraps the existing emitters.

    ``capture_imports`` always runs regardless of ``allowed``: it
    populates ``collector.aliases``, the resolver's source of truth.
    IMPORTS *rows* are filtered downstream by ``ReferenceCaptureStage``
    when ``"imports"`` isn't allowed (spec §5.3).
    """

    capabilities: ClassVar[LanguageCapabilities] = PYTHON_CAPABILITIES

    def capture(
        self,
        source: str,
        *,
        path: str,
        root: Path,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None:
        # Deferred imports — chunkers pull in the whole chunker stack,
        # irrelevant until the first actual capture call.
        from pydocs_mcp.extraction.strategies.chunkers import _module_from_path
        from pydocs_mcp.extraction.strategies.references import capture_imports

        tree = ast.parse(source)
        module_qname = _module_from_path(path, root)
        capture_imports(
            tree.body,
            from_package=from_package,
            module_qname=module_qname,
            collector=collector,
        )
        if "calls" in allowed or "inherits" in allowed:
            self._capture_definitions(
                tree.body,
                module_qname=module_qname,
                from_package=from_package,
                allowed=allowed,
                collector=collector,
            )

    def _capture_definitions(
        self,
        body: list[ast.stmt],
        *,
        module_qname: str,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None:
        from pydocs_mcp.extraction.strategies.references import capture_calls

        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and "calls" in allowed:
                capture_calls(
                    stmt.body,
                    from_package=from_package,
                    from_node_id=f"{module_qname}.{stmt.name}",
                    collector=collector,
                )
            elif isinstance(stmt, ast.ClassDef):
                self._capture_class(
                    stmt,
                    class_qname=f"{module_qname}.{stmt.name}",
                    from_package=from_package,
                    allowed=allowed,
                    collector=collector,
                )

    def _capture_class(
        self,
        stmt: ast.ClassDef,
        *,
        class_qname: str,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None:
        from pydocs_mcp.extraction.strategies.references import (
            capture_calls,
            capture_inherits,
            capture_self_attribute_types,
        )

        if "inherits" in allowed:
            capture_inherits(
                list(stmt.bases),
                from_package=from_package,
                class_qname=class_qname,
                collector=collector,
            )
        if "calls" not in allowed:
            return
        # self.X.Y inference: learn attribute types from this class FIRST,
        # then walk every method body for calls. The capture helper
        # re-iterates ``cls.body`` internally — a few dozen extra
        # iterations per class, negligible beside per-method ast.walk.
        collector.record_class_attrs(class_qname, capture_self_attribute_types(stmt))
        for m in stmt.body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                capture_calls(
                    m.body,
                    from_package=from_package,
                    from_node_id=f"{class_qname}.{m.name}",
                    collector=collector,
                )


@register_analyzer(".md")
class MarkdownMentionsAnalyzer:
    """Regex-fuzzy MENTIONS for backtick-quoted dotted names.

    Gated on ``"mentions" in allowed`` because the shipped default omits
    MENTIONS (lower-precision than AST capture, opt-in per spec §5.3).
    """

    capabilities: ClassVar[LanguageCapabilities] = MARKDOWN_CAPABILITIES

    def capture(
        self,
        source: str,
        *,
        path: str,
        root: Path,
        from_package: str,
        allowed: frozenset[str],
        collector: ReferenceCollector,
    ) -> None:
        if "mentions" not in allowed:
            return
        # WORKAROUND: markdown identity uses the suffix-preserving doc-path
        # rule (HeadingMarkdownChunker._module_from_doc_path ->
        # "pkg.README.md"), NOT the .py qname rule (_module_from_path ->
        # "pkg.README"). Using the wrong rule here produces MENTIONS rows
        # whose from_node_id matches no persisted tree node / chunk, so the
        # edges can never be joined back to their source.
        from pydocs_mcp.extraction.strategies.chunkers._shared import (
            _module_from_doc_path,
        )
        from pydocs_mcp.extraction.strategies.references import capture_mentions

        capture_mentions(
            source,
            from_package=from_package,
            from_node_id=_module_from_doc_path(path, root),
            collector=collector,
        )


__all__ = (
    "MARKDOWN_CAPABILITIES",
    "PYTHON_CAPABILITIES",
    "LanguageAnalyzer",
    "LanguageCapabilities",
    "MarkdownMentionsAnalyzer",
    "PythonAstAnalyzer",
    "analyzer_registry",
    "language_capabilities",
    "register_analyzer",
)
