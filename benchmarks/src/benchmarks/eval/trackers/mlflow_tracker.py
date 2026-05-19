"""MLflow tracker — lazy-imports ``mlflow`` so the core install stays
small (spec §4.5). Constructing the tracker triggers the import; users
hit the install message immediately, not deep inside ``open_run``."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..serialization import tracker_registry
from .base_tracker import RunHandle

# WHY: the install command is duplicated verbatim in the error message so
# users can copy-paste from any traceback. Keep this string in one place.
_INSTALL_MSG = "uv pip install -e benchmarks[mlflow]"


def _require_mlflow():
    try:
        import mlflow
    except ImportError as exc:
        raise ImportError(
            f"MlflowExperimentTracker requires the optional [mlflow] extra. "
            f"Install with: {_INSTALL_MSG}"
        ) from exc
    return mlflow


@tracker_registry.register("mlflow")
@dataclass
class MlflowExperimentTracker:
    """Adapts MLflow's run API to the ``ExperimentTracker`` Protocol."""

    name: str = "mlflow"
    # WHY: MLflow's tracking_uri expects a URI scheme string (file://, http://, …)
    # — not a filesystem Path.
    tracking_uri: str = "file://./benchmarks/mlruns"
    experiment_name: str = "pydocs-mcp-benchmarks"
    # WHY: cache the imported module on the instance — once __post_init__
    # succeeds mlflow is in sys.modules and cannot disappear under normal
    # usage, so per-method _require_mlflow() calls are redundant defensive
    # code. The construction-time install-error path is unchanged.
    _mlflow: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        # WHY: fail fast on missing optional dep — surfaces the install
        # command at construction time rather than at the first open_run.
        self._mlflow = _require_mlflow()
        self._mlflow.set_tracking_uri(self.tracking_uri)
        self._mlflow.set_experiment(self.experiment_name)

    def open_run(
        self,
        *,
        system: str,
        config_name: str,
        dataset: str,
        params: Mapping[str, str],
        tags: Mapping[str, str],
    ) -> RunHandle:
        active = self._mlflow.start_run(
            run_name=f"{system}_{config_name}",
            tags={"dataset": dataset, **dict(tags)},
        )
        if params:
            self._mlflow.log_params(dict(params))
        return RunHandle(tracker_name=self.name, raw=active)

    def log_metric(
        self,
        handle: RunHandle,
        name: str,
        value: float,
        step: int | None = None,
    ) -> None:
        self._mlflow.log_metric(name, value, step=step)

    def log_artifact(
        self,
        handle: RunHandle,
        path: Path,
        name: str | None = None,
    ) -> None:
        self._mlflow.log_artifact(str(path), artifact_path=name)

    def close_run(
        self,
        handle: RunHandle,
        status: Literal["finished", "failed"],
    ) -> None:
        self._mlflow.end_run(status="FINISHED" if status == "finished" else "FAILED")
