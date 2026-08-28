"""Strategy tournament: run several preset strategy configurations through the
existing backtest engines over the same historical data and rank them by
realized dollar P&L. The winner can then be promoted into the live config
(decision_engine/scoring.py + .env) with `python -m tournament promote`.

Nothing here reimplements the strategy — each competitor is just a bundle of
knobs (factor weights, confidence threshold, exit rules, sizing) fed to the
unchanged BacktestEngine / ForexBacktestEngine, so a tournament result and
live behavior can't silently diverge.
"""

from .strategies import STRATEGIES, EquityKnobs, ForexKnobs, Strategy, by_name

__all__ = ["STRATEGIES", "Strategy", "EquityKnobs", "ForexKnobs", "by_name"]
