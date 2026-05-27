from pydocs_mcp.retrieval.route_predicates import (
    PredicateRegistry,
    default_predicate_registry,
)


def test_copy_isolates_registrations():
    reg_copy = default_predicate_registry.copy()
    reg_copy.register("test_isolation_predicate", lambda state, ctx: True)
    assert "test_isolation_predicate" in reg_copy.names()
    assert "test_isolation_predicate" not in default_predicate_registry.names()


def test_unregister_is_idempotent():
    reg = PredicateRegistry()
    reg.register("p1", lambda s, c: True)
    reg.unregister("p1")
    reg.unregister("p1")  # idempotent — no error
    assert "p1" not in reg.names()
