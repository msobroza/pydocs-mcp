"""Concrete metric plug-ins (spec §4.11). Each metric lives in its own
module; importing this package registers them with ``metric_registry``."""
from __future__ import annotations

from .base_metric import Metric
from .coverage import Coverage
from .library_resolution_at_k import LibraryResolution1
from .mrr import MRR
from .ndcg_at_k import NDCGAtK
from .pass_at_1_needle import PassAt1Needle
from .precision_at_1 import Precision1
from .recall_at_k import RecallAtK

__all__ = [
    "MRR",
    "Coverage",
    "LibraryResolution1",
    "Metric",
    "NDCGAtK",
    "PassAt1Needle",
    "Precision1",
    "RecallAtK",
]
