def units_for_risk(equity: float, risk_pct: float, stop_loss_distance: float, quote_to_account_rate: float = 1.0) -> int:
    """Fixed-fractional position sizing: risk a fixed % of equity per trade,
    sized so a stop-out at stop_loss_distance loses exactly that amount.

    stop_loss_distance is denominated in the pair's quote currency (that's
    what the price/ATR math naturally produces). quote_to_account_rate
    converts it into account-currency terms -- 1.0 for a pair already quoted
    in the account currency (e.g. EUR_USD for a USD account), otherwise the
    quote-currency/account-currency exchange rate (see forex/conversion.py).
    Omitting it silently assumes a 1:1 rate, which is only correct for
    USD-quoted pairs on a USD account.
    """
    if equity <= 0:
        raise ValueError("equity must be positive")
    if not 0 < risk_pct <= 1:
        raise ValueError("risk_pct must be in (0, 1]")
    if stop_loss_distance <= 0:
        raise ValueError("stop_loss_distance must be positive")
    if quote_to_account_rate <= 0:
        raise ValueError("quote_to_account_rate must be positive")

    risk_dollars = equity * risk_pct
    stop_loss_distance_in_account_currency = stop_loss_distance * quote_to_account_rate
    return int(risk_dollars // stop_loss_distance_in_account_currency)
