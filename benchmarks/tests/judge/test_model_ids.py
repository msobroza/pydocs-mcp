"""OpenRouter model ids, read one way: the served spelling of a pin, and nothing more."""

from __future__ import annotations

import pytest

from pydocs_eval.judge.model_ids import (
    base_slug,
    is_batch_model,
    model_family,
    served_model_matches,
    variants_of,
)


@pytest.mark.parametrize(
    ("pinned", "served"),
    [
        ("jev-1.13", "jev-1.13"),
        ("jev-1.13", "typesafe/jev-1.13"),
        # OpenRouter's System One reference: a request for jev-1.13 is answered so.
        ("jev-1.13", "typesafe/jev-1.13-20260917"),
        ("openai/gpt-6-luna", "openai/gpt-6-luna"),
        ("openai/gpt-6-luna", "openai/gpt-6-luna-2026-09-01"),
        # The Batch API answers with the base slug; the :batch entry is its own.
        ("anthropic/claude-opus-5.5:batch", "anthropic/claude-opus-5.5"),
        ("anthropic/claude-opus-5.5:batch", "anthropic/claude-opus-5.5:batch"),
        ("anthropic/claude-opus-5.5:batch", "anthropic/claude-opus-5.5-20260901"),
    ],
)
def test_the_pinned_model_as_openrouter_names_it_matches(pinned: str, served: str) -> None:
    assert served_model_matches(pinned, served)


@pytest.mark.parametrize(
    ("pinned", "served"),
    [
        ("jev-1.13", "typesafe/jev-1.14-20261001"),
        ("jev-1.13", "typesafe/jev-1.13.1"),
        ("jev-1.13", "typesafe/jev-1.130"),
        # A bare System One id is served under typesafe/ only.
        ("jev-1.13", "evilcorp/jev-1.13"),
        ("jev-1.13", "qwen/jev-1.13"),
        # A variant the pin never asked for is another endpoint's model.
        ("jev-1.13", "typesafe/jev-1.13:free"),
        ("openai/gpt-6-luna", "openai/gpt-6-luna:nitro"),
        ("openai/gpt-6-luna", "openai/gpt-6-luna-mini"),
        ("openai/gpt-6-luna", "gpt-6-luna"),
        ("anthropic/claude-opus-5.5:batch", "anthropic/claude-opus-5.6"),
        ("anthropic/claude-opus-5.5:batch", "anthropic/claude-sonnet-5"),
    ],
)
def test_any_other_model_does_not_match(pinned: str, served: str) -> None:
    assert not served_model_matches(pinned, served)


def test_an_id_splits_into_its_base_slug_and_variants() -> None:
    assert base_slug("anthropic/claude-opus-5.5:batch") == "anthropic/claude-opus-5.5"
    assert base_slug("jev-1.13") == "jev-1.13"
    assert variants_of("poolside/laguna-s-2.1:free:nitro") == frozenset({"free", "nitro"})
    assert variants_of("openai/gpt-6-luna") == frozenset()


def test_only_a_batch_variant_selects_the_batch_api() -> None:
    assert is_batch_model("openai/gpt-6-astra:batch")
    assert is_batch_model("openai/gpt-6-astra:batch:nitro")
    assert not is_batch_model("openai/gpt-6-luna")
    assert not is_batch_model("openai/batch-labeller")


def test_a_family_is_the_vendor_and_a_bare_system_one_id_is_typesafe() -> None:
    assert model_family("anthropic/claude-opus-5.5:batch") == "anthropic"
    assert model_family("jev-1.13") == "typesafe"
