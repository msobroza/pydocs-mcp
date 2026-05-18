"""Reference capture + custom AST→str walker (spec §7.1).

Two surfaces:

- :func:`canonical_dotted` — normalises an AST expression to its dotted
  form (``a.b.c``) or ``None`` for shapes the resolver can't handle.
  Replaces ``ast.unparse`` because CPython's unparse output is not
  version-stable (3.11 emits ``a.b``; 3.13 may emit ``(a).b`` for
  subscripted bases), and the reference table is PK'd on the output.

- :class:`ReferenceCollector` — callable threaded into chunker
  ``build_tree(..., ref_collector=collector)`` to receive
  :class:`NodeReference` candidates as the chunker walks the AST. The
  resolver runs as a separate pass (see :class:`ReferenceResolver`).

Sub-PR #5b ships Python-only capture. Markdown / notebook chunkers do
NOT emit references (per spec Decision 7); MENTIONS lands in #5c.
"""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.storage.node_reference import NodeReference

log = logging.getLogger("pydocs-mcp")

# Defensive cap: pathologically nested expressions (200+ levels) would
# blow up the `node_references` row size. Truncate with an ellipsis to
# preserve the prefix and signal truncation to inspectors.
_MAX_TO_NAME_CHARS = 256

# Backtick-quoted dotted names with AT LEAST one dot (e.g. ``pkg.helpers.compute``).
# Bare backtick-quoted identifiers (``compute``, ``foo``) are intentionally
# excluded — they're variable names / one-word code snippets and would flood
# the graph with noise (sub-PR #5c, §5.3 + spec Decision 1).
_MENTION_RE = re.compile(r"`([a-zA-Z_][\w]*(?:\.[a-zA-Z_][\w]*)+)`")


