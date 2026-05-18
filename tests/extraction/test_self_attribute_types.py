"""capture_self_attribute_types — self.X.Y type inference (spec §7.x follow-up).

Walks a ``class C: def __init__(self, ...)`` body and emits a mapping
``{attr_name: type_qname}`` for four locked patterns (B/C/D/E). Annotated
assignments win on conflict with bare-Call assignments. The dict feeds
the resolver's Rule 0, which rewrites ``self.X.Y`` to ``<type>.Y`` before
Rule 5's short-circuit (so calls like ``self.client.fetch`` can resolve
when ``self.client`` is typed at construction).
"""
from __future__ import annotations

import ast

from pydocs_mcp.extraction.strategies.references import (
    ReferenceCollector,
    capture_self_attribute_types,
)


def _parse_class(source: str) -> ast.ClassDef:
    """Parse a single top-level ``class C: ...`` and return its ClassDef."""
    module = ast.parse(source)
    for stmt in module.body:
        if isinstance(stmt, ast.ClassDef):
            return stmt
    raise AssertionError("test source must declare a top-level class")


def test_pattern_b_bare_constructor_call():
    """Pattern B — ``self.client = ApiClient()`` records ``client → ApiClient``."""
    cls = _parse_class(
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.client = ApiClient()\n"
    )
    assert capture_self_attribute_types(cls) == {"client": "ApiClient"}


def test_pattern_c_dotted_constructor_call():
    """Pattern C — ``self.cache = redis.Cache()`` records ``cache → redis.Cache``."""
    cls = _parse_class(
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.cache = redis.Cache()\n"
    )
    assert capture_self_attribute_types(cls) == {"cache": "redis.Cache"}


def test_pattern_d_annotated_bare_assignment():
    """Pattern D — ``self.runner: Pipeline = build()`` records ``runner → Pipeline``.

    Annotation wins: the right-hand side is ``build()``, but the typed
    annotation is the source of truth for inference.
    """
    cls = _parse_class(
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.runner: Pipeline = build()\n"
    )
    assert capture_self_attribute_types(cls) == {"runner": "Pipeline"}


def test_pattern_e_dotted_annotation():
    """Pattern E — ``self.queue: asyncio.Queue = q`` records ``queue → asyncio.Queue``."""
    cls = _parse_class(
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.queue: asyncio.Queue = q\n"
    )
    assert capture_self_attribute_types(cls) == {"queue": "asyncio.Queue"}


def test_annotation_wins_over_bare_call_on_conflict():
    """When the SAME attribute appears as both ``self.x = T()`` (Pattern B)
    and ``self.x: U = ...`` (Pattern D), annotation wins per the locked design."""
    cls = _parse_class(
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.x = OldType()\n"
        "        self.x: NewType = something()\n"
    )
    assert capture_self_attribute_types(cls) == {"x": "NewType"}


def test_skips_pattern_a_passthrough_param_assignment():
    """Pattern A — ``self.x = x`` is intentionally skipped (no type info).

    Only Pattern B/C (call) and D/E (annotation) carry usable type info.
    """
    cls = _parse_class(
        "class Foo:\n"
        "    def __init__(self, x):\n"
        "        self.x = x\n"
    )
    assert capture_self_attribute_types(cls) == {}


def test_skips_non_self_attribute_targets():
    """``other.field = T()`` is not a self-attr — must be ignored."""
    cls = _parse_class(
        "class Foo:\n"
        "    def __init__(self):\n"
        "        other.field = ApiClient()\n"
    )
    assert capture_self_attribute_types(cls) == {}


def test_skips_call_with_non_dotted_func():
    """``self.x = make()()`` — the outer Call's func is itself a Call;
    canonical_dotted returns None and we drop the assignment."""
    cls = _parse_class(
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.x = make_factory()()\n"
    )
    # make_factory() is the inner Call (its func is Name "make_factory") and
    # the outer Call's func is a Call, which canonical_dotted rejects.
    # We capture nothing for `x` because the OUTER func isn't dotted.
    assert capture_self_attribute_types(cls) == {}


def test_class_without_init_returns_empty():
    """No ``__init__`` → nothing to infer; return empty dict."""
    cls = _parse_class(
        "class Foo:\n"
        "    def other(self):\n"
        "        self.x = ApiClient()\n"
    )
    assert capture_self_attribute_types(cls) == {}


def test_only_walks_init_body_not_other_methods():
    """Assignments in non-``__init__`` methods are NOT captured.

    Constraint #1 of the locked design: ``__init__`` only.
    """
    cls = _parse_class(
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.a = One()\n"
        "    def other(self):\n"
        "        self.b = Two()\n"
    )
    assert capture_self_attribute_types(cls) == {"a": "One"}


def test_multiple_patterns_in_same_init():
    """All four patterns coexist; each records its attr → type entry."""
    cls = _parse_class(
        "class Foo:\n"
        "    def __init__(self):\n"
        "        self.b = ApiClient()\n"
        "        self.c = redis.Cache()\n"
        "        self.d: Pipeline = build()\n"
        "        self.e: asyncio.Queue = q\n"
    )
    assert capture_self_attribute_types(cls) == {
        "b": "ApiClient",
        "c": "redis.Cache",
        "d": "Pipeline",
        "e": "asyncio.Queue",
    }


def test_collector_record_class_attrs_stores_per_class():
    """ReferenceCollector.class_attribute_types accumulates per class qname."""
    collector = ReferenceCollector()
    collector.record_class_attrs("pkg.mod.Foo", {"client": "ApiClient"})
    collector.record_class_attrs("pkg.mod.Bar", {"cache": "redis.Cache"})
    assert collector.class_attribute_types == {
        "pkg.mod.Foo": {"client": "ApiClient"},
        "pkg.mod.Bar": {"cache": "redis.Cache"},
    }


def test_collector_record_skips_empty_dicts():
    """Recording an empty dict is a no-op — keeps class_attribute_types tidy."""
    collector = ReferenceCollector()
    collector.record_class_attrs("pkg.mod.Empty", {})
    assert collector.class_attribute_types == {}
