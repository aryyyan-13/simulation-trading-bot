"""
Bollinger Bands Mean-Reversion Strategy.

Uses a 20-period SMA ± 2 standard deviations:
  - LONG  when the close drops below the lower band (price is statistically far below average).
  - SHORT when the close rises above the upper band (price is statistically far above average).
  - CLOSE when the close crosses back through the middle band (the mean-reversion is
    complete; exit the trade).

HONEST WARNING
--------------
Bollinger Bands are a mean-reversion signal — they assume price will return to
its recent average. In trending markets (common in crypto), price can ride along
one band for an extended period, generating repeated losing signals against the
trend. A breakout above the upper band that the strategy shorts can just keep
going up.

The middle-band exit means trades are typically short-lived; this limits both
upside capture and worst-case loss per trade, but also means many small fees
accumulate. The backtester counts every one of them.
"""
from __future__ import annotations

import math

from strategies.base import BaseStrategy, Signal


def _bollinger(
    closes: list[float], period: int, num_std: float
) -> tuple[float, float, float] | None:
    """Compute (lower_band, middle_band, upper_band) using population std dev.

    Returns None if fewer than `period` values available.
    """
    if len(closes) < period:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((x - mid) ** 2 for x in window) / period  # population std dev
    std = math.sqrt(variance)
    return (mid - num_std * std, mid, mid + num_std * std)


class BollingerBandsStrategy(BaseStrategy):
    """Mean-reversion via Bollinger Bands: buy below lower, sell above upper, exit at mid."""

    def __init__(self, period: int = 20, num_std: float = 2.0):
        self.period = period
        self.num_std = num_std

    def generate_signal(self, candles: list[dict]) -> Signal:
        closes = [c["close"] for c in candles]
        # Need period+1 to compute two consecutive band sets (for crossover detection)
        if len(closes) < self.period + 1:
            return Signal.HOLD

        bands_prev = _bollinger(closes[:-1], self.period, self.num_std)
        bands_curr = _bollinger(closes, self.period, self.num_std)
        if bands_prev is None or bands_curr is None:
            return Signal.HOLD

        prev_close = closes[-2]
        curr_close = closes[-1]
        lower_curr, mid_curr, upper_curr = bands_curr
        _, mid_prev, _ = bands_prev

        # Entry signals: price entered a band extreme this bar
        if curr_close < lower_curr:
            return Signal.LONG
        if curr_close > upper_curr:
            return Signal.SHORT

        # Exit signals: price crossed back through the middle band
        # Long exit: was below mid, now at or above mid
        if prev_close < mid_prev and curr_close >= mid_curr:
            return Signal.CLOSE
        # Short exit: was above mid, now at or below mid
        if prev_close > mid_prev and curr_close <= mid_curr:
            return Signal.CLOSE

        return Signal.HOLD
