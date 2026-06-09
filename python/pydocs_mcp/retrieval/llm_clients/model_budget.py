"""Map a configured LLM model to a tree-reasoning word budget.

The ``llm_tree_reasoning`` step serializes the project tree into a prompt, so
the budget must fit the model's context window. Rather than hand-tune one
constant, derive the budget from the model's context window so it auto-scales
across models — a 1M-context model gets a far larger tree, an 8K model a
smaller one. There is no other model-metadata source in the repo today; this
module is the single home for it (next to ``build_llm_client``).
"""

from __future__ import annotations

# Context windows (tokens), prefix-matched against the configured model_name.
# Longest matching prefix wins, so "gpt-4o-mini" resolves before "gpt-4o" or
# "gpt-4". OpenAI appends dated suffixes (gpt-4o-mini-2024-07-18), hence prefix
# matching rather than exact.
_MODEL_CONTEXT_TOKENS: dict[str, int] = {
    "gpt-4.1": 1_000_000,
    "gpt-4o-mini": 128_000,
    "gpt-4o": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4": 8_192,
    "gpt-3.5-turbo": 16_385,
    "o1": 200_000,
    "o3": 200_000,
    "o4": 200_000,
}
# Conservative fallback for an unknown model — safe even for a gpt-3.5-class
# 16K window, so an unrecognized model never overflows the context.
_DEFAULT_CONTEXT_TOKENS = 16_000
# Reserve headroom (1 - fraction) of the window for the prompt template, the
# query, and the model's JSON response (thinking + node_list).
_BUDGET_SAFETY_FRACTION = 0.75
# A serialized-JSON word is ~1.5-3 model tokens; 1.7 is a calibrated midpoint.
# Calibration: 128_000 * 0.75 / 1.7 ≈ 56_470 words ≈ the previous hand-tuned
# 60K default for gpt-4o-mini, so the default model's behavior is essentially
# unchanged while other models now scale to their own windows.
_TOKENS_PER_WORD = 1.7


def context_window_tokens(model_name: str) -> int:
    """Context window (tokens) for ``model_name`` via longest-prefix match.

    Unknown models fall back to ``_DEFAULT_CONTEXT_TOKENS`` (conservative) so
    an unrecognized model never overflows the context window.
    """
    name = (model_name or "").lower()
    best = ""
    for prefix in _MODEL_CONTEXT_TOKENS:
        if name.startswith(prefix) and len(prefix) > len(best):
            best = prefix
    return _MODEL_CONTEXT_TOKENS[best] if best else _DEFAULT_CONTEXT_TOKENS


def derive_max_tree_words(model_name: str) -> int:
    """Tree word budget derived from the model's context window.

    ``context_window * safety_fraction / tokens_per_word`` — leaves headroom
    for the prompt + query + response. Always ``>= 1``.
    """
    tokens = context_window_tokens(model_name)
    return max(1, int(tokens * _BUDGET_SAFETY_FRACTION / _TOKENS_PER_WORD))


__all__ = ("context_window_tokens", "derive_max_tree_words")
