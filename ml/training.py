from collections import Counter

from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from .models import FeatureSnapshot, TrainingResult

MIN_LABELED_EXAMPLES = 20


def train_win_probability_model(
    snapshots: list[FeatureSnapshot], test_size: float = 0.2, random_state: int = 42
) -> TrainingResult:
    labeled = [s for s in snapshots if s.win is not None]
    if len(labeled) < MIN_LABELED_EXAMPLES:
        raise ValueError(f"need at least {MIN_LABELED_EXAMPLES} labeled examples to train a model, got {len(labeled)}")

    feature_names = sorted({name for s in labeled for name in s.factors})
    X = [[s.factors.get(name, 0.0) for name in feature_names] for s in labeled]
    y = [1 if s.win else 0 for s in labeled]

    # Stratifying requires >= 2 examples of every class; fall back to a plain
    # split rather than crashing when the data doesn't support it yet.
    class_counts = Counter(y)
    stratify = y if len(class_counts) > 1 and min(class_counts.values()) >= 2 else None

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=test_size, random_state=random_state, stratify=stratify
    )

    model = XGBClassifier(n_estimators=100, max_depth=3, random_state=random_state, eval_metric="logloss")
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    auc = None
    if len(set(y_test)) > 1:  # AUC is undefined with only one class in the test split
        y_proba = model.predict_proba(X_test)[:, 1]
        auc = roc_auc_score(y_test, y_proba)

    return TrainingResult(
        model=model,
        feature_names=feature_names,
        accuracy=accuracy,
        auc=auc,
        train_size=len(X_train),
        test_size=len(X_test),
    )
