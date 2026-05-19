"""Concrete metric plug-ins (spec §4.11). Each metric lives in its own
module; importing this package registers them with ``metric_registry``."""
from __future__ import annotations

from .base_metric import Metric
from .mrr import MRR
from .pass_at_1_needle import PassAt1Needle
from .recall_at_k import RecallAtK

__all__ = ["MRR", "Metric", "PassAt1Needle", "RecallAtK"]
