def pip_size(pair: str) -> float:
    """JPY quote pairs use 2-decimal pips; everything else uses 4-decimal pips."""
    return 0.01 if pair.endswith("JPY") else 0.0001


def units_for_risk(equity: float, risk_pct: float, stop_loss_distance: float) -> int:
    """Fixed-fractional position sizing: risk a fixed % of equity per trade,
    sized so a stop-out at stop_loss_distance loses exactly that amount.

    Assumes the quote currency is ~1:1 with the account currency (true for
    the USD-quoted majors this bot trades — EUR_USD, GBP_USD, etc.); a
    non-USD-quote pair would need a currency conversion this doesn't do.
    """
    if equity <= 0:
        raise ValueError("equity must be positive")
    if not 0 < risk_pct <= 1:
        raise ValueError("risk_pct must be in (0, 1]")
    if stop_loss_distance <= 0:
        raise ValueError("stop_loss_distance must be positive")

    risk_dollars = equity * risk_pct
    return int(risk_dollars // stop_loss_distance)
