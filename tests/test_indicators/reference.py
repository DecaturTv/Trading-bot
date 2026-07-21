import math


def assert_close_with_nans(actual, expected, tol=1e-9):
    assert len(actual) == len(expected)
    for i, (a, e) in enumerate(zip(actual, expected)):
        if e != e:  # NaN
            assert a != a, f"index {i}: expected NaN, got {a}"
        else:
            assert math.isclose(a, e, rel_tol=tol, abs_tol=tol), f"index {i}: {a} != {e}"


def reference_ema(values, period):
    """Textbook recursive EMA (adjust=False semantics), skipping leading NaNs
    for both seeding and the min_periods count — used to cross-check the
    pandas-based implementation without hardcoding expected numbers."""
    alpha = 2 / (period + 1)
    n = len(values)
    result = [float("nan")] * n
    ema_val = None
    count = 0
    for i, v in enumerate(values):
        if v != v:
            continue
        count += 1
        ema_val = v if ema_val is None else alpha * v + (1 - alpha) * ema_val
        if count >= period:
            result[i] = ema_val
    return result


def reference_rsi(values, period):
    """Textbook Wilder's RSI via a plain recursive loop."""
    n = len(values)
    result = [float("nan")] * n
    avg_gain = None
    avg_loss = None
    alpha = 1 / period
    count = 0
    for i in range(1, n):
        change = values[i] - values[i - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        count += 1
        if avg_gain is None:
            avg_gain, avg_loss = gain, loss
        else:
            avg_gain = alpha * gain + (1 - alpha) * avg_gain
            avg_loss = alpha * loss + (1 - alpha) * avg_loss
        if count >= period:
            if avg_gain == 0 and avg_loss == 0:
                result[i] = 50.0
            elif avg_loss == 0:
                result[i] = 100.0
            else:
                rs = avg_gain / avg_loss
                result[i] = 100 - (100 / (1 + rs))
    return result


def reference_wilder_smoothing(values, period):
    alpha = 1 / period
    n = len(values)
    result = [float("nan")] * n
    avg = None
    count = 0
    for i, v in enumerate(values):
        if v != v:
            continue
        count += 1
        avg = v if avg is None else alpha * v + (1 - alpha) * avg
        if count >= period:
            result[i] = avg
    return result
