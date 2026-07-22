from collections import defaultdict
from collections.abc import Sequence
from datetime import date

from broker.models import OptionContract, OptionRight


def group_by_expiration(contracts: Sequence[OptionContract]) -> dict[date, list[OptionContract]]:
    groups: dict[date, list[OptionContract]] = defaultdict(list)
    for c in contracts:
        groups[c.expiration].append(c)
    return dict(groups)


def filter_by_right(contracts: Sequence[OptionContract], right: OptionRight) -> list[OptionContract]:
    return [c for c in contracts if c.right is right]


def filter_by_expiration(contracts: Sequence[OptionContract], expiration: date) -> list[OptionContract]:
    return [c for c in contracts if c.expiration == expiration]
