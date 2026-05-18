"""ReferenceResolver Rule 0 — self.X.Y inference via class_attribute_types.

Rule 0 runs BEFORE Rule 5's short-circuit: when the from_node_id is a
method of a class whose ``self.X`` attribute type was captured at
``__init__`` time, the resolver rewrites ``self.X.Y`` to ``<type>.Y``
and then proceeds through the normal rules (A → B/F20 → C → D → E).
"""
from __future__ import annotations

from pydocs_mcp.extraction.reference_kind import ReferenceKind
from pydocs_mcp.extraction.strategies.reference_resolver import ReferenceResolver
from pydocs_mcp.storage.node_reference import NodeReference


def _ref(**kw) -> NodeReference:
    base = dict(
        from_package="pkg",
        from_node_id="pkg.mod.Cls.method",
        to_name="x",
        to_node_id=None,
        kind=ReferenceKind.CALLS,
    )
    base.update(kw)
    return NodeReference(**base)


def test_rule_0_self_attr_call_resolves_via_inferred_type():
    """``self.client.fetch`` resolves to ``pkg.api.ApiClient.fetch`` when
    ``self.client`` was typed as ``ApiClient`` at __init__ (Pattern B) AND
    ``ApiClient`` rewrites via the module's import alias."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.api.ApiClient.fetch"},
        aliases={"pkg.mod": {"ApiClient": "pkg.api.ApiClient"}},
        class_attribute_types={"pkg.mod.Cls": {"client": "ApiClient"}},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.mod.Cls.method", to_name="self.client.fetch"),
    ])
    assert out[0].to_node_id == "pkg.api.ApiClient.fetch"


def test_rule_0_dotted_type_annotation_resolves():
    """``self.cache.get`` resolves to ``redis.Cache.get`` when ``self.cache``
    was typed as ``redis.Cache`` at __init__ (Pattern C)."""
    resolver = ReferenceResolver(
        qname_universe={"redis.Cache.get"},
        aliases={},
        class_attribute_types={"pkg.mod.Cls": {"cache": "redis.Cache"}},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.mod.Cls.method", to_name="self.cache.get"),
    ])
    assert out[0].to_node_id == "redis.Cache.get"


def test_rule_0_unknown_attr_falls_back_to_rule_5_short_circuit():
    """If the class is known but the attr isn't, fall through to Rule 5 —
    no inference, to_node_id stays None, to_name preserved verbatim."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.api.ApiClient.fetch"},
        aliases={},
        class_attribute_types={"pkg.mod.Cls": {"client": "ApiClient"}},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.mod.Cls.method", to_name="self.unknown_attr.x"),
    ])
    assert out[0].to_node_id is None
    assert out[0].to_name == "self.unknown_attr.x"


def test_rule_0_no_class_attribute_types_falls_through_to_rule_5():
    """Empty ``class_attribute_types`` preserves the pre-#5d behavior:
    every ``self.*`` ref short-circuits at Rule 5 with to_node_id=None."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.api.ApiClient.fetch"},
        aliases={},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.mod.Cls.method", to_name="self.client.fetch"),
    ])
    assert out[0].to_node_id is None


def test_rule_0_module_level_function_does_not_trigger_inference():
    """``from_node_id="pkg.mod.fn"`` (a free function, not a method) —
    there's no enclosing class, so Rule 0 has nothing to look up and
    Rule 5 short-circuits self.* normally.

    Heuristic: if the second-to-last dotted segment doesn't start uppercase,
    we assume it's a module, not a class.
    """
    resolver = ReferenceResolver(
        qname_universe={"pkg.api.ApiClient.fetch"},
        aliases={},
        class_attribute_types={"pkg.mod.fn": {"client": "ApiClient"}},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.mod.fn", to_name="self.client.fetch"),
    ])
    assert out[0].to_node_id is None


def test_rule_0_self_attr_call_no_trailing_method_resolves_to_type():
    """``self.cache(...)`` (calling the attribute directly — the typed
    object is itself callable) resolves to the type qname.

    AST capture sees ``Call(Attribute(Name("self"), "cache"), ...)``;
    canonical_dotted gives ``self.cache``; Rule 0 rewrites to the type."""
    resolver = ReferenceResolver(
        qname_universe={"redis.Cache"},
        aliases={},
        class_attribute_types={"pkg.mod.Cls": {"cache": "redis.Cache"}},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.mod.Cls.method", to_name="self.cache"),
    ])
    assert out[0].to_node_id == "redis.Cache"


def test_rule_0_inferred_type_then_rule_c_suffix_match():
    """After Rule 0 rewrites ``self.runner.start`` → ``Pipeline.start``,
    Rule C resolves the bare ``Pipeline.start`` against the from_package."""
    resolver = ReferenceResolver(
        qname_universe={"pkg.runners.Pipeline.start"},
        aliases={},
        class_attribute_types={"pkg.app.Cls": {"runner": "Pipeline"}},
    )
    out = resolver.resolve([
        _ref(
            from_package="pkg",
            from_node_id="pkg.app.Cls.method",
            to_name="self.runner.start",
        ),
    ])
    assert out[0].to_node_id == "pkg.runners.Pipeline.start"


def test_rule_0_self_dot_with_no_attr_falls_through():
    """``to_name = "self."`` (an empty attr after self) — Rule 0 finds
    no head match and Rule 5 short-circuits. Defensive: not a shape the
    capturer should emit, but the resolver stays correct under it."""
    resolver = ReferenceResolver(
        qname_universe={"any.thing"},
        aliases={},
        class_attribute_types={"pkg.mod.Cls": {"client": "X"}},
    )
    out = resolver.resolve([
        _ref(from_node_id="pkg.mod.Cls.method", to_name="self."),
    ])
    assert out[0].to_node_id is None
