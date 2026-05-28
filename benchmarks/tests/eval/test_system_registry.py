"""Pin: the three shipped systems (``pydocs-mcp``, ``context7``,
``neuledge``) all register against ``system_registry`` and are
constructible by name; the metric/dataset/tracker registries stay
disjoint (so a system name can't shadow a tracker etc.).
"""

from __future__ import annotations

from benchmarks.eval.serialization import (
    dataset_registry,
    metric_registry,
    system_registry,
    tracker_registry,
)
from benchmarks.eval.systems import (
    Context7System,
    NeuledgeSystem,
    PydocsMcpSystem,
)


def test_all_three_systems_registered() -> None:
    names = system_registry.names()
    assert "pydocs-mcp" in names
    assert "context7" in names
    assert "neuledge" in names


def test_system_registry_is_disjoint_from_other_registries() -> None:
    # WHY: the runner threads names through three independent registries
    # (datasets, metrics, trackers, systems). A name collision would let
    # one accidentally resolve as another and produce silent misroutes.
    system_names = set(system_registry.names())
    assert system_names.isdisjoint(dataset_registry.names())
    assert system_names.isdisjoint(metric_registry.names())
    assert system_names.isdisjoint(tracker_registry.names())


def test_pydocs_system_constructs_by_name() -> None:
    system = system_registry.build("pydocs-mcp")
    assert system.name == "pydocs-mcp"


def test_context7_system_constructs_by_name() -> None:
    # WHY: construction must not touch the network — the HTTP session is
    # built lazily inside ``index`` / ``search``. Asserting bare
    # construction works lets the runner enumerate available systems
    # without doing I/O.
    system = system_registry.build("context7")
    assert system.name == "context7"


def test_neuledge_system_constructs_by_name() -> None:
    system = system_registry.build("neuledge")
    assert system.name == "neuledge"
