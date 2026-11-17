"""Opening the pinned judges on OpenRouter for one command's duration, behind injectable openers.

A command takes an opener — a context manager factory over the judge
configuration — so its tests hand it offline fakes and nothing reaches the
network. The defaults here are the paid ones: each opens one HTTP client and
closes it when the command is done.

Example:
    >>> with open_openrouter_jev(judge) as jev:  # doctest: +SKIP
    ...     jev.judge(request)
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager

import httpx

from pydocs_eval.judge.config import JudgeConfig
from pydocs_eval.judge.jev_cache import JevResponseCache, jev_cache_dir
from pydocs_eval.judge.jev_client import JevJudgeClient
from pydocs_eval.judge.jev_wire import JevJudge

#: Opens the pinned Jev judge for a command's duration.
JevOpener = Callable[[JudgeConfig], AbstractContextManager[JevJudge]]


@contextmanager
def open_openrouter_jev(judge: JudgeConfig) -> Iterator[JevJudge]:
    """The pinned Jev judge on OpenRouter, cached by input hash (a warm cache calls nothing)."""
    with httpx.Client() as http:
        yield JevJudgeClient(judge.jev, cache=JevResponseCache(jev_cache_dir(judge.jev)), http=http)


__all__ = ("JevOpener", "open_openrouter_jev")
