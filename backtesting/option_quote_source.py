"""How the backtest engine gets an option's price.

Two implementations behind one interface:

- `SimulatedOptionQuoteSource` — the original Black-Scholes stub
  (`simulated_pricing.py`). Prices any strike/expiration off the underlying +
  a realized-vol proxy. The engine default, so existing tests are unchanged.

- `HistoricalOptionQuoteSource` — marks against **real historical option
  bars** loaded from `option_bars` (see `data.option_bars_repository` and
  `backtesting.ingest_option_history`). No synthetic fallback: if a contract
  has no bar at/near the timestamp (within `max_ffill_bars`), `mark` returns
  `None` and the engine skips the entry / holds the position.

Both are synchronous — `HistoricalOptionQuoteSource` is built from data
preloaded once per symbol (see `build_historical_quote_source`), so the
engine's per-bar loop stays a pure in-memory lookup.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import NamedTuple, Protocol

from broker.models import Bar, OptionRight, OrderSide
from options.greeks import black_scholes

from .simulated_pricing import (
    SimulatedLeg,
    build_synthetic_chain,
    select_synthetic_strike_by_delta,
    simulated_strategy_value,
    strike_increment,
)

_CONTRACT_MULTIPLIER = 100

# Source tags on a MarkResult, and the coverage buckets the report rolls up.
HISTORICAL = "historical"
FFILL = "ffill"
SIMULATED = "simulated"


class MarkResult(NamedTuple):
    value_per_unit: float  # per-contract dollars (per-share price x 100), signed by leg side
    source: str  # HISTORICAL | FFILL | SIMULATED


class OptionQuoteSource(Protocol):
    def select_leg(
        self,
        *,
        underlying: str,
        as_of: datetime,
        target_expiration: date,
        right: OptionRight,
        target_delta: float,
        underlying_price: float,
        volatility: float,
    ) -> SimulatedLeg | None:
        """Pick the contract to open, or None if nothing usable is available."""

    def mark(
        self, legs: Sequence[SimulatedLeg], underlying_price: float, as_of: datetime, volatility: float
    ) -> MarkResult | None:
        """Value one unit of the strategy at as_of, or None if it can't be priced."""

    def last_mark(
        self, legs: Sequence[SimulatedLeg], underlying_price: float, as_of: datetime, volatility: float
    ) -> MarkResult | None:
        """Best-effort close-out value (last known price, any age) for a
        forced end-of-data close. None only if the legs were never priced."""


class SimulatedOptionQuoteSource:
    """Black-Scholes stub — the original behavior, always returns a price."""

    def __init__(self, risk_free_rate: float = 0.0):
        self._rfr = risk_free_rate

    def select_leg(
        self, *, underlying, as_of, target_expiration, right, target_delta, underlying_price, volatility
    ) -> SimulatedLeg | None:
        chain = build_synthetic_chain(
            underlying_price, target_expiration, as_of.date(), volatility, right,
            strike_increment=strike_increment(underlying_price),
        )
        signed_target = target_delta if right is OptionRight.CALL else -target_delta
        strike = select_synthetic_strike_by_delta(chain, signed_target)
        return SimulatedLeg(strike=strike, expiration=target_expiration, right=right, side=OrderSide.BUY)

    def mark(self, legs, underlying_price, as_of, volatility) -> MarkResult | None:
        value = simulated_strategy_value(legs, underlying_price, as_of.date(), volatility, self._rfr)
        return MarkResult(value, SIMULATED)

    def last_mark(self, legs, underlying_price, as_of, volatility) -> MarkResult | None:
        return self.mark(legs, underlying_price, as_of, volatility)


@dataclass(frozen=True)
class _ContractSeries:
    occ_symbol: str
    strike: float
    expiration: date
    right: OptionRight
    timestamps: list[datetime]  # sorted; parallel to closes
    closes: list[float]


