"""Compare tool answers exactly, except item ``score`` floats (float32 noise).

Identity tests compare ``text`` and ``items`` of two answers. Wrap the EXPECTED
side in :func:`scores_within_float_noise`: every float ``score`` at any depth
becomes a ``pytest.approx``, and every other value (text, ids, names, spans,
list order) still compares exactly.
"""

from __future__ import annotations

import pytest

# WHY: item scores come from float32 embedding / scoring math whose last digits
# are not stable, while every other byte is:
# - across CPUs — CI on #351 scored one hit -7.990472316741943 against the
#   tree-tier golden's -7.99047327041626 (relative gap ~1e-7);
# - across search paths on one CPU — CI on #353 scored one dependency docs hit
#   6.083324432373047 on a bundle whose dense branch takes the candidate-id
#   allowlist path (the dependency-decision exclusion) against 6.083323955535889
#   on the stock bundle's unfiltered ANN path: the two accumulate float32 in a
#   different order.
# The tolerance absorbs that noise (abs covers near-zero scores) and still fails
# on any real score change.
SCORE_REL_TOLERANCE = 1e-5
SCORE_ABS_TOLERANCE = 1e-6


def scores_within_float_noise(node: object) -> object:
    """``node`` with every float ``score`` (at any depth) swapped for an approx."""
    if isinstance(node, list):
        return [scores_within_float_noise(child) for child in node]
    if not isinstance(node, dict):
        return node
    return {
        key: (
            pytest.approx(value, rel=SCORE_REL_TOLERANCE, abs=SCORE_ABS_TOLERANCE)
            if key == "score" and isinstance(value, float)
            else scores_within_float_noise(value)
        )
        for key, value in node.items()
    }
