"""What can go wrong asking a judge, one exception per way it goes wrong.

The eval has no shared error root (``MeasurementPlanError``, ``TrajectoryError``
and the rest subclass ``Exception`` directly), so neither do these. Each one's
text names the offending value and carries no bearer.

Example:
    >>> str(JudgeModelMismatchError(model="typesafe/jev-1.14-20261001", pinned="jev-1.13"))
    "judge model mismatch: got 'typesafe/jev-1.14-20261001', expected 'jev-1.13'"
"""

from __future__ import annotations


class JudgeConfigError(Exception):
    """A judge role that cannot run as configured, named by its YAML key or variable."""


class JudgeUnavailableError(Exception):
    """The judge service gave no answer: an outage, booked ``undefined``, never 0."""


class JudgeRequestError(Exception):
    """The judge service refused the request (a 4xx other than 429): the request is wrong."""

    def __init__(self, message: str, *, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class JudgeResponseError(Exception):
    """A judge service answered in a shape its documentation does not describe."""


class JudgeModelMismatchError(Exception):
    """The model that answered is not the pinned one: its thresholds were never fitted to it."""

    def __init__(self, *, model: str, pinned: str) -> None:
        super().__init__(f"judge model mismatch: got {model!r}, expected {pinned!r}")


__all__ = (
    "JudgeConfigError",
    "JudgeModelMismatchError",
    "JudgeRequestError",
    "JudgeResponseError",
    "JudgeUnavailableError",
)
