from datetime import datetime, timezone

from ml.forex_performance import build_factor_agreement, build_forex_performance_report, build_pair_performance
from ml.models import FeatureSnapshot

_NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _trade(symbol: str, pnl: float) -> dict:
    return {"symbol": symbol, "closed_at": _NOW, "pnl": pnl, "asset_class": "forex", "details": {}}


def _snapshot(direction: str, factors: dict[str, float], win: bool) -> FeatureSnapshot:
    return FeatureSnapshot(
        id=1, symbol="EUR_USD", as_of=_NOW, factors=factors, confidence=90.0,
        direction=direction, pnl=10.0 if win else -10.0, win=win,
    )


def test_build_pair_performance_groups_and_sorts_by_total_pnl_ascending():
    trades = [_trade("EUR_USD", 10.0), _trade("EUR_USD", -30.0), _trade("USD_JPY", 5.0)]

    result = build_pair_performance(trades)

    assert [p.symbol for p in result] == ["EUR_USD", "USD_JPY"]
    eur_usd = result[0]
    assert eur_usd.count == 2
    assert eur_usd.win_rate == 0.5
    assert eur_usd.total_pnl == -20.0
    assert eur_usd.avg_win == 10.0
    assert eur_usd.avg_loss == 30.0
    assert eur_usd.expectancy == -10.0


def test_build_pair_performance_empty_input():
    assert build_pair_performance([]) == []


def test_build_factor_agreement_flags_factor_anti_correlated_with_outcome():
    # unusual_volume agrees with the (losing) direction every time it fires,
    # and never agrees with a winner -- exactly the pattern that got it
    # zero-weighted for forex in decision_engine/scoring.py.
    snapshots = [
        _snapshot("bearish", {"unusual_volume": -0.8, "trend": 0.5}, win=False),
        _snapshot("bearish", {"unusual_volume": -0.6, "trend": -0.2}, win=False),
        _snapshot("bullish", {"unusual_volume": -0.3, "trend": 0.7}, win=True),
    ]

    result = {f.factor: f for f in build_factor_agreement(snapshots)}

    unusual_volume = result["unusual_volume"]
    assert unusual_volume.loser_agreement_rate == 1.0
    assert unusual_volume.loser_sample_size == 2
    assert unusual_volume.winner_agreement_rate == 0.0
    assert unusual_volume.winner_sample_size == 1


def test_build_factor_agreement_ignores_zero_value_readings():
    snapshots = [_snapshot("bullish", {"gap": 0.0}, win=True)]

    result = {f.factor: f for f in build_factor_agreement(snapshots)}

    assert result["gap"].winner_sample_size == 0
    assert result["gap"].winner_agreement_rate is None


def test_build_forex_performance_report_aggregates_everything():
    trades = [_trade("EUR_USD", 10.0), _trade("USD_JPY", -20.0)]
    snapshots = [_snapshot("bullish", {"trend": 0.5}, win=True)]

    report = build_forex_performance_report(trades, snapshots)

    assert report.trade_count == 2
    assert report.win_rate == 0.5
    assert report.total_pnl == -10.0
    assert len(report.by_pair) == 2
    assert len(report.by_factor) == 1


def test_build_forex_performance_report_empty_input():
    report = build_forex_performance_report([], [])

    assert report.trade_count == 0
    assert report.win_rate == 0.0
    assert report.expectancy == 0.0
    assert report.by_pair == []
    assert report.by_factor == []
