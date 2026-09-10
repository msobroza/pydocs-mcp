"""Plain-language labels for the activity panel's steps (PROPOSAL §3).

What a step IS, before it has a result: the tool's label, the vision node's label, the
scope and rephrase notes, and why a turn stopped. What a result SAYS lives in
``activity_outcomes``. The running form ends in "…" and the done form is past tense;
argument values are clipped to 60 characters. Nothing here renders — the view shows every
string as plain text, because model arguments are untrusted.

Example:
    >>> tool_step_label("get_references", {"target": "m.f"}, running=False)
    'Found callers of m.f'
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydocs_mcp.harness.ask_your_docs.scope_pin import CODE_SCOPE_WORDS

VALUE_MAX_CHARS = 60  # an argument value inside a label
NOTE_MAX_CHARS = 160  # a hint, a first line, a rephrased question
_CONTEXT_TARGETS_SHOWN = 2
_VERBS: dict[str, tuple[str, str]] = {  # key -> (running form, done form)
    "search": ("Searching", "Searched"),
    "look_up": ("Looking up", "Looked up"),
    "outline": ("Outlining", "Outlined"),
    "read_source": ("Reading the source of", "Read the source of"),
    "gather": ("Gathering context for", "Gathered context for"),
    "find": ("Finding", "Found"),
    "check": ("Checking", "Checked"),
    "estimate": ("Estimating", "Estimated"),
    "look_for": ("Looking for", "Looked for"),
    "get": ("Getting", "Got"),
    "list": ("Listing", "Listed"),
    "open": ("Opening", "Opened"),
    "look_at": ("Looking at", "Looked at"),
    "analyze": ("Analyzing", "Analyzed"),
    "call": ("Calling", "Called"),
}
_CORPUS_WORDS = {"project": "project code", "deps": "dependencies"}  # else "all code"
_SEARCH_KIND_WORDS = {"api": 'symbols matching "{q}"', "decision": 'decisions about "{q}"'}
_SYMBOL_VERBS = {"tree": "outline", "source": "read_source"}  # summary (default): look_up
_REFERENCE_PHRASES = {
    "callers": ("find", "callers of {t}"),
    "callees": ("find", "what {t} calls"),
    "inherits": ("check", "the class hierarchy of {t}"),
    "impact": ("estimate", "what changing {t} affects"),
    "governed_by": ("find", "decisions governing {t}"),
}
_REJECTED = "the model endpoint rejected the request"
_TIMED_OUT = "the model endpoint did not answer in time"
_FAILURE_REASONS = {
    "BearerRejectedError": _REJECTED,
    "AuthenticationError": _REJECTED,
    "PermissionDeniedError": _REJECTED,
    "BearerUnavailableError": "no token could be fetched for the model endpoint",
    "GraphRecursionError": "the agent hit its step limit",
    "APITimeoutError": _TIMED_OUT,
    "TimeoutError": _TIMED_OUT,
}

_Phrase = tuple[str, str]  # (verb key, the rest of the sentence)


def tool_step_label(name: str, args: Mapping[str, Any], *, running: bool) -> str:
    """ "Finding callers of X …" while the call runs; "Found callers of X" once it is done."""
    phrase = _PHRASES.get(name)
    verb, rest = phrase(args) if phrase else ("call", clip_label_text(name))
    return _sentence(verb, rest, running)


def vision_step_label(*, running: bool) -> str:
    """The vision node's step (it sees the images before the agent's first round)."""
    return _sentence("analyze", "the attached images", running)


def _sentence(verb: str, rest: str, running: bool) -> str:
    doing, done = _VERBS[verb]
    return f"{doing} {rest} …" if running else f"{done} {rest}"


def clip_label_text(value: Any, limit: int = VALUE_MAX_CHARS) -> str:
    """``value`` on one line, cut to ``limit`` characters with a trailing "…"."""
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def lenient_int(value: Any) -> int | None:
    """An int from an int or a digit string (model arguments arrive either way), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return int(value) if isinstance(value, str) and value.isdigit() else None


def _search_phrase(args: Mapping[str, Any]) -> _Phrase:
    corpus = _CORPUS_WORDS.get(str(args.get("scope")), "all code")
    query = clip_label_text(args.get("query", ""))
    what = _SEARCH_KIND_WORDS.get(str(args.get("kind")), '"{q}"').format(q=query)
    package = f" in {clip_label_text(args['package'])}" if args.get("package") else ""
    return "search", f"{corpus} for {what}{package}"


