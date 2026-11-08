"""The fresh draw a finished run's arms recorded — what the verbs that re-read them load.

``--report-only`` and ``before-after-compare`` score stored answers against a
split's gold. For a fresh-draw run that gold belongs to the redraw, and each
arm's ``arm_settings.json`` froze it (seed and excluded ids) when the plan was
made. So the record, not the operator's flags, is what those verbs load
through. Without it, forgetting ``--split-seed`` would score every answer
against ``small_test``'s gold and read as n/a, and an exclusion file edited
after the run would silently re-draw the sample.

Split out of ``before_after_fresh_draw`` because reading an arm's settings
imports ``before_after_arm``, which imports the draw.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_arm import ARM_SETTINGS_FILENAME, read_arm_settings
from pydocs_eval.campaign.before_after_fresh_draw import FreshTestDraw


def draw_the_arms_recorded(arm_dirs: Sequence[Path]) -> FreshTestDraw | None:
    """The one draw every arm in ``arm_dirs`` answered; None when they answered none.

    Raises:
        MeasurementPlanError: two arms answered different samples, or an arm's
            settings file is unreadable.
    """
    recorded = {arm_dir: _recorded_draw(arm_dir) for arm_dir in arm_dirs}
    if len({_key(draw) for draw in recorded.values()}) > 1:
        answered = "; ".join(f"{arm_dir}: {_described(draw)}" for arm_dir, draw in recorded.items())
        raise MeasurementPlanError(
            f"the arms answered different samples ({answered}); expected one draw for "
            "every arm, so their answers are scored against the same gold"
        )
    return next(iter(recorded.values()), None)


def reconciled_with_flags(
    recorded: FreshTestDraw | None, flags: FreshTestDraw | None
) -> FreshTestDraw | None:
    """The recorded draw, once the flags (if given) are checked to name the same sample.

    Raises:
        MeasurementPlanError: the flags name a draw the arms did not answer.
    """
    if flags is None or _key(flags) == _key(recorded):
        return recorded
    raise MeasurementPlanError(
        f"--split-seed/--exclude-task-ids name {flags.describe()}, but the recorded arms "
        f"answered {_described(recorded)}; expected the flags left out, or naming the "
        "recorded sample (an exclusion file edited since the run has another sha256)"
    )


def _recorded_draw(arm_dir: Path) -> FreshTestDraw | None:
    """The draw ``arm_dir`` answered; None for an arm that predates recording one."""
    try:
        return read_arm_settings(arm_dir).task_draw
    except FileNotFoundError:
        # No settings file: the arm reader that follows refuses the arm by name.
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise MeasurementPlanError(
            f"{arm_dir / ARM_SETTINGS_FILENAME} is unreadable ({exc}); expected the settings "
            "file a before/after arm writes"
        ) from exc


def _key(draw: FreshTestDraw | None) -> tuple[int, str | None] | None:
    return None if draw is None else draw.sample_key()


def _described(draw: FreshTestDraw | None) -> str:
    return "no fresh draw" if draw is None else draw.describe()


__all__ = ("draw_the_arms_recorded", "reconciled_with_flags")
