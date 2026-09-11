"""Which model setting did a 400 reject? The learned-rejection parser (model-params v2 §3.3).

Pure and import-light: the error is read duck-typed (``status_code``, ``param``,
its text), so neither the ``openai`` SDK nor LangChain is imported here. A match
counts only for a wire name that was actually SENT — an error that merely quotes
some other word never hides a control — and nothing is ever retried.

Example:
    >>> class Rejected(Exception):
    ...     status_code = 400
    >>> exc = Rejected("openai does not support parameters: ['reasoning_effort']")
    >>> rejected_control_from_error(exc, {"reasoning_effort": "low"})
    'thinking'
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# Wire names (as sent) → the dialog control that hides when a 400 names one of them.
_CONTROL_FOR_WIRE_NAME = {
    **{name: name for name in ("temperature", "max_tokens", "top_p", "seed")},
    "reasoning_effort": "thinking",
    "max_completion_tokens": "max_tokens",  # LangChain always sends the cap under this name
}
_BAD_REQUEST = 400
_LITELLM_LISTED = re.compile(r"does not support parameters: \[([^\]]*)\]")
_LITELLM_ASSIGNED = re.compile(r"doesn't support (\w+)=")
_QUOTED_NAME = re.compile(r"""['"`](\w+)['"`]""")


def _named_candidates(exc: BaseException) -> list[str]:
    """Names the error mentions, in parser order: ``.param``, LiteLLM's phrasings, any quoted name."""
    message = str(exc)
    param = getattr(exc, "param", None)
    listed = _LITELLM_LISTED.search(message)
    return [
        *([param] if isinstance(param, str) else []),
        *(_QUOTED_NAME.findall(listed.group(1)) if listed else []),
        *_LITELLM_ASSIGNED.findall(message),
        *_QUOTED_NAME.findall(message),
    ]


def rejected_control_from_error(exc: BaseException, sent: Mapping[str, object]) -> str | None:
    """The control a 400 rejected (``"thinking"``, ``"temperature"``, …), else None.

    ``sent`` is the request's wire kwargs; the first candidate that was sent wins.
    An error without a ``status_code`` (LiteLLM's in-process exceptions) is read too.
    """
    if getattr(exc, "status_code", _BAD_REQUEST) != _BAD_REQUEST:
        return None
    for name in _named_candidates(exc):
        if name in sent and name in _CONTROL_FOR_WIRE_NAME:
            return _CONTROL_FOR_WIRE_NAME[name]
    return None
