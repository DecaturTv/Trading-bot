from datetime import datetime, timezone

import pytest

from risk.halt_manager import HaltManager
from risk.halt_repository import HaltRepository


@pytest.mark.asyncio
async def test_not_halted_by_default(pool):
    manager = HaltManager(HaltRepository(pool))
    assert await manager.is_halted() is False


@pytest.mark.asyncio
async def test_halt_flips_state_and_persists(pool):
    manager = HaltManager(HaltRepository(pool))
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await manager.halt("daily loss limit breached", now)

    assert await manager.is_halted() is True


@pytest.mark.asyncio
async def test_resume_flips_state_back(pool):
    manager = HaltManager(HaltRepository(pool))
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await manager.halt("test halt", now)
    await manager.resume("manual clear", now)

    assert await manager.is_halted() is False


@pytest.mark.asyncio
async def test_state_reflects_most_recent_event_regardless_of_history(pool):
    manager = HaltManager(HaltRepository(pool))
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await manager.halt("first halt", now)
    await manager.resume("resumed", now)
    await manager.halt("halted again", now)

    assert await manager.is_halted() is True


@pytest.mark.asyncio
async def test_check_and_halt_on_loss_limits_triggers_on_daily_breach(pool):
    manager = HaltManager(HaltRepository(pool))
    now = datetime.now(timezone.utc).replace(microsecond=0)

    triggered = await manager.check_and_halt_on_loss_limits(
        daily_pnl_pct=-0.06, weekly_pnl_pct=-0.02, daily_limit_pct=0.05, weekly_limit_pct=0.10, now=now
    )

    assert triggered is True
    assert await manager.is_halted() is True


@pytest.mark.asyncio
async def test_check_and_halt_on_loss_limits_triggers_on_weekly_breach(pool):
    manager = HaltManager(HaltRepository(pool))
    now = datetime.now(timezone.utc).replace(microsecond=0)

    triggered = await manager.check_and_halt_on_loss_limits(
        daily_pnl_pct=-0.01, weekly_pnl_pct=-0.11, daily_limit_pct=0.05, weekly_limit_pct=0.10, now=now
    )

    assert triggered is True
    assert await manager.is_halted() is True


@pytest.mark.asyncio
async def test_check_and_halt_on_loss_limits_does_not_trigger_within_limits(pool):
    manager = HaltManager(HaltRepository(pool))
    now = datetime.now(timezone.utc).replace(microsecond=0)

    triggered = await manager.check_and_halt_on_loss_limits(
        daily_pnl_pct=-0.02, weekly_pnl_pct=-0.04, daily_limit_pct=0.05, weekly_limit_pct=0.10, now=now
    )

    assert triggered is False
    assert await manager.is_halted() is False


@pytest.mark.asyncio
async def test_check_and_halt_on_loss_limits_ignores_gains(pool):
    manager = HaltManager(HaltRepository(pool))
    now = datetime.now(timezone.utc).replace(microsecond=0)

    triggered = await manager.check_and_halt_on_loss_limits(
        daily_pnl_pct=0.10, weekly_pnl_pct=0.20, daily_limit_pct=0.05, weekly_limit_pct=0.10, now=now
    )

    assert triggered is False


@pytest.mark.asyncio
async def test_scopes_are_independent(pool):
    manager = HaltManager(HaltRepository(pool))
    now = datetime.now(timezone.utc).replace(microsecond=0)

    await manager.halt("options blew up", now, scope="equities")

    assert await manager.is_halted("equities") is True
    assert await manager.is_halted("forex") is False  # unaffected by the equities halt


@pytest.mark.asyncio
async def test_check_and_halt_on_loss_limits_only_halts_the_given_scope(pool):
    manager = HaltManager(HaltRepository(pool))
    now = datetime.now(timezone.utc).replace(microsecond=0)

    triggered = await manager.check_and_halt_on_loss_limits(
        daily_pnl_pct=-0.06, weekly_pnl_pct=-0.02, daily_limit_pct=0.05, weekly_limit_pct=0.10, now=now, scope="forex"
    )

    assert triggered is True
    assert await manager.is_halted("forex") is True
    assert await manager.is_halted("equities") is False
