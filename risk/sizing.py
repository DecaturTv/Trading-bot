from .kelly import KellyResult


def position_budget_dollars(account_equity: float, kelly_result: KellyResult) -> float:
    if account_equity < 0:
        raise ValueError("account_equity must be >= 0")
    return account_equity * kelly_result.position_fraction


def contracts_for_budget(budget_dollars: float, net_debit_per_contract: float) -> int:
    if net_debit_per_contract <= 0:
        raise ValueError("net_debit_per_contract must be positive")
    if budget_dollars < 0:
        raise ValueError("budget_dollars must be >= 0")
    return int(budget_dollars // net_debit_per_contract)
