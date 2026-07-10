"""Pin _Registry: decorator returns class unchanged; build() instantiates by
name; unknown name raises KeyError listing the registered names so callers
can fix the typo without grepping the codebase."""

from __future__ import annotations

import pytest
from pydocs_eval.serialization import (
    _Registry,
    dataset_registry,
    metric_registry,
    system_registry,
    tracker_registry,
)


class _Fake:
    def __init__(self, *, value: int = 0) -> None:
        self.value = value


def test_register_returns_class_unchanged() -> None:
    registry: _Registry[_Fake] = _Registry()

    @registry.register("fake")
    class Subclass(_Fake):
        pass

    # WHY: decorator must not wrap; metaclass + isinstance checks elsewhere
    # depend on the original class identity.
    assert issubclass(Subclass, _Fake)


def test_build_instantiates_with_kwargs() -> None:
    registry: _Registry[_Fake] = _Registry()
    registry.register("fake")(_Fake)

    instance = registry.build("fake", value=42)

    assert isinstance(instance, _Fake)
    assert instance.value == 42


def test_build_unknown_name_raises_with_available_names() -> None:
    registry: _Registry[_Fake] = _Registry()
    registry.register("alpha")(_Fake)
    registry.register("beta")(_Fake)

    with pytest.raises(KeyError) as excinfo:
        registry.build("gamma")

    message = str(excinfo.value)
    assert "gamma" in message
    assert "alpha" in message
    assert "beta" in message


def test_module_level_registries_are_distinct_instances() -> None:
    # WHY: a shared registry would let a metric mask a dataset by name; spec
    # §4.4 requires four independent namespaces.
    assert dataset_registry is not metric_registry
    assert metric_registry is not tracker_registry
    assert tracker_registry is not system_registry
    assert system_registry is not dataset_registry
