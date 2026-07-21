import asyncio

import pytest

from utils.retry import CircuitBreaker, CircuitOpenError, CircuitState, retry


def test_retry_sync_succeeds_after_transient_failures(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    calls = {"count": 0}

    @retry(max_attempts=3, base_delay=0.01)
    def flaky():
        calls["count"] += 1
        if calls["count"] < 3:
            raise ValueError("transient")
        return "ok"

    assert flaky() == "ok"
    assert calls["count"] == 3


def test_retry_sync_raises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)

    @retry(max_attempts=2, base_delay=0.01)
    def always_fails():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        always_fails()


@pytest.mark.asyncio
async def test_retry_async_succeeds_after_transient_failures(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    calls = {"count": 0}

    @retry(max_attempts=3, base_delay=0.01)
    async def flaky():
        calls["count"] += 1
        if calls["count"] < 2:
            raise ValueError("transient")
        return "ok"

    assert await flaky() == "ok"
    assert calls["count"] == 2


def test_circuit_breaker_opens_after_threshold():
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=60)

    def fails():
        raise RuntimeError("down")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            breaker.call(fails)

    assert breaker.state is CircuitState.OPEN
    with pytest.raises(CircuitOpenError):
        breaker.call(fails)


def test_circuit_breaker_recovers_after_timeout(monkeypatch):
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout=10)

    def fails():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        breaker.call(fails)
    assert breaker.state is CircuitState.OPEN

    clock = {"t": 100.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["t"])
    breaker._opened_at = 0.0
    clock["t"] = 20.0
    assert breaker.state is CircuitState.HALF_OPEN

    assert breaker.call(lambda: "recovered") == "recovered"
    assert breaker.state is CircuitState.CLOSED
