from datetime import datetime, timedelta, timezone

import pytest

from ml.trade_outcome_repository import TradeOutcomeRepository
from ml.trade_outcomes import get_live_trade_statistics


@pytest.mark.asyncio
async def test_get_live_trade_statistics_none_when_no_history(pool):
    repo = TradeOutcomeRepository(pool)
    assert await get_live_trade_statistics(repo) is None


@pytest.mark.asyncio
async def test_get_live_trade_statistics_computes_from_recorded_outcomes(pool):
    repo = TradeOutcomeRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await repo.record_outcome("AAPL", now, 150.0)
    await repo.record_outcome("TSLA", now, 150.0)
    await repo.record_outcome("NVDA", now, -100.0)

    stats = await get_live_trade_statistics(repo)

    assert stats is not None
    assert stats.win_rate == pytest.approx(2 / 3)
    assert stats.avg_win == pytest.approx(150.0)
    assert stats.avg_loss == pytest.approx(100.0)
    assert stats.sample_size == 3


@pytest.mark.asyncio
async def test_get_live_trade_statistics_respects_lookback_window(pool):
    repo = TradeOutcomeRepository(pool)
    base = datetime.now(timezone.utc).replace(microsecond=0)

    # a huge older loss that a lookback window should exclude
    await repo.record_outcome("AAPL", base - timedelta(days=30), -5000.0)
    # recent mixed trades inside the lookback window
    await repo.record_outcome("AAPL", base - timedelta(days=2), -100.0)
    await repo.record_outcome("AAPL", base - timedelta(days=1), 100.0)
    await repo.record_outcome("AAPL", base, 100.0)

    stats = await get_live_trade_statistics(repo, lookback=3)

    assert stats is not None
    assert stats.sample_size == 3
    assert stats.win_rate == pytest.approx(2 / 3)
    assert stats.avg_loss == pytest.approx(100.0)  # not 5000 -> confirms the old loss was excluded


@pytest.mark.asyncio
async def test_recent_pnls_returns_all_when_no_limit(pool):
    repo = TradeOutcomeRepository(pool)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    for pnl in (10.0, -5.0, 20.0):
        await repo.record_outcome("AAPL", now, pnl)

    pnls = await repo.recent_pnls()
    assert sorted(pnls) == [-5.0, 10.0, 20.0]


@pytest.mark.asyncio
async def test_pnls_since_excludes_outcomes_before_cutoff(pool):
    repo = TradeOutcomeRepository(pool)
    base = datetime.now(timezone.utc).replace(microsecond=0)
    await repo.record_outcome("AAPL", base - timedelta(days=2), -500.0)
    await repo.record_outcome("AAPL", base - timedelta(hours=1), 50.0)
    await repo.record_outcome("AAPL", base, 25.0)

    pnls = await repo.pnls_since(base - timedelta(days=1))

    assert sorted(pnls) == [25.0, 50.0]
