from collections.abc import Sequence
from dataclasses import dataclass, field

from broker.models import Account, Position

from .halt_manager import HaltManager


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    reason: str | None = None


@dataclass(frozen=True)
class PreTradeCheckResult:
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)


class PreTradeChecker:
    """Pre-trade checks: halt state, buying power, correlation/exposure with
    existing positions. "Correlation" here is scoped to what's computable
    from broker Position data alone (same-symbol concentration, total
    exposure) — a real correlation matrix would need sector/factor reference
    data this project doesn't have yet.

    `positions` must be scoped to the calling sleeve's own currently-tracked
    book (its own position repository's symbols/legs), not the raw combined
    account from broker.get_positions() — max_open_positions and
    total_exposure both size their cap off `account.equity`, which is the
    caller's own small synthetic sub-balance (see
    dashboard/context.py:get_effective_*_account), not the real shared
    Alpaca account. Passing the unfiltered combined book compares one
    sleeve's virtual few-thousand-dollar equity against every sleeve's
    positions combined, plus anything sitting in the account that no sleeve
    is even tracking -- which is exactly what happened on 2026-08-31: an
    orphaned INTC position no repository knew about pushed "exposure" past
    90% for every sleeve simultaneously and froze all new entries for three
    weeks. See project memory on the INTC trade.
    """

    def __init__(
        self,
        halt_manager: HaltManager,
        max_open_positions: int = 10,
        max_positions_per_symbol: int = 1,
        max_total_exposure_pct: float = 0.90,
    ):
        self._halt_manager = halt_manager
        self._max_open_positions = max_open_positions
        self._max_positions_per_symbol = max_positions_per_symbol
        self._max_total_exposure_pct = max_total_exposure_pct

    @property
    def max_total_exposure_pct(self) -> float:
        """Exposed so callers can pre-filter candidates that could never pass
        _check_total_exposure (e.g. scanner/universe.py's price ceiling)
        without duplicating this threshold as a separate constant."""
        return self._max_total_exposure_pct

    async def evaluate(
        self,
        account: Account,
        positions: Sequence[Position],
        symbol: str,
        estimated_cost: float,
    ) -> PreTradeCheckResult:
        checks = [
            await self._check_not_halted(),
            self._check_buying_power(account, estimated_cost),
            self._check_max_open_positions(positions),
            self._check_symbol_concentration(positions, symbol),
            self._check_total_exposure(account, positions, estimated_cost),
        ]
        return PreTradeCheckResult(passed=all(c.passed for c in checks), checks=checks)

    async def _check_not_halted(self) -> CheckResult:
        # Only used for the equities (options + direct stock) entry paths --
        # forex checks halt_manager directly with scope="forex" instead of
        # going through PreTradeChecker.
        halted = await self._halt_manager.is_halted("equities")
        return CheckResult(name="not_halted", passed=not halted, reason="trading is halted" if halted else None)

    def _check_buying_power(self, account: Account, estimated_cost: float) -> CheckResult:
        passed = estimated_cost <= account.buying_power
        reason = None if passed else f"estimated cost {estimated_cost:.2f} exceeds buying power {account.buying_power:.2f}"
        return CheckResult(name="buying_power", passed=passed, reason=reason)

    def _check_max_open_positions(self, positions: Sequence[Position]) -> CheckResult:
        count = len(positions)
        passed = count < self._max_open_positions
        reason = None if passed else f"{count} open positions already at/above cap {self._max_open_positions}"
        return CheckResult(name="max_open_positions", passed=passed, reason=reason)

    def _check_symbol_concentration(self, positions: Sequence[Position], symbol: str) -> CheckResult:
        count = sum(1 for p in positions if p.symbol == symbol)
        passed = count < self._max_positions_per_symbol
        reason = (
            None
            if passed
            else f"{count} existing position(s) in {symbol} already at/above cap {self._max_positions_per_symbol}"
        )
        return CheckResult(name="symbol_concentration", passed=passed, reason=reason)

    def _check_total_exposure(self, account: Account, positions: Sequence[Position], estimated_cost: float) -> CheckResult:
        current_exposure = sum(abs(p.market_value) for p in positions)
        projected_exposure = current_exposure + estimated_cost
        exposure_pct = projected_exposure / account.equity if account.equity > 0 else float("inf")
        passed = exposure_pct <= self._max_total_exposure_pct
        reason = None if passed else f"projected exposure {exposure_pct:.1%} exceeds cap {self._max_total_exposure_pct:.1%}"
        return CheckResult(name="total_exposure", passed=passed, reason=reason)
