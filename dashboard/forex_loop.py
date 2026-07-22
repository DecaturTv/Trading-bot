import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from alerts.models import Alert, Severity
from broker.models import OrderSide
from decision_engine.models import TradeDirection
from forex.models import OpenForexPosition
from forex.sizing import units_for_risk
from indicators.volatility import atr
from scanner.scans import scan_gap, scan_momentum, scan_unusual_volume
from utils.time import is_forex_market_open

from .context import AppContext, get_effective_forex_account

logger = logging.getLogger(__name__)

_CANDLE_LOOKBACK = 100
_MIN_CANDLES_FOR_SIGNAL = 30
_ATR_PERIOD = 14
_SCAN_FUNCTIONS = (scan_unusual_volume, scan_gap, scan_momentum)

EventCallback = Callable[[dict], Awaitable[None]] | None


async def _emit(on_event: EventCallback, event: dict) -> None:
    if on_event is not None:
        await on_event(event)


async def forex_entry_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    """Scans every tradeable currency pair OANDA offers for new entries (not
    a fixed list — fetched fresh each cycle so newly-listed pairs are picked
    up automatically). Gated on the forex broker being configured (opt-in),
    the 24/5 forex session, and the shared halt state — same halt as the
    equities loop, so a loss-limit breach on either side stops both."""
    if context.forex_broker is None:
        return
    if not is_forex_market_open(now):
        logger.info("forex entry cycle skipped: market closed")
        return
    if await context.halt_manager.is_halted():
        logger.info("forex entry cycle skipped: trading halted")
        return

    pairs = await context.forex_broker.get_tradeable_pairs()
    logger.info("forex entry cycle: scanning %d pairs", len(pairs))
    for pair in pairs:
        try:
            await _maybe_enter_forex(context, pair, now, on_event)
        except Exception:
            logger.exception("forex entry cycle failed for %s", pair)


async def _maybe_enter_forex(context: AppContext, pair: str, now: datetime, on_event: EventCallback) -> None:
    if await context.forex_position_repository.get(pair) is not None:
        return  # already have an open position in this pair

    bars = await context.forex_broker.get_candles(pair, count=_CANDLE_LOOKBACK)
    if len(bars) < _MIN_CANDLES_FOR_SIGNAL:
        return

    scan_hits = [hit for fn in _SCAN_FUNCTIONS if (hit := fn(pair, bars)) is not None]
    signal = context.decision_model.score(pair, bars, scan_hits, context.settings.forex_confidence_threshold)
    if not signal.meets_threshold or signal.direction is TradeDirection.NEUTRAL:
        return

    atr_values = atr(bars, _ATR_PERIOD)
    latest_atr = atr_values[-1]
    if latest_atr != latest_atr or latest_atr <= 0:  # NaN (warming up) or degenerate
        return
    stop_distance = latest_atr * context.settings.forex_stop_atr_multiplier

    _, entry_price = await context.forex_broker.get_pricing(pair)  # ask; conservative for either direction

    side = OrderSide.BUY if signal.direction is TradeDirection.BULLISH else OrderSide.SELL
    if side is OrderSide.BUY:
        stop_loss_price = entry_price - stop_distance
        take_profit_price = entry_price + stop_distance * context.settings.forex_take_profit_r_multiple
    else:
        stop_loss_price = entry_price + stop_distance
        take_profit_price = entry_price - stop_distance * context.settings.forex_take_profit_r_multiple

    account = await get_effective_forex_account(context)
    units = units_for_risk(account.equity, context.settings.forex_risk_pct_per_trade, stop_distance)
    if units <= 0:
        return

    trade_id = await context.forex_broker.submit_market_order(pair, units, side, stop_loss_price, take_profit_price)

    position = OpenForexPosition(
        pair=pair,
        side=side,
        units=units,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        take_profit_price=take_profit_price,
        oanda_trade_id=trade_id,
        opened_at=now,
    )
    await context.forex_position_repository.upsert(position)

    await context.alert_manager.send(
        Alert(
            title=f"Opened {side.value} on {pair}",
            message=(
                f"units={units} entry={entry_price:.5f} stop={stop_loss_price:.5f} "
                f"target={take_profit_price:.5f} confidence={signal.confidence:.1f}"
            ),
            severity=Severity.INFO,
            timestamp=now,
        )
    )
    await _emit(on_event, {"type": "forex_position_opened", "pair": pair, "units": units, "side": side.value})


async def forex_position_management_cycle(context: AppContext, now: datetime, on_event: EventCallback = None) -> None:
    """OANDA manages stop-loss/take-profit/trade closure itself (attached at
    order time); this cycle only notices when a tracked trade has closed on
    OANDA's side and reconciles the realized P&L — it never actively closes
    a position itself."""
    if context.forex_broker is None:
        return
    if not is_forex_market_open(now):
        logger.info("forex position management cycle skipped: market closed")
        return

    tracked = await context.forex_position_repository.get_all()
    logger.info("forex position management cycle: checking %d tracked positions", len(tracked))
    if not tracked:
        return

    open_trade_ids = await context.forex_broker.get_open_trade_ids()
    for position in tracked:
        try:
            await _reconcile_forex_position(context, position, open_trade_ids, now, on_event)
        except Exception:
            logger.exception("forex position reconciliation failed for %s", position.pair)


async def _reconcile_forex_position(
    context: AppContext, position: OpenForexPosition, open_trade_ids: set[str], now: datetime, on_event: EventCallback
) -> None:
    if position.oanda_trade_id in open_trade_ids:
        return  # still open

    pnl = await context.forex_broker.get_trade_realized_pnl(position.oanda_trade_id)
    await context.trade_outcome_repository.record_outcome(position.pair, now, pnl)
    await context.forex_position_repository.delete(position.pair)

    severity = Severity.WARNING if pnl < 0 else Severity.INFO
    await context.alert_manager.send(
        Alert(title=f"Closed {position.pair}", message=f"pnl={pnl:.2f}", severity=severity, timestamp=now)
    )
    await _emit(on_event, {"type": "forex_position_closed", "pair": position.pair, "pnl": pnl})
