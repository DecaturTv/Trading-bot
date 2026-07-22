from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from broker.models import Bar
from scanner.service import ScannerService
from scanner.universe import UniverseManager


def make_bars(closes, volumes=None):
    now = datetime.now(timezone.utc)
    volumes = volumes or [1000.0] * len(closes)
    return [
        Bar(
            symbol="TEST",
            timestamp=now + timedelta(days=i),
            open=close,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=volumes[i],
        )
        for i, close in enumerate(closes)
    ]


@pytest.mark.asyncio
async def test_run_scans_aggregates_hits_across_universe():
    now = datetime.now(timezone.utc)
    broker = AsyncMock()
    universe = AsyncMock(spec=UniverseManager)
    universe.get_universe.return_value = ["AAPL", "TSLA"]

    closes = [100.0] * 21
    volumes = [1000.0] * 20 + [5000.0]
    broker.get_bars.return_value = make_bars(closes, volumes=volumes)

    service = ScannerService(broker, universe, bars_lookback=30)
    hits = await service.run_scans(now)

    assert {h.symbol for h in hits} == {"AAPL", "TSLA"}
    assert broker.get_bars.await_count == 2


@pytest.mark.asyncio
async def test_run_scans_skips_symbols_with_no_bars():
    now = datetime.now(timezone.utc)
    broker = AsyncMock()
    universe = AsyncMock(spec=UniverseManager)
    universe.get_universe.return_value = ["AAPL"]
    broker.get_bars.return_value = []

    service = ScannerService(broker, universe)
    hits = await service.run_scans(now)

    assert hits == []


@pytest.mark.asyncio
async def test_run_scans_returns_empty_when_universe_is_empty():
    now = datetime.now(timezone.utc)
    broker = AsyncMock()
    universe = AsyncMock(spec=UniverseManager)
    universe.get_universe.return_value = []

    service = ScannerService(broker, universe)
    hits = await service.run_scans(now)

    assert hits == []
    broker.get_bars.assert_not_awaited()
