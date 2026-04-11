"""Data models for the fake analytics project."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DataPoint:
    """A single observation with a label and numeric features.

    Attributes:
        label: Category label for this data point.
        features: List of float values representing measurements.
        weight: Optional sample weight for weighted aggregations.
    """
    label: str
    features: list[float] = field(default_factory=list)
    weight: Optional[float] = None

    def normalize(self) -> "DataPoint":
        """Return a new DataPoint with features scaled to [0, 1]."""
        if not self.features:
            return self
        mn, mx = min(self.features), max(self.features)
        span = mx - mn or 1.0
        return DataPoint(
            label=self.label,
            features=[(f - mn) / span for f in self.features],
            weight=self.weight,
        )


@dataclass
class BatchResult:
    """Aggregated result from processing a batch of DataPoints."""
    count: int
    mean_score: float
    failed: list[str] = field(default_factory=list)