class HistoricalOptionQuoteSource:
    """Marks against preloaded real option bars. No synthetic fallback."""

    def __init__(self, series: Sequence[_ContractSeries], bar_interval: timedelta, max_ffill_bars: int = 3):
        self._max_gap = bar_interval * max_ffill_bars
        self._by_key: dict[str, _ContractSeries] = {s.occ_symbol: s for s in series}
        # expiration -> right -> [(strike, occ_symbol)] sorted by strike
        self._chain: dict[date, dict[OptionRight, list[tuple[float, str]]]] = {}
        for s in series:
            self._chain.setdefault(s.expiration, {}).setdefault(s.right, []).append((s.strike, s.occ_symbol))
        for by_right in self._chain.values():
            for lst in by_right.values():
                lst.sort()

    @property
    def contract_count(self) -> int:
        """How many real contract series were preloaded (0 = no option data
        for this underlying in the window)."""
        return len(self._by_key)

    def select_leg(
        self, *, underlying, as_of, target_expiration, right, target_delta, underlying_price, volatility
    ) -> SimulatedLeg | None:
        expirations = [e for e in self._chain if right in self._chain[e] and e > as_of.date()]
        if not expirations:
            return None
        expiration = min(expirations, key=lambda e: abs((e - target_expiration).days))
        strikes = self._chain[expiration][right]

        tte_years = max((expiration - as_of.date()).days, 0) / 365

        def delta_gap(strike: float) -> float:
            if tte_years == 0 or volatility <= 0:
                return abs(strike - underlying_price)  # fall back to moneyness ordering
            greeks = black_scholes(underlying_price, strike, tte_years, volatility, right)
            return abs(abs(greeks.delta) - abs(target_delta))

        # Nearest-delta strike that actually has a mark at as_of; walk outward if not.
        for strike, occ in sorted(strikes, key=lambda pair: delta_gap(pair[0])):
            if self._mark_one(occ, as_of) is not None:
                return SimulatedLeg(
                    strike=strike, expiration=expiration, right=right, side=OrderSide.BUY, occ_symbol=occ
                )
        return None

    def mark(self, legs, underlying_price, as_of, volatility) -> MarkResult | None:
        total = 0.0
        source = HISTORICAL
        for leg in legs:
            if leg.occ_symbol is None:
                return None
            hit = self._mark_one(leg.occ_symbol, as_of)
            if hit is None:
                return None
            per_share, exact = hit
            if not exact:
                source = FFILL
            signed = per_share if leg.side is OrderSide.BUY else -per_share
            total += signed
        return MarkResult(total * _CONTRACT_MULTIPLIER, source)

    def last_mark(self, legs, underlying_price, as_of, volatility) -> MarkResult | None:
        total = 0.0
        for leg in legs:
            if leg.occ_symbol is None:
                return None
            series = self._by_key.get(leg.occ_symbol)
            if series is None or not series.timestamps:
                return None
            idx = bisect.bisect_right(series.timestamps, as_of) - 1
            if idx < 0:
                return None
            per_share = series.closes[idx]
            total += per_share if leg.side is OrderSide.BUY else -per_share
        return MarkResult(total * _CONTRACT_MULTIPLIER, FFILL)

    def _mark_one(self, occ_symbol: str, as_of: datetime) -> tuple[float, bool] | None:
        """(per-share close, is_exact) for the bar at/just-before as_of, or
        None if the nearest prior bar is older than max_ffill_bars intervals.
        is_exact = a bar exists at as_of itself (no forward-fill needed)."""
        series = self._by_key.get(occ_symbol)
        if series is None or not series.timestamps:
            return None
        idx = bisect.bisect_right(series.timestamps, as_of) - 1
        if idx < 0:
            return None
        gap = as_of - series.timestamps[idx]
        if gap <= timedelta(0):
            return series.closes[idx], True
        if gap > self._max_gap:
            return None
        return series.closes[idx], False


def _bar_interval(timeframe: str) -> timedelta:
    m = re.fullmatch(r"(\d+)(Min|Hour|Day)", timeframe)
    if not m:
        raise ValueError(f"unsupported timeframe {timeframe!r}")
    n, unit = int(m.group(1)), m.group(2)
    return {"Min": timedelta(minutes=n), "Hour": timedelta(hours=n), "Day": timedelta(days=n)}[unit]


async def build_historical_quote_source(
    option_repo,
    underlying: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    exp_gte: date,
    exp_lte: date,
    max_ffill_bars: int = 3,
) -> HistoricalOptionQuoteSource:
    """Preload every real option series for `underlying` that has a bar in the
    window, so the engine's per-bar loop is a pure lookup."""
    contracts = await option_repo.get_contracts_with_bars(underlying, timeframe, exp_gte, exp_lte, start, end)
    series: list[_ContractSeries] = []
    for c in contracts:
        bars: list[Bar] = await option_repo.get_option_bars(c.symbol, timeframe, start, end)
        if not bars:
            continue
        bars.sort(key=lambda b: b.timestamp)
        series.append(
            _ContractSeries(
                occ_symbol=c.symbol,
                strike=c.strike,
                expiration=c.expiration,
                right=c.right,
                timestamps=[b.timestamp for b in bars],
                closes=[b.close for b in bars],
            )
        )
    return HistoricalOptionQuoteSource(series, _bar_interval(timeframe), max_ffill_bars=max_ffill_bars)
