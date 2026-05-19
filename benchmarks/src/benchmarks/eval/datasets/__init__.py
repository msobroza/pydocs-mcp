"""Concrete dataset plug-ins (spec §4.3). Importing this package fires
the ``@dataset_registry.register`` decorators on every concrete dataset
so the runner can look them up by name."""
from __future__ import annotations

from .base_dataset import Dataset
from .repoqa import RepoQADataset

__all__ = [
    "Dataset",
    "RepoQADataset",
]
