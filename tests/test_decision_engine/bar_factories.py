from datetime import datetime, timedelta, timezone

from broker.models import Bar


def make_bars(closes, volumes=None, spread=1.0):
    now = datetime.now(timezone.utc)
    volumes = volumes or [1000.0] * len(closes)
    return [
        Bar(
            symbol="TEST",
            timestamp=now + timedelta(days=i),
            open=close,
            high=close + spread,
            low=close - spread,
            close=close,
            volume=volumes[i],
        )
        for i, close in enumerate(closes)
    ]
