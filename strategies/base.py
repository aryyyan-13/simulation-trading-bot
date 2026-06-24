"""
Abstract base for all trading strategies.

A strategy receives a list of closed candles and returns a Signal. The
backtester decides what to do with that signal — the strategy only observes,
never executes directly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum


class Signal(str, Enum):
    LONG  = "LONG"   # open or stay long
    SHORT = "SHORT"  # open or stay short
    CLOSE = "CLOSE"  # close whatever is open, go flat
    HOLD  = "HOLD"   # do nothing (including "not enough data yet")


class BaseStrategy(ABC):
    """All strategies must implement this interface.

    Args to generate_signal:
        candles: list of dicts from exchange_client.get_klines(), oldest first.
                 Keys: open_time, open, high, low, close, volume, close_time.
                 The dict at index [-1] is the most recent *closed* bar.

    Returns:
        A Signal value. Return HOLD if there is not enough data yet.
        The strategy must never return a LONG or SHORT based on the current
        bar's open (look-ahead bias). Only closed candles are in the list.
    """

    @abstractmethod
    def generate_signal(self, candles: list[dict]) -> Signal:
        ...
