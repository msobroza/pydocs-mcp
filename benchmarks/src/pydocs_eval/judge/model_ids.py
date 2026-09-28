"""OpenRouter model ids, read one way everywhere: vendor, base slug, variants, served spelling.

An id is ``<vendor>/<slug>`` plus optional ``:<variant>`` suffixes
(``anthropic/claude-opus-5.5:batch``). A bare System One id (``jev-1.13``) has
no vendor: OpenRouter serves it under ``typesafe/`` (OpenRouter's TypeSafe SDK
guide). ``:batch`` is a catalog variant served only by the Batch API, which
takes the base slug (OpenRouter's model-variants docs).

Example:
    >>> base_slug("anthropic/claude-opus-5.5:batch"), model_family("jev-1.13")
    ('anthropic/claude-opus-5.5', 'typesafe')
"""

from __future__ import annotations

import re

from pydocs_eval.judge.judge_errors import JudgeModelMismatchError
from pydocs_eval.judge.openrouter_body import redact

#: The vendor OpenRouter serves a bare System One id under.
SYSTEM_ONE_VENDOR = "typesafe"
_BATCH_VARIANT = "batch"
_VARIANT_SEPARATOR = ":"
_VENDOR_SEPARATOR = "/"
# The dated snapshot OpenRouter names after a pinned id: -20260917 or -2026-09-17.
_DATED_SNAPSHOT = re.compile(r"-(?:\d{8}|\d{4}-\d{2}-\d{2})")


def base_slug(model: str) -> str:
    """``model`` without its variants: ``anthropic/claude-opus-5.5:batch`` → ``anthropic/claude-opus-5.5``.

    Example:
        >>> base_slug("openai/gpt-6-astra:batch")
        'openai/gpt-6-astra'
    """
    return model.split(_VARIANT_SEPARATOR, 1)[0]


def variants_of(model: str) -> frozenset[str]:
    """The ``:<variant>`` suffixes ``model`` carries.

    Example:
        >>> sorted(variants_of("poolside/laguna-s-2.1:free:nitro"))
        ['free', 'nitro']
    """
    return frozenset(model.split(_VARIANT_SEPARATOR)[1:])


def is_batch_model(model: str) -> bool:
    """Whether ``model`` names a ``:batch`` catalog variant, served only by the Batch API.

    Example:
        >>> is_batch_model("openai/gpt-6-astra:batch"), is_batch_model("openai/gpt-6-luna")
        (True, False)
    """
    return _BATCH_VARIANT in variants_of(model)


def model_family(model: str) -> str:
    """The family a model belongs to: its vendor, ``typesafe`` for a bare System One id.

    Example:
        >>> model_family("openai/gpt-6-luna")
        'openai'
    """
    vendor, separator, _ = model.partition(_VENDOR_SEPARATOR)
    return vendor if separator else SYSTEM_ONE_VENDOR


def served_model_matches(pinned: str, served: str) -> bool:
    """Whether ``served`` names the model ``pinned`` names: that version, never another.

    OpenRouter reports the id of the model that served a call, which may differ
    from the pin in three documented ways only: a bare System One id comes back
    under ``typesafe/`` (``jev-1.13`` → ``typesafe/jev-1.13``), the id may name
    the dated snapshot that answered (``…-20260917``), and a variant the request
    selected may be dropped (``:batch``). A served id with another vendor, a
    variant the pin lacks, or any other suffix names another model.

    Example:
        >>> served_model_matches("jev-1.13", "typesafe/jev-1.13-20260917")
        True
        >>> served_model_matches("jev-1.13", "evilcorp/jev-1.13")
        False
    """
    if not variants_of(served) <= variants_of(pinned):
        return False
    served_base = base_slug(served)
    return any(
        served_base == spelling or _is_dated_snapshot(served_base, spelling)
        for spelling in _served_spellings(base_slug(pinned))
    )


def check_served_model(pinned: str, served: str, *, bearer: str = "") -> None:
    """Raise unless ``served`` is the model ``pinned`` names (:func:`served_model_matches`).

    Example:
        >>> check_served_model("jev-1.13", "typesafe/jev-1.13-20260917")

    Raises:
        JudgeModelMismatchError: another model served it, naming both (``served`` redacted).
    """
    if not served_model_matches(pinned, served):
        raise JudgeModelMismatchError(model=redact(served, bearer), pinned=pinned)


def _served_spellings(pinned_base: str) -> tuple[str, ...]:
    """The base ids OpenRouter may answer ``pinned_base`` with."""
    if _VENDOR_SEPARATOR in pinned_base:
        return (pinned_base,)
    return (pinned_base, f"{SYSTEM_ONE_VENDOR}{_VENDOR_SEPARATOR}{pinned_base}")


def _is_dated_snapshot(served_base: str, spelling: str) -> bool:
    suffix = served_base.removeprefix(spelling)
    return suffix != served_base and _DATED_SNAPSHOT.fullmatch(suffix) is not None


__all__ = (
    "SYSTEM_ONE_VENDOR",
    "base_slug",
    "check_served_model",
    "is_batch_model",
    "model_family",
    "served_model_matches",
    "variants_of",
)
