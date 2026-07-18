"""Synthetic package module fixture (SWE-QA-Pro corpus)."""


def load(name: str) -> dict:
    """Load a record by name."""
    return {"name": name}


def store(record: dict) -> None:
    """Persist a record."""
    _ = record
