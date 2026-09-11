"""The one source of the reasoning-model family prefixes (gpt-5+, the o-series).

A light, import-free module so both the retrieval ``OpenAiLlmClient`` and the
ask-your-docs family table (``harness/ask_your_docs/provider_profiles.py``)
read the same tuple without pulling the ``openai`` SDK into the harness.

Example:
    >>> "o3-mini".startswith(REASONING_MODEL_PREFIXES)
    True
"""

from __future__ import annotations

# Reasoning models differ from gpt-4o-class chat models in two request-shape
# ways that 400 otherwise: they accept only the default temperature, and they
# reject the legacy ``max_tokens`` param (it must be ``max_completion_tokens``).
REASONING_MODEL_PREFIXES: tuple[str, ...] = ("gpt-5", "o1", "o3", "o4")