def _symbol_phrase(args: Mapping[str, Any]) -> _Phrase:
    verb = _SYMBOL_VERBS.get(str(args.get("depth")), "look_up")
    return verb, clip_label_text(args.get("target", ""))


def _context_phrase(args: Mapping[str, Any]) -> _Phrase:
    targets = [str(target) for target in args.get("targets") or []]
    shown = ", ".join(clip_label_text(target) for target in targets[:_CONTEXT_TARGETS_SHOWN])
    hidden = len(targets) - _CONTEXT_TARGETS_SHOWN
    return "gather", shown + (f" (+{hidden} more)" if hidden > 0 else "")


def _references_phrase(args: Mapping[str, Any]) -> _Phrase:
    direction = str(args.get("direction") or "callers")
    verb, template = _REFERENCE_PHRASES.get(direction, _REFERENCE_PHRASES["callers"])
    return verb, template.format(t=clip_label_text(args.get("target", "")))


def _why_phrase(args: Mapping[str, Any]) -> _Phrase:
    if args.get("query"):
        return "look_for", f'design decisions about "{clip_label_text(args["query"])}"'
    targets = ", ".join(clip_label_text(target) for target in args.get("targets") or [])
    return "look_for", f"design decisions about {targets}"


def _overview_phrase(args: Mapping[str, Any]) -> _Phrase:
    package = clip_label_text(args["package"]) if args.get("package") else "the workspace"
    return "get", f"an overview of {package}"


def _grep_phrase(args: Mapping[str, Any]) -> _Phrase:
    where = clip_label_text(args.get("glob") or args.get("path") or "the project")
    return "search", f"file text for /{clip_label_text(args.get('pattern', ''))}/ in {where}"


def _glob_phrase(args: Mapping[str, Any]) -> _Phrase:
    return "list", f"files matching {clip_label_text(args.get('pattern', ''))}"


def _read_file_phrase(args: Mapping[str, Any]) -> _Phrase:
    path = clip_label_text(args.get("file_path", ""))
    offset, limit = lenient_int(args.get("offset")), lenient_int(args.get("limit"))
    if limit:
        first = offset or 1
        return "open", f"{path}:{first}–{first + limit - 1}"
    return "open", f"{path} from line {offset}" if offset else path


def _reinspect_phrase(args: Mapping[str, Any]) -> _Phrase:
    names = [clip_label_text(name) for name in args.get("names") or []]
    noun = "image" if len(names) == 1 else "images"
    return "look_at", f"{noun} {', '.join(names)} again"


_PHRASES: dict[str, Callable[[Mapping[str, Any]], _Phrase]] = {
    "search_codebase": _search_phrase,
    "get_symbol": _symbol_phrase,
    "get_context": _context_phrase,
    "get_references": _references_phrase,
    "get_why": _why_phrase,
    "get_overview": _overview_phrase,
    "grep": _grep_phrase,
    "glob": _glob_phrase,
    "read_file": _read_file_phrase,
    "reinspect_images": _reinspect_phrase,
}


def scope_note(scope: Mapping[str, str]) -> str | None:
    """'Scope: project "x" (pinned by you)' — only when a pin applies."""
    keys = ("project", "package")
    parts = [f'{key} "{clip_label_text(scope[key])}"' for key in keys if scope.get(key)]
    code = CODE_SCOPE_WORDS.get(str(scope.get("code", "all")))
    parts += [code] if code else []
    return f"Scope: {', '.join(parts)} (pinned by you)" if parts else None


def rephrase_note(original: str, rewritten: str) -> str | None:
    """'Rephrased your question as "…"' — only when it differs after normalizing."""
    if _normalized_question(original) == _normalized_question(rewritten):
        return None
    return f'Rephrased your question as "{clip_label_text(rewritten, NOTE_MAX_CHARS)}"'


def _normalized_question(question: str) -> str:
    return " ".join(question.casefold().split()).rstrip("?.! ")


def failure_reason(exc_class_name: str) -> str:
    """Why a turn stopped, in words, from the exception's class name."""
    return _FAILURE_REASONS.get(exc_class_name, "an error stopped the turn")


__all__ = (
    "NOTE_MAX_CHARS",
    "VALUE_MAX_CHARS",
    "clip_label_text",
    "failure_reason",
    "lenient_int",
    "rephrase_note",
    "scope_note",
    "tool_step_label",
    "vision_step_label",
)
