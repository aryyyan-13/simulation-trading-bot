"""
RSI Mean-Reversion Strategy.

Uses Wilder's 14-period RSI to find oversold entry points:
  - Goes LONG when RSI crosses above the oversold threshold (crosses up through 30).
  - Issues CLOSE when RSI crosses above the overbought threshold (crosses up through 70)
    — this is the take-profit signal.
  - Does NOT short. If already long and neither signal fires, holds.

HONEST WARNING
--------------
RSI is a mean-reversion signal. It performs well in range-bound markets and
poorly in strong trends. In a trending bear market, price will repeatedly make
new lows, repeatedly trigger oversold, and repeatedly lose money going long.
The backtest will tell you which regime you were in; that may not be the regime
in the next 30 days.

"Crosses above 30" fires far fewer times than "RSI < 30" — this is intentional.
Fewer trades mean higher statistical uncertainty (each trade has more weight in
the result), but also lower fee drag. The trade-off is real and worth noting.
"""
from __future__ import annotations

from strategies.base import BaseStrategy, Signal


def _wilder_rsi(closes: list[float], period: int) -> float | None:
    """Compute Wilder's RSI for the last data point.

    Uses the standard initialisation: first `period` changes give the seed
    avg_gain / avg_loss via a simple average; subsequent changes are smoothed
    with Wilder's exponential formula (factor = 1/period).

    Needs at least period+1 values (for period changes). Returns None if
    insufficient data.
    """
    n = len(closes)
    if n < period + 1:
        return None

    # Seed: simple average of first `period` gains and losses
    gains, losses = [], []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    # Wilder's smoothing for any remaining bars
    for i in range(period + 1, n):
        delta = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


class RSIStrategy(BaseStrategy):
    """Oversold-entry, overbought-exit mean-reversion using Wilder's RSI."""

    def __init__(self, period: int = 14, oversold: float = 30.0, overbought: float = 70.0):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def generate_signal(self, candles: list[dict]) -> Signal:
        closes = [c["close"] for c in candles]
        # Need at least period+2 closes to compute two consecutive RSI values
        if len(closes) < self.period + 2:
            return Signal.HOLD

        rsi_prev = _wilder_rsi(closes[:-1], self.period)
        rsi_curr = _wilder_rsi(closes, self.period)
        if rsi_prev is None or rsi_curr is None:
            return Signal.HOLD

        # RSI crossed UP through oversold threshold → buy the bounce
        if rsi_prev <= self.oversold < rsi_curr:
            return Signal.LONG

        # RSI crossed UP through overbought threshold → take profit
        if rsi_prev <= self.overbought < rsi_curr:
            return Signal.CLOSE

        return Signal.HOLD
