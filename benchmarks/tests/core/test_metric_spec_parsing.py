"""Pin ``sweep_support._build_metric`` — how a ``--metrics`` string becomes a Metric.

Registration alone is NOT enough for a ``<name>@<int>`` metric: the registry's
``build`` takes no kwargs, so ``k`` has to be parsed from the spec. That parse
lives in one table, and these pins cover every branch of it — the fixed names,
each parameterised name, the registry fall-through, and the loud error a
malformed ``k`` must raise instead of a bare KeyError.
"""

from __future__ import annotations

import pytest

from pydocs_eval.metrics import MRR, HitAtK, MAPAtK, NDCGAtK, PassAt1Needle, RecallAtK
from pydocs_eval.sweep_support import _build_metric


def test_fixed_names_resolve_to_their_singletons() -> None:
    assert isinstance(_build_metric("mrr"), MRR)
    assert isinstance(_build_metric("pass@1-needle"), PassAt1Needle)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("recall@5", RecallAtK(k=5)),
        ("ndcg@10", NDCGAtK(k=10)),
        ("hit@1", HitAtK(k=1)),
        ("hit@20", HitAtK(k=20)),
        ("map@5", MAPAtK(k=5)),
    ],
)
def test_parameterised_specs_carry_their_k(spec: str, expected: object) -> None:
    built = _build_metric(spec)
    assert built == expected
    assert built.name == spec


def test_registry_fallthrough_still_resolves_single_key_metrics() -> None:
    # ``coverage`` takes no k, so it resolves through the registry — the
    # fall-through the parameterised table must not swallow.
    assert _build_metric("coverage").name == "coverage"


@pytest.mark.parametrize("spec", ["hit@", "map@x", "recall@five", "ndcg@1.5"])
def test_a_malformed_k_names_the_offending_spec(spec: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        _build_metric(spec)
    message = str(excinfo.value)
    assert spec in message
    assert "<int>" in message
