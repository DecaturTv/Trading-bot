import sys
from pathlib import Path

# Reuse the bar factories from the backtesting test suite (make_bars,
# make_hourly_bars) — pytest only puts a test file's own directory on
# sys.path, so add the sibling suite explicitly.
_BT_TESTS = Path(__file__).parent.parent / "test_backtesting"
if str(_BT_TESTS) not in sys.path:
    sys.path.insert(0, str(_BT_TESTS))
