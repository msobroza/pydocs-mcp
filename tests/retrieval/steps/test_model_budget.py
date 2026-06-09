"""Model -> tree-reasoning word budget derivation.

The LLM tree-reasoning budget (max_tree_words) is derived from the configured
model's context window so it auto-scales across models instead of a single
hand-tuned constant.
"""

from __future__ import annotations

from pydocs_mcp.retrieval.llm_clients.model_budget import (
    context_window_tokens,
    derive_max_tree_words,
)


# ── context_window_tokens ─────────────────────────────────────────────────


def test_known_model_window() -> None:
    assert context_window_tokens("gpt-4o-mini") == 128_000


def test_prefix_match_ignores_dated_suffix() -> None:
    # OpenAI appends dated suffixes; match by prefix.
    assert context_window_tokens("gpt-4o-mini-2024-07-18") == 128_000


def test_longest_prefix_wins() -> None:
    # "gpt-4o-mini" must beat both "gpt-4o" and "gpt-4".
    assert context_window_tokens("gpt-4o-mini") == 128_000
    assert context_window_tokens("gpt-4o") == 128_000
    # bare gpt-4 is the small (8K) window, NOT matched by the gpt-4o entry.
    assert context_window_tokens("gpt-4") == 8_192
    assert context_window_tokens("gpt-4-0613") == 8_192


def test_large_context_model() -> None:
    assert context_window_tokens("gpt-4.1") == 1_000_000


def test_unknown_model_falls_back_conservatively() -> None:
    assert context_window_tokens("some-unknown-model") == 16_000
    assert context_window_tokens("") == 16_000


# ── derive_max_tree_words ─────────────────────────────────────────────────


def test_gpt4o_mini_budget_near_prior_default() -> None:
    # Calibrated to land near the previous hand-tuned 60K constant.
    w = derive_max_tree_words("gpt-4o-mini")
    assert 50_000 <= w <= 65_000


def test_budget_scales_with_window() -> None:
    assert (
        derive_max_tree_words("gpt-4.1")
        > derive_max_tree_words("gpt-4o-mini")
        > derive_max_tree_words("gpt-4")
    )


def test_unknown_budget_is_positive_and_smaller_than_default_model() -> None:
    w = derive_max_tree_words("some-unknown-model")
    assert w >= 1
    assert w < derive_max_tree_words("gpt-4o-mini")
