"""Map a configured LLM model to a tree-reasoning TOKEN budget + count tokens.

The ``llm_tree_reasoning`` step serializes the project tree into a prompt, so
the budget must fit the model's context window. Two pieces:

- ``derive_max_tree_tokens`` — a budget in REAL tokens, a fraction of the
  model's context window, so it auto-scales (a 1M-context model gets a far
  larger tree, an 8K model a smaller one) and leaves headroom for the prompt
  template + query + response.
- ``count_tokens`` — exact token count via ``tiktoken`` for the model's
  encoding. The pruner measures the serialized tree in these tokens.

WHY tokens, not words: a serialized-JSON word of code is ~3 model tokens
(qualified names, signatures, punctuation all split heavily), so the previous
``words × 1.7`` heuristic under-counted by ~2× and let prompts blow past the
context window (e.g. a 50K-word tree = ~170K tokens >> gpt-4o-mini's 128K,
causing a 400 context_length_exceeded). Counting real tokens makes the bound
exact, so the prompt can never overflow.
"""

from __future__ import annotations

import tiktoken

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
# tiktoken encoding for models tiktoken doesn't recognize (e.g. test fakes,
# future providers) — o200k_base is the modern OpenAI encoding.
_DEFAULT_ENCODING = "o200k_base"


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


def derive_max_tree_tokens(model_name: str) -> int:
    """Tree token budget = ``context_window * safety_fraction`` (>= 1).

    Strictly below the window so the tree + prompt template + query + response
    all fit. Scales with the model's window.
    """
    return max(1, int(context_window_tokens(model_name) * _BUDGET_SAFETY_FRACTION))


def _encoding_for(model_name: str) -> tiktoken.Encoding:
    """tiktoken encoding for ``model_name``; fall back to the default encoding
    for models tiktoken doesn't know (never raises)."""
    try:
        return tiktoken.encoding_for_model(model_name)
    except KeyError:
        return tiktoken.get_encoding(_DEFAULT_ENCODING)


def count_tokens(text: str, model_name: str) -> int:
    """Exact token count of ``text`` under ``model_name``'s encoding.

    tiktoken caches encodings internally, so repeated calls are cheap.
    """
    if not text:
        return 0
    return len(_encoding_for(model_name).encode(text))


__all__ = ("context_window_tokens", "count_tokens", "derive_max_tree_tokens")
