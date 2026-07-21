import asyncio
import functools
import inspect
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


def retry(
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 10.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
):
    """Retry a sync or async callable with exponential backoff and jitter."""

    def decorator(func: Callable):
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                attempt = 0
                while True:
                    try:
                        return await func(*args, **kwargs)
                    except exceptions as exc:
                        attempt += 1
                        if attempt >= max_attempts:
                            raise
                        delay = _backoff_delay(attempt, base_delay, max_delay)
                        logger.warning(
                            "%s failed (attempt %d/%d): %s — retrying in %.2fs",
                            func.__name__, attempt, max_attempts, exc, delay,
                        )
                        await asyncio.sleep(delay)

            return async_wrapper

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    attempt += 1
                    if attempt >= max_attempts:
                        raise
                    delay = _backoff_delay(attempt, base_delay, max_delay)
                    logger.warning(
                        "%s failed (attempt %d/%d): %s — retrying in %.2fs",
                        func.__name__, attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)

        return sync_wrapper

    return decorator


def _backoff_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    delay = min(max_delay, base_delay * 2 ** (attempt - 1))
    return delay + random.uniform(0, delay * 0.1)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    pass


@dataclass
class CircuitBreaker:
    """Trips after consecutive failures; probes again after recovery_timeout."""

    failure_threshold: int = 5
    recovery_timeout: float = 30.0
    _state: CircuitState = field(default=CircuitState.CLOSED, init=False)
    _failure_count: int = field(default=0, init=False)
    _opened_at: float | None = field(default=None, init=False)

    @property
    def state(self) -> CircuitState:
        if self._state is CircuitState.OPEN and self._opened_at is not None:
            if time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
        return self._state

    def _on_success(self) -> None:
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at = None

    def _on_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    def call(self, func: Callable, *args, **kwargs):
        if self.state is CircuitState.OPEN:
            raise CircuitOpenError(f"circuit open for {getattr(func, '__name__', func)}")
        try:
            result = func(*args, **kwargs)
        except Exception:
            self._on_failure()
            raise
        self._on_success()
        return result

    async def call_async(self, func: Callable, *args, **kwargs):
        if self.state is CircuitState.OPEN:
            raise CircuitOpenError(f"circuit open for {getattr(func, '__name__', func)}")
        try:
            result = await func(*args, **kwargs)
        except Exception:
            self._on_failure()
            raise
        self._on_success()
        return result
