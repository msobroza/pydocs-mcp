"""Concrete dataset plug-ins (spec §4.3). Importing this package fires
the ``@dataset_registry.register`` decorators on every concrete dataset
so the runner can look them up by name."""

from __future__ import annotations

from .base_dataset import Dataset
from .ds1000 import Ds1000Dataset
from .repoqa import RepoQADataset
from .structural_recall import StructuralRecallDataset
from .swe_qa import SweQaDataset
from .swe_qa_pro import SweQaProDataset

__all__ = [
    "Dataset",
    "Ds1000Dataset",
    "RepoQADataset",
    "StructuralRecallDataset",
    "SweQaDataset",
    "SweQaProDataset",
]
