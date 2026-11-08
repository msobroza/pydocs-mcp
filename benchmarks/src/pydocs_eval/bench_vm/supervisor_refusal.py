"""The supervisor's refusal: a run it must not start, refused before any child runs or anything is written."""


class SupervisorRefused(ValueError):
    """The run cannot be supervised as found; the message names what and what was expected."""
