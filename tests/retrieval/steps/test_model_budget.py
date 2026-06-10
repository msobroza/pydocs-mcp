"""Model -> tree-reasoning TOKEN budget + tiktoken token counting.

The LLM tree-reasoning budget is derived from the configured model's context
window (in tokens) so it auto-scales across models, and the tree is measured
in REAL tokens (tiktoken) — not words — so the prompt can never exceed the
model's context window (which it did under the old word×ratio heuristic).
"""

from __future__ import annotations

from pydocs_mcp.retrieval.llm_clients.model_budget import (
    context_window_tokens,
    count_tokens,
    derive_max_tree_tokens,
)


# ── context_window_tokens ─────────────────────────────────────────────────


def test_known_model_window() -> None:
    assert context_window_tokens("gpt-4o-mini") == 128_000


def test_prefix_match_ignores_dated_suffix() -> None:
    assert context_window_tokens("gpt-4o-mini-2024-07-18") == 128_000


def test_longest_prefix_wins() -> None:
    assert context_window_tokens("gpt-4o-mini") == 128_000
    assert context_window_tokens("gpt-4o") == 128_000
    assert context_window_tokens("gpt-4") == 8_192
    assert context_window_tokens("gpt-4-0613") == 8_192


def test_large_context_model() -> None:
    assert context_window_tokens("gpt-4.1") == 1_000_000


def test_unknown_model_falls_back_conservatively() -> None:
    assert context_window_tokens("some-unknown-model") == 16_000
    assert context_window_tokens("") == 16_000


# ── derive_max_tree_tokens ────────────────────────────────────────────────


def test_gpt4o_mini_budget_is_fraction_of_window() -> None:
    # 128_000 * 0.75 = 96_000 tokens, leaving headroom for prompt + response.
    assert derive_max_tree_tokens("gpt-4o-mini") == 96_000


def test_budget_well_under_context_window() -> None:
    # The whole point: the tree budget must be strictly below the window so the
    # prompt (tree + template + query) + the response still fit.
    for m in ("gpt-4o-mini", "gpt-4.1", "gpt-4", "o1"):
        assert derive_max_tree_tokens(m) < context_window_tokens(m)


def test_budget_scales_with_window() -> None:
    assert (
        derive_max_tree_tokens("gpt-4.1")
        > derive_max_tree_tokens("gpt-4o-mini")
        > derive_max_tree_tokens("gpt-4")
    )


def test_unknown_budget_positive_and_smaller_than_default_model() -> None:
    b = derive_max_tree_tokens("some-unknown-model")
    assert b >= 1
    assert b < derive_max_tree_tokens("gpt-4o-mini")


# ── count_tokens (real tiktoken) ──────────────────────────────────────────


def test_count_tokens_known_model() -> None:
    n = count_tokens("def login(req: Request) -> Response:", "gpt-4o-mini")
    assert n > 0
    # Real tokenization is far denser than whitespace words for code: this
    # 5-word signature is ~9 tokens (the bug the word budget missed).
    assert n > len(["def", "login(req:", "Request)", "->", "Response:"])


def test_count_tokens_unknown_model_uses_fallback_no_crash() -> None:
    # Unknown model -> fallback encoding, never raises.
    n = count_tokens("hello world", "fake-llm-model")
    assert n > 0


def test_count_tokens_empty() -> None:
    assert count_tokens("", "gpt-4o-mini") == 0