def canonical_dotted(node: ast.expr) -> str | None:
    """AST→str without ast.unparse. Returns dotted form or None.

    Walks ``Attribute(Attribute(...))`` chains until the root must be a
    bare ``Name`` for the result to be a valid dotted target. Anything
    else (Call, Subscript, Lambda, BinOp, etc.) returns ``None`` and is
    silently dropped by the collector — counted in a future metric, never
    written.
    """
    parts: list[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return None
    result = ".".join(reversed(parts))
    if len(result) > _MAX_TO_NAME_CHARS:
        return result[: _MAX_TO_NAME_CHARS - 1] + "…"  # trailing ellipsis
    return result


@dataclass
class ReferenceCollector:
    """Mutable buffer of (unresolved) NodeReference candidates.

    Threaded into ``AstPythonChunker.build_tree(..., ref_collector=...)``
    so the chunker emits one candidate per call/import/inherit site.
    ``to_node_id`` is None for every emitted ref — the resolver flips
    that field in a post-pass. Alias info is also captured here so a
    second pass can use it (the resolver merges per-module alias tables
    from this collector).
    """

    refs: list[NodeReference] = field(default_factory=list)
    # Per-module alias table: module_qname → {alias_name: dotted_target}.
    # Populated by `capture_imports` (and used by the resolver).
    aliases: dict[str, dict[str, str]] = field(default_factory=dict)
    # Per-class attribute-type table: class_qname → {attr_name: type_qname}.
    # Populated by `capture_self_attribute_types` and consumed by the
    # resolver's Rule 0 to rewrite ``self.X.Y`` → ``<type>.Y`` before the
    # Rule 5 short-circuit. Sibling of ``aliases`` (same shape, different
    # axis: aliases are per-module, attribute types are per-class).
    class_attribute_types: dict[str, dict[str, str]] = field(default_factory=dict)

    def add(self, ref: NodeReference) -> None:
        self.refs.append(ref)

    def record_class_attrs(self, class_qname: str, attrs: dict[str, str]) -> None:
        """Record inferred ``self.X = ...`` attribute types for one class.

        Empty input is a no-op so callers don't have to guard. Otherwise
        store under the class's fully-qualified name — the resolver keys
        on that to find the table for a given from_node_id.
        """
        if attrs:
            self.class_attribute_types[class_qname] = attrs


def capture_calls(
    body: list[ast.stmt],
    *,
    from_package: str,
    from_node_id: str,
    collector: ReferenceCollector,
) -> None:
    """Walk a function/method body's AST, emit CALLS candidates.

    Per-call try/except keeps one malformed ast.Call from aborting the
    whole walk (spec §7.1 — per-call error containment).
    """
    for node in ast.walk(ast.Module(body=body, type_ignores=[])):
        if not isinstance(node, ast.Call):
            continue
        try:
            to_name = canonical_dotted(node.func)
        except Exception as exc:  # noqa: BLE001 -- defensive per-call
            log.debug("canonical_dotted failed on %r: %s", node.func, exc)
            continue
        if to_name is None:
            continue  # dropped — non-dotted shape
        collector.add(NodeReference(
            from_package=from_package,
            from_node_id=from_node_id,
            to_name=to_name,
            to_node_id=None,
            kind=ReferenceKind.CALLS,
        ))


def capture_imports(
    body: list[ast.stmt],
    *,
    from_package: str,
    module_qname: str,
    collector: ReferenceCollector,
) -> None:
    """Walk module-level imports, emit IMPORTS candidates AND populate the
    per-module alias table (spec §7.2 — alias awareness Rule A).

    ``from X import Y as Z`` records ``aliases[module][Z] = "X.Y"``.
    ``import X as Z`` records ``aliases[module][Z] = "X"``.
    Function-scoped imports are ignored — only module-top-level imports
    feed the alias table.
    """
    aliases = collector.aliases.setdefault(module_qname, {})
    for stmt in body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                to_name = alias.name
                collector.add(NodeReference(
                    from_package=from_package,
                    from_node_id=module_qname,
                    to_name=to_name,
                    to_node_id=None,
                    kind=ReferenceKind.IMPORTS,
                ))
                if alias.asname:
                    aliases[alias.asname] = to_name
        elif isinstance(stmt, ast.ImportFrom):
            module = stmt.module or ""
            for alias in stmt.names:
                to_name = f"{module}.{alias.name}" if module else alias.name
                collector.add(NodeReference(
                    from_package=from_package,
                    from_node_id=module_qname,
                    to_name=to_name,
                    to_node_id=None,
                    kind=ReferenceKind.IMPORTS,
                ))
                alias_key = alias.asname or alias.name
                aliases[alias_key] = to_name


def capture_inherits(
    bases: list[ast.expr],
    *,
    from_package: str,
    class_qname: str,
    collector: ReferenceCollector,
) -> None:
    """Emit one INHERITS edge per base class (spec §7.1)."""
    for base in bases:
        try:
            to_name = canonical_dotted(base)
        except Exception as exc:  # noqa: BLE001 -- defensive per-base
            log.debug("canonical_dotted failed on base %r: %s", base, exc)
            continue
        if to_name is None:
            continue
        collector.add(NodeReference(
            from_package=from_package,
            from_node_id=class_qname,
            to_name=to_name,
            to_node_id=None,
            kind=ReferenceKind.INHERITS,
        ))


def capture_self_attribute_types(cls: ast.ClassDef) -> dict[str, str]:
    """Infer ``self.X`` attribute types from a class definition.

    Walks two sources of type information:

    1. **Class-body annotations** — the dataclass/Protocol pattern:

       .. code-block:: python

           @dataclass
           class Service:
               cache:  redis.Cache             # → cache  → redis.Cache
               runner: Pipeline = build()      # → runner → Pipeline

    2. **__init__ body** — the manual constructor pattern:

       - **B**: ``self.client = ApiClient()``       → ``ApiClient``
       - **C**: ``self.cache  = redis.Cache()``     → ``redis.Cache``
       - **D**: ``self.runner: Pipeline = build()`` → ``Pipeline``
       - **E**: ``self.queue:  asyncio.Queue = q``  → ``asyncio.Queue``

    Pattern A (``self.x = x`` — pass-through, no type info) is skipped.
    Only ``__init__`` is walked for assignments; other methods could
    legitimately rebind attributes to local helpers and we'd rather
    miss those than introduce noise.

    Conflict policy: annotation wins. The order is class-body annotations
    first (most authoritative — that's the type system speaking), then
    ``__init__`` bare-call assignments (lowest priority), then
    ``__init__`` annotated assignments (highest priority — explicit at
    the assignment site).

    Returns an empty dict when no pattern matches; the resolver treats
    absence the same as "no info" and falls back to Rule 5.
    """
    class_body_types = _class_body_annotations(cls)
    init_types = _init_body_attribute_types(cls)

    # Annotation wins on conflict: class-body annotations are the type
    # system's declaration; __init__ assignments are runtime intent.
    # Apply class-body LAST so it overrides any __init__ bare-call entry
    # for the same attribute (e.g. dataclass field that __init__ also
    # explicitly assigns).
    result: dict[str, str] = {}
    result.update(init_types)
    result.update(class_body_types)
    return result


def _class_body_annotations(cls: ast.ClassDef) -> dict[str, str]:
    """Return ``{attr: type_qname}`` for AnnAssigns at the class body level.

    Catches the dataclass/Protocol pattern where field types are declared
    directly on the class body. Targets must be plain ``Name`` nodes
    (excludes nested attribute targets like ``self.X``, which only appear
    inside methods). Subscripted annotations (e.g. ``tuple[Chunk, ...]``,
    ``Callable[[], UnitOfWork]``) silently drop because ``canonical_dotted``
    rejects them — that's correct, they don't name a single class qname.
    """
    result: dict[str, str] = {}
    for stmt in cls.body:
        if not isinstance(stmt, ast.AnnAssign):
            continue
        target = stmt.target
        if not isinstance(target, ast.Name):
            continue
        type_name = canonical_dotted(stmt.annotation)
        if type_name is None:
            continue
        result[target.id] = type_name
    return result


def _init_body_attribute_types(cls: ast.ClassDef) -> dict[str, str]:
    """Return ``{attr: type_qname}`` for self.X assignments inside __init__.

    Recognises Patterns B/C (bare constructor call) and D/E (annotated
    assignment). Annotation wins when the same attr appears as both.
    """
    init = _find_init(cls)
    if init is None:
        return {}

    bare: dict[str, str] = {}
    annotated: dict[str, str] = {}

    for stmt in init.body:
        if isinstance(stmt, ast.AnnAssign):
            attr = _self_attr_name(stmt.target)
            if attr is None:
                continue
            type_name = canonical_dotted(stmt.annotation)
            if type_name is None:
                continue
            annotated[attr] = type_name
        elif isinstance(stmt, ast.Assign):
            if not isinstance(stmt.value, ast.Call):
                continue
            type_name = canonical_dotted(stmt.value.func)
            if type_name is None:
                continue
            for target in stmt.targets:
                attr = _self_attr_name(target)
                if attr is None:
                    continue
                bare[attr] = type_name

    bare.update(annotated)
    return bare


def _find_init(cls: ast.ClassDef) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the class's ``__init__`` method node, or None."""
    for stmt in cls.body:
        if (
            isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))
            and stmt.name == "__init__"
        ):
            return stmt
    return None


