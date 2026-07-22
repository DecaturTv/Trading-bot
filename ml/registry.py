import mlflow
import mlflow.xgboost
from mlflow.tracking import MlflowClient

from .models import TrainingResult


class ModelRegistry:
    """Thin wrapper over MLflow (per project stack) using a local file-based
    SQLite tracking store — no tracking server needed. Tracks runs (params +
    accuracy/auc metrics) and the model artifact itself, and can fetch the
    most recently logged run as "the current model."
    """

    def __init__(self, tracking_uri: str, experiment_name: str = "win-probability"):
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(experiment_name)
        self._experiment_name = experiment_name
        self._client = MlflowClient(tracking_uri=tracking_uri)

    def log_training_run(self, result: TrainingResult, params: dict | None = None) -> str:
        with mlflow.start_run() as run:
            for key, value in (params or {}).items():
                mlflow.log_param(key, value)
            mlflow.log_param("feature_names", ",".join(result.feature_names))
            mlflow.log_metric("accuracy", result.accuracy)
            if result.auc is not None:
                mlflow.log_metric("auc", result.auc)
            mlflow.log_metric("train_size", result.train_size)
            mlflow.log_metric("test_size", result.test_size)
            mlflow.xgboost.log_model(result.model, name="model")
            return run.info.run_id

    def load_model(self, run_id: str):
        return mlflow.xgboost.load_model(f"runs:/{run_id}/model")

    def get_feature_names(self, run_id: str) -> list[str]:
        run = self._client.get_run(run_id)
        names = run.data.params.get("feature_names", "")
        return names.split(",") if names else []

    def get_latest_run_id(self) -> str | None:
        experiment = self._client.get_experiment_by_name(self._experiment_name)
        if experiment is None:
            return None
        runs = self._client.search_runs([experiment.experiment_id], order_by=["start_time DESC"], max_results=1)
        return runs[0].info.run_id if runs else None
