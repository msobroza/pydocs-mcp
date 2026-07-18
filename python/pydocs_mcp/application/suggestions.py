"""Deterministic routing suggestions — fixed rendering + fired-rule log (ADR 0007).

The three rule texts are deterministic-behavior output, NOT optimizable
description text: they sit on the description-source exception list next to
the envelope rendering strings (freshness header, truncation footer, pointer
templates). Letting the optimizer mutate them would blur the machinery/model
boundary requirement R7 keeps sharp — a transcript line starting with the
fixed ``[suggestion:`` prefix is always a harness-initiated nudge, never
model-earned routing. Per-rule on/off flags live in YAML
(``output.suggestions.*``, ``SuggestionsConfig``); each fired rule emits one
structured log line here so Phase 2 analysis can attribute outcomes.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

# Deterministic marker prefix — every server-initiated suggestion line starts
# with this so transcript analysis can separate machinery from model (R7).
SUGGESTION_PREFIX = "[suggestion:"

GREP_ZERO_HIT_SUGGESTION = (
    '[suggestion: no exact matches — for conceptual queries, try search_codebase(query="...")]'
)
GREP_TRUNCATED_SUGGESTION = (
    "[suggestion: output cut by head_limit — narrow with path= or glob=, or raise head_limit=]"
)
SEARCH_ZERO_HIT_SUGGESTION = "[suggestion: zero hits — orient with get_overview()]"


def log_suggestion_fired(tool: str, rule: str) -> None:
    """One structured line per fired rule — the Phase 2 attribution input."""
    log.info(json.dumps({"event": "suggestion_fired", "tool": tool, "rule": rule}))