def _self_attr_name(target: ast.expr) -> str | None:
    """Return ``X`` if ``target`` is the AST ``self.X``, else None."""
    if not isinstance(target, ast.Attribute):
        return None
    if not isinstance(target.value, ast.Name) or target.value.id != "self":
        return None
    return target.attr


def capture_mentions(
    text: str,
    *,
    from_package: str,
    from_node_id: str,
    collector: ReferenceCollector,
) -> None:
    """Scan markdown ``text`` for backtick-quoted dotted names, emit MENTIONS.

    Sub-PR #5c — the regex-fuzzy counterpart to the three AST-precise
    captures above. Only names with AT LEAST one dot are emitted
    (``pkg.helpers.compute`` yes; bare ``compute`` no) — the dot
    requirement filters out variable names and one-word code snippets
    that would otherwise flood the reference graph.

    Per-chunk dedupe: a local ``seen: set[str]`` ensures the same dotted
    name appearing multiple times in one chunk yields ONE edge, not N.
    Cross-chunk dedupe is intentionally not done here — different chunks
    may legitimately mention the same target and the resolver / renderer
    decides how to surface that.
    """
    seen: set[str] = set()
    for match in _MENTION_RE.finditer(text):
        to_name = match.group(1)
        if to_name in seen:
            continue
        seen.add(to_name)
        if len(to_name) > _MAX_TO_NAME_CHARS:
            to_name = to_name[: _MAX_TO_NAME_CHARS - 1] + "…"
        collector.add(NodeReference(
            from_package=from_package,
            from_node_id=from_node_id,
            to_name=to_name,
            to_node_id=None,
            kind=ReferenceKind.MENTIONS,
        ))
