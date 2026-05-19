"""Concrete tracker plug-ins (spec §4.5). Importing this package fires
the ``@tracker_registry.register`` decorators on every concrete tracker
so the runner can look them up by name."""
from __future__ import annotations

from .base_tracker import ExperimentTracker
from .jsonl_tracker import JsonlExperimentTracker
from .mlflow_tracker import MlflowExperimentTracker

__all__ = [
    "ExperimentTracker",
    "JsonlExperimentTracker",
    "MlflowExperimentTracker",
]
