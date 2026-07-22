from decision_engine.models import FactorScore


def factors_to_dict(factor_scores: list[FactorScore]) -> dict[str, float]:
    return {f.name: f.value for f in factor_scores}


def predict_win_probability(model, feature_names: list[str], factors: dict[str, float]) -> float:
    """Standalone inference — not wired into decision_engine.WeightedFactorModel.
    Adding an ML-derived factor to the live scoring model is a real design
    decision (how to weight/normalize it against the hand-tuned factors) that
    shouldn't happen until there's a trained model with evidence it actually
    improves things; this just makes prediction possible once that's decided.
    """
    if not feature_names:
        raise ValueError("feature_names must not be empty")
    x = [[factors.get(name, 0.0) for name in feature_names]]
    return float(model.predict_proba(x)[0][1])
