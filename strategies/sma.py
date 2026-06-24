"""
SMA Crossover Strategy.

Goes LONG when the fast SMA is above the slow SMA, SHORT when below.
This is a pure trend-following strategy — it is always in the market
(either long or short), flipping on every cross.

Default: fast=9, slow=21 on 1-hour candles → roughly 9-hour and 21-hour
trend windows.

HONEST WARNING
--------------
SMA crossover is probably the single most backtested strategy in existence.
Studies consistently find it has no reliable edge after transaction costs in
liquid markets — any apparent edge is highly sensitive to the chosen periods
and the specific date range tested. A good backtest result here should be
treated as noise until proven otherwise across many different market regimes
and time windows.
"""
from __future__ import annotations

from strategies.base import BaseStrategy, Signal


def _sma(closes: list[float], period: int) -> float | None:
    """Simple moving average of the last `period` values.  Returns None if
    there are fewer than `period` data points."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


class SMACrossoverStrategy(BaseStrategy):
    """Trend-following: LONG when fast SMA > slow SMA, SHORT otherwise."""

    def __init__(self, fast: int = 9, slow: int = 21):
        if fast >= slow:
            raise ValueError(
                f"fast period ({fast}) must be strictly less than slow period ({slow})"
            )
        self.fast = fast
        self.slow = slow

    def generate_signal(self, candles: list[dict]) -> Signal:
        closes = [c["close"] for c in candles]
        fast_val = _sma(closes, self.fast)
        slow_val = _sma(closes, self.slow)
        if fast_val is None or slow_val is None:
            return Signal.HOLD
        return Signal.LONG if fast_val > slow_val else Signal.SHORT
